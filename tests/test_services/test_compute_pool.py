"""Provider-neutral pool lifecycle using explicit test doubles for the ports."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from volundr.domain.compute import (
    ComputeCapacityError,
    ComputePoolPolicy,
    LeaseState,
    Machine,
    MachineBootstrap,
    MachineProviderError,
    MachineState,
    SlotState,
)
from volundr.domain.services.compute_leases import ComputeLeaseService


@pytest.fixture
def setup():
    from tests.compute_fakes import pool_setup

    return pool_setup()


async def warm(setup):
    pool, _, repo, _, runtime, _ = setup
    await pool.maintain()
    await pool.maintain()
    lease = next(iter(repo.leases.values()))
    assert lease.state == LeaseState.IDLE
    assert not repo.bindings
    runtime.warm.assert_awaited_once()
    return lease


async def request_stop(repo, binding):
    """What VmPodManager.stop persists before archiving: a started runtime and a stop intent."""
    async with repo.operation(binding.allocation_id):
        stopping = binding.model_copy(
            update={"runtime_data_started": True, "runtime_started": True, "stop_requested": True}
        )
        await repo.save_slot(stopping)
    return stopping


async def test_warm_bind_preserves_identity_then_replaces_used_machine(setup):
    pool, leases, repo, provider, runtime, store = setup
    lease = await warm(setup)
    original = await leases.machine_bootstrap_for(lease)
    bound_bootstrap = MachineBootstrap(commands=(("session-only",),))
    session_id = uuid4()
    async with repo.operation(lease.id):
        bound = await leases.bind(lease, session_id, "owner", "tenant", bound_bootstrap, 120)
    # The machine stays idle and keeps its identity; the session lives in its slot.
    assert bound.allocation.state == LeaseState.IDLE
    assert bound.binding.state == SlotState.STARTING
    assert bound.machine == lease.machine
    assert await leases.machine_bootstrap_for(bound.allocation) == original
    assert await leases.bootstrap_for(bound) == bound_bootstrap
    with pytest.raises(ValueError, match="Stop the session"):
        await pool.dispose(bound.id)
    await request_stop(repo, bound.binding)
    await pool.maintain()
    runtime.stop.assert_awaited_once()
    assert repo.leases[bound.id].state == LeaseState.RELEASED
    assert repo.bindings[bound.binding.id].state == SlotState.RETIRED
    assert bound.id not in provider.machines
    assert (
        await store.get_value(
            "compute",
            bound.allocation.bootstrap_owner or "pool",
            bound.binding.session_bootstrap_ref,
        )
        is None
    )
    assert any(
        item.id != bound.id and item.state != LeaseState.RELEASED for item in repo.leases.values()
    )


async def test_archive_failure_never_deletes_guest_and_retries_stop_intent(setup):
    pool, leases, repo, provider, runtime, _ = setup
    lease = await leases.acquire(session_id=uuid4(), owner_id="o", tenant_id="t", profile="small")
    await request_stop(repo, lease.binding)
    runtime.stop.side_effect = RuntimeError("archive failed")
    with pytest.raises(RuntimeError, match="archive failed"):
        await pool.maintain()
    assert lease.id in provider.machines
    assert repo.bindings[lease.binding.id].stop_requested
    runtime.stop.side_effect = None
    await pool.maintain()
    assert repo.leases[lease.id].state == LeaseState.RELEASED


async def test_spares_within_warm_minimum_never_expire(setup):
    pool, _, repo, provider, runtime, _ = setup
    spare = await warm(setup)
    async with repo.operation(spare.id):
        await repo.save(
            spare.model_copy(update={"idle_since": datetime.now(UTC) - timedelta(days=1)})
        )
    await pool.maintain()
    await pool.maintain()
    assert repo.leases[spare.id].state == LeaseState.IDLE
    assert spare.id in provider.machines
    assert len(repo.leases) == 1
    runtime.warm.assert_awaited_once()


async def test_expire_surplus_spare_and_drain_preserves_bound_sessions(setup):
    pool, leases, repo, provider, _, _ = setup
    spare = await warm(setup)
    async with repo.operation(spare.id):
        await repo.save(
            spare.model_copy(update={"idle_since": datetime.now(UTC) - timedelta(days=1)})
        )
    await pool.configure((await pool.policy()).model_copy(update={"warm_min": 0}))
    await pool.maintain()
    assert repo.leases[spare.id].state == LeaseState.RELEASED
    active = await leases.acquire(session_id=uuid4(), owner_id="o", tenant_id="t", profile="small")
    await pool.configure((await pool.policy()).model_copy(update={"drain": True}))
    await pool.maintain()
    assert active.id in provider.machines
    assert all(
        lease.state == LeaseState.RELEASED
        or any(slot.allocation_id == lease.id for slot in repo.bindings.values())
        for lease in repo.leases.values()
    )


async def test_orphans_quarantined_and_counted_without_destructive_guessing(setup):
    pool, _, repo, provider, runtime, _ = setup
    orphan_id = uuid4()
    provider.machines[orphan_id] = Machine(
        allocation_id=orphan_id, resource_id="opaque", state=MachineState.RUNNING
    )
    await pool.maintain()
    assert repo.leases[orphan_id].state == LeaseState.QUARANTINED
    assert orphan_id in provider.machines
    snapshot = await pool.snapshot()
    assert snapshot["counts"]["quarantined"] == 1
    assert "bootstrap_ref" not in snapshot["allocations"][0]
    runtime.stop.assert_not_awaited()
    await pool.dispose(orphan_id)
    assert orphan_id not in provider.machines
    with pytest.raises(LookupError):
        await pool.dispose(uuid4())


async def test_quarantined_inventory_keeps_provider_identity_before_manual_disposal(setup):
    pool, _, repo, provider, _, store = setup
    original_leases = ComputeLeaseService(
        repo,
        provider,
        pool_id="pool",
        max_machines=2,
        bootstrap=MachineBootstrap(),
        bootstrap_store=store,
        provider_binding="provider-a",
        provider_fingerprint="deployment-a",
    )
    pool.leases = original_leases
    pool.defaults = pool.defaults.model_copy(update={"warm_min": 0})
    orphan_id = uuid4()
    provider.machines[orphan_id] = Machine(
        allocation_id=orphan_id,
        resource_id="opaque",
        state=MachineState.RUNNING,
    )

    await pool.maintain()

    quarantined = repo.leases[orphan_id]
    assert quarantined.execution_plan is None
    assert quarantined.provider_binding == "provider-a"
    assert quarantined.provider_fingerprint == "deployment-a"

    changed_leases = ComputeLeaseService(
        repo,
        provider,
        pool_id="pool",
        max_machines=2,
        bootstrap=MachineBootstrap(),
        bootstrap_store=store,
        provider_binding="provider-a",
        provider_fingerprint="deployment-b",
    )
    pool.leases = changed_leases
    with pytest.raises(MachineProviderError, match="provider binding is unavailable or changed"):
        await pool.dispose(orphan_id)
    assert orphan_id in provider.machines


async def test_persistent_deadline_survives_new_service_and_expired_warm_guest_cleans_up(setup):
    pool, leases, repo, provider, runtime, _ = setup
    await pool.maintain()
    lease = next(iter(repo.leases.values()))
    expired = lease.model_copy(
        update={"provision_deadline": datetime.now(UTC) - timedelta(seconds=1)}
    )
    async with repo.operation(lease.id):
        await repo.save(expired)
    await pool.configure((await pool.policy()).model_copy(update={"paused": True}))
    await pool.maintain()
    assert repo.leases[lease.id].state == LeaseState.RELEASED
    runtime.warm.assert_not_awaited()
    bound = await leases.acquire(session_id=uuid4(), owner_id="o", tenant_id="t", profile="small")
    async with repo.operation(bound.id):
        await repo.save_slot(
            bound.binding.model_copy(update={"start_deadline": expired.provision_deadline})
        )
    recreated = ComputeLeaseService(
        repo, provider, pool_id="pool", max_machines=2, bootstrap=MachineBootstrap()
    )
    # The machine runs; its session's start deadline passed, which fails the slot.
    assert (await recreated.reconcile(bound.id)).state == LeaseState.READY
    assert repo.bindings[bound.binding.id].state == SlotState.FAILED
    assert bound.id in provider.machines


async def test_maintenance_recovers_interrupted_deletion_and_backpressure(setup):
    pool, _, repo, provider, _, _ = setup
    lease = await warm(setup)
    provider.delete_pending = True
    await pool.dispose(lease.id)
    assert repo.leases[lease.id].state == LeaseState.DRAINING
    provider.delete_pending = False
    await pool.maintain()
    assert repo.leases[lease.id].state == LeaseState.RELEASED
    await pool.configure((await pool.policy()).model_copy(update={"warm_min": 2}))
    pool.leases.acquire_standby = AsyncMock(side_effect=ComputeCapacityError())
    await pool.maintain()
    assert pool.last_reconciled is not None


def test_policy_rejects_impossible_capacity():
    with pytest.raises(ValueError, match="Warm minimum"):
        ComputePoolPolicy(profile="small", max_machines=1, warm_min=2)
    with pytest.raises(ValueError):
        ComputePoolPolicy(profile="small", max_machines=0)


@pytest.mark.parametrize("stored_revision", ["revision-1", ""])
async def test_profile_revision_change_retires_spares_but_preserves_bound_guests(
    setup, stored_revision
):
    pool, leases, repo, provider, _, _ = setup
    spare = await warm(setup)
    repo.leases[spare.id] = spare.model_copy(update={"profile_revision": stored_revision})
    bound = await leases.acquire(
        session_id=uuid4(), owner_id="owner", tenant_id="tenant", profile="small"
    )
    provider.profile_revision = "revision-2"
    await pool.maintain()
    assert repo.leases[spare.id].state == LeaseState.RELEASED
    assert bound.id in provider.machines
    replacement = next(
        lease
        for lease in repo.leases.values()
        if lease.id != bound.id and lease.state != LeaseState.RELEASED
    )
    assert replacement.profile_revision == "revision-2"
    assert "profile_revision" not in str(await pool.snapshot())


async def test_reuse_preserves_machine_clears_binding_and_expires_idle(setup):
    pool, leases, repo, provider, runtime, store = setup
    runtime.supports_reuse = True
    await pool.configure((await pool.policy()).model_copy(update={"reuse_policy": "reuse"}))
    lease = await warm(setup)
    async with repo.operation(lease.id):
        bound = await leases.bind(lease, uuid4(), "one", "tenant", MachineBootstrap(), 120)
    await request_stop(repo, bound.binding)
    await pool.maintain()
    idle = repo.leases[lease.id]
    assert idle.state == LeaseState.IDLE
    released = repo.bindings[bound.binding.id]
    assert released.state == SlotState.RELEASED
    assert not released.runtime_started and not released.runtime_data_started
    assert not released.stop_requested
    assert idle.machine == lease.machine and lease.id in provider.machines
    runtime.stop.assert_awaited_once()
    runtime.recycle.assert_awaited_once()
    assert (
        await store.get_value(
            "compute", bound.allocation.bootstrap_owner, bound.binding.session_bootstrap_ref
        )
        is None
    )
    # A different session claims exactly the same guest, without old credentials.
    async with repo.operation(idle.id):
        second = await leases.bind(idle, uuid4(), "two", "tenant", MachineBootstrap(), 120)
    assert second.slot == 0 and second.owner_id == "two"
    await request_stop(repo, second.binding)
    await pool.maintain()
    assert repo.leases[lease.id].state == LeaseState.IDLE
    # Keep surplus reused capacity until idle expiry; do not immediately delete it.
    await pool.configure((await pool.policy()).model_copy(update={"warm_min": 0}))
    await pool.maintain()
    assert lease.id in provider.machines
    async with repo.operation(lease.id):
        await repo.save(
            repo.leases[lease.id].model_copy(
                update={"idle_since": datetime.now(UTC) - timedelta(days=1)}
            )
        )
    await pool.maintain()
    assert repo.leases[lease.id].state == LeaseState.RELEASED
    assert lease.id not in provider.machines


async def test_failed_recycle_keeps_session_bound_until_retry(setup):
    pool, leases, repo, provider, runtime, _ = setup
    runtime.supports_reuse = True
    await pool.configure((await pool.policy()).model_copy(update={"reuse_policy": "reuse"}))
    lease = await warm(setup)
    async with repo.operation(lease.id):
        bound = await leases.bind(lease, uuid4(), "owner", "tenant", MachineBootstrap(), 120)
    await request_stop(repo, bound.binding)
    runtime.recycle.side_effect = RuntimeError("cleanup failed")
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await pool.maintain()
    kept = repo.bindings[bound.binding.id]
    assert kept.session_id == bound.session_id and kept.stop_requested
    assert kept.state == SlotState.STARTING
    assert lease.id in provider.machines
    runtime.recycle.side_effect = None
    await pool.maintain()
    assert repo.leases[lease.id].state == LeaseState.IDLE
    assert repo.bindings[bound.binding.id].state == SlotState.RELEASED


async def test_reuse_requires_runtime_capability(setup):
    pool, _, _, _, runtime, _ = setup
    runtime.supports_reuse = False
    with pytest.raises(ValueError, match="does not support"):
        await pool.configure((await pool.policy()).model_copy(update={"reuse_policy": "reuse"}))


async def test_stale_stop_snapshot_cannot_stop_next_session_on_reused_vm(setup):
    pool, leases, repo, _, runtime, _ = setup
    runtime.supports_reuse = True
    await pool.configure((await pool.policy()).model_copy(update={"reuse_policy": "reuse"}))
    lease = await warm(setup)
    async with repo.operation(lease.id):
        first = await leases.bind(lease, uuid4(), "one", "tenant", MachineBootstrap(), 120)
    stale = await request_stop(repo, first.binding)
    await pool.maintain()
    async with repo.operation(lease.id):
        second = await leases.bind(
            repo.leases[lease.id], uuid4(), "two", "tenant", MachineBootstrap(), 120
        )
    runtime.stop.reset_mock()
    await pool._maintain_lease(repo.leases[lease.id], [stale], await pool.policy(), None)
    runtime.stop.assert_not_called()
    live = await repo.active_for_session("pool", second.session_id)
    assert live is not None and not live.binding.stop_requested


async def test_unstarted_allocation_can_be_deleted_without_bootstrap_secret(setup):
    pool, leases, repo, provider, runtime, store = setup
    bound = await leases.acquire(
        session_id=uuid4(),
        owner_id="owner",
        tenant_id="tenant",
        profile="small",
        bootstrap=MachineBootstrap(),
    )
    await store.delete("compute", bound.allocation.bootstrap_owner, bound.allocation.bootstrap_ref)
    async with repo.operation(bound.id):
        await repo.save_slot(bound.binding.model_copy(update={"stop_requested": True}))
    await pool.maintain()
    assert repo.leases[bound.id].state == LeaseState.RELEASED
    assert bound.id not in provider.machines
    runtime.stop.assert_not_called()


async def test_durable_policy_must_reference_a_validated_provider_profile(setup):
    pool, _, repo, provider, _, _ = setup
    repo.policies["pool"] = (await pool.policy()).model_copy(update={"profile": "removed"})

    with pytest.raises(ValueError, match="configured by the provider"):
        await pool.validate_policy(await pool.policy())

    provider.profile_revision = ""
    with pytest.raises(ValueError, match="revision"):
        await pool.validate_policy(pool.defaults)


# -- slot support is explicit: a pool never runs with slots its runtime cannot honour ----------


def _pool_with(setup, **policy):
    pool, leases, repo, provider, runtime, store = setup
    pool.defaults = ComputePoolPolicy(profile="small", max_machines=2, **policy)
    return pool, repo, runtime


async def test_slots_above_one_are_refused_from_the_defaults_before_they_are_stored(setup):
    pool, repo, runtime = _pool_with(setup, slots_per_machine=2)
    runtime.supports_slots = False

    with pytest.raises(ValueError, match="slots_per_machine=2") as refused:
        await pool.policy()

    # The refusal names the pool, the runtime class and both remedies.
    assert "pool" in str(refused.value) and "AsyncMock" in str(refused.value)
    assert "Sessions per machine" in str(refused.value)
    assert "supports slots" in str(refused.value)
    assert repo.policies == {}


async def test_slots_above_one_are_refused_from_a_stored_policy(setup):
    pool, leases, repo, _, runtime, _ = setup
    runtime.supports_slots = False
    repo.policies["pool"] = ComputePoolPolicy(profile="small", max_machines=2, slots_per_machine=3)

    with pytest.raises(ValueError, match="slots_per_machine=3"):
        await pool.policy()
    with pytest.raises(ValueError, match="slots_per_machine=3"):
        await pool.snapshot()


async def test_an_admin_change_to_more_slots_is_refused_and_not_stored(setup):
    pool, _, repo, _, runtime, _ = setup
    runtime.supports_slots = False
    stored = await pool.policy()

    with pytest.raises(ValueError, match="slots_per_machine=2"):
        await pool.configure(stored.model_copy(update={"slots_per_machine": 2}))
    with pytest.raises(ValueError, match="slots_per_machine=2"):
        await pool.validate_policy(stored.model_copy(update={"slots_per_machine": 2}))

    assert repo.policies["pool"] == stored


async def test_every_configured_execution_runtime_must_support_slots(setup):
    pool, _, repo, _, runtime, _ = setup
    runtime.supports_slots = True
    other = AsyncMock()
    other.supports_slots = False
    pool._execution_runtimes = {"plan": other}
    stored = await pool.policy()

    with pytest.raises(ValueError, match="slots_per_machine=2"):
        await pool.configure(stored.model_copy(update={"slots_per_machine": 2}))

    other.supports_slots = True
    await pool.configure(stored.model_copy(update={"slots_per_machine": 2}))
    assert repo.policies["pool"].slots_per_machine == 2


async def test_one_slot_needs_no_slot_support(setup):
    pool, _, _, _, runtime, _ = setup
    runtime.supports_slots = False

    assert (await pool.policy()).slots_per_machine == 1


async def test_reusing_cleaned_slots_in_place_needs_a_runtime_that_supports_reuse(setup):
    pool, repo, runtime = _pool_with(setup, max_sessions_per_machine=3)
    runtime.supports_reuse = False

    with pytest.raises(ValueError, match="max_sessions_per_machine=3"):
        await pool.policy()

    runtime.supports_reuse = True
    assert (await pool.policy()).max_sessions_per_machine == 3


async def test_a_session_limit_at_or_below_the_slots_needs_no_reuse(setup):
    pool, _, runtime = _pool_with(setup, max_sessions_per_machine=1)
    runtime.supports_reuse = False

    assert (await pool.policy()).max_sessions_per_machine == 1


async def test_the_session_limit_replaces_a_machine_only_after_it_has_served_that_many(setup):
    pool, leases, repo, provider, runtime, _ = setup
    runtime.supports_reuse = True
    await pool.configure((await pool.policy()).model_copy(update={"max_sessions_per_machine": 2}))
    machine = await leases.acquire_standby(profile="small")
    machine = await leases.reconcile(machine.id)
    async with repo.operation(machine.id):
        await repo.save(machine.model_copy(update={"state": LeaseState.IDLE}))
        first = await leases.bind(
            repo.leases[machine.id], uuid4(), "o", "t", MachineBootstrap(), 60
        )
    await request_stop(repo, first.binding)
    await pool.maintain()
    # Served 1 of 2: the cleaned slot is reused in place.
    assert repo.leases[machine.id].state == LeaseState.IDLE
    assert repo.bindings[first.binding.id].state == SlotState.RELEASED

    async with repo.operation(machine.id):
        second = await leases.bind(
            repo.leases[machine.id], uuid4(), "o", "t", MachineBootstrap(), 60
        )
    await request_stop(repo, second.binding)
    await pool.maintain()
    # Served 2 of 2: this stop drains the machine.
    assert repo.leases[machine.id].state == LeaseState.RELEASED
    assert machine.id not in provider.machines
    assert repo.bindings[second.binding.id].state == SlotState.RETIRED


async def test_an_empty_machine_past_its_age_limit_is_disposed(setup):
    pool, _, repo, provider, _, _ = setup
    spare = await warm(setup)
    await pool.configure(
        (await pool.policy()).model_copy(update={"max_machine_age_seconds": 60, "warm_min": 0})
    )
    repo.leases[spare.id] = spare.model_copy(
        update={"created_at": datetime.now(UTC) - timedelta(hours=1)}
    )

    await pool.maintain()

    assert repo.leases[spare.id].state == LeaseState.RELEASED
    assert spare.id not in provider.machines


# -- one slot behaves exactly as one session per machine always did ------------------------------


async def test_one_slot_counts_show_ready_busy_and_failed_as_they_always_did(setup):
    pool, leases, repo, provider, _, _ = setup
    await pool.configure((await pool.policy()).model_copy(update={"warm_min": 0}))
    claim = await leases.acquire(session_id=uuid4(), owner_id="o", tenant_id="t", profile="small")

    # Bound, runtime start unconfirmed: the machine is ready.
    assert (await pool.snapshot())["counts"] == {"ready": 1}

    await leases.mark_busy(claim.id, claim.binding.id)
    snapshot = await pool.snapshot()
    assert snapshot["counts"] == {"busy": 1}
    assert snapshot["allocations"][0]["state"] == "busy"
    assert repo.leases[claim.id].state == LeaseState.IDLE  # stored idle, shown busy

    async with repo.operation(claim.id):
        await repo.save_slot(
            repo.bindings[claim.binding.id].model_copy(update={"state": SlotState.FAILED})
        )
    assert (await pool.snapshot())["counts"] == {"failed": 1}


async def test_one_slot_snapshot_lists_the_session_in_its_slot(setup):
    pool, leases, repo, _, _, _ = setup
    claim = await leases.acquire(session_id=uuid4(), owner_id="o", tenant_id="t", profile="small")
    await leases.mark_busy(claim.id, claim.binding.id)

    (allocation,) = (await pool.snapshot())["allocations"]

    assert allocation["slots"] == 1
    assert allocation["sessions"] == [
        {"slot": 0, "session_id": str(claim.session_id), "state": "busy", "error": None}
    ]
    assert "session_id" not in allocation and "owner_id" not in allocation


async def test_one_slot_machine_gauge_counts_by_display_state(setup, monkeypatch):
    from niuu.observability import get_observability

    pool, leases, _, _, _, _ = setup
    await pool.configure((await pool.policy()).model_copy(update={"warm_min": 0}))
    claim = await leases.acquire(session_id=uuid4(), owner_id="o", tenant_id="t", profile="small")
    await leases.mark_busy(claim.id, claim.binding.id)
    gauge = MagicMock()
    monkeypatch.setattr(get_observability(), "gauge", gauge, raising=False)

    await pool.maintain()

    values = {c.kwargs["attributes"]["state"]: c.args[1] for c in gauge.call_args_list}
    assert values["busy"] == 1 and values["idle"] == 0


async def test_finishing_one_slots_stop_drains_the_machine_when_it_was_the_last(setup):
    pool, leases, repo, _, runtime, _ = setup
    claim = await leases.acquire(session_id=uuid4(), owner_id="o", tenant_id="t", profile="small")
    stopping = await request_stop(repo, claim.binding)

    async with repo.operation(claim.id):
        machine, binding = await pool.finish_stop(repo.leases[claim.id], stopping)

    assert (machine.state, binding.state) == (LeaseState.DRAINING, SlotState.DRAINING)
    runtime.stop.assert_awaited_once()


async def test_finishing_one_slots_stop_leaves_the_machine_serving_its_other_sessions(setup):
    pool, leases, repo, provider, runtime, _ = setup
    spare = await leases.acquire_standby(profile="small", slots=2)
    await leases.reconcile(spare.id)
    async with repo.operation(spare.id):
        await repo.save(repo.leases[spare.id].model_copy(update={"state": LeaseState.IDLE}))
        one = await leases.bind(repo.leases[spare.id], uuid4(), "o", "t", MachineBootstrap(), 60)
        two = await leases.bind(repo.leases[spare.id], uuid4(), "o", "t", MachineBootstrap(), 60)
    stopping = await request_stop(repo, one.binding)

    async with repo.operation(spare.id):
        machine, binding = await pool.finish_stop(repo.leases[spare.id], stopping)

    assert machine.state == LeaseState.IDLE
    assert binding.state == SlotState.RETIRED
    assert repo.bindings[two.binding.id].state == SlotState.STARTING
    assert spare.id in provider.machines
    # The machine takes no new session while a slot waits for cleanup, and is not disposed.
    with pytest.raises(ValueError, match="Stop the session"):
        await pool.dispose(spare.id)


async def test_a_stop_intent_on_a_slot_that_is_already_draining_is_left_to_the_machine(setup):
    pool, leases, repo, provider, runtime, _ = setup
    claim = await leases.acquire(session_id=uuid4(), owner_id="o", tenant_id="t", profile="small")
    stopping = await request_stop(repo, claim.binding)
    async with repo.operation(claim.id):
        await pool.finish_stop(repo.leases[claim.id], stopping)
    runtime.stop.reset_mock()

    await pool.maintain()

    runtime.stop.assert_not_awaited()
    assert repo.leases[claim.id].state == LeaseState.RELEASED
    assert claim.id not in provider.machines


async def test_a_machine_a_session_took_after_the_pools_snapshot_is_not_warmed(setup):
    pool, leases, repo, _, runtime, _ = setup
    claim = await leases.acquire(session_id=uuid4(), owner_id="o", tenant_id="t", profile="small")
    assert claim.allocation.state == LeaseState.READY

    # The pool's snapshot of this machine's slots was read before the session claimed it.
    await pool._maintain_lease(repo.leases[claim.id], [], await pool.policy(), None)

    runtime.warm.assert_not_awaited()
    assert repo.leases[claim.id].state == LeaseState.READY


async def test_a_pool_at_its_machine_cap_short_of_warm_slots_is_loud_and_counted(
    setup, monkeypatch, caplog
):
    import logging

    from niuu.observability import get_observability
    from volundr.domain.compute import ComputePoolFullError

    pool, _, _, _, _, _ = setup
    await pool.configure((await pool.policy()).model_copy(update={"warm_min": 1}))
    acquire = pool.leases.acquire_standby
    pool.leases.acquire_standby = AsyncMock(side_effect=ComputePoolFullError("full"))
    count = MagicMock()
    monkeypatch.setattr(get_observability(), "count", count, raising=False)

    with caplog.at_level(logging.ERROR, logger="volundr.domain.services.compute_pool"):
        await pool.maintain()
        await pool.maintain()

    hits = [c for c in count.call_args_list if c.args[0] == "volundr.compute.warm.capacity_hits"]
    assert len(hits) == 2
    # Logged once when the shortfall starts, not on every tick.
    assert sum("at its maximum" in record.getMessage() for record in caplog.records) == 1
    shortfall = (await pool.snapshot())["capacity_shortfall"]
    assert shortfall.startswith("0 of 1 warm machines") and "raise the maximum" in shortfall

    pool.leases.acquire_standby = acquire
    await pool.maintain()
    await pool.maintain()
    assert (await pool.snapshot())["capacity_shortfall"] is None


async def test_a_standby_target_already_met_is_not_a_shortfall(setup):
    from volundr.domain.compute import ComputeStandbyTargetMetError

    pool, _, _, _, _, _ = setup
    await pool.configure((await pool.policy()).model_copy(update={"warm_min": 1}))
    pool.leases.acquire_standby = AsyncMock(side_effect=ComputeStandbyTargetMetError())

    await pool.maintain()

    assert (await pool.snapshot())["capacity_shortfall"] is None
