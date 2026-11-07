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
    ComputePoolPolicy,
    ComputeProvisioningLimitError,
    ComputeSlot,
    LeaseState,
    MachineBootstrap,
    MachineProviderError,
    MachineState,
    SlotState,
)
from volundr.domain.models import PodSpecAdditions, Session, SessionSpec, SessionStatus
from volundr.domain.services.compute_leases import ComputeLeaseService
from volundr.domain.session_startup import StartupStageState
from volundr.domain.vm_runtime import (
    VmRuntime,
    VmRuntimeStageError,
    VmRuntimeUnavailableError,
)

Repository = LeaseRepository


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
    binding = next(iter(repository.bindings.values()))
    # A machine in service is idle; its slot carries the session's busy state.
    assert (lease.state, binding.state) == (LeaseState.IDLE, SlotState.BUSY)
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
    binding = next(iter(repository.bindings.values()))
    # A failed runtime start fails the session's slot; the machine stays ready.
    assert lease.state == LeaseState.READY
    assert binding.state == SlotState.FAILED
    assert binding.error == "Runtime startup failed; inspect guest and stop session"

    runtime.prepare.side_effect = None
    result = await manager.start(session, spec)

    assert result.pod_name == str(lease.id)
    assert len(repository.leases) == 1
    assert len(provider.created) == 1
    assert repository.leases[lease.id].state == LeaseState.READY
    retried = repository.bindings[binding.id]
    assert retried.state == SlotState.STARTING
    assert retried.error is None


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
    binding = next(iter(repository.bindings.values()))
    await store.delete("compute", lease.bootstrap_owner or "pool", binding.session_bootstrap_ref)
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
    binding = next(iter(repository.bindings.values()))
    assert not binding.runtime_data_started
    assert await manager.stop(session)
    runtime.stop.assert_not_awaited()
    assert not provider.machines


