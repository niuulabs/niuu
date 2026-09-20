"""Exercise allocation recovery against explicit in-memory test ports."""

from uuid import uuid4

import pytest

from tests.compute_fakes import LeaseRepository, Provider
from volundr.domain.compute import (
    ComputeCapacityError,
    LeaseState,
    Machine,
    MachineBootstrap,
    MachineProfile,
    MachineState,
)
from volundr.domain.services.compute_leases import ComputeLeaseService


@pytest.fixture
def setup():
    repository, provider = LeaseRepository(), Provider()
    service = ComputeLeaseService(
        repository, provider, pool_id="test-pool", max_machines=1, bootstrap=MachineBootstrap()
    )
    return service, repository, provider


async def acquire(service, session_id=None):
    return await service.acquire(
        session_id=session_id or uuid4(), owner_id="owner", tenant_id="tenant", profile="small"
    )


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
    lease = await acquire(service)

    assert lease.state == LeaseState.PROVISIONING
    assert lease.error == "No CPU hosts available. Your request will be automatically retried."
    assert lease.machine.status_detail == lease.error


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


async def test_busy_requires_machine_ready_and_failed_machine_never_frees_capacity(setup):
    service, repository, provider = setup
    lease = await acquire(service)
    with pytest.raises(ValueError, match="ready"):
        await service.mark_busy(lease.id)

    # Provider create must return the observed running state when replaying the operation.
    async def running(request):
        return Machine(
            allocation_id=request.allocation_id, resource_id="vm", state=MachineState.RUNNING
        )

    provider.create = running
    lease = await service.reconcile(lease.id)
    assert lease.state == LeaseState.READY
    assert (await service.mark_busy(lease.id)).state == LeaseState.BUSY
    assert (await service.mark_busy(lease.id)).state == LeaseState.BUSY
    provider.machines[lease.id] = lease.machine.model_copy(update={"state": MachineState.FAILED})
    provider.machines[lease.id] = provider.machines[lease.id].model_copy(
        update={"status_detail": "Provider rejected the machine request"}
    )
    failed = await service.reconcile(lease.id)
    assert failed.state == LeaseState.FAILED
    assert failed.error == "Provider rejected the machine request"
    assert (await service.reconcile(lease.id)).state == LeaseState.FAILED
    with pytest.raises(ComputeCapacityError):
        await acquire(service)


async def test_missing_allocated_machine_does_not_recreate_busy_session(setup):
    service, repository, provider = setup
    lease = await acquire(service)
    repository.leases[lease.id] = lease.model_copy(update={"state": LeaseState.BUSY})
    provider.machines.clear()
    assert (await service.reconcile(lease.id)).state == LeaseState.FAILED
    assert len(provider.created) == 1


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
    repository.leases[lease.id] = lease.model_copy(update={"pool_id": "other-pool"})
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
