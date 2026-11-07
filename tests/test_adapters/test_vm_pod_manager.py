"""VM lifecycle tests with explicit in-memory infrastructure ports."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from niuu.adapters.memory_credential_store import MemoryCredentialStore
from niuu.ports.session_proxy import SessionProxyTarget
from tests.compute_fakes import LeaseRepository, Provider
from volundr.adapters.outbound.vm_pod_manager import VmPodManager
from volundr.domain.compute import (
    ComputeCapacityError,
    ComputeLeaseBusyError,
    ComputeProvisioningLimitError,
    LeaseState,
    MachineBootstrap,
    MachineProviderError,
    MachineState,
)
from volundr.domain.models import PodSpecAdditions, Session, SessionSpec, SessionStatus
from volundr.domain.services.compute_leases import ComputeLeaseService
from volundr.domain.session_startup import StartupStageState
from volundr.domain.vm_runtime import (
    VmRuntime,
    VmRuntimeStageError,
    VmRuntimeUnavailableError,
)


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
    runtime.machine_bootstrap = lambda defaults: defaults
    runtime.session_bootstrap = lambda session, spec, defaults: MachineBootstrap(
        commands=(("true",),)
    )
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
    assert "bootstrap" not in lease.model_dump_json().replace("bootstrap_ref", "").replace(
        "bootstrap_owner", ""
    ).replace("session_bootstrap_ref", "")
    # Starting the same session uses its existing, persisted bootstrap and allocation.
    await manager.start(session, spec)
    assert len(repository.leases) == 1

    async def preserve(*args):
        assert lease.id in provider.machines

    runtime.stop.side_effect = preserve
    assert await manager.stop(session)
    assert repository.leases[lease.id].state == LeaseState.RELEASED
    assert (
        await store.get_value("compute", lease.bootstrap_owner or "pool", lease.bootstrap_ref)
        is None
    )
    assert await manager.status(session) == SessionStatus.STOPPED
    assert await manager.session_proxy_target(session) is None
    assert await manager.stop(session)
    await manager.close()
    runtime.close.assert_awaited_once()


async def test_start_retries_transient_reconcile_contention_without_duplicate(setup):
    manager, service, repository, provider, _, _, session = setup
    reconcile = service.reconcile
    attempts = 0

    async def contested(lease_id):
        nonlocal attempts
        attempts += 1
        # acquire() performs the first reconciliation. Contend when start()
        # enters its own readiness loop, matching concurrent pool maintenance.
        if attempts == 2:
            raise ComputeLeaseBusyError("pool maintenance")
        return await reconcile(lease_id)

    service.reconcile = contested

    result = await manager.start(
        session,
        SessionSpec(values={}, pod_spec=PodSpecAdditions()),
    )

    assert attempts >= 3
    assert result.pod_name == str(next(iter(repository.leases)))
    assert len(repository.leases) == 1
    assert len(provider.created) == 1
    assert provider.created[0] == next(iter(repository.leases))


async def test_start_retries_failed_runtime_on_same_running_machine(setup):
    manager, _, repository, provider, runtime, _, session = setup
    spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())
    runtime.prepare.side_effect = RuntimeError("guest package conflict")

    with pytest.raises(RuntimeError, match="guest package conflict"):
        await manager.start(session, spec)

    lease = next(iter(repository.leases.values()))
    assert lease.state == LeaseState.FAILED
    assert lease.error == "Runtime startup failed; inspect guest and stop session"

    runtime.prepare.side_effect = None
    result = await manager.start(session, spec)

    assert result.pod_name == str(lease.id)
    assert len(repository.leases) == 1
    assert len(provider.created) == 1
    assert repository.leases[lease.id].state == LeaseState.READY


async def test_capacity_for_existing_session_reuses_its_allocation(setup):
    manager, _, repository, _, _, _, session = setup
    await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))

    assert (await manager.capacity()).available == 0
    assert (await manager.capacity_for(session)).available == 1
    assert len(repository.leases) == 1


async def test_session_can_select_another_configured_compute_profile(setup):
    from volundr.domain.compute import MachineProfile

    manager, _, repository, provider, _, _, session = setup
    provider.profiles = AsyncMock(
        return_value=(
            MachineProfile(name="small", revision="revision-1", details={}),
            MachineProfile(name="openshell", revision="revision-2", details={}),
        )
    )
    selected = session.model_copy(update={"workload_config": {"compute_profile": "openshell"}})

    await manager.start(selected, SessionSpec(values={}, pod_spec=PodSpecAdditions()))

    assert next(iter(repository.leases.values())).profile == "openshell"


async def test_session_rejects_unknown_compute_profile(setup):
    manager, _, repository, _, _, _, session = setup
    selected = session.model_copy(update={"workload_config": {"compute_profile": "missing"}})

    with pytest.raises(ValueError, match="not configured"):
        await manager.start(selected, SessionSpec(values={}, pod_spec=PodSpecAdditions()))

    assert not repository.leases


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
    await store.delete("compute", lease.bootstrap_owner or "pool", lease.session_bootstrap_ref)
    with pytest.raises(RuntimeError, match="missing"):
        await manager.status(session)


async def test_readiness_and_failure_states(setup):
    manager, _, repository, provider, runtime, _, session = setup
    await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))
    runtime.ready.return_value = False
    assert await manager.wait_for_ready(session, 0.01) == SessionStatus.FAILED
    lease = next(iter(repository.leases.values()))
    provider.machines[lease.id] = provider.machines[lease.id].model_copy(
        update={
            "state": MachineState.FAILED,
            "status_detail": "Provider rejected the machine request",
        }
    )
    assert await manager.status(session) == SessionStatus.FAILED
    with pytest.raises(RuntimeError, match="Provider rejected"):
        await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))


async def test_liveness_does_not_reap_session_before_lease_reservation(setup):
    manager, _, _, _, _, _, session = setup

    assert (
        await manager.status(session.model_copy(update={"status": SessionStatus.STARTING}))
        == SessionStatus.PROVISIONING
    )
    assert (
        await manager.status(session.model_copy(update={"status": SessionStatus.PROVISIONING}))
        == SessionStatus.PROVISIONING
    )
    assert await manager.status(session) == SessionStatus.STOPPED


async def test_status_detail_exposes_non_terminal_provider_wait(setup):
    manager, service, repository, provider, _, _, session = setup

    async def waiting(request):
        machine = await Provider.create(provider, request)
        machine = machine.model_copy(update={"status_detail": "No CPU hosts available"})
        provider.machines[request.allocation_id] = machine
        return machine

    provider.create = waiting
    lease = await service.acquire(
        session_id=session.id,
        owner_id=session.owner_id,
        tenant_id=session.tenant_id,
        profile="small",
        bootstrap=MachineBootstrap(),
    )

    assert repository.leases[lease.id].state == LeaseState.PROVISIONING
    assert await manager.status_detail(session) == "No CPU hosts available"


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


async def test_background_recovery_preserves_only_explicit_safe_runtime_stage(setup):
    manager, service, repository, provider, runtime, store, session = setup
    lease = await service.acquire(
        session_id=session.id,
        owner_id=session.owner_id,
        tenant_id=session.tenant_id,
        profile="small",
        bootstrap=MachineBootstrap(),
    )
    runtime.start.side_effect = VmRuntimeStageError("sandbox-create")

    assert await manager.status(session) == SessionStatus.PROVISIONING
    await manager._recoveries[lease.id]

    assert await manager.status(session) == SessionStatus.FAILED
    assert repository.leases[lease.id].error == (
        "Runtime startup failed at stage sandbox-create; inspect guest and stop session"
    )


async def test_stop_cancels_recovery_before_guest_data_is_touched(setup):

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


@pytest.mark.parametrize("profile_changed", [False, True])
async def test_manager_claims_standby_without_provisioning_another_machine(setup, profile_changed):
    from volundr.domain.compute import ComputePoolPolicy
    from volundr.domain.services.compute_pool import ComputePoolService

    manager, service, repo, provider, runtime, _, session = setup
    runtime.machine_bootstrap = lambda defaults: defaults
    runtime.session_bootstrap = lambda session, spec, machine: machine
    pool = ComputePoolService(
        repo,
        service,
        provider,
        runtime,
        pool_id="pool",
        defaults=ComputePoolPolicy(profile="small", max_machines=1, warm_min=1),
        bootstrap=MachineBootstrap(),
    )
    manager.configure_pool(pool)
    await pool.maintain()
    await pool.maintain()
    spare = next(iter(repo.leases.values()))
    assert spare.state == LeaseState.IDLE
    assert (await manager.capacity()).available == 1
    if profile_changed:
        provider.profile_revision = "revision-2"
        assert (
            await manager._claim_warm(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))
            is None
        )
        assert repo.leases[spare.id].session_id is None
        return
    creates = len(provider.created)
    await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))
    assert len(provider.created) == creates
    assert (await repo.active_for_session("pool", session.id)).id == spare.id
    assert await manager.wait_for_ready(session, 0.1) == SessionStatus.RUNNING
    await pool.configure((await pool.policy()).model_copy(update={"paused": True}))
    assert (await manager.capacity()).available == 0
    assert manager.profile == "small"


async def test_stop_retries_when_maintenance_owns_allocation(setup):
    manager, _, repository, _, runtime, _, session = setup
    await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))
    operation = repository.operation
    attempts = 0

    @asynccontextmanager
    async def contested(lease_id):
        nonlocal attempts
        attempts += 1
        # Contend before archiving and again after stop persisted draining.
        if attempts in {1, 3}:
            raise ComputeLeaseBusyError("maintenance")
        async with operation(lease_id):
            yield

    repository.operation = contested
    assert await manager.stop(session)
    runtime.stop.assert_awaited_once()
    assert await manager.status(session) == SessionStatus.STOPPED


async def test_cold_machine_bootstrap_never_contains_session_content(setup):
    manager, service, repository, _, runtime, _, session = setup
    spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())
    await manager.start(session, spec)
    lease = next(iter(repository.leases.values()))
    assert (await service.machine_bootstrap_for(lease)).commands == ()
    assert (await service.bootstrap_for(lease)).commands == (("true",),)


async def test_pending_background_start_is_not_reconciled_to_stopped(setup):
    manager, _, _, _, _, _, session = setup
    pending = session.model_copy(update={"status": SessionStatus.STARTING})
    assert await manager.status(pending) == SessionStatus.PROVISIONING


async def test_stop_retries_transient_provider_deletion_failure(setup):
    manager, _, repository, provider, runtime, store, session = setup
    service = ComputeLeaseService(
        repository,
        provider,
        pool_id="pool",
        max_machines=1,
        bootstrap=MachineBootstrap(),
        bootstrap_store=store,
        retry_interval_seconds=0.001,
        retry_max_seconds=0.001,
    )
    manager.configure_compute(
        service, repository, runtime, MachineBootstrap(), pool_id="pool", max_machines=1
    )
    await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))
    delete = provider.delete
    attempts = 0

    async def transient_failure(allocation_id):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise MachineProviderError("provider delete conflict")
        return await delete(allocation_id)

    provider.delete = transient_failure

    assert await manager.stop(session)
    assert attempts == 2
    runtime.stop.assert_awaited_once()
    assert next(iter(repository.leases.values())).state == LeaseState.RELEASED


async def test_stop_keeps_capacity_until_provider_deletion_retry_deadline(setup):
    manager, _, repository, provider, runtime, store, session = setup
    service = ComputeLeaseService(
        repository,
        provider,
        pool_id="pool",
        max_machines=1,
        bootstrap=MachineBootstrap(),
        bootstrap_store=store,
        retry_interval_seconds=0.001,
        retry_max_seconds=0.001,
    )
    manager.configure_compute(
        service, repository, runtime, MachineBootstrap(), pool_id="pool", max_machines=1
    )
    # Room for a retry on a loaded runner; after the retry the provider hangs so
    # the cleanup deadline, not a wall-clock race, is what ends stop().
    manager._cleanup_timeout = 0.5
    await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))
    attempts = 0

    async def persistent_failure(allocation_id):
        nonlocal attempts
        attempts += 1
        if attempts > 1:
            await asyncio.Event().wait()
        raise MachineProviderError("provider delete conflict")

    provider.delete = persistent_failure

    with pytest.raises(TimeoutError):
        await manager.stop(session)

    lease = next(iter(repository.leases.values()))
    assert attempts > 1
    assert lease.state == LeaseState.DRAINING
    assert lease.id in provider.machines
    assert (await manager.capacity()).available == 0
    runtime.stop.assert_awaited_once()


async def test_stop_does_not_retry_runtime_machine_provider_error(setup):
    manager, _, repository, _, runtime, _, session = setup
    await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))
    runtime.stop.side_effect = MachineProviderError("guest bootstrap unavailable")

    with pytest.raises(MachineProviderError, match="guest bootstrap unavailable"):
        await manager.stop(session)

    runtime.stop.assert_awaited_once()
    assert next(iter(repository.leases.values())).state == LeaseState.READY


async def test_unconfirmed_sandbox_deletion_keeps_vm_and_capacity(setup):
    from volundr.domain.vm_runtime import VmRuntimeStageError

    manager, _, repository, provider, runtime, _, session = setup
    await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))
    runtime.stop.side_effect = VmRuntimeStageError("sandbox-delete-verify", "deletion-unconfirmed")
    with pytest.raises(VmRuntimeStageError):
        await manager.stop(session)
    assert len(provider.machines) == 1
    assert (await manager.capacity()).available == 0
    assert next(iter(repository.leases.values())).state == LeaseState.READY


async def test_status_of_a_ready_session_never_fails_on_a_concurrent_lease_operation(setup):
    manager, service, repository, provider, runtime, store, session = setup
    await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))
    assert await manager.wait_for_ready(session, 0.2) == SessionStatus.RUNNING
    lease = next(iter(repository.leases.values()))
    repository.leases[lease.id] = lease.model_copy(update={"state": LeaseState.READY})
    busy = AsyncMock(side_effect=ComputeLeaseBusyError("in progress"))
    service.mark_busy = busy
    assert await manager.status(session) == SessionStatus.RUNNING
    busy.assert_awaited_once()
    repository.leases[lease.id] = lease.model_copy(update={"state": LeaseState.BUSY})
    busy.reset_mock()
    assert await manager.status(session) == SessionStatus.RUNNING
    busy.assert_not_awaited()


def _manager(setup, **kwargs):
    _, service, repository, _, runtime, _, _ = setup
    manager = VmPodManager(
        profile="small", pool_id="pool", max_machines=1, poll_interval_seconds=0.001, **kwargs
    )
    manager.configure_compute(
        service, repository, runtime, MachineBootstrap(), pool_id="pool", max_machines=1
    )
    return manager


async def test_start_waits_behind_the_provisioning_limit_instead_of_failing(setup):
    manager, service, repository, _, _, _, session = setup
    acquire = service.acquire
    refusals = 0

    async def busy_pool(**claim):
        nonlocal refusals
        if refusals < 3:
            refusals += 1
            raise ComputeProvisioningLimitError("Compute pool provisioning limit reached")
        return await acquire(**claim)

    service.acquire = busy_pool
    result = await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))

    assert refusals == 3
    assert result.pod_name == str(next(iter(repository.leases)))


async def test_a_full_pool_still_fails_at_once_and_a_stuck_queue_times_out(setup):
    _, service, _, _, _, _, session = setup
    manager = _manager(setup, provisioning_timeout_seconds=0.05)
    spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())

    async def full(**claim):
        raise ComputeCapacityError("Compute pool pool has no unreserved slots")

    service.acquire = full
    with pytest.raises(ComputeCapacityError, match="no unreserved slots"):
        await manager.start(session, spec)

    async def limited(**claim):
        raise ComputeProvisioningLimitError("Compute pool provisioning limit reached")

    service.acquire = limited
    with pytest.raises(ComputeProvisioningLimitError):
        await manager.start(session, spec)


async def test_an_unprepared_runtime_is_logged_surfaced_and_named_on_timeout(setup, caplog):
    _, _, repository, _, runtime, _, session = setup
    manager = _manager(
        setup,
        provisioning_timeout_seconds=0.2,
        runtime_wait_log_every=1000,
        runtime_wait_surface_after=2,
    )
    reason = "Runner has no Skuld venv at /home/horde/.niuu-runner/venv-bc174c8d/bin/python"
    details: list[str | None] = []

    async def unprepared(*_args, **_kwargs):
        # Read the detail while the session is still provisioning: after the timeout the
        # lease's own deadline may already have failed it, and a failed lease shows none.
        if len(runtime.start.await_args_list) > 2 and not details:
            details.append(await manager.status_detail(session))
        raise VmRuntimeUnavailableError(reason)

    runtime.start.side_effect = unprepared

    with caplog.at_level("WARNING", logger="volundr.adapters.outbound.vm_pod_manager"):
        with pytest.raises(RuntimeError, match="did not become ready within 0.2s: Runner has no"):
            await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))

    warnings = [r for r in caplog.records if "VM runtime not ready" in r.getMessage()]
    assert len(warnings) == 1 and reason in warnings[0].getMessage()
    # The session showed why it was still provisioning; a stop clears the reason.
    assert details == [f"Runtime not ready: {reason}"]
    runtime.start.side_effect = None
    assert await manager.stop(session)
    assert session.id not in manager._wait_reasons


async def test_a_lease_expiring_while_the_runtime_is_unprepared_still_names_it(setup):
    """The durable lease deadline may fire before the wait's own timeout."""
    _, _, repository, _, runtime, _, session = setup
    manager = _manager(setup, provisioning_timeout_seconds=30)
    reason = "Runner has no Skuld venv"

    async def unprepared(*_args, **_kwargs):
        # The lease's deadline passes while the guest is still unprepared.
        for lease_id, lease in list(repository.leases.items()):
            repository.leases[lease_id] = lease.model_copy(
                update={"provision_deadline": datetime.now(UTC) - timedelta(seconds=1)}
            )
        raise VmRuntimeUnavailableError(reason)

    runtime.start.side_effect = unprepared
    with pytest.raises(RuntimeError) as raised:
        await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))
    message = str(raised.value)
    assert message.startswith(f"VM runtime did not become ready within 30s: {reason}. ")
    assert message.endswith("Provisioning expired; preserve data and release the allocation")