async def test_reconcile_resumes_cancelled_start_without_new_allocation(setup):

    manager, service, repository, provider, runtime, store, session = setup
    runtime.start.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))
    lease = next(iter(repository.leases.values()))
    binding = next(iter(repository.bindings.values()))
    assert binding.runtime_data_started and not binding.runtime_started
    runtime.start.side_effect = None
    recovered = VmPodManager(profile="small", pool_id="pool", max_machines=1)
    recovered.configure_compute(
        service, repository, runtime, MachineBootstrap(), pool_id="pool", max_machines=1
    )
    assert await recovered.status(session) == SessionStatus.PROVISIONING
    await recovered._recoveries[(lease.id, binding.id)]
    assert await recovered.status(session) == SessionStatus.RUNNING
    assert len(repository.leases) == len(provider.machines) == 1
    assert repository.bindings[binding.id].runtime_started
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
    await manager._recoveries[(lease.id, lease.binding.id)]
    assert await manager.status(session) == SessionStatus.FAILED
    assert (await manager.capacity()).available == 0
    assert "sensitive" not in repository.bindings[lease.binding.id].error
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
    await manager._recoveries[(lease.id, lease.binding.id)]

    assert await manager.status(session) == SessionStatus.FAILED
    assert repository.bindings[lease.binding.id].error == (
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
        assert not repo.bindings
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
    lease = await repository.active_for_session("pool", session.id)
    assert (await service.machine_bootstrap_for(lease.allocation)).commands == ()
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
    binding = next(iter(repository.bindings.values()))
    repository.leases[lease.id] = lease.model_copy(update={"state": LeaseState.READY})
    repository.bindings[binding.id] = binding.model_copy(update={"state": SlotState.STARTING})
    busy = AsyncMock(side_effect=ComputeLeaseBusyError("in progress"))
    service.mark_busy = busy
    assert await manager.status(session) == SessionStatus.RUNNING
    busy.assert_awaited_once()
    repository.leases[lease.id] = lease.model_copy(update={"state": LeaseState.IDLE})
    repository.bindings[binding.id] = binding.model_copy(update={"state": SlotState.BUSY})
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
        # The slot's start deadline passes while the guest is still unprepared.
        for slot_id, binding in list(repository.bindings.items()):
            repository.bindings[slot_id] = binding.model_copy(
                update={"start_deadline": datetime.now(UTC) - timedelta(seconds=1)}
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


# --- The slot ledger: one slot behaves as one session per machine always did ----------------


SPEC = SessionSpec(values={}, pod_spec=PodSpecAdditions())


def _ledger(repository):
    """The only machine and the only binding of a one-session test."""
    return next(iter(repository.leases.values())), next(iter(repository.bindings.values()))


async def _edit(repository, machine=None, binding=None):
    """Rewrite ledger rows the way a concurrent controller would, under the allocation lock."""
    current, slot = _ledger(repository)
    async with repository.operation(current.id):
        if machine is not None:
            await repository.save(current.model_copy(update=machine))
        if binding is not None:
            await repository.save_slot(slot.model_copy(update=binding))


async def test_a_cold_start_binds_the_session_to_slot_zero_of_its_own_machine(setup):
    manager, service, repository, provider, _, _, session = setup

    result = await manager.start(session, SPEC)

    machine, binding = _ledger(repository)
    assert result.pod_name == str(machine.id)
    assert (binding.slot, binding.session_id, binding.state) == (0, session.id, SlotState.STARTING)
    assert (binding.owner_id, binding.tenant_id) == ("owner", "tenant")
    assert machine.slots == 1 and machine.state == LeaseState.READY
    assert binding.runtime_started
    assert len(provider.created) == 1


async def test_a_running_session_leaves_its_machine_idle_and_its_slot_busy(setup):
    manager, _, repository, _, _, _, session = setup
    await manager.start(session, SPEC)

    assert await manager.status(session) == SessionStatus.RUNNING

    machine, binding = _ledger(repository)
    assert (machine.state, binding.state) == (LeaseState.IDLE, SlotState.BUSY)
    # Asking again changes nothing.
    assert await manager.status(session) == SessionStatus.RUNNING
    assert _ledger(repository) == (machine, binding)


async def test_capacity_counts_slots_and_a_session_frees_its_own_slot_for_a_restart(setup):
    manager, _, repository, _, _, _, session = setup
    capacity = await manager.capacity()
    assert (capacity.limit, capacity.active, capacity.available) == (1, 0, 1)

    await manager.start(session, SPEC)
    assert await manager.status(session) == SessionStatus.RUNNING

    busy = await manager.capacity()
    assert (busy.limit, busy.active, busy.available) == (1, 1, 0)
    mine = await manager.capacity_for(session)
    assert mine.available == 1
    other = session.model_copy(update={"id": uuid4()})
    assert (await manager.capacity_for(other)).available == 0


async def test_an_idle_machine_with_a_free_slot_is_capacity_even_without_a_pool_policy(setup):
    manager, _, repository, _, _, _, session = setup
    await manager.start(session, SPEC)
    await _edit(
        repository, machine={"state": LeaseState.IDLE}, binding={"state": SlotState.RELEASED}
    )

    free = await manager.capacity()

    assert (free.limit, free.active) == (1, 0)
    # Still in service with a live session in the slot: the same machine counts as used.
    await _edit(repository, binding={"state": SlotState.BUSY})
    assert (await manager.capacity()).active == 1


async def test_capacity_honours_a_paused_pool(setup):
    manager, _, _, _, _, _, _ = setup
    pool = AsyncMock()
    pool.policy.return_value = ComputePoolPolicy(profile="small", max_machines=1, paused=True)
    manager.configure_pool(pool)

    capacity = await manager.capacity()

    assert (capacity.limit, capacity.active) == (0, 0)


async def test_capacity_multiplies_machines_by_the_policy_slots(setup):
    manager, _, repository, _, _, _, session = setup
    pool = AsyncMock()
    pool.policy.return_value = ComputePoolPolicy(profile="small", max_machines=3)
    manager.configure_pool(pool)
    await manager.start(session, SPEC)

    capacity = await manager.capacity()

    # One machine is in service; two more may be created: three slots, one in use.
    assert (capacity.limit, capacity.active) == (3, 1)


async def test_a_machine_with_more_slots_than_the_policy_counts_its_own(setup):
    manager, _, repository, _, _, _, session = setup
    pool = AsyncMock()
    pool.policy.return_value = ComputePoolPolicy(
        profile="small", max_machines=2, slots_per_machine=2
    )
    manager.configure_pool(pool)
    await manager.start(session, SPEC)
    await _edit(repository, machine={"slots": 4})

    capacity = await manager.capacity()

    # The existing machine's four slots plus one new two-slot machine.
    assert (capacity.limit, capacity.active) == (6, 4)


async def test_a_session_cold_start_reserves_the_policys_slots_per_machine(setup):
    manager, _, repository, _, _, _, session = setup
    pool = AsyncMock()
    pool.policy.return_value = ComputePoolPolicy(
        profile="small", max_machines=1, slots_per_machine=2, provisioning_timeout_seconds=5
    )
    manager.configure_pool(pool)

    await manager.start(session, SPEC)

    assert next(iter(repository.leases.values())).slots == 2


async def test_a_staged_runtime_failure_is_not_retried_by_a_second_start(setup):
    manager, _, repository, _, runtime, _, session = setup
    runtime.prepare.side_effect = VmRuntimeStageError("sandbox-create")
    with pytest.raises(VmRuntimeStageError):
        await manager.start(session, SPEC)
    _, binding = _ledger(repository)
    assert binding.error == (
        "Runtime startup failed at stage sandbox-create; inspect guest and stop session"
    )

    runtime.prepare.side_effect = None
    with pytest.raises(RuntimeError, match="stage sandbox-create"):
        await manager.start(session, SPEC)

    assert _ledger(repository)[1].state == SlotState.FAILED
    assert runtime.start.await_count == 0


async def test_a_failed_slot_with_a_machine_that_is_gone_is_not_retried(setup):
    manager, _, repository, provider, runtime, _, session = setup
    runtime.prepare.side_effect = RuntimeError("guest package conflict")
    with pytest.raises(RuntimeError, match="package conflict"):
        await manager.start(session, SPEC)
    runtime.prepare.side_effect = None
    machine, _ = _ledger(repository)
    await _edit(
        repository,
        machine={"machine": machine.machine.model_copy(update={"state": MachineState.STOPPED})},
    )

    with pytest.raises(RuntimeError):
        await manager.start(session, SPEC)

    assert _ledger(repository)[1].state == SlotState.FAILED


async def test_the_slot_binding_disappearing_during_start_is_an_error(setup):
    manager, service, repository, _, _, _, session = setup
    reconcile = service.reconcile

    calls = 0

    async def lose_binding(lease_id):
        nonlocal calls
        calls += 1
        machine = await reconcile(lease_id)
        # The first reconcile is the claim's own; the start loop's is the second.
        if calls > 1:
            repository.bindings.clear()
        return machine

    service.reconcile = lose_binding

    with pytest.raises(RuntimeError, match="slot binding disappeared"):
        await manager.start(session, SPEC)


async def test_a_slot_that_ended_while_starting_fails_the_start_with_its_own_error(setup):
    manager, service, repository, _, _, _, session = setup
    reconcile = service.reconcile

    async def drain_slot(lease_id):
        machine = await reconcile(lease_id)
        _, binding = _ledger(repository)
        repository.bindings[binding.id] = binding.model_copy(
            update={"state": SlotState.DRAINING, "error": "slot is being deleted"}
        )
        return machine

    service.reconcile = drain_slot

    with pytest.raises(RuntimeError, match="slot is being deleted"):
        await manager.start(session, SPEC)


async def test_start_refuses_a_machine_the_session_does_not_own(setup):
    manager, _, repository, _, _, _, session = setup
    await manager.start(session, SPEC)

    with pytest.raises(ValueError, match="ownership"):
        await manager.start(session.model_copy(update={"tenant_id": "other"}), SPEC)


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"binding": {"state": SlotState.RETIRED}}, SessionStatus.STOPPED),
        ({"binding": {"state": SlotState.DRAINING}}, SessionStatus.PROVISIONING),
        ({"binding": {"state": SlotState.FAILED}}, SessionStatus.FAILED),
        # A draining machine is deleted by the reconcile this very status performs.
        ({"machine": {"state": LeaseState.DRAINING}}, SessionStatus.STOPPED),
        ({"machine": {"state": LeaseState.FAILED}}, SessionStatus.FAILED),
    ],
)
async def test_session_status_follows_the_slot_and_then_the_machine(setup, change, expected):
    manager, _, repository, provider, _, _, session = setup
    await manager.start(session, SPEC)
    await _edit(repository, **change)

    assert await manager.status(session) == expected


