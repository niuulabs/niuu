"""Generic VM-backed Forge sessions, composed with infrastructure and guest ports."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID

from niuu.observability import get_observability
from niuu.ports.session_proxy import SessionProxyTarget
from volundr.domain.compute import (
    ComputeLease,
    ComputeLeaseBusyError,
    ComputeLeaseRepository,
    LeaseState,
    MachineBootstrap,
    MachineProviderError,
    MachineState,
)
from volundr.domain.execution_catalog import (
    CatalogRef,
    ExecutionCatalogError,
    ExecutionPlanMismatchError,
    ExecutionSelectionError,
    ResolvedExecutionPlan,
)
from volundr.domain.host_preparation import HostPreparation
from volundr.domain.models import Session, SessionSpec, SessionStatus
from volundr.domain.ports import PodManager, PodStartResult, SessionCapacity
from volundr.domain.services.compute_leases import ComputeLeaseService
from volundr.domain.services.execution_catalog import ExecutionPlanResolver
from volundr.domain.vm_runtime import (
    ProfileAwareVmRuntime,
    VmRuntime,
    VmRuntimeStageError,
    VmRuntimeUnavailableError,
)

logger = logging.getLogger(__name__)


class VmPodManager(PodManager):
    @property
    def runtime_backend(self) -> str:
        return "vm"

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
        self._execution_resolver: ExecutionPlanResolver | None = None
        self._execution_runtimes: dict[str, VmRuntime] = {}
        self._execution_preparations: dict[str, HostPreparation] = {}
        self._defaults = MachineBootstrap()
        self._pool_service = None
        self._owns_runtime = True
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
        owns_runtime: bool = True,
    ):
        if (pool_id, max_machines) != (self._pool_id, self._limit):
            raise ValueError("VM pod manager and compute service pool settings must agree")
        self._service, self._repository, self._runtime, self._defaults = (
            service,
            repository,
            runtime,
            bootstrap,
        )
        self._owns_runtime = owns_runtime

    @property
    def profile(self) -> str:
        return self._profile

    def configure_pool(self, service) -> None:
        self._pool_service = service

    def configure_execution(
        self,
        resolver: ExecutionPlanResolver,
        runtimes: Mapping[str, VmRuntime],
        preparations: Mapping[str, HostPreparation],
    ) -> None:
        plans = resolver.plans()
        required = {plan.plan_digest for plan in plans}
        if set(runtimes) != required or set(preparations) != required:
            raise ValueError("Execution runtime and preparation registries must cover every plan")
        if any(not isinstance(runtime, VmRuntime) for runtime in runtimes.values()):
            raise TypeError("Execution runtime registry values must implement VmRuntime")
        if any(
            not isinstance(preparation, HostPreparation) for preparation in preparations.values()
        ):
            raise TypeError("Execution preparation registry values must implement HostPreparation")
        self._execution_resolver = resolver
        self._execution_runtimes = dict(runtimes)
        self._execution_preparations = dict(preparations)

    def _configured(self):
        if self._service is None or self._repository is None or self._runtime is None:
            raise RuntimeError(
                "VM runtime is not composed; configure compute and its runtime adapter"
            )
        return self._service, self._repository, self._runtime

    def initial_chat_endpoint(self, session: Session) -> str:
        origin = self._origin.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        return f"{origin}/s/{session.id}/session"

    async def resolve_execution(self, session: Session) -> ResolvedExecutionPlan | None:
        resolver = self._require_execution_resolver()
        _, repository, _ = self._configured()
        existing = await repository.active_for_session(self._pool_id, session.id)
        explicit = str(session.workload_config.get("execution_profile") or "").strip()
        legacy = str(session.workload_config.get("compute_profile") or "").strip()
        pinned = session.workload_config.get("_compute_execution")
        if existing is not None and existing.execution_plan is None:
            if explicit or pinned is not None:
                raise ExecutionSelectionError(
                    "An active legacy compute lease cannot change execution profile"
                )
            if legacy and legacy != existing.profile:
                raise ExecutionSelectionError(
                    "An active legacy compute lease cannot change machine profile"
                )
            return None
        if pinned is not None:
            plan = await self.execution_for(session)
            self._require_same_selection(plan, explicit, legacy, resolver)
            if existing is not None:
                await self._validate_lease_plan(existing, plan)
            else:
                await self._validate_plan(plan)
            return plan
        selected = resolver.resolve(explicit or None)
        if legacy:
            legacy_plan = resolver.resolve_legacy_compute_profile(legacy)
            if explicit and legacy_plan.execution != selected.execution:
                raise ExecutionSelectionError(
                    "execution_profile and compute_profile select different executions"
                )
            selected = legacy_plan
        await self._validate_plan(selected)
        if existing is not None:
            await self._validate_lease_plan(existing, selected)
        return selected

    async def has_legacy_allocation(self, session: Session) -> bool:
        _, repository, _ = self._configured()
        lease = await repository.active_for_session(self._pool_id, session.id)
        return lease is not None and lease.execution_plan is None

    async def execution_for(self, session: Session) -> ResolvedExecutionPlan:
        resolver = self._require_execution_resolver()
        raw = session.workload_config.get("_compute_execution")
        if not isinstance(raw, dict) or set(raw) != {
            "execution_id",
            "revision",
            "plan_digest",
        }:
            raise ExecutionPlanMismatchError(
                "Pinned compute execution reference is missing or malformed"
            )
        if any(not isinstance(raw[key], str) for key in raw):
            raise ExecutionPlanMismatchError(
                "Pinned compute execution reference is missing or malformed"
            )
        try:
            reference = CatalogRef(id=raw["execution_id"], revision=raw["revision"])
            expected_digest = str(raw["plan_digest"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ExecutionPlanMismatchError(
                "Pinned compute execution reference is missing or malformed"
            ) from exc
        plan = resolver.resolve_pinned(reference, expected_digest)
        _, repository, _ = self._configured()
        existing = await repository.active_for_session(self._pool_id, session.id)
        if existing is None:
            self._validate_composed_plan(plan)
        else:
            await self._validate_lease_plan(existing, plan)
        return plan

    @staticmethod
    def execution_reference(plan: ResolvedExecutionPlan) -> dict[str, str]:
        return {
            "execution_id": plan.execution.id,
            "revision": plan.execution.revision,
            "plan_digest": plan.plan_digest,
        }

    def _require_execution_resolver(self) -> ExecutionPlanResolver:
        if self._execution_resolver is None:
            raise RuntimeError("Compute execution catalog is not configured")
        return self._execution_resolver

    async def _validate_plan(self, plan: ResolvedExecutionPlan) -> None:
        service, _, _ = self._configured()
        try:
            await service.validate_execution_plan(plan)
        except ValueError as exc:
            raise ExecutionCatalogError(
                "Selected execution is incompatible with the configured compute provider"
            ) from exc
        self._validate_composed_plan(plan)

    def _validate_composed_plan(self, plan: ResolvedExecutionPlan) -> None:
        if plan.pool_id != self._pool_id:
            raise ExecutionPlanMismatchError(
                "Pinned execution plan targets a different compute pool"
            )
        if plan.plan_digest not in self._execution_runtimes:
            raise ExecutionCatalogError("Pinned execution runtime binding is unavailable")
        if plan.plan_digest not in self._execution_preparations:
            raise ExecutionCatalogError("Pinned host preparation binding is unavailable")

    async def _validate_lease_plan(
        self,
        lease: ComputeLease,
        selected: ResolvedExecutionPlan,
    ) -> None:
        if lease.execution_plan is None:
            raise ExecutionPlanMismatchError("Active compute lease has no resolved execution plan")
        pinned = await self._plan_for_lease(lease)
        if pinned.plan_digest != selected.plan_digest:
            raise ExecutionPlanMismatchError(
                "Session execution reference does not match its active compute lease"
            )

    @staticmethod
    def _require_same_selection(
        plan: ResolvedExecutionPlan,
        explicit: str,
        legacy: str,
        resolver: ExecutionPlanResolver,
    ) -> None:
        try:
            explicit_reference = CatalogRef.parse(explicit) if explicit else None
        except ValueError as exc:
            raise ExecutionSelectionError(
                "Execution selection must use exact 'id@revision' syntax"
            ) from exc
        if explicit_reference is not None and explicit_reference != plan.execution:
            raise ExecutionPlanMismatchError(
                "execution_profile does not match the pinned execution"
            )
        if legacy and resolver.resolve_legacy_compute_profile(legacy).execution != plan.execution:
            raise ExecutionPlanMismatchError("compute_profile does not match the pinned execution")

    async def _plan_for_lease(self, lease: ComputeLease) -> ResolvedExecutionPlan:
        if lease.execution_plan is None:
            raise ExecutionPlanMismatchError(
                "Compute lease does not contain a resolved execution plan"
            )
        resolver = self._require_execution_resolver()
        plan = resolver.resolve_pinned(
            lease.execution_plan.execution,
            lease.execution_plan.plan_digest,
        )
        if plan.plan_digest != lease.execution_plan.plan_digest:
            raise ExecutionPlanMismatchError(
                "Compute lease execution plan changed after allocation"
            )
        service, _, _ = self._configured()
        if not service.provider_compatible(lease):
            raise ExecutionCatalogError("Pinned compute provider binding is unavailable or changed")
        if plan.pool_id != self._pool_id:
            raise ExecutionPlanMismatchError(
                "Pinned execution plan targets a different compute pool"
            )
        if plan.plan_digest not in self._execution_runtimes:
            raise ExecutionCatalogError("Pinned execution runtime binding is unavailable")
        if plan.plan_digest not in self._execution_preparations:
            raise ExecutionCatalogError("Pinned host preparation binding is unavailable")
        return plan

    async def _runtime_for_lease(self, lease: ComputeLease) -> VmRuntime:
        if lease.execution_plan is None:
            _, _, runtime = self._configured()
            return runtime
        plan = await self._plan_for_lease(lease)
        return self._execution_runtimes[plan.plan_digest]

    async def _preparation_for_lease(
        self, lease: ComputeLease
    ) -> tuple[ResolvedExecutionPlan, HostPreparation]:
        plan = await self._plan_for_lease(lease)
        return plan, self._execution_preparations[plan.plan_digest]

    async def capacity(self) -> SessionCapacity:
        _, repository, _ = self._configured()
        leases = await repository.list(self._pool_id, include_released=False)
        policy = await self._pool_service.policy() if self._pool_service else None
        if policy and (policy.paused or policy.drain):
            return SessionCapacity(limit=0, active=0, remedy="resume the pool in admin settings")
        return SessionCapacity(
            limit=policy.max_machines if policy else self._limit,
            active=sum(
                lease.state not in {LeaseState.RELEASED, LeaseState.IDLE} for lease in leases
            ),
            remedy="increase pool capacity or drain unused machines in admin settings",
        )

    async def capacity_for(self, session: Session) -> SessionCapacity:
        capacity = await self.capacity()
        _, repository, _ = self._configured()
        lease = await repository.active_for_session(self._pool_id, session.id)
        if lease is None or lease.state in {LeaseState.RELEASED, LeaseState.IDLE}:
            return capacity
        return SessionCapacity(
            limit=capacity.limit,
            active=max(capacity.active - 1, 0),
            remedy=capacity.remedy,
        )

    async def start(self, session: Session, spec: SessionSpec) -> PodStartResult:
        service, repository, legacy_runtime = self._configured()
        async with self._locks.setdefault(session.id, asyncio.Lock()):
            lease = await repository.active_for_session(self._pool_id, session.id)
            if (
                self._execution_resolver is not None
                and "_compute_execution" not in session.workload_config
                and (lease is None or lease.execution_plan is not None)
            ):
                raise ValueError(
                    "Catalog compute sessions require a resolved execution pin before start"
                )
            if lease is not None:
                if (lease.owner_id, lease.tenant_id) != (session.owner_id, session.tenant_id):
                    raise ValueError("VM allocation ownership does not match the session")
                if lease.execution_plan is not None and lease.session_bootstrap_ref is None:
                    plan = await self._plan_for_lease(lease)
                    runtime = self._execution_runtimes[plan.plan_digest]
                    machine_bootstrap = await service.machine_bootstrap_for(lease)
                    bootstrap = runtime.session_bootstrap(session, spec, machine_bootstrap)
                    lease = await service.attach_session_bootstrap(
                        lease.id,
                        session.id,
                        bootstrap,
                    )
                bootstrap = await service.bootstrap_for(lease)
                lease = await self._retry_failed_runtime(lease.id)
            else:
                lease = await self._claim_warm(session, spec)
                if lease is not None:
                    bootstrap = await service.bootstrap_for(lease)
            if lease is None:
                plan = (
                    await self.execution_for(session)
                    if "_compute_execution" in session.workload_config
                    else None
                )
                if plan is None:
                    profile = await self._requested_profile(session)
                    if isinstance(legacy_runtime, ProfileAwareVmRuntime):
                        machine_bootstrap = legacy_runtime.machine_bootstrap_for(
                            profile, self._defaults
                        )
                        bootstrap = legacy_runtime.session_bootstrap_for(
                            profile, session, spec, machine_bootstrap
                        )
                    else:
                        machine_bootstrap = legacy_runtime.machine_bootstrap(self._defaults)
                        bootstrap = legacy_runtime.session_bootstrap(
                            session, spec, machine_bootstrap
                        )
                else:
                    preparation = self._execution_preparations[plan.plan_digest]
                    runtime = self._execution_runtimes[plan.plan_digest]
                    machine_bootstrap = preparation.machine_bootstrap(
                        plan.host_recipe, self._defaults
                    )
                    bootstrap = runtime.session_bootstrap(session, spec, machine_bootstrap)
                    profile = plan.provider_profile
                get_observability().count(
                    "volundr.compute.assignments",
                    attributes={"pool": self._pool_id, "source": "cold"},
                )
                lease = await service.acquire(
                    session_id=session.id,
                    owner_id=session.owner_id or "",
                    tenant_id=session.tenant_id or "",
                    profile=profile,
                    bootstrap=machine_bootstrap,
                    session_bootstrap=bootstrap,
                    execution_plan=plan,
                    timeout_seconds=(await self._pool_service.policy()).provisioning_timeout_seconds
                    if self._pool_service
                    else self._provisioning_timeout,
                )
                if plan is not None:
                    bootstrap = await service.bootstrap_for(lease)
            async with asyncio.timeout(self._provisioning_timeout):
                while True:
                    try:
                        lease = await service.reconcile(lease.id)
                    except ComputeLeaseBusyError:
                        # Pool maintenance and session startup share the same
                        # per-allocation operation lock. Retry this durable
                        # claim within the existing provisioning deadline.
                        await asyncio.sleep(self._poll)
                        continue
                    if lease.state in {LeaseState.FAILED, LeaseState.DRAINING, LeaseState.RELEASED}:
                        raise RuntimeError(
                            lease.error or "VM allocation cannot start; inspect its durable lease"
                        )
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

    async def _retry_failed_runtime(self, lease_id: UUID):
        """Re-open only a failed guest-runtime start on the same running machine."""
        service, repository, _ = self._configured()
        async with repository.operation(lease_id):
            lease = await repository.get(lease_id)
            if lease is None:
                raise RuntimeError("VM claim disappeared before runtime retry")
            if lease.state != LeaseState.FAILED:
                return lease
            if (
                lease.error != "Runtime startup failed; inspect guest and stop session"
                or lease.machine is None
                or lease.machine.state != MachineState.RUNNING
                or not lease.machine.addresses
                or not await service.compatible(lease)
            ):
                return lease
            timeout = (
                (await self._pool_service.policy()).provisioning_timeout_seconds
                if self._pool_service
                else self._provisioning_timeout
            )
            lease = lease.model_copy(
                update={
                    "state": LeaseState.READY,
                    "provision_deadline": datetime.now(UTC) + timedelta(seconds=timeout),
                    "retry_after": None,
                    "failures": 0,
                    "runtime_data_started": False,
                    "runtime_started": False,
                    "error": None,
                }
            )
            await repository.save(lease)
            return lease

    async def _claim_warm(self, session: Session, spec: SessionSpec):
        if self._pool_service is None:
            return None
        service, repository, legacy_runtime = self._configured()
        policy = await self._pool_service.policy()
        plan = (
            await self.execution_for(session)
            if "_compute_execution" in session.workload_config
            else None
        )
        profile = (
            plan.provider_profile if plan is not None else await self._requested_profile(session)
        )
        runtime = self._execution_runtimes[plan.plan_digest] if plan is not None else legacy_runtime
        for candidate in await repository.list(self._pool_id, include_released=False):
            if candidate.state != LeaseState.IDLE or candidate.profile != profile:
                continue
            if plan is None and candidate.execution_plan is not None:
                continue
            if plan is not None and (
                candidate.execution_plan is None
                or candidate.execution_plan.host_compatibility_digest
                != plan.host_compatibility_digest
            ):
                continue
            try:
                async with repository.operation(candidate.id):
                    lease = await repository.get(candidate.id)
                    if (
                        lease is None
                        or lease.state != LeaseState.IDLE
                        or lease.session_id is not None
                        or not await service.compatible(lease)
                    ):
                        continue
                    machine_bootstrap = await service.machine_bootstrap_for(lease)
                    if isinstance(runtime, ProfileAwareVmRuntime):
                        bootstrap = runtime.session_bootstrap_for(
                            profile, session, spec, machine_bootstrap
                        )
                    else:
                        bootstrap = runtime.session_bootstrap(session, spec, machine_bootstrap)
                    bound = await service.bind(
                        lease,
                        session.id,
                        session.owner_id or "",
                        session.tenant_id or "",
                        bootstrap,
                        policy.provisioning_timeout_seconds,
                        execution_plan=plan,
                    )
                    get_observability().count(
                        "volundr.compute.assignments",
                        attributes={"pool": self._pool_id, "source": "warm"},
                    )
                    return bound
            except ComputeLeaseBusyError:
                continue
        return None

    async def _requested_profile(self, session: Session) -> str:
        """Resolve an operator-approved compute profile for this session.

        The pool policy remains the default and controls warm-pool maintenance.
        A session may select another configured profile explicitly, which lets
        one Forge instance host different VM runtime profiles without silently
        assigning the current administrative warm-pool profile.
        """
        policy_profile = (
            (await self._pool_service.policy()).profile
            if self._pool_service is not None
            else self._profile
        )
        requested = str(session.workload_config.get("compute_profile") or "").strip()
        if not requested:
            return policy_profile
        service, _, _ = self._configured()
        configured = {profile.name for profile in await service.validated_profiles()}
        if requested not in configured:
            raise ValueError("Selected compute profile is not configured by the provider")
        return requested

    async def _start_runtime(self, lease_id: UUID, bootstrap: MachineBootstrap) -> None:
        service, repository, _ = self._configured()
        async with repository.operation(lease_id):
            lease = await repository.get(lease_id)
            if lease is None or lease.state not in {LeaseState.READY, LeaseState.BUSY}:
                raise RuntimeError("VM claim changed before runtime startup")
            if bootstrap != await service.bootstrap_for(lease):
                raise ComputeLeaseBusyError("Allocation was rebound before runtime startup")
            runtime = await self._runtime_for_lease(lease)
            try:
                remaining = self._provisioning_timeout
                if lease.provision_deadline is not None:
                    remaining = (lease.provision_deadline - datetime.now(UTC)).total_seconds()
                async with asyncio.timeout(max(0, remaining)):
                    lease = await self._ensure_host_prepared(lease, service, repository)
                    await runtime.prepare(lease, bootstrap)
                    lease = lease.model_copy(update={"runtime_data_started": True})
                    await repository.save(lease)
                    await runtime.start(lease, bootstrap)
                    await repository.save(lease.model_copy(update={"runtime_started": True}))
            except VmRuntimeUnavailableError:
                raise
            except Exception as error:
                detail = (
                    f"Runtime startup failed at stage {error.stage}; inspect guest and stop session"
                    if isinstance(error, VmRuntimeStageError)
                    else "Runtime startup failed; inspect guest and stop session"
                )
                await repository.save(
                    lease.model_copy(
                        update={
                            "state": LeaseState.FAILED,
                            "error": detail,
                        }
                    )
                )
                raise

    async def _ensure_host_prepared(
        self,
        lease: ComputeLease,
        service: ComputeLeaseService,
        repository: ComputeLeaseRepository,
    ) -> ComputeLease:
        if lease.execution_plan is None:
            return lease
        plan, preparation = await self._preparation_for_lease(lease)
        if not lease.host_preparation_started:
            lease = lease.model_copy(update={"host_preparation_started": True})
            await repository.save(lease)
        machine_bootstrap = await service.machine_bootstrap_for(lease)
        if lease.host_preparation_result is None:
            result = await preparation.ensure(lease, machine_bootstrap, plan.host_recipe)
        else:
            result = await preparation.observe(lease, machine_bootstrap, plan.host_recipe)
        if result.recipe_digest != plan.host_recipe_digest:
            raise RuntimeError("Host preparation result does not match the pinned recipe")
        result.verify_requirements(plan.host_requirements)
        lease = lease.model_copy(update={"host_preparation_result": result.model_dump(mode="json")})
        await repository.save(lease)
        return lease

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
        service, repository, _ = self._configured()
        lease = await repository.active_for_session(self._pool_id, session.id)
        if lease is None or lease.state not in {LeaseState.READY, LeaseState.BUSY}:
            return None
        if not lease.machine or not lease.machine.addresses:
            return None
        runtime = await self._runtime_for_lease(lease)
        return await runtime.target(lease, await service.bootstrap_for(lease))

    async def status(self, session: Session) -> SessionStatus:
        service, repository, _ = self._configured()
        lease = await repository.active_for_session(self._pool_id, session.id)
        if lease is None:
            # Session creation persists STARTING before its background task
            # reserves the durable compute lease.  A concurrent liveness sweep
            # must not interpret that normal admission window as a dead VM and
            # terminally reap the session.
            if session.status in {SessionStatus.STARTING, SessionStatus.PROVISIONING}:
                return SessionStatus.PROVISIONING
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
        if lease.execution_plan is not None and lease.session_bootstrap_ref is None:
            return SessionStatus.PROVISIONING
        bootstrap = await service.bootstrap_for(lease)
        runtime = await self._runtime_for_lease(lease)
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

    async def status_detail(self, session: Session) -> str | None:
        """Expose a provider-safe wait reason without treating it as failure."""
        _, repository, _ = self._configured()
        lease = await repository.active_for_session(self._pool_id, session.id)
        if lease is None or lease.state not in {LeaseState.PROVISIONING, LeaseState.READY}:
            return None
        return lease.error

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
        service, repository, _ = self._configured()
        async with self._locks.setdefault(session.id, asyncio.Lock()):
            lease = await repository.active_for_session(self._pool_id, session.id)
            if lease is None:
                return True
            recovery = self._recoveries.get(lease.id)
            if recovery is not None:
                recovery.cancel()
                await asyncio.gather(recovery, return_exceptions=True)
            async with asyncio.timeout(self._cleanup_timeout):
                while True:
                    lease = await repository.get(lease.id)
                    if lease is None:
                        raise RuntimeError("VM claim disappeared during stop")
                    if lease.state == LeaseState.RELEASED or lease.session_id != session.id:
                        return True
                    try:
                        if lease.state != LeaseState.DRAINING:
                            # Archive failures deliberately prevent destructive VM cleanup.
                            async with repository.operation(lease.id):
                                lease = await repository.get(lease.id)
                                if lease is None:
                                    raise RuntimeError("VM claim disappeared during stop")
                                if (
                                    lease.state == LeaseState.RELEASED
                                    or lease.session_id != session.id
                                ):
                                    return True
                                if lease.state != LeaseState.DRAINING:
                                    lease = lease.model_copy(update={"stop_requested": True})
                                    await repository.save(lease)
                                    lease = await self._finish_stop(lease)
                                    if lease.state == LeaseState.IDLE:
                                        return True
                            lease = await service.release(lease.id)
                        lease = await service.reconcile(lease.id)
                    except ComputeLeaseBusyError:
                        # Maintenance contention retains the claim. Retry within the
                        # existing cleanup deadline; capacity remains reserved.
                        pass
                    except MachineProviderError:
                        # Only a provider failure after archive and the durable DRAINING
                        # transition is retryable here. Bootstrap, configuration and
                        # runtime failures must remain visible immediately.
                        current = await repository.get(lease.id)
                        if (
                            current is None
                            or current.state != LeaseState.DRAINING
                            or not service.provider_compatible(current)
                        ):
                            raise
                    await asyncio.sleep(self._poll)
            return True

    async def _finish_stop(self, lease):
        if self._pool_service:
            return await self._pool_service.finish_stop(lease)
        service, repository, _ = self._configured()
        if lease.runtime_data_started:
            runtime = await self._runtime_for_lease(lease)
            await runtime.stop(lease, await service.bootstrap_for(lease))
        lease = lease.model_copy(update={"state": LeaseState.DRAINING})
        await repository.save(lease)
        return lease

    async def close(self) -> None:
        pending = list(self._recoveries.values())
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if not self._owns_runtime:
            return
        seen: set[int] = set()
        for runtime in (
            *((self._runtime,) if self._runtime is not None else ()),
            *self._execution_runtimes.values(),
        ):
            if id(runtime) in seen:
                continue
            seen.add(id(runtime))
            await runtime.close()
        for preparation in self._execution_preparations.values():
            if id(preparation) in seen:
                continue
            seen.add(id(preparation))
            await preparation.close()
