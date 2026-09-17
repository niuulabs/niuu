"""Exercise allocation recovery against explicit in-memory test ports."""

from uuid import uuid4

import pytest

from tests.compute_fakes import LeaseRepository, Provider
from volundr.domain.compute import (
    ComputeCapacityError,
    LeaseState,
    Machine,
    MachineBootstrap,
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
    assert (await service.reconcile(lease.id)).state == LeaseState.FAILED
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
