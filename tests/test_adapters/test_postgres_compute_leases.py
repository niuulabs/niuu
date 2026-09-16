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
    conn.fetchval.side_effect = [None, 0]
    assert await repo.reserve(request, 1) == request
    assert "INSERT INTO compute_leases" in conn.execute.call_args.args[0]
    conn.fetchval.side_effect = [request.model_dump_json()]
    assert await repo.reserve(request, 1) == request
    conn.fetchval.side_effect = [request.model_dump_json()]
    with pytest.raises(ValueError, match="different compute claim"):
        await repo.reserve(request.model_copy(update={"tenant_id": "other"}), 1)
    conn.fetchval.side_effect = [None, 1]
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
