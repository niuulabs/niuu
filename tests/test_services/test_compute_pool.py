"""Provider-neutral pool lifecycle using explicit test doubles for the ports."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from volundr.domain.compute import (
    ComputeCapacityError,
    ComputePoolPolicy,
    HostSelector,
    LeaseState,
    Machine,
    MachineBootstrap,
    MachineProviderError,
    MachineState,
    SlotState,
    usable_slots,
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


async def test_a_slot_that_cannot_be_recycled_is_retired_loudly_and_the_machine_drains(
    setup, monkeypatch, caplog
):
    import logging

    from niuu.observability import get_observability

    pool, leases, repo, provider, runtime, _ = setup
    runtime.supports_reuse = True
    await pool.configure((await pool.policy()).model_copy(update={"reuse_policy": "reuse"}))
    lease = await warm(setup)
    async with repo.operation(lease.id):
        bound = await leases.bind(lease, uuid4(), "owner", "tenant", MachineBootstrap(), 120)
    await request_stop(repo, bound.binding)
    runtime.recycle.side_effect = RuntimeError("slot 0 has leftovers")
    count = MagicMock()
    monkeypatch.setattr(get_observability(), "count", count, raising=False)

    with caplog.at_level(logging.ERROR, logger="volundr.domain.services.compute_pool"):
        await pool.maintain()

    # Nothing is re-raised (a retry would repeat the same stop), the slot takes no other
    # session, and the machine with no other live session drains.
    runtime.recycle.assert_awaited_once()
    assert repo.bindings[bound.binding.id].state in {SlotState.RETIRED, SlotState.DRAINING}
    assert repo.leases[lease.id].state != LeaseState.IDLE
    failures = [
        c for c in count.call_args_list if c.args[0] == "volundr.compute.slot.recycle_failures"
    ]
    assert len(failures) == 1
    assert failures[0].args[1] == 1 and failures[0].kwargs["attributes"] == {"pool": "pool"}
    [record] = [r for r in caplog.records if "could not be cleaned for reuse" in r.getMessage()]
    assert record.levelno == logging.ERROR
    assert "retired" in record.getMessage() and "slot 0 has leftovers" in record.getMessage()
    assert record.exc_info is not None
    await pool.maintain()
    assert lease.id not in provider.machines


async def test_a_slot_that_cannot_be_recycled_leaves_the_machine_serving_its_other_sessions(
    setup,
):
    pool, leases, repo, provider, runtime, _ = setup
    runtime.supports_reuse = True
    await pool.configure((await pool.policy()).model_copy(update={"reuse_policy": "reuse"}))
    spare = await leases.acquire_standby(profile="small", slots=2)
    await leases.reconcile(spare.id)
    async with repo.operation(spare.id):
        await repo.save(repo.leases[spare.id].model_copy(update={"state": LeaseState.IDLE}))
        one = await leases.bind(repo.leases[spare.id], uuid4(), "o", "t", MachineBootstrap(), 60)
        two = await leases.bind(repo.leases[spare.id], uuid4(), "o", "t", MachineBootstrap(), 60)
    stopping = await request_stop(repo, one.binding)
    runtime.recycle.side_effect = RuntimeError("audit finding")

    async with repo.operation(spare.id):
        machine, binding = await pool.finish_stop(repo.leases[spare.id], stopping)

    assert binding.state == SlotState.RETIRED
    assert machine.state == LeaseState.IDLE
    assert repo.bindings[two.binding.id].state == SlotState.STARTING
    assert spare.id in provider.machines


async def test_a_slot_that_recycles_cleanly_is_released_for_the_next_session(setup, monkeypatch):
    from niuu.observability import get_observability

    pool, leases, repo, _, runtime, _ = setup
    runtime.supports_reuse = True
    await pool.configure((await pool.policy()).model_copy(update={"reuse_policy": "reuse"}))
    lease = await warm(setup)
    async with repo.operation(lease.id):
        bound = await leases.bind(lease, uuid4(), "owner", "tenant", MachineBootstrap(), 120)
    await request_stop(repo, bound.binding)
    count = MagicMock()
    monkeypatch.setattr(get_observability(), "count", count, raising=False)

    await pool.maintain()

    runtime.recycle.assert_awaited_once()
    assert repo.leases[lease.id].state == LeaseState.IDLE
    assert repo.bindings[bound.binding.id].state == SlotState.RELEASED
    assert not [
        c for c in count.call_args_list if c.args[0] == "volundr.compute.slot.recycle_failures"
    ]


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


async def test_only_the_default_runtime_must_support_slots(setup):
    pool, _, repo, _, runtime, _ = setup
    runtime.supports_slots = True
    other = AsyncMock()
    other.supports_slots = False
    pool._execution_runtimes = {"default": runtime, "other": other}
    stored = await pool.policy()

    await pool.configure(stored.model_copy(update={"slots_per_machine": 2}))
    assert repo.policies["pool"].slots_per_machine == 2

    # The default runtime serves the warm slots, so it alone decides.
    runtime.supports_slots = False
    with pytest.raises(ValueError, match="default runtime") as refused:
        await pool.configure(stored.model_copy(update={"slots_per_machine": 3}))
    assert "slots_per_machine=3" in str(refused.value)


def test_a_runtime_without_slots_gets_machines_to_itself(setup):
    pool, _, _, _, runtime, _ = setup
    runtime.supports_slots = True
    other = AsyncMock()
    other.supports_slots = False
    pool._execution_runtimes = {"default": runtime, "other": other}
    policy = ComputePoolPolicy(profile="small", max_machines=2, slots_per_machine=2)

    assert pool.new_machine_slots(policy) == 2
    assert pool.new_machine_slots(policy, SimpleNamespace(plan_digest="default")) == 2
    assert pool.new_machine_slots(policy, SimpleNamespace(plan_digest="other")) == 1


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
    assert shortfall.startswith("0 of 1 free warm slots") and "raise the maximum" in shortfall

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


# -- pools on registered hosts: a fixed inventory -------------------------------------------


def _fixed_inventory_provider(hosts):
    from tests.test_adapters.test_vm_pod_manager import ReadyProvider

    class FixedInventoryProvider(ReadyProvider):
        """Claims from ``hosts`` registered machines, none of which can be made on demand."""

        def __init__(self):
            super().__init__()
            self.hosts = hosts
            self.claimable_calls = []

        @property
        def fixed_inventory(self):
            return True

        async def claimable(self, profile, selector):
            self.claimable_calls.append((profile, selector))
            return max(self.hosts - len(self.machines), 0)

    return FixedInventoryProvider()


def _fixed_pool(hosts, **policy):
    from tests.compute_fakes import pool_setup

    pool, leases, repo, provider, runtime, store = pool_setup(
        max_machines=8, provider=_fixed_inventory_provider(hosts)
    )
    runtime.requires_root_daemon = False
    runtime.supports_slots = True
    pool.defaults = pool.defaults.model_copy(update=policy)
    return pool, leases, repo, provider, runtime


def _plain_pool(**policy):
    from tests.compute_fakes import pool_setup

    pool, leases, repo, provider, runtime, store = pool_setup(max_machines=8)
    runtime.supports_slots = True
    pool.defaults = pool.defaults.model_copy(update=policy)
    return pool, leases, repo, provider, runtime


async def test_a_registered_host_pool_refuses_a_runtime_that_needs_a_root_daemon():
    pool, _, repo, _, runtime = _fixed_pool(1)
    runtime.requires_root_daemon = True

    with pytest.raises(ValueError, match="runs on registered hosts") as refused:
        await pool.policy()

    # The refusal names the pool, the runtime and the way out.
    assert "pool" in str(refused.value) and "AsyncMock" in str(refused.value)
    assert "needs a root daemon" in str(refused.value)
    assert "rootless Podman" in str(refused.value)
    assert repo.policies == {}


async def test_an_admin_policy_on_registered_hosts_is_refused_for_a_root_daemon_runtime():
    pool, _, repo, _, runtime = _fixed_pool(1)
    stored = await pool.policy()
    runtime.requires_root_daemon = True

    with pytest.raises(ValueError, match="needs a root daemon"):
        await pool.validate_policy(stored)
    with pytest.raises(ValueError, match="needs a root daemon"):
        await pool.configure(stored.model_copy(update={"warm_min": 2}))

    assert repo.policies["pool"] == stored


async def test_an_execution_runtime_that_needs_a_root_daemon_is_refused_too():
    pool, _, _, _, runtime = _fixed_pool(1)
    other = AsyncMock()
    other.requires_root_daemon = True
    pool._execution_runtimes = {"plan": other}

    with pytest.raises(ValueError, match="needs a root daemon"):
        await pool.policy()

    other.requires_root_daemon = False
    assert (await pool.policy()).profile == "small"


async def test_a_pool_of_disposable_machines_may_use_a_root_daemon_runtime():
    pool, _, _, _, runtime = _plain_pool()
    runtime.requires_root_daemon = True

    assert (await pool.policy()).profile == "small"
    await pool.validate_policy(await pool.policy())


async def test_registered_hosts_with_a_rootless_runtime_are_accepted():
    pool, _, _, _, _ = _fixed_pool(1)

    assert (await pool.policy()).profile == "small"
    await pool.validate_policy(await pool.policy())


async def test_registered_hosts_accept_the_linux_and_mac_host_runtimes(tmp_path):
    from tests.test_adapters.test_ssh_darwin_host_runtime import _runtime as mac_runtime
    from tests.test_adapters.test_ssh_host_vm_runtime import _runtime as linux_runtime

    linux, mac = linux_runtime(tmp_path), mac_runtime(tmp_path)
    assert linux.requires_root_daemon is False and mac.requires_root_daemon is False
    pool, _, _, _, _ = _fixed_pool(1)

    for runtime in (linux, mac):
        pool.runtime = runtime
        assert (await pool.policy()).profile == "small"
        await pool.validate_policy(await pool.policy())

    pool._execution_runtimes = {"linux": linux, "mac": mac}
    assert (await pool.policy()).profile == "small"


async def test_a_fixed_inventory_is_claimed_up_to_what_it_can_serve():
    pool, _, repo, provider, _ = _fixed_pool(1, warm_min=3)

    await pool.maintain()

    assert len(provider.created) == 1
    assert provider.claimable_calls[0] == ("small", HostSelector())
    assert (await pool.snapshot())["capacity_shortfall"] == (
        "0 of 3 free warm slots: 1 registered host(s) can take sessions; register or resume a host"
    )


async def test_enough_registered_hosts_claim_the_whole_warm_minimum_without_a_shortfall(
    monkeypatch,
):
    from niuu.observability import get_observability

    pool, _, _, provider, _ = _fixed_pool(5, warm_min=2)
    count = MagicMock()
    monkeypatch.setattr(get_observability(), "count", count, raising=False)

    await pool.maintain()

    assert len(provider.created) == 2
    assert (await pool.snapshot())["capacity_shortfall"] is None
    assert not [
        c for c in count.call_args_list if c.args[0] == "volundr.compute.warm.capacity_hits"
    ]


async def test_a_pool_with_no_registered_host_claims_nothing_counts_each_tick_and_logs_once(
    monkeypatch, caplog
):
    import logging

    from niuu.observability import get_observability

    pool, _, repo, provider, _ = _fixed_pool(0, warm_min=1)
    count = MagicMock()
    monkeypatch.setattr(get_observability(), "count", count, raising=False)

    with caplog.at_level(logging.ERROR, logger="volundr.domain.services.compute_pool"):
        await pool.maintain()
        await pool.maintain()
        await pool.maintain()

    assert provider.created == []
    assert repo.leases == {}
    hits = [c for c in count.call_args_list if c.args[0] == "volundr.compute.warm.capacity_hits"]
    assert len(hits) == 3
    assert hits[0].kwargs["attributes"] == {"pool": "pool"}
    # Logged at error when the shortfall starts, not again while its text is the same.
    messages = [
        r for r in caplog.records if "registered host(s) can take sessions" in r.getMessage()
    ]
    assert len(messages) == 1
    assert messages[0].levelno == logging.ERROR
    assert "0 of 1 free warm slots: 0 registered host(s)" in messages[0].getMessage()


async def test_the_shortfall_is_logged_again_when_its_text_changes_and_clears_when_met(
    caplog,
):
    import logging

    pool, _, _, provider, _ = _fixed_pool(1, warm_min=3)

    with caplog.at_level(logging.ERROR, logger="volundr.domain.services.compute_pool"):
        await pool.maintain()
        await pool.maintain()
        await pool.maintain()

    shortfalls = [
        r.getMessage() for r in caplog.records if "registered host(s) can take" in r.getMessage()
    ]
    assert len(shortfalls) == 2
    assert "0 of 3 free warm slots: 1 registered" in shortfalls[0]
    assert "1 of 3 free warm slots: 0 registered" in shortfalls[1]

    provider.hosts = 5
    await pool.maintain()
    await pool.maintain()
    await pool.maintain()
    assert (await pool.snapshot())["capacity_shortfall"] is None
    assert len(provider.created) == 3


async def test_a_pool_of_disposable_machines_never_asks_a_provider_what_is_claimable():
    pool, _, _, provider, _ = _plain_pool(warm_min=2)
    provider.claimable = AsyncMock(side_effect=AssertionError("not a fixed inventory"))

    await pool.maintain()

    provider.claimable.assert_not_awaited()
    assert len(provider.created) == 2


async def test_a_host_in_service_has_its_machine_snapshot_refreshed_each_tick():
    pool, leases, repo, provider, _ = _fixed_pool(2, warm_min=0)
    claim = await leases.acquire(session_id=uuid4(), owner_id="o", tenant_id="t", profile="small")
    provider.machines[claim.id] = provider.machines[claim.id].model_copy(
        update={"accepting": False}
    )

    await pool.maintain()

    assert repo.leases[claim.id].machine.accepting is False
    assert repo.leases[claim.id].state == LeaseState.READY
    assert usable_slots(repo.leases[claim.id], [], await pool.policy(), datetime.now(UTC)) == 0


async def test_refresh_is_asked_only_for_machines_with_live_sessions_on_a_fixed_inventory():
    pool, leases, repo, provider, _ = _fixed_pool(3, warm_min=0)
    busy = await leases.acquire(session_id=uuid4(), owner_id="o", tenant_id="t", profile="small")
    empty = await leases.acquire_standby(profile="small")
    pool.leases.refresh_machine = AsyncMock(side_effect=leases.refresh_machine)

    await pool.maintain()

    refreshed = [call.args[0] for call in pool.leases.refresh_machine.await_args_list]
    assert refreshed == [busy.id]
    assert empty.id not in refreshed


async def test_a_pool_of_disposable_machines_does_not_refresh_machine_snapshots():
    pool, leases, _, _, _ = _plain_pool(warm_min=0)
    await leases.acquire(session_id=uuid4(), owner_id="o", tenant_id="t", profile="small")
    pool.leases.refresh_machine = AsyncMock(side_effect=AssertionError("not a fixed inventory"))

    await pool.maintain()

    pool.leases.refresh_machine.assert_not_awaited()
