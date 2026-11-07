"""SQL adapter unit tests; real transaction invariants have CI integration tests."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from volundr.adapters.outbound.postgres_compute_leases import PostgresComputeLeaseRepository
from volundr.domain.compute import (
    ComputeCapacityError,
    ComputeLease,
    ComputeLeaseBusyError,
    ComputePoolFullError,
    ComputePoolPolicy,
    ComputeProvisioningLimitError,
    ComputeSlot,
    LeaseState,
    SlotLease,
)


def machine(**update):
    fields = {
        "id": uuid4(),
        "pool_id": "pool",
        "profile": "small",
        "request_fingerprint": "test-digest",
    }
    return ComputeLease(**{**fields, **update})


def binding(lease, **update):
    fields = {
        "id": uuid4(),
        "allocation_id": lease.id,
        "slot": 0,
        "pool_id": lease.pool_id,
        "session_id": uuid4(),
        "tenant_id": "tenant",
        "owner_id": "owner",
    }
    return ComputeSlot(**{**fields, **update})


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


def claim_row(lease, slot):
    return {"machine": lease.model_dump_json(), "slot": slot.model_dump_json()}


async def test_reserve_inserts_the_machine_and_its_binding_when_the_pool_has_room(setup):
    repo, _, conn = setup
    lease = machine()
    slot = binding(lease)
    conn.fetchrow.return_value = None
    conn.fetchval.side_effect = [None, 0]

    assert await repo.reserve(lease, 1, slot) == (lease, slot)

    inserts = [call.args[0] for call in conn.execute.call_args_list]
    assert "pg_advisory_xact_lock" in inserts[0]
    assert "INSERT INTO compute_leases" in inserts[1]
    assert "INSERT INTO compute_slots" in inserts[2]
    # The machine record is stored with no session; the binding carries it.
    machine_args = conn.execute.call_args_list[1].args
    assert machine_args[3] is not None and "session_id" not in machine_args[4]
    slot_args = conn.execute.call_args_list[2].args
    assert slot_args[1:7] == (slot.id, lease.id, 0, "pool", slot.session_id, "starting")


async def test_reserve_without_a_session_inserts_only_the_machine(setup):
    repo, _, conn = setup
    lease = machine()
    conn.fetchval.side_effect = [None, 0]

    assert await repo.reserve(lease, 1) == (lease, None)

    assert [("compute_slots" in call.args[0]) for call in conn.execute.call_args_list] == [
        False,
        False,
    ]
    conn.fetchrow.assert_not_awaited()


async def test_reserve_returns_the_sessions_existing_claim_and_checks_its_identity(setup):
    repo, _, conn = setup
    lease = machine()
    slot = binding(lease)
    conn.fetchrow.return_value = claim_row(lease, slot)

    assert await repo.reserve(lease, 1, slot) == (lease, slot)
    conn.fetchval.assert_not_awaited()

    for changed in (
        {"tenant_id": "other"},
        {"owner_id": "other"},
    ):
        with pytest.raises(ValueError, match="different compute claim"):
            await repo.reserve(lease, 1, slot.model_copy(update=changed))
    for changed in ({"profile": "large"}, {"request_fingerprint": "other"}):
        with pytest.raises(ValueError, match="different compute claim"):
            await repo.reserve(lease.model_copy(update=changed), 1, slot)


async def test_reserve_counts_all_unreleased_machines_and_a_full_pool_is_loud(setup):
    repo, _, conn = setup
    lease = machine()
    conn.fetchrow.return_value = None
    conn.fetchval.side_effect = [None, 1]

    with pytest.raises(ComputePoolFullError, match="maximum machines"):
        await repo.reserve(lease, 1, binding(lease))
    with pytest.raises(ValueError):
        await repo.reserve(lease, 0)
    assert "state != 'released'" in conn.fetchval.call_args.args[0]


async def test_the_existing_claim_is_found_through_the_sessions_live_binding(setup):
    repo, _, conn = setup
    lease = machine()
    conn.fetchrow.return_value = None
    conn.fetchval.side_effect = [None, 0]

    await repo.reserve(lease, 1, binding(lease))

    sql, pool_id, session_id, states = conn.fetchrow.call_args.args
    assert "JOIN compute_leases" in sql and "FROM compute_slots s" in sql
    assert states == ["busy", "draining", "failed", "starting"]


async def test_read_and_list(setup):
    repo, pool, _ = setup
    lease = machine()
    pool.fetchval.return_value = lease.model_dump_json()
    assert await repo.get(lease.id) == lease
    pool.fetchval.return_value = None
    assert await repo.get(lease.id) is None
    pool.fetch.return_value = [{"data": lease.model_dump_json()}]
    assert await repo.list("pool") == [lease]


async def test_operation_owns_same_connection_for_updates_and_unlocks_on_failure(setup):
    repo, _, conn = setup
    lease = machine()
    conn.fetchval.side_effect = [True, lease.model_dump_json()]
    conn.execute.return_value = "UPDATE 1"
    with pytest.raises(RuntimeError, match="provider failed"):
        async with repo.operation(lease.id):
            assert await repo.get(lease.id) == lease
            await repo.save(lease)
            with pytest.raises(RuntimeError, match="nested"):
                async with repo.operation(uuid4()):
                    pass
            raise RuntimeError("provider failed")
    assert "pg_advisory_unlock" in conn.execute.call_args.args[0]
    with pytest.raises(RuntimeError, match="ownership"):
        await repo.save(lease)


async def test_busy_and_disappeared_lease_fail(setup):
    repo, _, conn = setup
    lease = machine()
    conn.fetchval.return_value = False
    with pytest.raises(ComputeLeaseBusyError):
        async with repo.operation(lease.id):
            pass
    conn.fetchval.return_value = True
    conn.execute.return_value = "UPDATE 0"
    async with repo.operation(lease.id):
        with pytest.raises(LookupError):
            await repo.save(lease)


async def test_policy_is_durable_and_pool_admission_uses_it(setup):
    repo, pool, conn = setup
    policy = ComputePoolPolicy(profile="small", max_machines=3, warm_min=1)
    pool.fetchval.return_value = policy.model_dump_json()
    assert await repo.policy("pool", policy) == policy
    await repo.set_policy("pool", policy)
    assert "ON CONFLICT" in conn.execute.call_args.args[0]
    lease = machine()
    slot = binding(lease)
    conn.fetchrow.return_value = None
    # Even if a caller has a stale higher cap, database policy wins.
    conn.fetchval.side_effect = [policy.model_dump_json(), 0, 3]
    with pytest.raises(ComputePoolFullError):
        await repo.reserve(lease, 100, slot)
    conn.fetchval.side_effect = [policy.model_copy(update={"paused": True}).model_dump_json()]
    with pytest.raises(ComputeCapacityError, match="paused"):
        await repo.reserve(lease, 100, slot)
    conn.fetchval.side_effect = [policy.model_dump_json(), 1]
    with pytest.raises(ComputeProvisioningLimitError, match="provisioning"):
        await repo.reserve(lease, 100, slot)


async def test_a_policy_stored_before_slots_existed_reads_as_one_slot(setup):
    repo, pool, _ = setup
    pool.fetchval.return_value = (
        '{"profile": "small", "max_machines": 2, "reuse_policy": "replace", "warm_min": 1,'
        ' "max_provisioning": 1, "idle_timeout_seconds": 3600.0,'
        ' "provisioning_timeout_seconds": 600.0, "paused": false, "drain": false}'
    )

    policy = await repo.policy("pool", ComputePoolPolicy(profile="small", max_machines=2))

    assert (policy.slots_per_machine, policy.max_sessions_per_machine) == (1, None)
    assert policy.max_machine_age_seconds is None


async def test_binding_requires_ownership_and_rechecks_idle_and_admission(setup):
    repo, pool, conn = setup
    idle = machine(state=LeaseState.IDLE)
    slot = binding(idle)
    policy = ComputePoolPolicy(profile="small", max_machines=2)
    with pytest.raises(RuntimeError, match="ownership"):
        await repo.bind(idle, slot)

    # fetchval: lock, policy, machine; fetch: its slots; fetchrow: the session's live claim.
    conn.fetchrow.return_value = None
    conn.fetch.return_value = []
    conn.fetchval.side_effect = [True, policy.model_dump_json(), idle.model_dump_json()]
    conn.execute.return_value = "UPDATE 1"
    async with repo.operation(idle.id):
        bound = await repo.bind(idle, slot)
    assert isinstance(bound, SlotLease)
    assert bound.binding == slot
    # The machine stays idle but is no longer empty.
    assert bound.allocation.state == LeaseState.IDLE and bound.allocation.idle_since is None
    sql = [call.args[0] for call in conn.execute.call_args_list]
    assert any("INSERT INTO compute_slots" in text for text in sql)
    assert any("UPDATE compute_leases" in text for text in sql)

    conn.fetchval.side_effect = [True, policy.model_copy(update={"drain": True}).model_dump_json()]
    async with repo.operation(idle.id):
        with pytest.raises(ComputeCapacityError, match="paused"):
            await repo.bind(idle, slot)

    ready = idle.model_copy(update={"state": LeaseState.READY})
    conn.fetchval.side_effect = [True, policy.model_dump_json(), ready.model_dump_json()]
    async with repo.operation(idle.id):
        with pytest.raises(ComputeLeaseBusyError, match="no longer idle"):
            await repo.bind(idle, slot)

    conn.fetchval.side_effect = [True, policy.model_dump_json(), None]
    async with repo.operation(idle.id):
        with pytest.raises(ComputeLeaseBusyError, match="no longer idle"):
            await repo.bind(idle, slot)

    await repo.quarantine(idle.model_copy(update={"state": LeaseState.QUARANTINED}))
    assert "compute_leases.state = 'released'" in pool.execute.call_args.args[0]


async def test_binding_refuses_a_session_that_already_owns_a_live_binding(setup):
    repo, _, conn = setup
    idle = machine(state=LeaseState.IDLE)
    slot = binding(idle)
    policy = ComputePoolPolicy(profile="small", max_machines=2)
    other = machine()
    conn.fetch.return_value = []
    conn.fetchrow.return_value = claim_row(other, binding(other, session_id=slot.session_id))
    conn.fetchval.side_effect = [True, policy.model_dump_json(), idle.model_dump_json()]

    async with repo.operation(idle.id):
        with pytest.raises(ComputeLeaseBusyError, match="already owns"):
            await repo.bind(idle, slot)


async def test_quarantine_keeps_released_rows_only_and_stores_no_session(setup):
    repo, pool, _ = setup
    lease = machine(state=LeaseState.QUARANTINED)

    await repo.quarantine(lease)

    sql = pool.execute.call_args.args[0]
    assert "VALUES ($1, $2, NULL, $3, $4::jsonb)" in sql
    assert "session_id = NULL" in sql