async def test_a_released_machine_is_a_stopped_session(setup):
    manager, _, repository, _, _, _, session = setup
    await manager.start(session, SPEC)
    machine, _ = _ledger(repository)
    manager._service.reconcile = AsyncMock(
        return_value=machine.model_copy(update={"state": LeaseState.RELEASED})
    )

    assert await manager.status(session) == SessionStatus.STOPPED


async def test_a_vanished_binding_is_a_stopped_session(setup):
    manager, _, repository, _, _, _, session = setup
    await manager.start(session, SPEC)
    repository.get_slot = AsyncMock(return_value=None)

    assert await manager.status(session) == SessionStatus.STOPPED


async def test_a_second_status_while_recovery_runs_does_not_start_another(setup):
    manager, _, repository, _, runtime, _, session = setup
    await manager.start(session, SPEC)
    await _edit(repository, binding={"runtime_started": False})
    release = asyncio.Event()

    async def hold(*args):
        await release.wait()

    runtime.prepare.side_effect = hold

    assert await manager.status(session) == SessionStatus.PROVISIONING
    assert await manager.status(session) == SessionStatus.PROVISIONING
    assert len(manager._recoveries) == 1
    release.set()
    await asyncio.gather(*manager._recoveries.values())


async def test_recovery_that_finds_the_guest_booting_is_retried_later(setup):
    manager, _, repository, _, runtime, _, session = setup
    await manager.start(session, SPEC)
    await _edit(repository, binding={"runtime_started": False})
    runtime.prepare.side_effect = VmRuntimeUnavailableError("booting")

    assert await manager.status(session) == SessionStatus.PROVISIONING
    await asyncio.gather(*manager._recoveries.values())

    assert not manager._recoveries
    assert _ledger(repository)[1].state == SlotState.STARTING