def test_runtime_wait_reporting_counts_must_be_positive():
    with pytest.raises(ValueError, match="reporting counts"):
        VmPodManager(profile="small", pool_id="pool", max_machines=1, runtime_wait_log_every=0)


async def test_broker_target_is_the_session_proxy_target_of_a_ready_session(setup):
    manager, _, _, _, _, _, session = setup
    await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))
    assert await manager.wait_for_ready(session, 0.2) == SessionStatus.RUNNING

    target = await manager.broker_target(session)

    assert target == SessionProxyTarget("http://127.0.0.1:9000", "127.0.0.1", 9000)


async def test_broker_target_refuses_a_session_without_a_ready_vm(setup):
    manager, _, _, _, _, _, session = setup

    with pytest.raises(ValueError, match="no ready VM broker"):
        await manager.broker_target(session)


async def test_broker_target_names_an_unreachable_guest(setup):
    manager, _, _, _, runtime, _, session = setup
    await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))
    assert await manager.wait_for_ready(session, 0.2) == SessionStatus.RUNNING
    runtime.target.side_effect = VmRuntimeUnavailableError("tunnel refused")

    with pytest.raises(ValueError, match="VM broker is unreachable: tunnel refused"):
        await manager.broker_target(session)


# --- Launch stages -----------------------------------------------------------------


