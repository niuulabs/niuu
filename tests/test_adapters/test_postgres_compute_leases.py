"""SQL adapter unit tests; real transaction invariants have CI integration tests."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from volundr.adapters.outbound.postgres_compute_leases import PostgresComputeLeaseRepository
from volundr.domain.compute import ComputeCapacityError, ComputeLease, ComputeLeaseBusyError


def lease():
    return ComputeLease(
        id=uuid4(),
        pool_id="pool",
        session_id=uuid4(),
        tenant_id="tenant",
        owner_id="owner",
        profile="small",
        request_fingerprint="test-digest",
    )


@pytest.fixture
def setup():
    conn = AsyncMock()
    conn.transaction = MagicMock()
    conn.transaction.return_value.__aenter__ = AsyncMock()
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    pool = AsyncMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return PostgresComputeLeaseRepository(pool), pool, conn


async def test_reserve_counts_all_unreleased_claims_and_matches_identity(setup):
    repo, pool, conn = setup
    request = lease()
    conn.fetchval.side_effect = [None, None, 0]
    assert await repo.reserve(request, 1) == request
    assert "INSERT INTO compute_leases" in conn.execute.call_args.args[0]
    conn.fetchval.side_effect = [request.model_dump_json()]
    assert await repo.reserve(request, 1) == request
    conn.fetchval.side_effect = [request.model_dump_json()]
    with pytest.raises(ValueError, match="different compute claim"):
        await repo.reserve(request.model_copy(update={"tenant_id": "other"}), 1)
    conn.fetchval.side_effect = [None, None, 1]
    with pytest.raises(ComputeCapacityError):
        await repo.reserve(request, 1)
    with pytest.raises(ValueError):
        await repo.reserve(request, 0)


async def test_read_and_list(setup):
    repo, pool, _ = setup
    request = lease()
    pool.fetchval.return_value = request.model_dump_json()
    assert await repo.get(request.id) == request
    pool.fetchval.return_value = None
    assert await repo.get(request.id) is None
    pool.fetch.return_value = [{"data": request.model_dump_json()}]
    assert await repo.list("pool") == [request]


async def test_operation_owns_same_connection_for_updates_and_unlocks_on_failure(setup):
    repo, _, conn = setup
    request = lease()
    conn.fetchval.side_effect = [True, request.model_dump_json()]
    conn.execute.return_value = "UPDATE 1"
    with pytest.raises(RuntimeError, match="provider failed"):
        async with repo.operation(request.id):
            assert await repo.get(request.id) == request
            await repo.save(request)
            with pytest.raises(RuntimeError, match="nested"):
                async with repo.operation(uuid4()):
                    pass
            raise RuntimeError("provider failed")
    assert "pg_advisory_unlock" in conn.execute.call_args.args[0]
    with pytest.raises(RuntimeError, match="ownership"):
        await repo.save(request)


async def test_busy_and_disappeared_lease_fail(setup):
    repo, _, conn = setup
    request = lease()
    conn.fetchval.return_value = False
    with pytest.raises(ComputeLeaseBusyError):
        async with repo.operation(request.id):
            pass
    conn.fetchval.return_value = True
    conn.execute.return_value = "UPDATE 0"
    async with repo.operation(request.id):
        with pytest.raises(LookupError):
            await repo.save(request)


async def test_policy_is_durable_and_pool_admission_uses_it(setup):
    from volundr.domain.compute import ComputePoolPolicy

    repo, pool, conn = setup
    policy = ComputePoolPolicy(profile="small", max_machines=3, warm_min=1)
    pool.fetchval.return_value = policy.model_dump_json()
    assert await repo.policy("pool", policy) == policy
    await repo.set_policy("pool", policy)
    assert "ON CONFLICT" in conn.execute.call_args.args[0]
    # Even if a caller has a stale higher cap, database policy wins.
    conn.fetchval.side_effect = [None, policy.model_dump_json(), 0, 3]
    with pytest.raises(ComputeCapacityError):
        await repo.reserve(lease(), 100)
    conn.fetchval.side_effect = [None, policy.model_copy(update={"paused": True}).model_dump_json()]
    with pytest.raises(ComputeCapacityError, match="paused"):
        await repo.reserve(lease(), 100)
    conn.fetchval.side_effect = [None, policy.model_dump_json(), 1]
    with pytest.raises(ComputeCapacityError, match="provisioning"):
        await repo.reserve(lease(), 100)
    conn.fetchval.side_effect = [None, policy.model_dump_json(), 0, 1]
    with pytest.raises(ComputeCapacityError, match="standby"):
        await repo.reserve(lease().model_copy(update={"session_id": None}), 100)


async def test_binding_requires_ownership_and_rechecks_idle_and_admission(setup):
    from volundr.domain.compute import ComputePoolPolicy, LeaseState

    repo, pool, conn = setup
    bound = lease().model_copy(update={"state": LeaseState.READY})
    idle = bound.model_copy(update={"session_id": None, "state": LeaseState.IDLE})
    policy = ComputePoolPolicy(profile="small", max_machines=2)
    with pytest.raises(RuntimeError, match="ownership"):
        await repo.bind(bound)
    conn.fetchval.side_effect = [True, policy.model_dump_json(), idle.model_dump_json(), None]
    conn.execute.return_value = "UPDATE 1"
    async with repo.operation(bound.id):
        assert await repo.bind(bound) == bound
    conn.fetchval.side_effect = [True, policy.model_copy(update={"drain": True}).model_dump_json()]
    async with repo.operation(bound.id):
        with pytest.raises(ComputeCapacityError, match="paused"):
            await repo.bind(bound)
    conn.fetchval.side_effect = [True, policy.model_dump_json(), bound.model_dump_json()]
    async with repo.operation(bound.id):
        with pytest.raises(ComputeLeaseBusyError, match="no longer idle"):
            await repo.bind(bound)
    conn.fetchval.side_effect = [
        True,
        policy.model_dump_json(),
        idle.model_dump_json(),
        bound.model_dump_json(),
    ]
    async with repo.operation(bound.id):
        with pytest.raises(ComputeLeaseBusyError, match="already owns"):
            await repo.bind(bound)
    await repo.quarantine(idle.model_copy(update={"state": LeaseState.QUARANTINED}))
    assert "compute_leases.state = 'released'" in pool.execute.call_args.args[0]