async def test_runtime_start_refuses_a_slot_that_changed_under_it(setup):
    manager, _, repository, _, _, _, session = setup
    await manager.start(session, SPEC)
    machine, binding = _ledger(repository)
    await _edit(repository, binding={"state": SlotState.FAILED})

    with pytest.raises(RuntimeError, match="changed before runtime startup"):
        await manager._start_runtime(machine.id, binding.id, MachineBootstrap())
    with pytest.raises(RuntimeError, match="changed before runtime startup"):
        await manager._start_runtime(machine.id, uuid4(), MachineBootstrap())


async def test_runtime_start_refuses_a_bootstrap_the_slot_no_longer_has(setup):
    manager, _, repository, _, _, _, session = setup
    await manager.start(session, SPEC)
    machine, binding = _ledger(repository)

    with pytest.raises(ComputeLeaseBusyError, match="rebound"):
        await manager._start_runtime(
            machine.id, binding.id, MachineBootstrap(commands=(("other",),))
        )


async def test_runtime_start_without_a_slot_deadline_uses_the_provisioning_timeout(setup):
    manager, service, repository, _, runtime, _, session = setup
    await manager.start(session, SPEC)
    machine, binding = _ledger(repository)
    await _edit(repository, binding={"start_deadline": None, "runtime_started": False})
    runtime.start.reset_mock()

    await manager._start_runtime(
        machine.id,
        binding.id,
        await service.bootstrap_for(await repository.active_for_session("pool", session.id)),
    )

    runtime.start.assert_awaited_once()
    assert _ledger(repository)[1].runtime_started


