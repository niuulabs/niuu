"""Generic VM-backed Forge sessions, composed with infrastructure and guest ports."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from niuu.ports.session_proxy import SessionProxyTarget
from volundr.domain.compute import (
    ComputeLeaseBusyError,
    ComputeLeaseRepository,
    LeaseState,
    MachineBootstrap,
    MachineState,
)
from volundr.domain.models import Session, SessionSpec, SessionStatus
from volundr.domain.ports import PodManager, PodStartResult, SessionCapacity
from volundr.domain.services.compute_leases import ComputeLeaseService
from volundr.domain.vm_runtime import VmRuntime, VmRuntimeUnavailableError

logger = logging.getLogger(__name__)


class VmPodManager(PodManager):
    def __init__(
        self,
        *,
        profile: str,
        pool_id: str,
        max_machines: int,
        server_host: str = "127.0.0.1",
        server_public_host: str = "127.0.0.1",
        server_port: int = 8080,
        public_origin: str = "",
        provisioning_timeout_seconds: float = 600,
        cleanup_timeout_seconds: float = 300,
        poll_interval_seconds: float = 2,
        **_extra,
    ):
        if not profile or not pool_id or max_machines < 1:
            raise ValueError("VM sessions require a profile, pool_id and positive max_machines")
        if min(provisioning_timeout_seconds, cleanup_timeout_seconds, poll_interval_seconds) <= 0:
            raise ValueError("VM session timeouts must be positive")
        self._profile, self._pool_id, self._limit = profile, pool_id, max_machines
        self._origin = (public_origin or f"http://{server_public_host}:{server_port}").rstrip("/")
        self._provisioning_timeout = provisioning_timeout_seconds
        self._cleanup_timeout = cleanup_timeout_seconds
        self._poll = poll_interval_seconds
        self._service: ComputeLeaseService | None = None
        self._repository: ComputeLeaseRepository | None = None
        self._runtime: VmRuntime | None = None
        self._defaults = MachineBootstrap()
        self._locks: dict[UUID, asyncio.Lock] = {}
        self._recoveries: dict[UUID, asyncio.Task] = {}

    def configure_compute(
        self,
        service: ComputeLeaseService,
        repository: ComputeLeaseRepository,
        runtime: VmRuntime,
        bootstrap: MachineBootstrap,
        *,
        pool_id: str,
        max_machines: int,
    ):
        if (pool_id, max_machines) != (self._pool_id, self._limit):
            raise ValueError("VM pod manager and compute service pool settings must agree")
        self._service, self._repository, self._runtime, self._defaults = (
            service,
            repository,
            runtime,
            bootstrap,
        )

    def _configured(self):
        if self._service is None or self._repository is None or self._runtime is None:
            raise RuntimeError(
                "VM runtime is not composed; configure compute and its runtime adapter"
            )
        return self._service, self._repository, self._runtime

    def initial_chat_endpoint(self, session: Session) -> str:
        origin = self._origin.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        return f"{origin}/s/{session.id}/session"

    async def capacity(self) -> SessionCapacity:
        _, repository, _ = self._configured()
        leases = await repository.list(self._pool_id)
        return SessionCapacity(
            limit=self._limit,
            active=sum(lease.state != LeaseState.RELEASED for lease in leases),
            remedy="raise compute.max_machines and the matching VM pod manager limit",
        )

    async def start(self, session: Session, spec: SessionSpec) -> PodStartResult:
        service, repository, runtime = self._configured()
        async with self._locks.setdefault(session.id, asyncio.Lock()):
            lease = await repository.active_for_session(self._pool_id, session.id)
            if lease is not None:
                if (lease.owner_id, lease.tenant_id) != (session.owner_id, session.tenant_id):
                    raise ValueError("VM allocation ownership does not match the session")
                bootstrap = await service.bootstrap_for(lease)
            else:
                bootstrap = runtime.bootstrap(session, spec, self._defaults)
                lease = await service.acquire(
                    session_id=session.id,
                    owner_id=session.owner_id or "",
                    tenant_id=session.tenant_id or "",
                    profile=self._profile,
                    bootstrap=bootstrap,
                )
            async with asyncio.timeout(self._provisioning_timeout):
                while True:
                    lease = await service.reconcile(lease.id)
                    if lease.state in {LeaseState.FAILED, LeaseState.DRAINING, LeaseState.RELEASED}:
                        raise RuntimeError("VM allocation cannot start; inspect its durable lease")
                    if (
                        lease.machine
                        and lease.machine.state == MachineState.RUNNING
                        and lease.machine.addresses
                    ):
                        try:
                            await self._start_runtime(lease.id, bootstrap)
                            break
                        except (VmRuntimeUnavailableError, ComputeLeaseBusyError):
                            pass  # Retry the same pinned guest while SSH finishes booting.
                    await asyncio.sleep(self._poll)
            return PodStartResult(
                chat_endpoint=self.initial_chat_endpoint(session),
                code_endpoint=None,
                pod_name=str(lease.id),
            )

    async def _start_runtime(self, lease_id: UUID, bootstrap: MachineBootstrap) -> None:
        _, repository, runtime = self._configured()
        async with repository.operation(lease_id):
            lease = await repository.get(lease_id)
            if lease is None or lease.state not in {LeaseState.READY, LeaseState.BUSY}:
                raise RuntimeError("VM claim changed before runtime startup")
            try:
                async with asyncio.timeout(self._provisioning_timeout):
                    await runtime.prepare(lease, bootstrap)
                    lease = lease.model_copy(update={"runtime_data_started": True})
                    await repository.save(lease)
                    await runtime.start(lease, bootstrap)
                    await repository.save(lease.model_copy(update={"runtime_started": True}))
            except VmRuntimeUnavailableError:
                raise
            except Exception:
                await repository.save(
                    lease.model_copy(
                        update={
                            "state": LeaseState.FAILED,
                            "error": "Runtime startup failed; inspect guest and stop session",
                        }
                    )
                )
                raise

    async def _recover_runtime(self, lease_id: UUID, bootstrap: MachineBootstrap) -> None:
        try:
            await self._start_runtime(lease_id, bootstrap)
        except (VmRuntimeUnavailableError, ComputeLeaseBusyError):
            # Booting/busy claims are retried by the next reconciliation.
            return
        except Exception:
            # _start_runtime persists failure while holding the claim lock.
            # Do not log exception data, which may include adapter credentials.
            logger.error("VM runtime recovery failed for allocation %s", lease_id)
        finally:
            self._recoveries.pop(lease_id, None)

    async def session_proxy_target(self, session: Session) -> SessionProxyTarget | None:
        service, repository, runtime = self._configured()
        lease = await repository.active_for_session(self._pool_id, session.id)
        if lease is None or lease.state not in {LeaseState.READY, LeaseState.BUSY}:
            return None
        if not lease.machine or not lease.machine.addresses:
            return None
        return await runtime.target(lease, await service.bootstrap_for(lease))

    async def status(self, session: Session) -> SessionStatus:
        service, repository, runtime = self._configured()
        lease = await repository.active_for_session(self._pool_id, session.id)
        if lease is None:
            return SessionStatus.STOPPED
        try:
            lease = await service.reconcile(lease.id)
        except ComputeLeaseBusyError:
            # A runtime start/stop owns the claim; do not overwrite its session state.
            return session.status
        if lease.state == LeaseState.FAILED:
            return SessionStatus.FAILED
        if lease.state == LeaseState.RELEASED:
            return SessionStatus.STOPPED
        if lease.state not in {LeaseState.READY, LeaseState.BUSY}:
            return SessionStatus.PROVISIONING
        if not lease.machine or not lease.machine.addresses:
            return SessionStatus.PROVISIONING
        bootstrap = await service.bootstrap_for(lease)
        if not lease.runtime_started:
            # A controller can die after allocation or any guest startup step.
            # Replay the persisted bootstrap under the same claim lock; start is
            # idempotent and never restores over an initialized allocation.
            if lease.id not in self._recoveries:
                self._recoveries[lease.id] = asyncio.create_task(
                    self._recover_runtime(lease.id, bootstrap)
                )
            return SessionStatus.PROVISIONING
        if not await runtime.ready(lease, bootstrap):
            return SessionStatus.PROVISIONING
        await service.mark_busy(lease.id)
        return SessionStatus.RUNNING

    async def wait_for_ready(self, session: Session, timeout: float) -> SessionStatus:
        try:
            async with asyncio.timeout(timeout):
                while True:
                    status = await self.status(session)
                    if status != SessionStatus.PROVISIONING:
                        return status
                    await asyncio.sleep(self._poll)
        except TimeoutError:
            return SessionStatus.FAILED

    async def stop(self, session: Session) -> bool:
        service, repository, runtime = self._configured()
        async with self._locks.setdefault(session.id, asyncio.Lock()):
            lease = await repository.active_for_session(self._pool_id, session.id)
            if lease is None:
                return True
            recovery = self._recoveries.get(lease.id)
            if recovery is not None:
                recovery.cancel()
                await asyncio.gather(recovery, return_exceptions=True)
            async with asyncio.timeout(self._cleanup_timeout):
                if lease.state != LeaseState.DRAINING:
                    # Archive failures deliberately prevent destructive VM cleanup.
                    async with repository.operation(lease.id):
                        lease = await repository.get(lease.id)
                        if lease is None:
                            raise RuntimeError("VM claim disappeared during stop")
                        if lease.state == LeaseState.RELEASED:
                            return True
                        if lease.state != LeaseState.DRAINING:
                            if lease.runtime_data_started:
                                await runtime.stop(lease, await service.bootstrap_for(lease))
                            lease = lease.model_copy(update={"state": LeaseState.DRAINING})
                            await repository.save(lease)
                    lease = await service.release(lease.id)
                while lease.state != LeaseState.RELEASED:
                    await asyncio.sleep(self._poll)
                    lease = await service.reconcile(lease.id)
            return True

    async def close(self) -> None:
        pending = list(self._recoveries.values())
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if self._runtime is not None:
            await self._runtime.close()