class StagedRuntime(VmRuntime):
    """A guest runtime that does not name the steps of its own start."""

    def __init__(self):
        self.prepare_errors: list[Exception] = []
        self.prepare_calls = 0
        self.on_prepare = None
        self.is_ready = True

    def machine_bootstrap(self, defaults):
        return defaults

    def session_bootstrap(self, session, spec, machine):
        return MachineBootstrap(commands=(("true",),))

    def bootstrap(self, session, spec, defaults):
        return defaults

    async def warm(self, lease, bootstrap):
        return None

    async def prepare(self, lease, bootstrap):
        self.prepare_calls += 1
        if self.on_prepare is not None:
            await self.on_prepare(self.prepare_calls)
        if self.prepare_errors:
            raise self.prepare_errors.pop(0)

    async def start(self, lease, bootstrap):
        return None

    async def target(self, lease, bootstrap):
        return SessionProxyTarget("http://127.0.0.1:9000", "127.0.0.1", 9000)

    async def ready(self, lease, bootstrap):
        return self.is_ready

    async def stop(self, lease, bootstrap):
        return None

    async def close(self):
        return None


class ReportingRuntime(StagedRuntime):
    """A guest runtime that names each step of its start (StartupReportingVmRuntime)."""

    def __init__(self):
        super().__init__()
        self.reported_to = []

    def report_startup_to(self, report):
        self.reported_to.append(report)

    async def start(self, lease, bootstrap):
        for key, label in (
            ("guest_files", "Delivering session files"),
            ("guest_tunnel", "Opening the tunnel to Forge"),
            ("guest_skuld", "Pulling and starting Skuld"),
        ):
            self.reported_to[-1](lease.session_id, key, label)