async def test_a_session_with_a_failed_or_draining_slot_has_no_proxy_target(setup):
    manager, _, repository, _, _, _, session = setup
    await manager.start(session, SPEC)
    assert await manager.session_proxy_target(session) is not None

    await _edit(repository, binding={"state": SlotState.FAILED})
    assert await manager.session_proxy_target(session) is None
    await _edit(repository, binding={"state": SlotState.STARTING})
    await _edit(repository, machine={"state": LeaseState.DRAINING})
    assert await manager.session_proxy_target(session) is None
    await _edit(repository, machine={"state": LeaseState.READY, "machine": None})
    assert await manager.session_proxy_target(session) is None


async def test_status_detail_is_only_given_while_the_slot_is_starting(setup):
    manager, _, repository, _, _, _, session = setup
    assert await manager.status_detail(session) is None
    await manager.start(session, SPEC)
    manager._wait_reasons[session.id] = "Runtime not ready: booting"
    assert await manager.status_detail(session) == "Runtime not ready: booting"

    await _edit(repository, binding={"state": SlotState.BUSY})
    assert await manager.status_detail(session) is None


# --- stopping a slot -------------------------------------------------------------------------


async def test_stop_of_a_session_whose_slot_already_ended_is_done(setup):
    manager, _, repository, _, runtime, _, session = setup
    await manager.start(session, SPEC)
    machine, binding = _ledger(repository)
    # Another controller already retired the slot.
    async with repository.operation(machine.id):
        await repository.save_slot(binding.model_copy(update={"state": SlotState.RETIRED}))

    assert await manager.stop(session)
    assert await manager.stop(session)
    runtime.stop.assert_not_awaited()


async def test_stop_finishes_a_deletion_an_earlier_attempt_began(setup):
    manager, service, repository, provider, runtime, _, session = setup
    await manager.start(session, SPEC)
    machine, binding = _ledger(repository)
    async with repository.operation(machine.id):
        stopping = binding.model_copy(update={"stop_requested": True})
        await repository.save_slot(stopping)
        await service.retire_slot(machine, stopping, drain=True)
    provider.delete_pending = True
    stop = asyncio.create_task(manager.stop(session))
    await asyncio.sleep(0.01)
    provider.delete_pending = False

    assert await stop
    assert repository.leases[machine.id].state == LeaseState.RELEASED
    assert machine.id not in provider.machines
    # The archive was done before the earlier attempt marked the machine; it is not repeated.
    runtime.stop.assert_not_awaited()


async def test_stop_after_a_crash_between_marking_the_slot_and_the_machine_still_deletes(setup):
    manager, _, repository, provider, _, _, session = setup
    await manager.start(session, SPEC)
    machine, binding = _ledger(repository)
    await _edit(repository, binding={"state": SlotState.DRAINING, "stop_requested": True})

    assert await manager.stop(session)

    assert repository.leases[machine.id].state == LeaseState.RELEASED
    assert machine.id not in provider.machines


