"""Exercise allocation recovery against explicit in-memory test ports."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from niuu.adapters.memory_credential_store import MemoryCredentialStore
from niuu.domain.models import SecretType
from tests.compute_fakes import LeaseRepository, Provider
from volundr.domain.compute import (
    ComputeCapacityError,
    ComputeLease,
    ComputeLeaseBusyError,
    ComputePoolFullError,
    HostSelector,
    LeaseState,
    Machine,
    MachineBootstrap,
    MachineProfile,
    MachineProviderError,
    MachineState,
    SlotLease,
    SlotState,
)
from volundr.domain.services.compute_leases import ComputeLeaseService


@pytest.fixture
def setup():
    repository, provider = LeaseRepository(), Provider()
    service = ComputeLeaseService(
        repository, provider, pool_id="test-pool", max_machines=1, bootstrap=MachineBootstrap()
    )
    return service, repository, provider


@pytest.fixture
def stored():
    """A service with a credential store and room for two machines."""
    repository, provider, store = LeaseRepository(), Provider(), MemoryCredentialStore()
    service = ComputeLeaseService(
        repository,
        provider,
        pool_id="test-pool",
        max_machines=2,
        bootstrap=MachineBootstrap(),
        bootstrap_store=store,
    )
    return service, repository, provider, store


async def acquire(service, session_id=None, **kwargs):
    return await service.acquire(
        session_id=session_id or uuid4(),
        owner_id="owner",
        tenant_id="tenant",
        profile="small",
        **kwargs,
    )


async def run(provider, service, allocation_id):
    """Make the provider report the machine running, then reconcile it to READY."""

    async def running(request):
        return Machine(
            allocation_id=request.allocation_id, resource_id="vm", state=MachineState.RUNNING
        )

    provider.create = running
    return await service.reconcile(allocation_id)


async def test_ambiguous_create_remains_reserved_and_retry_reuses_allocation(setup):
    service, repository, provider = setup
    session_id = uuid4()
    provider.create_error = True
    with pytest.raises(RuntimeError, match="provider-secret"):
        await acquire(service, session_id)
    lease = next(iter(repository.leases.values()))
    assert lease.state == LeaseState.PROVISIONING
    assert "provider-secret" not in lease.model_dump_json()
    with pytest.raises(ComputeCapacityError):
        await acquire(service)
    provider.create_error = False
    recovered = await acquire(service, session_id)
    assert recovered.id == lease.id
    assert provider.created == [lease.id, lease.id]


async def test_acquire_binds_the_session_to_slot_zero_of_a_new_machine(setup):
    service, repository, _ = setup
    session_id = uuid4()

    claim = await acquire(service, session_id)

    assert isinstance(claim, SlotLease)
    assert (claim.slot, claim.session_id, claim.owner_id, claim.tenant_id) == (
        0,
        session_id,
        "owner",
        "tenant",
    )
    assert claim.allocation.slots == 1
    assert claim.binding.state is SlotState.STARTING
    # The binding's start deadline is the machine's provisioning deadline.
    assert claim.binding.start_deadline == claim.allocation.provision_deadline
    assert await repository.active_for_session("test-pool", session_id) == claim


async def test_acquire_reserves_the_requested_number_of_slots(setup):
    service, _, _ = setup

    assert (await acquire(service, slots=3)).allocation.slots == 3


async def test_standby_machines_carry_no_binding(setup):
    service, repository, _ = setup

    spare = await service.acquire_standby(profile="small")

    assert isinstance(spare, ComputeLease)
    assert not repository.bindings
    assert repository.leases[spare.id].state is LeaseState.PROVISIONING


async def test_provider_wait_detail_is_preserved_on_provisioning_lease(setup):
    service, _, provider = setup

    async def waiting(request):
        machine = Machine(
            allocation_id=request.allocation_id,
            resource_id="request-1",
            state=MachineState.PROVISIONING,
            status_detail="No CPU hosts available. Your request will be automatically retried.",
        )
        provider.machines[request.allocation_id] = machine
        return machine

    provider.create = waiting
    claim = await acquire(service)

    assert claim.allocation.state == LeaseState.PROVISIONING
    assert claim.allocation.error == (
        "No CPU hosts available. Your request will be automatically retried."
    )
    assert claim.machine.status_detail == claim.allocation.error


async def test_release_recovers_after_failure_and_capacity_waits_for_confirmed_deletion(setup):
    service, repository, provider = setup
    lease = await acquire(service)
    provider.delete_error = True
    with pytest.raises(RuntimeError, match="delete error"):
        await service.release(lease.id)
    assert repository.leases[lease.id].state == LeaseState.DRAINING
    with pytest.raises(ComputeCapacityError):
        await acquire(service)
    assert repository.leases[lease.id].retry_after is not None
    # Backoff survives a controller restart; explicit release retries immediately.
    assert (await service.reconcile(lease.id)).state == LeaseState.DRAINING
    provider.delete_error = False
    provider.delete_pending = True
    await service.release(lease.id)
    assert (await service.reconcile(lease.id)).state == LeaseState.DRAINING
    provider.delete_pending = False
    assert (await service.reconcile(lease.id)).state == LeaseState.RELEASED
    assert (await service.release(lease.id)).state == LeaseState.RELEASED
    assert (await service.reconcile(lease.id)).state == LeaseState.RELEASED
    assert (await acquire(service)).id != lease.id


async def test_a_full_pool_is_a_loud_capacity_error_not_an_empty_result(setup):
    service, _, _ = setup
    await acquire(service)

    with pytest.raises(ComputePoolFullError):
        await acquire(service)


async def test_busy_requires_machine_ready_and_failed_machine_never_frees_capacity(setup):
    service, repository, provider = setup
    claim = await acquire(service)
    with pytest.raises(ValueError, match="ready"):
        await service.mark_busy(claim.id, claim.binding.id)

    # Provider create must return the observed running state when replaying the operation.
    machine = await run(provider, service, claim.id)
    assert machine.state == LeaseState.READY
    busy = await service.mark_busy(claim.id, claim.binding.id)
    # A machine in service is idle; its slot carries the busy state.
    assert (busy.allocation.state, busy.binding.state) == (LeaseState.IDLE, SlotState.BUSY)
    again = await service.mark_busy(claim.id, claim.binding.id)
    assert (again.allocation.state, again.binding.state) == (LeaseState.IDLE, SlotState.BUSY)
    provider.machines[claim.id] = machine.machine.model_copy(
        update={"state": MachineState.FAILED, "status_detail": "Provider rejected the machine"}
    )
    failed = await service.reconcile(claim.id)
    assert failed.state == LeaseState.FAILED
    assert failed.error == "Provider rejected the machine"
    assert (await service.reconcile(claim.id)).state == LeaseState.FAILED
    with pytest.raises(ComputeCapacityError):
        await acquire(service)


async def test_busy_refuses_a_slot_that_is_not_starting_or_busy(setup):
    service, repository, provider = setup
    claim = await acquire(service)
    await run(provider, service, claim.id)
    async with repository.operation(claim.id):
        await repository.save_slot(claim.binding.model_copy(update={"state": SlotState.FAILED}))

    with pytest.raises(ValueError, match="ready"):
        await service.mark_busy(claim.id, claim.binding.id)


async def test_a_slot_of_another_allocation_is_not_found(setup):
    service, _, provider = setup
    claim = await acquire(service)
    await run(provider, service, claim.id)

    with pytest.raises(LookupError, match="slot"):
        await service.mark_busy(claim.id, uuid4())


async def test_missing_allocated_machine_does_not_recreate_busy_session(setup):
    service, repository, provider = setup
    claim = await acquire(service)
    await run(provider, service, claim.id)
    await service.mark_busy(claim.id, claim.binding.id)
    provider.machines.clear()
    assert (await service.reconcile(claim.id)).state == LeaseState.FAILED
    assert len(provider.created) == 1


async def test_an_unfinished_start_past_its_deadline_fails_its_slot_not_the_machine(setup):
    service, repository, provider = setup
    claim = await acquire(service)
    await run(provider, service, claim.id)
    async with repository.operation(claim.id):
        await repository.save_slot(
            claim.binding.model_copy(
                update={"start_deadline": datetime.now(UTC) - timedelta(seconds=1)}
            )
        )

    machine = await service.reconcile(claim.id)

    assert machine.state == LeaseState.READY
    expired = repository.bindings[claim.binding.id]
    assert expired.state is SlotState.FAILED
    assert expired.error == "Provisioning expired; preserve data and release the allocation"


async def test_a_busy_slot_is_not_expired_by_its_start_deadline(setup):
    service, repository, provider = setup
    claim = await acquire(service)
    await run(provider, service, claim.id)
    busy = await service.mark_busy(claim.id, claim.binding.id)
    async with repository.operation(claim.id):
        await repository.save_slot(
            busy.binding.model_copy(
                update={"start_deadline": datetime.now(UTC) - timedelta(seconds=1)}
            )
        )

    assert (await service.reconcile(claim.id)).state == LeaseState.IDLE
    assert repository.bindings[claim.binding.id].state is SlotState.BUSY


async def test_a_machine_past_its_deadline_with_a_live_session_waits_for_the_slot_deadline(setup):
    service, repository, provider = setup
    claim = await acquire(service)
    await run(provider, service, claim.id)
    # A retried start extends the slot's deadline past the machine's.
    async with repository.operation(claim.id):
        await repository.save(
            claim.allocation.model_copy(
                update={
                    "state": LeaseState.READY,
                    "provision_deadline": datetime.now(UTC) - timedelta(seconds=1),
                }
            )
        )
        await repository.save_slot(
            claim.binding.model_copy(
                update={"start_deadline": datetime.now(UTC) + timedelta(seconds=60)}
            )
        )

    assert (await service.reconcile(claim.id)).state == LeaseState.READY


async def test_an_empty_ready_machine_and_a_provisioning_machine_fail_past_their_deadline(setup):
    service, repository, provider = setup
    spare = await service.acquire_standby(profile="small")
    past = datetime.now(UTC) - timedelta(seconds=1)
    async with repository.operation(spare.id):
        await repository.save(spare.model_copy(update={"provision_deadline": past}))
    assert (await service.reconcile(spare.id)).state == LeaseState.FAILED

    other = ComputeLeaseService(
        LeaseRepository(),
        provider,
        pool_id="test-pool",
        max_machines=1,
        bootstrap=MachineBootstrap(),
    )
    claim = await acquire(other)
    await run(provider, other, claim.id)
    async with other._repository.operation(claim.id):
        await other._repository.save_slot(
            claim.binding.model_copy(update={"state": SlotState.RELEASED})
        )
        await other._repository.save(
            claim.allocation.model_copy(
                update={"state": LeaseState.READY, "provision_deadline": past}
            )
        )
    assert (await other.reconcile(claim.id)).state == LeaseState.FAILED


async def test_recovery_rejects_changed_bootstrap_and_other_pool(setup):
    service, repository, provider = setup
    lease = await acquire(service)
    changed = ComputeLeaseService(
        repository,
        provider,
        pool_id="test-pool",
        max_machines=1,
        bootstrap=MachineBootstrap(commands=(("true",),)),
    )
    with pytest.raises(RuntimeError, match="Bootstrap configuration changed"):
        await changed.reconcile(lease.id)
    repository.leases[lease.id] = repository.leases[lease.id].model_copy(
        update={"pool_id": "other-pool"}
    )
    with pytest.raises(LookupError):
        await service.release(lease.id)
    with pytest.raises(LookupError):
        await service.reconcile(uuid4())


async def test_claim_identity_and_limits_required(setup):
    service, repository, provider = setup
    with pytest.raises(ValueError):
        ComputeLeaseService(
            repository, provider, pool_id="", max_machines=1, bootstrap=MachineBootstrap()
        )
    with pytest.raises(ValueError):
        await service.acquire(session_id=uuid4(), tenant_id="", owner_id="", profile="small")


async def test_bootstrap_requires_a_credential_store(setup):
    service, _, _ = setup

    with pytest.raises(ValueError, match="durable credential store"):
        await acquire(service, bootstrap=MachineBootstrap(commands=(("true",),)))
    with pytest.raises(ValueError, match="durable credential store"):
        await acquire(service, session_bootstrap=MachineBootstrap())


async def test_unknown_profile_fails_before_reserving_capacity(setup):
    service, repository, provider = setup
    with pytest.raises(ValueError, match="no longer configured"):
        await service.acquire(
            session_id=uuid4(), owner_id="owner", tenant_id="tenant", profile="removed"
        )
    assert not repository.leases
    assert not provider.created


@pytest.mark.parametrize(
    "profiles, message",
    [
        ((), "at least one"),
        (
            (
                MachineProfile(name="small", revision="revision-1", details={}),
                MachineProfile(name="small", revision="revision-2", details={}),
            ),
            "names must be unique",
        ),
        (
            (
                MachineProfile(name="small", revision="revision-1", details={}),
                MachineProfile(name="large", revision="revision-1", details={}),
            ),
            "revisions must be unique",
        ),
    ],
)
async def test_provider_profiles_require_unique_identities(setup, profiles, message):
    service, repository, provider = setup

    async def configured_profiles():
        return profiles

    provider.profiles = configured_profiles
    with pytest.raises(ValueError, match=message):
        await service.validated_profiles()
    assert not repository.leases


@pytest.mark.parametrize("field", ["name", "revision"])
def test_machine_profile_identity_rejects_blank_values(field):
    values = {"name": "small", "revision": "revision-1", "details": {}}
    values[field] = "  "
    with pytest.raises(ValueError, match="must not be blank"):
        MachineProfile(**values)


async def test_reconcile_does_not_replay_create_after_profile_revision_changes(setup):
    service, repository, provider = setup
    provider.create_error = True
    with pytest.raises(RuntimeError, match="provider-secret"):
        await acquire(service)
    lease = next(iter(repository.leases.values()))
    assert provider.created == [lease.id]

    provider.create_error = False
    provider.profile_revision = "revision-2"
    async with repository.operation(lease.id):
        await repository.save(lease.model_copy(update={"retry_after": None}))
    reconciled = await service.reconcile(lease.id)

    assert reconciled.state == LeaseState.FAILED
    assert reconciled.profile_revision == "revision-1"
    assert reconciled.error == "Machine profile changed during provisioning; cleanup is required"
    assert provider.created == [lease.id]


async def test_quarantine_records_unknown_inventory_without_any_session(setup):
    service, repository, _ = setup
    machine = Machine(allocation_id=uuid4(), resource_id="vm", state=MachineState.RUNNING)

    lease = await service.quarantine(machine, "small")

    assert lease.state is LeaseState.QUARANTINED
    assert repository.leases[machine.allocation_id] == lease
    assert not repository.bindings


async def test_session_and_machine_bootstrap_are_stored_and_read_back(stored):
    service, repository, provider, store = stored
    machine_bootstrap = MachineBootstrap(commands=(("echo", "machine"),))
    session_bootstrap = MachineBootstrap(commands=(("echo", "session"),))
    session_id = uuid4()

    claim = await service.acquire(
        session_id=session_id,
        owner_id="owner",
        tenant_id="tenant",
        profile="small",
        bootstrap=machine_bootstrap,
        session_bootstrap=session_bootstrap,
    )

    assert claim.binding.session_bootstrap_ref == f"{claim.id}-session-{session_id}"
    assert await service.bootstrap_for(claim) == session_bootstrap
    assert await service.machine_bootstrap_for(claim.allocation) == machine_bootstrap
    # Acquiring the same session again repeats nothing and re-verifies both secrets.
    again = await service.acquire(
        session_id=session_id,
        owner_id="owner",
        tenant_id="tenant",
        profile="small",
        bootstrap=machine_bootstrap,
        session_bootstrap=session_bootstrap,
    )
    assert again.id == claim.id


async def test_a_session_without_attached_bootstrap_reads_its_machines(stored):
    service, _, _, _ = stored
    claim = await acquire(service)

    assert await service.bootstrap_for(claim) == MachineBootstrap()


async def test_a_missing_session_secret_is_an_error_not_an_empty_bootstrap(stored):
    service, _, _, store = stored
    claim = await acquire(service, session_bootstrap=MachineBootstrap())
    await store.delete("compute", "test-pool", claim.binding.session_bootstrap_ref)

    with pytest.raises(MachineProviderError, match="Session bootstrap is missing"):
        await service.bootstrap_for(claim)


async def test_a_machine_does_not_reach_the_provider_before_its_session_secret_exists(stored):
    service, repository, provider, store = stored
    claim = await acquire(service, session_bootstrap=MachineBootstrap())
    await store.delete("compute", "test-pool", claim.binding.session_bootstrap_ref)
    provider.machines.clear()
    provider.created.clear()
    async with repository.operation(claim.id):
        await repository.save(
            repository.leases[claim.id].model_copy(update={"state": LeaseState.PROVISIONING})
        )

    with pytest.raises(MachineProviderError, match="Session bootstrap is missing"):
        await service.reconcile(claim.id)
    assert provider.created == []


async def test_retry_backoff_is_cleared_when_the_same_session_acquires_again(stored):
    service, repository, provider, _ = stored
    session_id = uuid4()
    provider.create_error = True
    with pytest.raises(RuntimeError, match="provider-secret"):
        await acquire(service, session_id, session_bootstrap=MachineBootstrap())
    allocation = next(iter(repository.leases))
    assert repository.leases[allocation].retry_after is not None

    provider.create_error = False
    claim = await acquire(service, session_id, session_bootstrap=MachineBootstrap())

    assert claim.id == allocation
    assert claim.allocation.retry_after is None


async def test_attach_session_bootstrap_is_stored_once_and_verified_after(stored):
    service, repository, _, store = stored
    session_id = uuid4()
    claim = await acquire(service, session_id)
    assert claim.binding.session_bootstrap_ref is None
    attached = MachineBootstrap(commands=(("true",),))

    view = await service.attach_session_bootstrap(claim.id, claim.binding.id, session_id, attached)

    assert view.binding.session_bootstrap_ref == f"{claim.id}-session-{session_id}"
    assert repository.bindings[claim.binding.id] == view.binding
    assert await service.bootstrap_for(view) == attached
    again = await service.attach_session_bootstrap(
        claim.id, claim.binding.id, session_id, MachineBootstrap(commands=(("false",),))
    )
    assert await service.bootstrap_for(again) == attached


async def test_attach_session_bootstrap_refuses_another_session_and_no_store(stored, setup):
    service, _, _, _ = stored
    claim = await acquire(service)

    with pytest.raises(ValueError, match="does not match"):
        await service.attach_session_bootstrap(
            claim.id, claim.binding.id, uuid4(), MachineBootstrap()
        )
    bare, _, _ = setup
    with pytest.raises(ValueError, match="credential store"):
        await bare.attach_session_bootstrap(claim.id, claim.binding.id, uuid4(), MachineBootstrap())


async def spare_ready(service, repository, provider, **kwargs):
    """An idle warm spare, as the pool leaves it."""
    spare = await service.acquire_standby(profile="small", **kwargs)
    spare = await run(provider, service, spare.id)
    async with repository.operation(spare.id):
        spare = spare.model_copy(update={"state": LeaseState.IDLE})
        await repository.save(spare)
    return spare


async def test_bind_takes_the_first_free_slot_of_an_idle_machine(stored):
    service, repository, provider, store = stored
    spare = await spare_ready(service, repository, provider, slots=2)
    session_id = uuid4()
    bootstrap = MachineBootstrap(commands=(("true",),))

    async with repository.operation(spare.id):
        view = await service.bind(spare, session_id, "owner", "tenant", bootstrap, 60)

    assert (view.slot, view.session_id, view.binding.state) == (0, session_id, SlotState.STARTING)
    assert view.allocation.state is LeaseState.IDLE
    assert view.binding.start_deadline > datetime.now(UTC)
    assert await service.bootstrap_for(view) == bootstrap
    async with repository.operation(spare.id):
        second = await service.bind(view.allocation, uuid4(), "owner", "tenant", bootstrap, 60)
    assert second.slot == 1


async def test_bind_with_no_free_slot_is_refused_before_storing_a_secret(stored):
    service, repository, provider, store = stored
    spare = await spare_ready(service, repository, provider)
    async with repository.operation(spare.id):
        await service.bind(spare, uuid4(), "owner", "tenant", MachineBootstrap(), 60)
        with pytest.raises(ComputeCapacityError, match="no free slot"):
            await service.bind(spare, uuid4(), "owner", "tenant", MachineBootstrap(), 60)

    assert len(await store.list("compute", "test-pool")) == 1


async def test_bind_removes_its_secret_when_the_ledger_refuses_the_binding(stored):
    service, repository, provider, store = stored
    spare = await spare_ready(service, repository, provider)
    repository.bind = AsyncMock(side_effect=ComputeLeaseBusyError("taken"))

    async with repository.operation(spare.id):
        with pytest.raises(ComputeLeaseBusyError):
            await service.bind(spare, uuid4(), "owner", "tenant", MachineBootstrap(), 60)

    assert await store.list("compute", "test-pool") == []


async def test_bind_requires_ownership_and_a_credential_store(stored, setup):
    service, repository, provider, _ = stored
    spare = await spare_ready(service, repository, provider)

    with pytest.raises(ValueError, match="ownership"):
        await service.bind(spare, uuid4(), "", "tenant", MachineBootstrap(), 60)
    bare, _, _ = setup
    with pytest.raises(ValueError, match="credential store"):
        await bare.bind(spare, uuid4(), "owner", "tenant", MachineBootstrap(), 60)


async def test_release_slot_returns_an_emptied_machine_to_idle_and_removes_the_secret(stored):
    service, repository, provider, store = stored
    spare = await spare_ready(service, repository, provider)
    async with repository.operation(spare.id):
        view = await service.bind(spare, uuid4(), "owner", "tenant", MachineBootstrap(), 60)
        running = view.binding.model_copy(
            update={
                "state": SlotState.BUSY,
                "runtime_data_started": True,
                "runtime_started": True,
                "stop_requested": True,
                "error": "x",
            }
        )
        await repository.save_slot(running)
        machine = await service.release_slot(view.allocation, running)

    assert machine.state is LeaseState.IDLE
    assert machine.idle_since is not None
    released = repository.bindings[view.binding.id]
    assert released.state is SlotState.RELEASED
    assert (released.runtime_started, released.stop_requested, released.error) == (
        False,
        False,
        None,
    )
    assert await store.list("compute", "test-pool") == []


async def test_release_slot_leaves_a_machine_with_other_live_sessions_in_service(stored):
    service, repository, provider, _ = stored
    spare = await spare_ready(service, repository, provider, slots=2)
    async with repository.operation(spare.id):
        first = await service.bind(spare, uuid4(), "owner", "tenant", MachineBootstrap(), 60)
        await service.bind(first.allocation, uuid4(), "owner", "tenant", MachineBootstrap(), 60)
        machine = await service.release_slot(first.allocation, first.binding)

    assert machine == first.allocation
    assert repository.leases[spare.id].idle_since is None


async def test_retire_slot_drains_the_machine_or_only_retires_the_slot(stored):
    service, repository, provider, _ = stored
    spare = await spare_ready(service, repository, provider, slots=2)
    async with repository.operation(spare.id):
        view = await service.bind(spare, uuid4(), "owner", "tenant", MachineBootstrap(), 60)
        machine, binding = await service.retire_slot(view.allocation, view.binding, drain=False)
        assert (machine.state, binding.state) == (LeaseState.IDLE, SlotState.RETIRED)
        machine, binding = await service.retire_slot(machine, binding, drain=True)

    assert (machine.state, binding.state) == (LeaseState.DRAINING, SlotState.DRAINING)
    assert repository.leases[spare.id].state is LeaseState.DRAINING
    assert repository.bindings[view.binding.id].state is SlotState.DRAINING


async def test_deleting_a_machine_removes_every_session_secret_and_retires_its_slots(stored):
    service, repository, provider, store = stored
    claim = await acquire(
        service, session_bootstrap=MachineBootstrap(), bootstrap=MachineBootstrap()
    )
    # A controller died between storing a secret and committing its binding.
    await store.store(
        "compute", str(claim.id), f"{claim.id}-session-{uuid4()}", SecretType.GENERIC, {"a": "b"}
    )
    assert len(await store.list("compute", str(claim.id))) == 3

    released = await service.release(claim.id)

    assert released.state is LeaseState.RELEASED
    assert await store.list("compute", str(claim.id)) == []
    assert repository.bindings[claim.binding.id].state is SlotState.RETIRED


async def test_deleting_with_stored_references_but_no_store_is_an_error(setup):
    service, repository, provider = setup
    claim = await acquire(service)
    async with repository.operation(claim.id):
        await repository.save(
            repository.leases[claim.id].model_copy(update={"bootstrap_ref": "orphan"})
        )

    with pytest.raises(MachineProviderError, match="credential store is unavailable"):
        await service.release(claim.id)


# -- host selectors, machine-set slots and refreshing a machine in service -----------------


def recording_provider(provider, **machine_fields):
    """Make the provider report a running machine, remembering every request it was given."""
    requests = []

    async def create(request):
        requests.append(request)
        machine = Machine(
            allocation_id=request.allocation_id,
            resource_id="host",
            state=MachineState.RUNNING,
            **machine_fields,
        )
        provider.machines[request.allocation_id] = machine
        return machine

    provider.create = create
    return requests


async def test_acquire_stores_the_hosts_selector_on_the_binding(setup):
    service, repository, _ = setup
    selector = HostSelector(tags=("rack-1", "gpu"), match="any")

    claim = await acquire(service, host_selector=selector)

    assert claim.binding.host_selector == selector
    assert repository.bindings[claim.binding.id].host_selector == selector


async def test_acquire_without_a_selector_stores_the_empty_one(setup):
    service, _, _ = setup

    assert (await acquire(service)).binding.host_selector == HostSelector()


async def test_reconcile_asks_the_provider_for_a_machine_matching_the_live_selector(setup):
    service, _, provider = setup
    selector = HostSelector(tags=("gpu",))
    requests = recording_provider(provider)
    claim = await acquire(service, host_selector=selector)

    await service.reconcile(claim.id)

    assert requests
    assert all(request.selector == selector for request in requests)


async def test_a_standby_machine_is_requested_with_the_empty_selector(setup):
    service, _, provider = setup
    requests = recording_provider(provider)

    spare = await service.acquire_standby(profile="small")
    await service.reconcile(spare.id)

    assert requests
    assert all(request.selector == HostSelector() for request in requests)


async def test_a_provider_that_sets_slots_per_machine_replaces_the_pools_while_provisioning(setup):
    service, repository, provider = setup
    recording_provider(provider, slots=2)
    claim = await acquire(service, slots=4)

    ready = await service.reconcile(claim.id)

    assert (ready.state, ready.slots) == (LeaseState.READY, 2)
    assert repository.leases[claim.id].slots == 2


async def test_a_machine_still_provisioning_takes_the_providers_slots_too(setup):
    service, _, provider = setup

    async def waiting(request):
        machine = Machine(
            allocation_id=request.allocation_id,
            resource_id="host",
            state=MachineState.PROVISIONING,
            slots=3,
        )
        provider.machines[request.allocation_id] = machine
        return machine

    provider.create = waiting
    claim = await acquire(service, slots=1)

    assert claim.allocation.state is LeaseState.PROVISIONING
    assert claim.allocation.slots == 3


async def test_slots_of_a_machine_in_service_never_change_when_the_provider_changes_its_mind(setup):
    service, repository, provider = setup
    recording_provider(provider, slots=2)
    claim = await acquire(service, slots=4)
    await service.reconcile(claim.id)
    provider.machines[claim.id] = provider.machines[claim.id].model_copy(update={"slots": 8})

    assert (await service.reconcile(claim.id)).slots == 2
    assert repository.leases[claim.id].slots == 2


async def test_a_machine_without_its_own_slots_keeps_the_pools(setup):
    service, _, provider = setup
    recording_provider(provider)
    claim = await acquire(service, slots=4)

    assert (await service.reconcile(claim.id)).slots == 4


async def saves_during(repository, action):
    saved = []
    save = repository.save

    async def counting(lease):
        saved.append(lease)
        await save(lease)

    repository.save = counting
    try:
        result = await action()
    finally:
        repository.save = save
    return result, saved


@pytest.mark.parametrize("state", [LeaseState.READY, LeaseState.IDLE])
async def test_refresh_saves_a_changed_snapshot_without_moving_the_lease(setup, state):
    service, repository, provider = setup
    recording_provider(provider)
    claim = await acquire(service)
    async with repository.operation(claim.id):
        await repository.save(repository.leases[claim.id].model_copy(update={"state": state}))
    changed = provider.machines[claim.id].model_copy(update={"accepting": False, "tags": ("a",)})
    provider.machines[claim.id] = changed
    before = repository.leases[claim.id]

    refreshed, saved = await saves_during(repository, lambda: service.refresh_machine(claim.id))

    assert refreshed.machine == changed
    assert repository.leases[claim.id].machine == changed
    assert refreshed.model_copy(update={"machine": before.machine}) == before
    assert len(saved) == 1


async def test_refresh_saves_nothing_when_the_machine_is_unchanged(setup):
    service, repository, provider = setup
    recording_provider(provider)
    claim = await acquire(service)
    ready = repository.leases[claim.id]
    assert ready.state is LeaseState.READY

    refreshed, saved = await saves_during(repository, lambda: service.refresh_machine(claim.id))

    assert refreshed == ready
    assert saved == []


async def test_refresh_leaves_the_lease_alone_when_the_provider_no_longer_knows_the_machine(setup):
    service, repository, provider = setup
    recording_provider(provider)
    claim = await acquire(service)
    ready = repository.leases[claim.id]
    assert ready.state is LeaseState.READY
    provider.machines.pop(claim.id)

    refreshed, saved = await saves_during(repository, lambda: service.refresh_machine(claim.id))

    assert refreshed == ready
    assert saved == []


@pytest.mark.parametrize(
    "state",
    [
        LeaseState.PROVISIONING,
        LeaseState.BUSY,
        LeaseState.DRAINING,
        LeaseState.QUARANTINED,
        LeaseState.FAILED,
        LeaseState.RELEASED,
    ],
)
async def test_refresh_ignores_a_machine_that_is_not_in_service(setup, state):
    service, repository, provider = setup
    claim = await acquire(service)
    async with repository.operation(claim.id):
        await repository.save(claim.allocation.model_copy(update={"state": state}))
    provider.get = AsyncMock(side_effect=AssertionError("the provider must not be asked"))

    refreshed, saved = await saves_during(repository, lambda: service.refresh_machine(claim.id))

    assert refreshed.state is state
    assert saved == []


async def test_refresh_of_an_unknown_lease_is_an_error(setup):
    service, _, _ = setup

    with pytest.raises(LookupError):
        await service.refresh_machine(uuid4())