class BootingProvider(Provider):
    """A provider whose machine reports its phase while it boots."""

    def __init__(self, provisioning_reads: int = 2):
        super().__init__()
        self.provisioning_reads = provisioning_reads
        self.provisioning_phase = "Starting on hnoss"
        self.provisioning_detail = None
        self.on_create = None

    async def create(self, request):
        machine = await super().create(request)
        if self.on_create is not None:
            await self.on_create(len(self.created))
        if len(self.created) <= self.provisioning_reads:
            machine = machine.model_copy(
                update={
                    "phase": self.provisioning_phase,
                    "status_detail": self.provisioning_detail,
                }
            )
        else:
            machine = machine.model_copy(
                update={
                    "state": MachineState.RUNNING,
                    "addresses": ("192.0.2.10",),
                    "phase": "Running on hnoss",
                }
            )
        self.machines[request.allocation_id] = machine
        return machine


def staged_manager(runtime, provider=None, **kwargs):
    repository, provider, store = Repository(), provider or ReadyProvider(), MemoryCredentialStore()
    service = ComputeLeaseService(
        repository,
        provider,
        pool_id="pool",
        max_machines=1,
        bootstrap=MachineBootstrap(),
        bootstrap_store=store,
    )
    manager = VmPodManager(
        profile="small", pool_id="pool", max_machines=1, poll_interval_seconds=0.001, **kwargs
    )
    manager.configure_compute(
        service, repository, runtime, MachineBootstrap(), pool_id="pool", max_machines=1
    )
    session = Session(id=uuid4(), name="VM session", owner_id="owner", tenant_id="tenant")
    return manager, service, repository, provider, session


