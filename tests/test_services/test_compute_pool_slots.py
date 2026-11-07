"""Warm capacity in free slots, and the drain rules, with a runtime that declares slot support.

The real runtimes serve one session per machine; ``supports_slots`` is switched on here so
the accounting and the drain rules can be exercised at two slots and more. At one slot every
test below has a counterpart that must behave as the pool always did.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from niuu.observability import get_observability
from tests.compute_fakes import pool_setup
from volundr.domain.compute import (
    ComputeLeaseBusyError,
    LeaseState,
    MachineBootstrap,
    SlotState,
    usable_slots,
)


@pytest.fixture
def setup():
    pool, leases, repo, provider, runtime, store = pool_setup(max_machines=4)
    runtime.supports_slots = True
    runtime.supports_reuse = False
    return pool, leases, repo, provider, runtime, store


async def configure(pool, **update):
    await pool.configure((await pool.policy()).model_copy(update=update))


async def settle(pool):
    """Two ticks: the first reserves and reconciles machines, the second warms them."""
    await pool.maintain()
    await pool.maintain()


def machines(repo):
    return [m for m in repo.leases.values() if m.state != LeaseState.RELEASED]


async def place(leases, repo, machine_id):
    async with repo.operation(machine_id):
        return await leases.bind(
            repo.leases[machine_id], uuid4(), "owner", "tenant", MachineBootstrap(), 60
        )


async def request_stop(repo, binding):
    """What VmPodManager.stop persists before archiving: a started runtime and a stop intent."""
    async with repo.operation(binding.allocation_id):
        stopping = binding.model_copy(
            update={"runtime_data_started": True, "runtime_started": True, "stop_requested": True}
        )
        await repo.save_slot(stopping)
    return stopping


async def free_slots(pool, repo):
    policy = await pool.policy()
    now = datetime.now(UTC)
    bindings = await repo.machine_slots("pool")
    return sum(usable_slots(m, bindings.get(m.id, []), policy, now) for m in machines(repo))


# -- one slot: the warm minimum is a number of machines, exactly as before -----------------------


async def test_one_slot_warm_minimum_boots_one_machine_per_missing_spare(setup):
    pool, leases, repo, provider, _, _ = setup
    await configure(pool, warm_min=2, max_machines=3)

    await pool.maintain()

    assert len(provider.created) == 2
    assert [m.slots for m in machines(repo)] == [1, 1]
    await settle(pool)
    assert len(provider.created) == 2
    assert [m.state for m in machines(repo)] == [LeaseState.IDLE, LeaseState.IDLE]

    # A session takes one spare; the pool boots the missing one.
    await place(leases, repo, machines(repo)[0].id)
    await pool.maintain()

    assert len(provider.created) == 3


async def test_one_slot_surplus_spares_are_disposed_down_to_the_warm_minimum(setup):
    pool, _, repo, provider, _, _ = setup
    await configure(pool, warm_min=3, max_machines=3)
    await settle(pool)
    assert len(machines(repo)) == 3

    await configure(pool, warm_min=1)
    await pool.maintain()

    assert [m.id for m in machines(repo)] == [list(repo.leases)[2]]
    assert len(provider.machines) == 1


# -- several slots: the warm minimum is a number of free slots -----------------------------------


async def test_the_warm_minimum_counts_free_slots_and_boots_whole_machines(setup):
    pool, _, repo, provider, _, _ = setup
    await configure(pool, slots_per_machine=2, warm_min=3, max_machines=3)

    await pool.maintain()

    # ceil(3 / 2): two machines of two slots each.
    assert len(provider.created) == 2
    assert [m.slots for m in machines(repo)] == [2, 2]
    await settle(pool)
    assert len(provider.created) == 2
    assert await free_slots(pool, repo) == 4


async def test_a_machine_is_booted_only_when_free_slots_fall_below_the_minimum(setup):
    pool, leases, repo, provider, _, _ = setup
    await configure(pool, slots_per_machine=2, warm_min=1, max_machines=3)
    await settle(pool)
    (machine,) = machines(repo)
    assert len(provider.created) == 1

    await place(leases, repo, machine.id)
    await pool.maintain()
    # One slot is still free: no machine ahead of demand.
    assert len(provider.created) == 1

    await place(leases, repo, machine.id)
    await pool.maintain()
    assert len(provider.created) == 2
    assert [m.slots for m in machines(repo)] == [2, 2]


async def test_slots_of_a_machine_still_provisioning_count_as_free(setup):
    pool, _, repo, provider, _, _ = setup
    await configure(pool, slots_per_machine=2, warm_min=2, max_machines=3)

    await pool.maintain()
    await pool.maintain()
    await pool.maintain()

    assert len(provider.created) == 1


async def test_a_machine_of_another_profile_does_not_count_towards_the_warm_minimum(setup):
    pool, _, repo, provider, _, _ = setup
    await configure(pool, slots_per_machine=2, warm_min=2, max_machines=3)
    await settle(pool)
    (machine,) = machines(repo)
    repo.leases[machine.id] = machine.model_copy(update={"profile": "large"})

    await pool.maintain()

    # The foreign machine is disposed and the pool boots its own free slots.
    assert repo.leases[machine.id].state == LeaseState.RELEASED
    assert len(provider.created) == 2


@pytest.mark.parametrize(
    ("warm_min", "kept"),
    [(2, 1), (1, 0)],
)
async def test_surplus_disposes_only_empty_machines_while_the_free_slots_stay_at_the_minimum(
    setup, warm_min, kept
):
    pool, leases, repo, _, _, _ = setup
    await configure(pool, slots_per_machine=2, warm_min=6, max_machines=4)
    await settle(pool)
    first, second, third = machines(repo)
    occupied = await place(leases, repo, first.id)

    await configure(pool, warm_min=warm_min)
    await pool.maintain()

    left = {m.id for m in machines(repo)}
    # The machine holding a session is never disposed; its free slot counts as warm.
    assert first.id in left
    assert repo.bindings[occupied.binding.id].state == SlotState.STARTING
    assert left - {first.id} == {third.id} if kept else not left - {first.id}
    assert repo.leases[second.id].state == LeaseState.RELEASED


async def test_under_reuse_surplus_spares_stay_until_they_expire(setup):
    pool, _, repo, _, runtime, _ = setup
    runtime.supports_reuse = True
    await configure(pool, slots_per_machine=2, warm_min=4, max_machines=4)
    await settle(pool)
    await configure(pool, reuse_policy="reuse", warm_min=2)

    await pool.maintain()

    assert len(machines(repo)) == 2


async def test_idle_expiry_trims_empty_machines_only_down_to_the_warm_minimum(setup):
    pool, leases, repo, _, runtime, _ = setup
    runtime.supports_reuse = True
    await configure(pool, slots_per_machine=2, warm_min=6, max_machines=4)
    await settle(pool)
    first, second, third = machines(repo)
    await place(leases, repo, first.id)
    await configure(pool, reuse_policy="reuse", warm_min=3)
    old = datetime.now(UTC) - timedelta(days=1)
    async with repo.operation(second.id):
        await repo.save(repo.leases[second.id].model_copy(update={"idle_since": old}))
    async with repo.operation(third.id):
        await repo.save(
            repo.leases[third.id].model_copy(update={"idle_since": old + timedelta(hours=1)})
        )

    await pool.maintain()

    # Free: 1 (first) + 2 + 2. The older empty machine expires; the other would leave 1 < 3.
    assert repo.leases[second.id].state == LeaseState.RELEASED
    assert repo.leases[third.id].state == LeaseState.IDLE
    assert repo.leases[first.id].state == LeaseState.IDLE


@pytest.mark.parametrize("error", [ComputeLeaseBusyError("busy"), ValueError("took a session")])
async def test_a_machine_a_session_took_while_trimming_is_left_alone(setup, error):
    pool, _, repo, _, runtime, _ = setup
    runtime.supports_reuse = True
    await configure(pool, slots_per_machine=2, warm_min=4, max_machines=4)
    await settle(pool)
    old = datetime.now(UTC) - timedelta(days=1)
    for machine in machines(repo):
        async with repo.operation(machine.id):
            await repo.save(machine.model_copy(update={"idle_since": old}))
    pool.dispose = AsyncMock(side_effect=error)

    # Surplus under replace, then idle expiry under reuse: the concurrent assignment wins.
    await configure(pool, warm_min=0)
    await pool.maintain()
    await configure(pool, reuse_policy="reuse")
    await pool.maintain()

    assert pool.dispose.await_count >= 2
    assert len(machines(repo)) == 2


async def test_a_machine_over_the_maximum_is_disposed_even_under_reuse(setup):
    pool, _, repo, _, runtime, _ = setup
    runtime.supports_reuse = True
    await configure(pool, slots_per_machine=2, warm_min=6, max_machines=4)
    await settle(pool)
    first = machines(repo)[0]
    await configure(pool, reuse_policy="reuse", warm_min=0, max_machines=2)

    await pool.maintain()

    assert repo.leases[first.id].state == LeaseState.RELEASED
    assert len(machines(repo)) == 2


async def test_a_pool_at_its_cap_reports_the_free_slots_it_holds(setup, monkeypatch, caplog):
    import logging

    pool, leases, repo, _, _, _ = setup
    pool_service_limit = 2
    leases._limit = pool_service_limit
    await configure(pool, slots_per_machine=2, warm_min=4, max_machines=2)
    await leases.acquire(session_id=uuid4(), owner_id="o", tenant_id="t", profile="small", slots=2)
    count = MagicMock()
    monkeypatch.setattr(get_observability(), "count", count, raising=False)

    with caplog.at_level(logging.ERROR, logger="volundr.domain.services.compute_pool"):
        await pool.maintain()

    # One free slot held, one machine booted (two more slots), the second would exceed the cap.
    snapshot = await pool.snapshot()
    assert snapshot["capacity_shortfall"].startswith("3 of 4 free warm slots")
    assert "maximum of 2 machines" in snapshot["capacity_shortfall"]
    assert [c for c in count.call_args_list if c.args[0] == "volundr.compute.warm.capacity_hits"]
    assert any("3 of 4 free warm slots" in record.getMessage() for record in caplog.records)


async def test_the_snapshot_reports_the_free_warm_slots(setup):
    pool, leases, repo, _, _, _ = setup
    await configure(pool, slots_per_machine=2, warm_min=2, max_machines=3)
    await settle(pool)
    (machine,) = machines(repo)
    assert (await pool.snapshot())["free_warm_slots"] == 2

    await place(leases, repo, machine.id)

    assert (await pool.snapshot())["free_warm_slots"] == 1


async def test_the_slot_gauge_counts_each_state_and_the_free_slots(setup, monkeypatch):
    pool, leases, repo, _, _, _ = setup
    await configure(pool, slots_per_machine=2, warm_min=1, max_machines=3)
    await settle(pool)
    (machine,) = machines(repo)
    await place(leases, repo, machine.id)
    gauge = MagicMock()
    monkeypatch.setattr(get_observability(), "gauge", gauge, raising=False)

    await pool.maintain()

    values = {
        call.kwargs["attributes"]["state"]: call.args[1]
        for call in gauge.call_args_list
        if call.args[0] == "volundr.compute.slots"
    }
    assert values == {
        "starting": 1,
        "busy": 0,
        "failed": 0,
        "draining": 0,
        "retired": 0,
        "free": 1,
    }
    assert all(
        call.kwargs["attributes"]["pool"] == "pool"
        for call in gauge.call_args_list
        if call.args[0] == "volundr.compute.slots"
    )


# -- drain rules ---------------------------------------------------------------------------------


async def test_a_machine_that_served_every_slot_takes_no_session_and_drains_after_the_last(setup):
    pool, leases, repo, provider, runtime, _ = setup
    await configure(pool, slots_per_machine=2, warm_min=1, max_machines=3)
    await settle(pool)
    (machine,) = machines(repo)
    first = await place(leases, repo, machine.id)
    second = await place(leases, repo, machine.id)
    await pool.maintain()
    # No free slot is left, so the pool booted the replacement ahead of the drain.
    assert len(provider.created) == 2

    await request_stop(repo, first.binding)
    await pool.maintain()

    # Each slot serves one session by default: the freed slot is retired, not reused.
    assert repo.bindings[first.binding.id].state == SlotState.RETIRED
    assert repo.leases[machine.id].state == LeaseState.IDLE
    bindings = await repo.machine_slots("pool")
    assert (
        usable_slots(
            repo.leases[machine.id], bindings[machine.id], await pool.policy(), datetime.now(UTC)
        )
        == 0
    )
    runtime.recycle.assert_not_awaited()

    await request_stop(repo, second.binding)
    await pool.maintain()

    assert repo.leases[machine.id].state == LeaseState.RELEASED
    assert machine.id not in provider.machines
    # Warm capacity is the replacement alone: two free slots.
    assert len(machines(repo)) == 1
    assert await free_slots(pool, repo) == 2


async def test_a_session_limit_above_the_slots_reuses_cleaned_slots_until_it_is_reached(setup):
    pool, leases, repo, provider, runtime, _ = setup
    runtime.supports_reuse = True
    await configure(
        pool, slots_per_machine=2, max_sessions_per_machine=3, warm_min=0, max_machines=3
    )
    machine = await leases.acquire_standby(profile="small", slots=2)
    await leases.reconcile(machine.id)
    async with repo.operation(machine.id):
        await repo.save(repo.leases[machine.id].model_copy(update={"state": LeaseState.IDLE}))
    first = await place(leases, repo, machine.id)
    second = await place(leases, repo, machine.id)

    await request_stop(repo, first.binding)
    await pool.maintain()
    # Served 2 of 3: the cleaned slot is free again and takes a third session.
    assert repo.bindings[first.binding.id].state == SlotState.RELEASED
    runtime.recycle.assert_awaited_once()
    third = await place(leases, repo, machine.id)
    assert third.binding.slot == first.binding.slot
    bindings = await repo.machine_slots("pool")
    policy = await pool.policy()
    assert (
        usable_slots(repo.leases[machine.id], bindings[machine.id], policy, datetime.now(UTC)) == 0
    )

    await request_stop(repo, second.binding)
    await pool.maintain()
    # Served 3 of 3 while the third still runs: retired, not reused, the machine stays.
    assert repo.bindings[second.binding.id].state == SlotState.RETIRED
    assert repo.leases[machine.id].state == LeaseState.IDLE

    await request_stop(repo, third.binding)
    await pool.maintain()
    assert repo.leases[machine.id].state == LeaseState.RELEASED
    assert machine.id not in provider.machines
    runtime.recycle.assert_awaited_once()


async def test_a_machine_past_its_lifetime_takes_no_session_and_is_not_cleaned_for_one(setup):
    pool, leases, repo, provider, runtime, _ = setup
    runtime.supports_reuse = True
    await configure(
        pool,
        slots_per_machine=2,
        max_machine_age_seconds=3600,
        warm_min=1,
        max_machines=3,
    )
    await settle(pool)
    (machine,) = machines(repo)
    bound = await place(leases, repo, machine.id)
    old = repo.leases[machine.id].model_copy(
        update={"created_at": datetime.now(UTC) - timedelta(hours=2)}
    )
    repo.leases[machine.id] = old
    bindings = await repo.machine_slots("pool")
    now = datetime.now(UTC)
    assert usable_slots(old, bindings[machine.id], await pool.policy(), now) == 0

    await pool.maintain()
    # Its free slot no longer counts, so the replacement is booted while the session runs.
    assert len(provider.created) == 2
    assert repo.leases[machine.id].state == LeaseState.IDLE

    await request_stop(repo, bound.binding)
    await pool.maintain()

    runtime.recycle.assert_not_awaited()
    assert repo.leases[machine.id].state == LeaseState.RELEASED
    assert machine.id not in provider.machines


async def test_an_empty_machine_past_its_lifetime_is_replaced_by_warm_capacity(setup):
    pool, _, repo, provider, _, _ = setup
    await configure(pool, slots_per_machine=2, max_machine_age_seconds=3600, warm_min=2)
    await settle(pool)
    (machine,) = machines(repo)
    repo.leases[machine.id] = machine.model_copy(
        update={"created_at": datetime.now(UTC) - timedelta(hours=2)}
    )

    await pool.maintain()

    assert repo.leases[machine.id].state == LeaseState.RELEASED
    assert machine.id not in provider.machines
    assert len(provider.created) == 2
    assert await free_slots(pool, repo) == 2
