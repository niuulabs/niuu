"""The slot ledger's SQL adapter against a mocked asyncpg pool.

Real transaction invariants (the unique indexes, concurrent binds) have the integration
tests in ``tests/integration/volundr/test_compute_leases.py``.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from volundr.adapters.outbound.postgres_compute_leases import PostgresComputeLeaseRepository
from volundr.domain.compute import (
    ComputeLease,
    ComputeLeaseBusyError,
    ComputePoolPolicy,
    ComputeProvisioningLimitError,
    ComputeSlot,
    ComputeStandbyTargetMetError,
    LeaseState,
    SlotLease,
    SlotState,
)


def machine(**update):
    fields = {
        "id": uuid4(),
        "pool_id": "pool",
        "profile": "small",
        "request_fingerprint": "digest",
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


POLICY = ComputePoolPolicy(profile="small", max_machines=4, max_provisioning=2, warm_min=2)


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


def rows(*models):
    return [{"data": model.model_dump_json()} for model in models]


# -- admission -------------------------------------------------------------------------------


async def test_the_provisioning_count_excludes_machines_whose_runtime_failed(setup):
    repo, _, conn = setup
    lease = machine()
    conn.fetchrow.return_value = None
    conn.fetchval.side_effect = [POLICY.model_dump_json(), 2]

    with pytest.raises(ComputeProvisioningLimitError):
        await repo.reserve(lease, 4, binding(lease))

    sql = conn.fetchval.call_args_list[1].args[0]
    assert "l.state = 'provisioning'" in sql
    assert "l.state = 'ready' AND NOT EXISTS" in sql
    assert "s.allocation_id = l.id AND s.state = 'failed'" in sql


async def test_a_standby_machine_is_refused_when_free_slots_already_meet_the_warm_target(setup):
    repo, _, conn = setup
    one, two = machine(state=LeaseState.IDLE), machine(state=LeaseState.READY)
    conn.fetchval.side_effect = [POLICY.model_dump_json(), 0]
    conn.fetch.side_effect = [rows(one, two), []]

    with pytest.raises(ComputeStandbyTargetMetError):
        await repo.reserve(machine(), 4)

    (states_sql,) = [c.args[0] for c in conn.fetch.call_args_list[:1]]
    assert "('provisioning', 'ready', 'idle')" in states_sql
    assert conn.fetch.call_args_list[1].args[1] == [one.id, two.id]


async def test_a_standby_machine_is_admitted_while_free_slots_are_below_the_target(setup):
    repo, _, conn = setup
    free = machine(state=LeaseState.IDLE)
    in_service = machine(state=LeaseState.IDLE)
    session = binding(in_service, state=SlotState.BUSY)
    conn.fetchval.side_effect = [POLICY.model_dump_json(), 0, 2]
    conn.fetch.side_effect = [rows(free, in_service), rows(session)]
    new = machine()

    # One free machine plus one in service: a single free slot is below warm_min=2.
    assert await repo.reserve(new, 4) == (new, None)


async def test_a_standby_machine_is_admitted_when_there_are_no_machines_at_all(setup):
    repo, _, conn = setup
    conn.fetchval.side_effect = [POLICY.model_dump_json(), 0, 0]
    conn.fetch.return_value = []
    new = machine()

    assert await repo.reserve(new, 4) == (new, None)
    assert conn.fetch.await_count == 1  # no machines, so no slot rows to read


async def test_a_machine_with_a_stop_intent_offers_no_standby_slot(setup):
    repo, _, conn = setup
    stopping = machine(state=LeaseState.IDLE)
    session = binding(stopping, state=SlotState.BUSY, stop_requested=True)
    conn.fetchval.side_effect = [POLICY.model_dump_json(), 0, 2]
    conn.fetch.side_effect = [rows(stopping), rows(session)]
    new = machine()

    assert await repo.reserve(new, 4) == (new, None)


async def test_a_session_claim_does_not_consult_the_warm_target(setup):
    repo, _, conn = setup
    lease = machine()
    conn.fetchrow.return_value = None
    conn.fetchval.side_effect = [POLICY.model_dump_json(), 0, 0]

    await repo.reserve(lease, 4, binding(lease))

    conn.fetch.assert_not_awaited()


# -- binding ---------------------------------------------------------------------------------


async def bind_with(setup, current, others, new, policy=POLICY, live=None):
    repo, _, conn = setup
    conn.fetchval.side_effect = [True, policy.model_dump_json(), current.model_dump_json()]
    conn.fetch.return_value = rows(*others)
    conn.fetchrow.return_value = live
    conn.execute.return_value = "UPDATE 1"
    async with repo.operation(current.id):
        return await repo.bind(current, new)


async def test_bind_takes_a_free_slot_of_an_idle_machine_with_room(setup):
    idle = machine(state=LeaseState.IDLE, slots=2, idle_since=datetime.now(UTC))
    first = binding(idle, state=SlotState.BUSY)
    second = binding(idle, slot=1)
    policy = POLICY.model_copy(update={"slots_per_machine": 2})

    bound = await bind_with(setup, idle, [first], second, policy)

    assert bound.slot == 1
    assert bound.allocation.idle_since is None


@pytest.mark.parametrize(
    "case",
    ["full", "limit-reached", "stopping", "too-old"],
)
async def test_bind_refuses_a_machine_with_no_usable_slot(setup, case):
    idle = machine(state=LeaseState.IDLE, slots=2)
    policy = POLICY.model_copy(update={"slots_per_machine": 2})
    served = [binding(idle, slot=0, state=SlotState.RELEASED)]
    if case == "full":
        served = [binding(idle, slot=0), binding(idle, slot=1)]
    if case == "stopping":
        served = [binding(idle, slot=0, stop_requested=True)]
    if case == "limit-reached":
        served = [binding(idle, slot=0, state=SlotState.RELEASED)] * 2
        policy = policy.model_copy(update={"max_sessions_per_machine": 2})
    if case == "too-old":
        idle = idle.model_copy(update={"created_at": datetime.now(UTC) - timedelta(hours=2)})
        policy = policy.model_copy(update={"max_machine_age_seconds": 60})

    with pytest.raises(ComputeLeaseBusyError, match="no usable slot"):
        await bind_with(setup, idle, served, binding(idle, slot=1), policy)


async def test_bind_refuses_an_occupied_or_unknown_slot(setup):
    idle = machine(state=LeaseState.IDLE, slots=2)
    policy = POLICY.model_copy(update={"slots_per_machine": 2})
    occupant = binding(idle, slot=0)

    with pytest.raises(ComputeLeaseBusyError, match="Slot 0 is no longer free"):
        await bind_with(setup, idle, [occupant], binding(idle, slot=0), policy)
    with pytest.raises(ComputeLeaseBusyError, match="Slot 2 is no longer free"):
        await bind_with(setup, idle, [occupant], binding(idle, slot=2), policy)


async def test_bind_checks_the_binding_belongs_to_the_machine(setup):
    repo, _, conn = setup
    idle = machine(state=LeaseState.IDLE)
    other = machine()
    conn.fetchval.return_value = True

    async with repo.operation(idle.id):
        with pytest.raises(RuntimeError, match="ownership"):
            await repo.bind(idle, binding(other))


async def test_a_released_slot_can_be_taken_again(setup):
    idle = machine(state=LeaseState.IDLE)
    policy = POLICY.model_copy(update={"reuse_policy": "reuse", "warm_min": 0})
    released = binding(idle, state=SlotState.RELEASED)

    bound = await bind_with(setup, idle, [released], binding(idle), policy)

    assert bound.slot == 0


# -- writes ----------------------------------------------------------------------------------


async def test_saving_a_released_machine_retires_its_live_bindings_in_the_same_transaction(setup):
    repo, _, conn = setup
    lease = machine(state=LeaseState.RELEASED)
    conn.fetchval.return_value = True
    conn.execute.return_value = "UPDATE 1"

    async with repo.operation(lease.id):
        conn.execute.reset_mock()
        await repo.save(lease)
        update, retire = conn.execute.call_args_list
    assert "UPDATE compute_leases" in update.args[0]
    assert "UPDATE compute_slots" in retire.args[0]
    assert retire.args[1:] == (lease.id, ["busy", "draining", "failed", "starting"], "retired")
    conn.transaction.assert_called()


async def test_saving_a_live_machine_leaves_its_bindings_alone(setup):
    repo, _, conn = setup
    lease = machine(state=LeaseState.DRAINING)
    conn.fetchval.return_value = True
    conn.execute.return_value = "UPDATE 1"

    async with repo.operation(lease.id):
        conn.execute.reset_mock()
        await repo.save(lease)
        assert conn.execute.await_count == 1


async def test_a_slot_is_saved_only_under_its_machines_operation(setup):
    repo, _, conn = setup
    lease = machine()
    slot = binding(lease)
    conn.fetchval.return_value = True
    conn.execute.return_value = "UPDATE 1"

    with pytest.raises(RuntimeError, match="ownership"):
        await repo.save_slot(slot)
    async with repo.operation(uuid4()):
        with pytest.raises(RuntimeError, match="ownership"):
            await repo.save_slot(slot)
    async with repo.operation(lease.id):
        conn.execute.reset_mock()
        await repo.save_slot(slot.model_copy(update={"state": SlotState.BUSY}))
        sql, slot_id, state, data = conn.execute.call_args.args
    assert "UPDATE compute_slots" in sql and slot_id == slot.id and state == "busy"
    assert '"state":"busy"' in data


async def test_a_slot_that_disappears_during_an_operation_is_an_error(setup):
    repo, _, conn = setup
    lease = machine()
    conn.fetchval.return_value = True
    conn.execute.return_value = "UPDATE 0"

    async with repo.operation(lease.id):
        with pytest.raises(LookupError, match="slot disappeared"):
            await repo.save_slot(binding(lease))


# -- reads -----------------------------------------------------------------------------------


async def test_slot_readers_use_the_operation_connection_inside_an_operation(setup):
    repo, pool, conn = setup
    lease = machine()
    slot = binding(lease)
    conn.fetchval.side_effect = [True, slot.model_dump_json()]

    async with repo.operation(lease.id):
        assert await repo.get_slot(slot.id) == slot

    pool.fetchval.assert_not_awaited()


async def test_get_slot_and_slot_read_one_binding(setup):
    repo, pool, _ = setup
    lease = machine()
    slot = binding(lease)
    pool.fetchval.return_value = slot.model_dump_json()
    assert await repo.get_slot(slot.id) == slot
    assert await repo.slot(lease.id, 0) == slot
    sql = pool.fetchval.call_args.args[0]
    assert "allocation_id = $1 AND slot = $2" in sql
    pool.fetchval.return_value = None
    assert await repo.get_slot(slot.id) is None
    assert await repo.slot(lease.id, 1) is None


async def test_slots_lists_every_binding_a_machine_has_had(setup):
    repo, pool, _ = setup
    lease = machine()
    first, second = binding(lease, state=SlotState.RELEASED), binding(lease)
    pool.fetch.return_value = rows(first, second)

    assert await repo.slots(lease.id) == [first, second]
    assert "ORDER BY created_at" in pool.fetch.call_args.args[0]


async def test_list_slots_is_live_only_by_default(setup):
    repo, pool, _ = setup
    lease = machine()
    slot = binding(lease)
    pool.fetch.return_value = rows(slot)

    assert await repo.list_slots("pool") == [slot]
    assert pool.fetch.call_args.args[2:] == (False, ["busy", "draining", "failed", "starting"])
    await repo.list_slots("pool", live_only=False)
    assert pool.fetch.call_args.args[2] is True


async def test_active_for_session_joins_the_machine_to_the_live_binding(setup):
    repo, pool, _ = setup
    lease = machine(state=LeaseState.IDLE)
    slot = binding(lease, state=SlotState.DRAINING)
    pool.fetchrow.return_value = {
        "machine": lease.model_dump_json(),
        "slot": slot.model_dump_json(),
    }

    view = await repo.active_for_session("pool", slot.session_id)

    assert view == SlotLease(allocation=lease, binding=slot)
    sql, pool_id, session_id, states = pool.fetchrow.call_args.args
    assert "JOIN compute_leases" in sql
    # A draining binding is still the session's claim, so a repeated stop waits for deletion.
    assert "draining" in states
    pool.fetchrow.return_value = None
    assert await repo.active_for_session("pool", uuid4()) is None


async def test_binding_requires_a_pool_policy_row(setup):
    repo, _, conn = setup
    idle = machine(state=LeaseState.IDLE)
    conn.fetchval.side_effect = [True, None]

    async with repo.operation(idle.id):
        with pytest.raises(ValueError):
            await repo.bind(idle, binding(idle))