def staged_spec():
    return SessionSpec(values={}, pod_spec=PodSpecAdditions())


async def stages_of(manager, session):
    return {stage.key: stage for stage in await manager.startup_stages(session)}


def test_vm_pod_manager_reports_startup_stages():
    assert VmPodManager(profile="small", pool_id="pool", max_machines=1).reports_startup_stages


async def test_a_session_that_never_started_has_no_stages():
    manager, _, _, _, session = staged_manager(StagedRuntime())

    assert await manager.startup_stages(session) == ()


async def test_cold_start_walks_allocate_boot_guest_and_broker_in_order():
    runtime = StagedRuntime()
    manager, _, _, provider, session = staged_manager(runtime, BootingProvider())

    await manager.start(session, staged_spec())

    stages = await manager.startup_stages(session)
    assert [s.key for s in stages] == ["allocate", "boot", "guest", "guest_start", "broker"]
    by_key = {s.key: s for s in stages}
    assert by_key["allocate"].state is StartupStageState.DONE
    assert by_key["allocate"].detail == "A new VM, profile small"
    assert by_key["boot"].state is StartupStageState.DONE
    assert by_key["boot"].detail == "Running on hnoss at 192.0.2.10"
    assert by_key["guest"].state is StartupStageState.DONE
    assert by_key["guest"].detail == "The guest answered"
    assert by_key["guest_start"].label == "Starting the runtime"
    assert by_key["guest_start"].state is StartupStageState.DONE
    # The runtime started, but the broker has not answered yet.
    assert by_key["broker"].state is StartupStageState.ACTIVE
    assert by_key["broker"].finished_at is None
    assert len(provider.created) > 2