async def test_stop_with_a_pool_reuses_the_slot_when_the_runtime_allows(setup):
    from volundr.domain.services.compute_pool import ComputePoolService

    manager, service, repository, provider, runtime, _, session = setup
    runtime.supports_reuse = True
    pool = ComputePoolService(
        repository,
        service,
        provider,
        runtime,
        pool_id="pool",
        defaults=ComputePoolPolicy(profile="small", max_machines=1, reuse_policy="reuse"),
        bootstrap=MachineBootstrap(),
    )
    manager.configure_pool(pool)
    await manager.start(session, SPEC)

    assert await manager.stop(session)

    machine, binding = _ledger(repository)
    assert machine.state == LeaseState.IDLE and binding.state == SlotState.RELEASED
    runtime.recycle.assert_awaited_once()
    assert machine.id in provider.machines
    # The next session takes the same machine.
    other = session.model_copy(update={"id": uuid4()})
    await manager.start(other, SPEC)
    assert len(provider.created) == 1
    assert (await repository.active_for_session("pool", other.id)).id == machine.id


async def test_stop_without_a_pool_never_reuses_a_slot(setup):
    manager, _, repository, provider, runtime, _, session = setup
    await manager.start(session, SPEC)

    assert await manager.stop(session)

    runtime.recycle.assert_not_awaited()
    assert not provider.machines
    machine, binding = _ledger(repository)
    assert (machine.state, binding.state) == (LeaseState.RELEASED, SlotState.RETIRED)


async def test_stop_of_a_slot_that_never_started_its_runtime_does_not_stop_the_guest(setup):
    manager, _, repository, _, runtime, _, session = setup
    runtime.prepare.side_effect = VmRuntimeUnavailableError("booting")
    task = asyncio.create_task(manager.start(session, SPEC))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await manager.stop(session)

    runtime.stop.assert_not_awaited()


@pytest.mark.parametrize("reads_before_it_vanishes", [0, 1])
async def test_stop_names_a_claim_that_vanished(setup, reads_before_it_vanishes):
    manager, _, repository, _, _, _, session = setup
    await manager.start(session, SPEC)
    original = repository.get
    calls = 0

    async def vanish(lease_id):
        nonlocal calls
        calls += 1
        return None if calls > reads_before_it_vanishes else await original(lease_id)

    repository.get = vanish

    with pytest.raises(RuntimeError, match="disappeared during stop"):
        await manager.stop(session)


# --- placing a session on a warm machine ---------------------------------------------------------


def _warm_pool(setup, **policy):
    from volundr.domain.services.compute_pool import ComputePoolService

    manager, service, repository, provider, runtime, _, session = setup
    runtime.machine_bootstrap = lambda defaults: defaults
    runtime.session_bootstrap = lambda session, spec, machine: machine
    pool = ComputePoolService(
        repository,
        service,
        provider,
        runtime,
        pool_id="pool",
        defaults=ComputePoolPolicy(profile="small", max_machines=1, warm_min=1, **policy),
        bootstrap=MachineBootstrap(),
    )
    manager.configure_pool(pool)
    return pool, manager, repository, session


async def test_a_warm_machine_with_a_stopping_session_is_not_offered(setup):
    pool, manager, repository, session = _warm_pool(setup)
    await pool.maintain()
    await pool.maintain()
    spare = next(iter(repository.leases.values()))
    assert spare.state == LeaseState.IDLE

    # Stop intent on a live slot hides the machine; here the slot is another session's.
    other = session.model_copy(update={"id": uuid4()})
    claimed = await manager._claim_warm(other, SPEC)
    assert claimed is not None
    await _edit(repository, binding={"stop_requested": True})

    assert await manager._claim_warm(session, SPEC) is None


async def test_a_warm_machine_of_another_profile_or_plan_is_skipped(setup):
    pool, manager, repository, session = _warm_pool(setup)
    await pool.maintain()
    await pool.maintain()
    spare = next(iter(repository.leases.values()))
    repository.leases[spare.id] = spare.model_copy(update={"profile": "large"})

    assert await manager._claim_warm(session, SPEC) is None

    repository.leases[spare.id] = spare.model_copy(update={"execution_plan": AsyncMock()})
    assert await manager._claim_warm(session, SPEC) is None


