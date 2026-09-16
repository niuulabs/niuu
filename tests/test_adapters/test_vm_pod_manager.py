"""VM lifecycle tests with explicit in-memory infrastructure ports."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from niuu.adapters.memory_credential_store import MemoryCredentialStore
from niuu.ports.session_proxy import SessionProxyTarget
from tests.compute_fakes import LeaseRepository, Provider
from volundr.adapters.outbound.vm_pod_manager import VmPodManager
from volundr.domain.compute import LeaseState, MachineBootstrap, MachineState
from volundr.domain.models import PodSpecAdditions, Session, SessionSpec, SessionStatus
from volundr.domain.services.compute_leases import ComputeLeaseService
from volundr.domain.vm_runtime import VmRuntimeUnavailableError


class Repository(LeaseRepository):
    async def active_for_session(self, pool_id, session_id):
        return next(
            (
                lease
                for lease in self.leases.values()
                if lease.pool_id == pool_id
                and lease.session_id == session_id
                and lease.state != LeaseState.RELEASED
            ),
            None,
        )


class ReadyProvider(Provider):
    async def create(self, request):
        machine = await super().create(request)
        machine = machine.model_copy(
            update={"state": MachineState.RUNNING, "addresses": ("192.0.2.10",)}
        )
        self.machines[request.allocation_id] = machine
        return machine


@pytest.fixture
def setup():
    repository, provider, store = Repository(), ReadyProvider(), MemoryCredentialStore()
    service = ComputeLeaseService(
        repository,
        provider,
        pool_id="pool",
        max_machines=1,
        bootstrap=MachineBootstrap(),
        bootstrap_store=store,
    )
    runtime = AsyncMock()
    runtime.bootstrap = lambda session, spec, defaults: MachineBootstrap(commands=(("true",),))
    runtime.ready.return_value = True
    runtime.target.return_value = SessionProxyTarget("http://127.0.0.1:9000", "127.0.0.1", 9000)
    manager = VmPodManager(
        profile="small", pool_id="pool", max_machines=1, poll_interval_seconds=0.001
    )
    manager.configure_compute(
        service, repository, runtime, MachineBootstrap(), pool_id="pool", max_machines=1
    )
    session = Session(id=uuid4(), name="VM session", owner_id="owner", tenant_id="tenant")
    return manager, service, repository, provider, runtime, store, session


async def test_start_proxy_stop_preserves_before_disposal_and_resumes(setup):
    manager, service, repository, provider, runtime, store, session = setup
    spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())
    runtime.start.side_effect = [VmRuntimeUnavailableError("booting"), None, None]
    result = await manager.start(session, spec)
    assert result.chat_endpoint.endswith(f"/s/{session.id}/session")
    assert (await manager.capacity()).available == 0
    assert await manager.wait_for_ready(session, 0.2) == SessionStatus.RUNNING
    lease = next(iter(repository.leases.values()))
    assert lease.state == LeaseState.BUSY
    assert (await manager.session_proxy_target(session)).connect_port == 9000
    assert "bootstrap" not in lease.model_dump_json().replace("bootstrap_ref", "")
    # Starting the same session uses its existing, persisted bootstrap and allocation.
    await manager.start(session, spec)
    assert len(repository.leases) == 1

    async def preserve(*args):
        assert lease.id in provider.machines

    runtime.stop.side_effect = preserve
    assert await manager.stop(session)
    assert repository.leases[lease.id].state == LeaseState.RELEASED
    assert await store.get_value("compute", "pool", lease.bootstrap_ref) is None
    assert await manager.status(session) == SessionStatus.STOPPED
    assert await manager.session_proxy_target(session) is None
    assert await manager.stop(session)
    await manager.close()
    runtime.close.assert_awaited_once()


async def test_archive_failure_keeps_vm_and_capacity(setup):
    manager, _, repository, provider, runtime, _, session = setup
    await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))
    runtime.stop.side_effect = OSError("disk full")
    with pytest.raises(OSError, match="disk full"):
        await manager.stop(session)
    assert len(provider.machines) == 1
    assert (await manager.capacity()).available == 0
    assert next(iter(repository.leases.values())).state == LeaseState.READY


async def test_restart_uses_bootstrap_store_and_enforces_owner(setup):
    manager, service, repository, provider, runtime, store, session = setup
    spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())
    await manager.start(session, spec)
    restored_service = ComputeLeaseService(
        repository,
        provider,
        pool_id="pool",
        max_machines=1,
        bootstrap=MachineBootstrap(commands=(("changed-default",),)),
        bootstrap_store=store,
    )
    manager.configure_compute(
        restored_service, repository, runtime, MachineBootstrap(), pool_id="pool", max_machines=1
    )
    assert await manager.status(session) == SessionStatus.RUNNING
    with pytest.raises(ValueError, match="ownership"):
        await manager.start(session.model_copy(update={"owner_id": "other"}), spec)
    lease = next(iter(repository.leases.values()))
    await store.delete("compute", "pool", lease.bootstrap_ref)
    with pytest.raises(RuntimeError, match="missing"):
        await manager.status(session)


async def test_readiness_and_failure_states(setup):
    manager, _, repository, provider, runtime, _, session = setup
    await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))
    runtime.ready.return_value = False
    assert await manager.wait_for_ready(session, 0.01) == SessionStatus.FAILED
    lease = next(iter(repository.leases.values()))
    provider.machines[lease.id] = provider.machines[lease.id].model_copy(
        update={"state": MachineState.FAILED}
    )
    assert await manager.status(session) == SessionStatus.FAILED
    with pytest.raises(RuntimeError, match="cannot start"):
        await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))


def test_config_validation():
    with pytest.raises(ValueError):
        VmPodManager(profile="", pool_id="pool", max_machines=1)
    with pytest.raises(ValueError):
        VmPodManager(profile="small", pool_id="pool", max_machines=1, poll_interval_seconds=0)
    manager = VmPodManager(profile="small", pool_id="pool", max_machines=1)
    with pytest.raises(RuntimeError, match="not composed"):
        manager._configured()
    with pytest.raises(ValueError, match="agree"):
        manager.configure_compute(
            None, None, None, MachineBootstrap(), pool_id="other", max_machines=1
        )


async def test_cancel_before_guest_control_releases_without_touching_archive(setup):
    import asyncio

    manager, _, repository, provider, runtime, _, session = setup
    entered = asyncio.Event()

    async def waiting(*args):
        entered.set()
        await asyncio.Event().wait()

    runtime.prepare.side_effect = waiting
    task = asyncio.create_task(
        manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    lease = next(iter(repository.leases.values()))
    assert not lease.runtime_data_started
    assert await manager.stop(session)
    runtime.stop.assert_not_awaited()
    assert not provider.machines


async def test_reconcile_resumes_cancelled_start_without_new_allocation(setup):
    import asyncio

    manager, service, repository, provider, runtime, store, session = setup
    runtime.start.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))
    lease = next(iter(repository.leases.values()))
    assert lease.runtime_data_started and not lease.runtime_started
    runtime.start.side_effect = None
    recovered = VmPodManager(profile="small", pool_id="pool", max_machines=1)
    recovered.configure_compute(
        service, repository, runtime, MachineBootstrap(), pool_id="pool", max_machines=1
    )
    assert await recovered.status(session) == SessionStatus.PROVISIONING
    await recovered._recoveries[lease.id]
    assert await recovered.status(session) == SessionStatus.RUNNING
    assert len(repository.leases) == len(provider.machines) == 1
    assert repository.leases[lease.id].runtime_started
    assert runtime.start.await_count == 2
    await recovered.status(session)
    assert runtime.start.await_count == 2


async def test_background_recovery_failure_is_visible_and_retains_capacity(setup):
    manager, service, repository, provider, runtime, store, session = setup
    lease = await service.acquire(
        session_id=session.id,
        owner_id=session.owner_id,
        tenant_id=session.tenant_id,
        profile="small",
        bootstrap=MachineBootstrap(),
    )
    runtime.start.side_effect = RuntimeError("sensitive remote error")
    assert await manager.status(session) == SessionStatus.PROVISIONING
    await manager._recoveries[lease.id]
    assert await manager.status(session) == SessionStatus.FAILED
    assert (await manager.capacity()).available == 0
    assert "sensitive" not in repository.leases[lease.id].error
    assert len(provider.machines) == 1


async def test_stop_cancels_recovery_before_guest_data_is_touched(setup):
    import asyncio

    manager, service, repository, provider, runtime, store, session = setup
    lease = await service.acquire(
        session_id=session.id,
        owner_id=session.owner_id,
        tenant_id=session.tenant_id,
        profile="small",
        bootstrap=MachineBootstrap(),
    )
    entered = asyncio.Event()

    async def booting(*args):
        entered.set()
        await asyncio.Future()

    runtime.prepare.side_effect = booting
    assert await manager.status(session) == SessionStatus.PROVISIONING
    await entered.wait()
    assert await manager.stop(session)
    assert repository.leases[lease.id].state == LeaseState.RELEASED
    runtime.stop.assert_not_awaited()
    assert not manager._recoveries
    assert not provider.machines