async def test_boot_is_active_with_the_machines_own_phase_until_it_runs():
    provider = BootingProvider(provisioning_reads=3)
    manager, _, _, _, session = staged_manager(StagedRuntime(), provider)
    seen = []

    async def watch(read):
        if read == 3:
            seen.append(await stages_of(manager, session))

    provider.on_create = watch

    await manager.start(session, staged_spec())

    while_booting = seen[0]
    assert while_booting["allocate"].state is StartupStageState.DONE
    assert while_booting["boot"].state is StartupStageState.ACTIVE
    assert while_booting["boot"].detail == "Starting on hnoss"
    assert while_booting["guest"].state is StartupStageState.PENDING
    assert while_booting["broker"].state is StartupStageState.PENDING
    boot = (await stages_of(manager, session))["boot"]
    assert boot.state is StartupStageState.DONE
    assert boot.started_at == while_booting["boot"].started_at
    assert boot.finished_at >= boot.started_at


@pytest.mark.parametrize(
    ("phase", "status_detail", "expected"),
    [
        (None, "No CPU hosts available", "No CPU hosts available"),
        (None, None, "VM is provisioning"),
    ],
)
async def test_boot_without_a_phase_shows_the_status_detail_then_the_state(
    phase, status_detail, expected
):
    provider = BootingProvider(provisioning_reads=3)
    provider.provisioning_phase, provider.provisioning_detail = phase, status_detail
    manager, _, _, _, session = staged_manager(StagedRuntime(), provider)
    seen = []

    async def watch(read):
        if read == 3:
            seen.append((await stages_of(manager, session))["boot"].detail)

    provider.on_create = watch

    await manager.start(session, staged_spec())

    assert seen == [expected]