@pytest.mark.parametrize("taken_by", ["failure", "another session"])
async def test_a_warm_machine_taken_while_choosing_is_not_claimed_twice(setup, taken_by):
    pool, manager, repository, session = _warm_pool(setup)
    await pool.maintain()
    await pool.maintain()
    spare = next(iter(repository.leases.values()))
    get = repository.get
    taken = False

    async def take_then_read(lease_id):
        nonlocal taken
        if not taken:
            taken = True
            if taken_by == "failure":
                repository.leases[spare.id] = spare.model_copy(update={"state": LeaseState.FAILED})
            else:
                rival = ComputeSlot(
                    id=uuid4(),
                    allocation_id=spare.id,
                    slot=0,
                    pool_id="pool",
                    session_id=uuid4(),
                    tenant_id="tenant",
                    owner_id="owner",
                )
                repository.bindings[rival.id] = rival
        return await get(lease_id)

    repository.get = take_then_read

    assert await manager._claim_warm(session, SPEC) is None


async def test_a_busy_warm_machine_is_skipped_for_the_next(setup):
    pool, manager, repository, session = _warm_pool(setup)
    await pool.maintain()
    await pool.maintain()
    attempts = 0

    @asynccontextmanager
    async def contested(lease_id):
        nonlocal attempts
        attempts += 1
        raise ComputeLeaseBusyError("another controller")
        yield

    repository.operation = contested

    assert await manager._claim_warm(session, SPEC) is None
    assert attempts == 1


async def test_close_cancels_recoveries_that_are_still_running(setup):
    manager, _, repository, _, runtime, _, session = setup
    await manager.start(session, SPEC)
    await _edit(repository, binding={"runtime_started": False})

    async def hold(*args):
        await asyncio.Event().wait()

    runtime.prepare.side_effect = hold

    assert await manager.status(session) == SessionStatus.PROVISIONING
    task = next(iter(manager._recoveries.values()))
    await asyncio.sleep(0)

    await manager.close()

    assert task.cancelled()


async def test_a_machine_still_booting_or_without_an_address_leaves_the_session_provisioning(setup):
    manager, service, repository, _, _, _, session = setup
    await manager.start(session, SPEC)
    machine, _ = _ledger(repository)

    service.reconcile = AsyncMock(
        return_value=machine.model_copy(update={"state": LeaseState.PROVISIONING})
    )
    assert await manager.status(session) == SessionStatus.PROVISIONING

    unaddressed = machine.machine.model_copy(update={"addresses": ()})
    service.reconcile = AsyncMock(return_value=machine.model_copy(update={"machine": unaddressed}))
    assert await manager.status(session) == SessionStatus.PROVISIONING


def _interleave(repository, change):
    """Run ``change`` just before the first operation lock of a stop, as maintenance would."""
    operation = repository.operation
    fired = False

    @asynccontextmanager
    async def wrapped(lease_id):
        nonlocal fired
        if not fired:
            fired = True
            change()
        async with operation(lease_id):
            yield

    repository.operation = wrapped


async def test_stop_is_done_when_maintenance_finished_the_slot_first(setup):
    manager, _, repository, _, runtime, _, session = setup
    await manager.start(session, SPEC)
    _, binding = _ledger(repository)
    _interleave(
        repository,
        lambda: repository.bindings.__setitem__(
            binding.id, binding.model_copy(update={"state": SlotState.RELEASED})
        ),
    )

    assert await manager.stop(session)

    runtime.stop.assert_not_awaited()


async def test_stop_deletes_a_machine_maintenance_began_draining_in_the_meantime(setup):
    manager, _, repository, provider, runtime, _, session = setup
    await manager.start(session, SPEC)
    machine, _ = _ledger(repository)
    _interleave(
        repository,
        lambda: repository.leases.__setitem__(
            machine.id, machine.model_copy(update={"state": LeaseState.DRAINING})
        ),
    )

    assert await manager.stop(session)

    assert machine.id not in provider.machines
    runtime.stop.assert_not_awaited()