async def test_broker_is_done_once_status_sees_the_runtime_ready():
    runtime = StagedRuntime()
    manager, _, _, _, session = staged_manager(runtime)
    await manager.start(session, staged_spec())
    runtime.is_ready = False

    assert await manager.status(session) == SessionStatus.PROVISIONING
    assert (await stages_of(manager, session))["broker"].state is StartupStageState.ACTIVE

    runtime.is_ready = True
    assert await manager.status(session) == SessionStatus.RUNNING

    broker = (await stages_of(manager, session))["broker"]
    assert broker.state is StartupStageState.DONE
    assert broker.finished_at is not None


async def test_a_runtime_that_names_its_steps_replaces_the_generic_start_stage():
    runtime = ReportingRuntime()
    manager, _, _, _, session = staged_manager(runtime)

    await manager.start(session, staged_spec())

    stages = await manager.startup_stages(session)
    assert [s.key for s in stages] == [
        "allocate",
        "guest",
        "guest_files",
        "guest_tunnel",
        "guest_skuld",
        "broker",
    ]
    assert {s.key: s.label for s in stages}["guest_tunnel"] == "Opening the tunnel to Forge"
    steps = [s for s in stages if s.key.startswith("guest_")]
    assert [s.state for s in steps] == [StartupStageState.DONE] * 3
    assert stages[-1].state is StartupStageState.ACTIVE


async def test_a_reporting_runtime_is_handed_the_managers_reporter_on_every_start():
    runtime = ReportingRuntime()
    manager, _, _, _, session = staged_manager(runtime)

    await manager.start(session, staged_spec())
    await manager.start(session, staged_spec())

    assert runtime.reported_to == [manager._runtime_step, manager._runtime_step]


async def test_boot_is_skipped_for_a_vm_that_was_already_running():
    runtime = StagedRuntime()
    manager, _, _, _, session = staged_manager(runtime)

    await manager.start(session, staged_spec())

    # ReadyProvider hands over a running machine at once: this start never booted it.
    assert "boot" not in await stages_of(manager, session)


async def test_a_warm_claim_says_so_and_drops_the_pending_boot_stage():
    from volundr.domain.compute import ComputePoolPolicy
    from volundr.domain.services.compute_pool import ComputePoolService

    runtime = StagedRuntime()
    manager, service, repository, provider, session = staged_manager(runtime)
    pool = ComputePoolService(
        repository,
        service,
        provider,
        runtime,
        pool_id="pool",
        defaults=ComputePoolPolicy(profile="small", max_machines=1, warm_min=1),
        bootstrap=MachineBootstrap(),
    )
    manager.configure_pool(pool)
    await pool.maintain()
    await pool.maintain()
    spare = next(iter(repository.leases.values()))
    assert spare.state == LeaseState.IDLE

    await manager.start(session, staged_spec())

    stages = await manager.startup_stages(session)
    assert [s.key for s in stages] == ["allocate", "guest", "guest_start", "broker"]
    assert stages[0].detail == "A warm VM from the pool"
    assert stages[0].state is StartupStageState.DONE


async def test_the_provisioning_slot_wait_is_the_allocate_detail():
    runtime = StagedRuntime()
    manager, service, _, _, session = staged_manager(runtime)
    acquire = service.acquire
    refusals = 0
    during: list = []

    async def busy_pool(**claim):
        nonlocal refusals
        if refusals < 3:
            refusals += 1
            raise ComputeProvisioningLimitError("Compute pool provisioning limit reached")
        during.append((await stages_of(manager, session))["allocate"])
        return await acquire(**claim)

    service.acquire = busy_pool

    await manager.start(session, staged_spec())

    waiting = during[0]
    assert waiting.state is StartupStageState.ACTIVE
    assert waiting.detail == (
        "Waiting for a provisioning slot: compute pool pool is already provisioning "
        "as many VMs as its policy allows"
    )
    allocate = (await stages_of(manager, session))["allocate"]
    assert allocate.state is StartupStageState.DONE
    assert allocate.detail == "A new VM, profile small"
    # The wait began when the first slot was refused, not when the VM was finally claimed.
    assert allocate.started_at == waiting.started_at


async def test_an_unavailable_guest_puts_its_reason_on_the_guest_stage():
    runtime = StagedRuntime()
    manager, _, _, _, session = staged_manager(runtime)
    reason = "ssh: connect to host 192.0.2.10 port 22: Connection refused"
    runtime.prepare_errors = [VmRuntimeUnavailableError(reason)]
    seen: list = []

    async def watch(call):
        if call == 2:
            seen.append((await stages_of(manager, session))["guest"])

    runtime.on_prepare = watch

    await manager.start(session, staged_spec())

    assert runtime.prepare_calls == 2
    assert seen[0].state is StartupStageState.ACTIVE
    assert seen[0].detail == reason
    guest = (await stages_of(manager, session))["guest"]
    assert guest.state is StartupStageState.DONE
    assert guest.detail == "The guest answered"
    assert guest.started_at == seen[0].started_at
    # Retrying the guest on a VM that was already up never brings a boot stage back.
    assert [s.key for s in await manager.startup_stages(session)] == [
        "allocate",
        "guest",
        "guest_start",
        "broker",
    ]


async def test_stop_forgets_the_stages():
    runtime = StagedRuntime()
    manager, _, _, _, session = staged_manager(runtime)
    await manager.start(session, staged_spec())
    assert await manager.startup_stages(session) != ()

    assert await manager.stop(session)

    assert await manager.startup_stages(session) == ()


async def test_a_new_start_begins_a_new_launch():
    runtime = StagedRuntime()
    manager, _, _, _, session = staged_manager(runtime)
    await manager.start(session, staged_spec())
    first = await stages_of(manager, session)

    await manager.start(session, staged_spec())

    second = await stages_of(manager, session)
    assert second["allocate"].detail == "This session's own VM"
    assert first["allocate"].detail == "A new VM, profile small"
