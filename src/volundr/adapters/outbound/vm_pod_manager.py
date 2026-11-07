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
    ComputeProvisioningLimitError,
    ComputeSlot,
    LeaseState,
    MachineBootstrap,
    MachineProviderError,
    MachineState,
    SlotLease,
    SlotState,
    usable_slots,
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
from volundr.domain.session_startup import SessionStartup, StartupStage, StartupStageState
from volundr.domain.vm_runtime import (
    ForgeSessionVmRuntime,
    ProfileAwareVmRuntime,
    StartupReportingVmRuntime,
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
        runtime_wait_log_every: int = 10,
        runtime_wait_surface_after: int = 3,
        **_extra,
    ):
        if not profile or not pool_id or max_machines < 1:
            raise ValueError("VM sessions require a profile, pool_id and positive max_machines")
        if min(provisioning_timeout_seconds, cleanup_timeout_seconds, poll_interval_seconds) <= 0:
            raise ValueError("VM session timeouts must be positive")
        if min(runtime_wait_log_every, runtime_wait_surface_after) < 1:
            raise ValueError("VM runtime wait reporting counts must be positive")
        self._profile, self._pool_id, self._limit = profile, pool_id, max_machines
        self._origin = (public_origin or f"http://{server_public_host}:{server_port}").rstrip("/")
        self._provisioning_timeout = provisioning_timeout_seconds
        self._cleanup_timeout = cleanup_timeout_seconds
        self._poll = poll_interval_seconds
        # A guest that stays "not ready" (e.g. a runner without the configured Skuld)
        # is logged on the first and every Nth attempt, and its reason becomes the
        # session's visible wait detail after M attempts.
        self._runtime_wait_log_every = runtime_wait_log_every
        self._runtime_wait_surface_after = runtime_wait_surface_after
        # Session id -> why its guest runtime is not ready yet. Kept here, not on the
        # lease, because every reconcile rewrites lease.error from the machine state.
        self._wait_reasons: dict[UUID, str] = {}
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
        self._recoveries: dict[tuple[UUID, UUID], asyncio.Task] = {}
        # Session id -> the stages of its current start, as this controller drove them.
        self._progress: dict[UUID, SessionStartup] = {}

    def _runtimes(self) -> tuple[VmRuntime, ...]:
        return (
            *((self._runtime,) if self._runtime is not None else ()),
            *self._execution_runtimes.values(),
        )

    @property
    def delivers_forge_session_token(self) -> bool:
        """Every configured runtime writes the launch credential into its guest."""
        runtimes = self._runtimes()
        return bool(runtimes) and all(
            isinstance(runtime, ForgeSessionVmRuntime) and runtime.delivers_forge_session_token
            for runtime in runtimes
        )

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
        lease: SlotLease,
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

    async def _plan_for_lease(self, lease: SlotLease) -> ResolvedExecutionPlan:
        """The session's pinned plan, from its slot binding."""
        return self._pinned_plan(lease.execution_plan, lease.allocation)

    async def _plan_for_machine(self, machine: ComputeLease) -> ResolvedExecutionPlan:
        """The plan the machine's host was prepared with."""
        return self._pinned_plan(machine.execution_plan, machine)

    def _pinned_plan(
        self, execution_plan: ResolvedExecutionPlan | None, machine: ComputeLease
    ) -> ResolvedExecutionPlan:
        if execution_plan is None:
            raise ExecutionPlanMismatchError(
                "Compute lease does not contain a resolved execution plan"
            )
        resolver = self._require_execution_resolver()
        plan = resolver.resolve_pinned(
            execution_plan.execution,
            execution_plan.plan_digest,
        )
        if plan.plan_digest != execution_plan.plan_digest:
            raise ExecutionPlanMismatchError(
                "Compute lease execution plan changed after allocation"
            )
        service, _, _ = self._configured()
        if not service.provider_compatible(machine):
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

    async def _runtime_for_lease(self, lease: SlotLease) -> VmRuntime:
        """The runtime of the session's own execution plan."""
        if lease.execution_plan is None:
            _, _, runtime = self._configured()
            return runtime
        plan = await self._plan_for_lease(lease)
        return self._execution_runtimes[plan.plan_digest]

    async def _preparation_for_lease(
        self, machine: ComputeLease
    ) -> tuple[ResolvedExecutionPlan, HostPreparation]:
        """The preparation of the plan the machine's host was prepared with."""
        plan = await self._plan_for_machine(machine)
        return plan, self._execution_preparations[plan.plan_digest]

    async def capacity(self) -> SessionCapacity:
        """Session slots, not machines: a machine counts as many as it has slots."""
        _, repository, _ = self._configured()
        machines = await repository.list(self._pool_id, include_released=False)
        bindings = await repository.machine_slots(self._pool_id)
        policy = await self._pool_service.policy() if self._pool_service else None
        if policy and (policy.paused or policy.drain):
            return SessionCapacity(limit=0, active=0, remedy="resume the pool in admin settings")
        # Without a pool service there is no policy: machines are single-slot, as reserved.
        slots_per_machine = policy.slots_per_machine if policy else 1
        max_machines = policy.max_machines if policy else self._limit
        now = datetime.now(UTC)

        def free(machine: ComputeLease) -> int:
            if machine.state != LeaseState.IDLE:
                return 0
            if policy:
                return usable_slots(machine, bindings.get(machine.id, []), policy, now)
            occupied = sum(b.state is not SlotState.RELEASED for b in bindings.get(machine.id, []))
            return max(0, machine.slots - occupied)

        return SessionCapacity(
            limit=max(
                0,
                sum(machine.slots for machine in machines)
                + (max_machines - len(machines)) * slots_per_machine,
            ),
            active=sum(machine.slots - free(machine) for machine in machines),
            remedy="increase pool capacity or drain unused machines in admin settings",
        )

    async def capacity_for(self, session: Session) -> SessionCapacity:
        capacity = await self.capacity()
        _, repository, _ = self._configured()
        lease = await repository.active_for_session(self._pool_id, session.id)
        if lease is None:
            return capacity
        return SessionCapacity(
            limit=capacity.limit,
            active=max(capacity.active - 1, 0),
            remedy=capacity.remedy,
        )

    @property
    def reports_startup_stages(self) -> bool:
        return True

    async def startup_stages(self, session: Session) -> tuple[StartupStage, ...]:
        """The stages this controller drove the session's current start through.

        After a controller restart the start is replayed by reconciliation, which
        reports from the step it resumes at.
        """
        progress = self._progress.get(session.id)
        return progress.stages if progress is not None else ()

    def _begin_progress(self, session_id: UUID) -> None:
        now = datetime.now(UTC)
        pending = StartupStageState.PENDING
        self._progress[session_id] = SessionStartup(
            started_at=now,
            stages=(
                StartupStage(key="allocate", label="Claiming a VM", state=pending),
                StartupStage(key="boot", label="Booting the VM", state=pending),
                StartupStage(key="guest", label="Waiting for the guest", state=pending),
                StartupStage(key="broker", label="Waiting for the broker", state=pending),
            ),
        )
        for runtime in self._runtimes():
            if isinstance(runtime, StartupReportingVmRuntime):
                runtime.report_startup_to(self._runtime_step)

    def _set_stage(
        self,
        session_id: UUID,
        key: str,
        label: str,
        state: StartupStageState,
        detail: str | None = None,
    ) -> None:
        progress = self._progress.get(session_id)
        if progress is None:
            return
        now = datetime.now(UTC)
        current = next((s for s in progress.stages if s.key == key), None)
        if current is not None and current.state is state and current.detail == detail:
            return
        started = current.started_at if current is not None and current.started_at else now
        self._progress[session_id] = progress.with_stage(
            StartupStage(
                key=key,
                label=label,
                state=state,
                detail=detail,
                started_at=started,
                finished_at=now if state is StartupStageState.DONE else None,
            )
        )

    def _skip_stage(self, session_id: UUID, key: str) -> None:
        """Drop a stage still pending that this start does not go through."""
        progress = self._progress.get(session_id)
        if progress is None:
            return
        self._progress[session_id] = progress.model_copy(
            update={
                "stages": tuple(
                    s
                    for s in progress.stages
                    if s.key != key or s.state is not StartupStageState.PENDING
                )
            }
        )

    def _stage_pending(self, session_id: UUID, key: str) -> bool:
        progress = self._progress.get(session_id)
        stage = next((s for s in progress.stages if s.key == key), None) if progress else None
        return stage is not None and stage.state is StartupStageState.PENDING

    def _runtime_step(self, session_id: UUID, key: str, label: str) -> None:
        self._set_stage(session_id, key, label, StartupStageState.ACTIVE)

    async def start(self, session: Session, spec: SessionSpec) -> PodStartResult:
        service, repository, legacy_runtime = self._configured()
        async with self._locks.setdefault(session.id, asyncio.Lock()):
            self._begin_progress(session.id)
            if spec.forge_session is not None:
                # Held per launch, never in the lease bootstrap: the next start
                # mints a new credential and revokes this one.
                for runtime in self._runtimes():
                    if isinstance(runtime, ForgeSessionVmRuntime):
                        runtime.stage_forge_session(session.id, spec.forge_session)
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
                if lease.execution_plan is not None and lease.binding.session_bootstrap_ref is None:
                    plan = await self._plan_for_lease(lease)
                    runtime = self._execution_runtimes[plan.plan_digest]
                    machine_bootstrap = await service.machine_bootstrap_for(lease.allocation)
                    bootstrap = runtime.session_bootstrap(session, spec, machine_bootstrap)
                    lease = await service.attach_session_bootstrap(
                        lease.id,
                        lease.binding.id,
                        session.id,
                        bootstrap,
                    )
                bootstrap = await service.bootstrap_for(lease)
                lease = await self._retry_failed_runtime(lease)
                self._set_stage(
                    session.id,
                    "allocate",
                    "Claiming a VM",
                    StartupStageState.DONE,
                    "This session's own VM",
                )
            else:
                lease = await self._claim_warm(session, spec)
                if lease is not None:
                    bootstrap = await service.bootstrap_for(lease)
                    self._set_stage(
                        session.id,
                        "allocate",
                        "Claiming a VM",
                        StartupStageState.DONE,
                        "A warm VM from the pool",
                    )
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
                self._set_stage(
                    session.id,
                    "allocate",
                    "Claiming a VM",
                    StartupStageState.ACTIVE,
                    f"Requesting a new VM, profile {profile}",
                )
                policy = await self._pool_service.policy() if self._pool_service else None
                timeout_seconds = (
                    policy.provisioning_timeout_seconds if policy else self._provisioning_timeout
                )
                lease = await self._acquire_when_provisioning_frees(
                    service,
                    session,
                    timeout_seconds,
                    session_id=session.id,
                    owner_id=session.owner_id or "",
                    tenant_id=session.tenant_id or "",
                    profile=profile,
                    bootstrap=machine_bootstrap,
                    session_bootstrap=bootstrap,
                    execution_plan=plan,
                    timeout_seconds=timeout_seconds,
                    # Without a pool service there is no policy: machines are single-slot.
                    slots=policy.slots_per_machine if policy else 1,
                )
                if plan is not None:
                    bootstrap = await service.bootstrap_for(lease)
                self._set_stage(
                    session.id,
                    "allocate",
                    "Claiming a VM",
                    StartupStageState.DONE,
                    f"A new VM, profile {profile}",
                )
            # Survives the timeout's cancellation of the wait, so the failure says why.
            wait: dict = {"count": 0, "reason": None}
            try:
                async with asyncio.timeout(self._provisioning_timeout):
                    lease = await self._wait_and_start(service, session, lease, bootstrap, wait)
            except TimeoutError:
                if wait["reason"] is None:
                    raise
                raise RuntimeError(self._runtime_not_ready(wait["reason"])) from None
            return PodStartResult(
                chat_endpoint=self.initial_chat_endpoint(session),
                code_endpoint=None,
                pod_name=str(lease.id),
            )

    async def _acquire_when_provisioning_frees(
        self, service: ComputeLeaseService, session: Session, wait_seconds: float, **claim
    ) -> SlotLease:
        """Queue behind the pool's provisioning limit instead of failing the start.

        A full pool (no unreserved slots) still fails at once; only the transient
        concurrent-provisioning limit waits, bounded by the provisioning timeout.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_seconds
        queued = False
        while True:
            try:
                return await service.acquire(**claim)
            except ComputeProvisioningLimitError:
                if loop.time() >= deadline:
                    raise
                if not queued:
                    logger.info(
                        "Session %s waits for a provisioning slot in compute pool %s",
                        session.id,
                        self._pool_id,
                    )
                    self._set_stage(
                        session.id,
                        "allocate",
                        "Claiming a VM",
                        StartupStageState.ACTIVE,
                        f"Waiting for a provisioning slot: compute pool {self._pool_id} is "
                        "already provisioning as many VMs as its policy allows",
                    )
                    queued = True
                await asyncio.sleep(self._poll)

    async def _wait_and_start(
        self,
        service: ComputeLeaseService,
        session: Session,
        lease: SlotLease,
        bootstrap: MachineBootstrap,
        wait: dict,
    ) -> SlotLease:
        _, repository, _ = self._configured()
        while True:
            try:
                machine = await service.reconcile(lease.id)
            except ComputeLeaseBusyError:
                # Pool maintenance and session startup share the same
                # per-allocation operation lock. Retry this durable
                # claim within the existing provisioning deadline.
                await asyncio.sleep(self._poll)
                continue
            binding = await repository.get_slot(lease.binding.id)
            if binding is None:
                raise RuntimeError("VM slot binding disappeared during start")
            lease = SlotLease(allocation=machine, binding=binding)
            machine_ended = machine.state in {
                LeaseState.FAILED,
                LeaseState.DRAINING,
                LeaseState.RELEASED,
            }
            if machine_ended or binding.state not in {SlotState.STARTING, SlotState.BUSY}:
                error = (machine.error if machine_ended else binding.error) or (
                    "VM allocation cannot start; inspect its durable lease"
                )
                if wait["reason"] is not None:
                    # The lease's durable deadline can expire just before this wait's
                    # own timeout: either way the guest never became ready, so say why.
                    error = f"{self._runtime_not_ready(wait['reason'])}. {error}"
                raise RuntimeError(error)
            self._observe_boot(session.id, lease)
            if (
                lease.machine
                and lease.machine.state == MachineState.RUNNING
                and lease.machine.addresses
            ):
                try:
                    await self._start_runtime(lease.id, lease.binding.id, bootstrap)
                    self._wait_reasons.pop(session.id, None)
                    return lease
                except ComputeLeaseBusyError:
                    pass
                except VmRuntimeUnavailableError as exc:
                    # Retry the same pinned guest while it finishes booting, but never
                    # silently: a guest that stays unprepared needs an operator.
                    wait["count"] += 1
                    wait["reason"] = str(exc) or type(exc).__name__
                    self._set_stage(
                        session.id,
                        "guest",
                        "Waiting for the guest",
                        StartupStageState.ACTIVE,
                        wait["reason"],
                    )
                    if wait["count"] == 1 or wait["count"] % self._runtime_wait_log_every == 0:
                        logger.warning(
                            "Session %s: VM runtime not ready on allocation %s (attempt %d): %s",
                            session.id,
                            lease.id,
                            wait["count"],
                            wait["reason"],
                        )
                    if wait["count"] >= self._runtime_wait_surface_after:
                        self._wait_reasons[session.id] = f"Runtime not ready: {wait['reason']}"
            await asyncio.sleep(self._poll)

    def _observe_boot(self, session_id: UUID, lease: SlotLease) -> None:
        """Report the machine's own state while it boots, then that the guest is next."""
        machine = lease.machine
        if machine is not None and machine.state == MachineState.RUNNING and machine.addresses:
            progress = self._progress.get(session_id)
            boot = next((s for s in progress.stages if s.key == "boot"), None) if progress else None
            if boot is None:
                # Skipped on an earlier pass (a warm or retained VM), or no launch is
                # being recorded: a retry of the guest must not bring a boot stage back.
                return
            if boot.state is StartupStageState.PENDING:
                # A warm or retained VM was already up: this start never booted it.
                self._skip_stage(session_id, "boot")
                return
            self._set_stage(
                session_id,
                "boot",
                "Booting the VM",
                StartupStageState.DONE,
                f"{machine.phase or 'Running'} at {', '.join(machine.addresses)}",
            )
            return
        if machine is None:
            detail = "Creating the VM"
        else:
            detail = machine.phase or machine.status_detail or f"VM is {machine.state.value}"
        self._set_stage(session_id, "boot", "Booting the VM", StartupStageState.ACTIVE, detail)

    def _runtime_not_ready(self, reason: str) -> str:
        return f"VM runtime did not become ready within {self._provisioning_timeout:g}s: {reason}"

    async def _retry_failed_runtime(self, view: SlotLease) -> SlotLease:
        """Re-open only a failed guest-runtime start on the same running machine."""
        service, repository, _ = self._configured()
        async with repository.operation(view.id):
            machine = await repository.get(view.id)
            binding = await repository.get_slot(view.binding.id)
            if machine is None or binding is None:
                raise RuntimeError("VM claim disappeared before runtime retry")
            current = SlotLease(allocation=machine, binding=binding)
            if binding.state != SlotState.FAILED:
                return current
            if (
                binding.error != "Runtime startup failed; inspect guest and stop session"
                or machine.state not in {LeaseState.READY, LeaseState.IDLE}
                or machine.machine is None
                or machine.machine.state != MachineState.RUNNING
                or not machine.machine.addresses
                or not await service.compatible(machine)
            ):
                return current
            timeout = (
                (await self._pool_service.policy()).provisioning_timeout_seconds
                if self._pool_service
                else self._provisioning_timeout
            )
            binding = binding.model_copy(
                update={
                    "state": SlotState.STARTING,
                    "start_deadline": datetime.now(UTC) + timedelta(seconds=timeout),
                    "runtime_data_started": False,
                    "runtime_started": False,
                    "error": None,
                }
            )
            await repository.save_slot(binding)
            return SlotLease(allocation=machine, binding=binding)

    async def _claim_warm(self, session: Session, spec: SessionSpec) -> SlotLease | None:
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
        bindings = await repository.machine_slots(self._pool_id)

        def occupied(machine: ComputeLease) -> int:
            return sum(
                item.state is not SlotState.RELEASED for item in bindings.get(machine.id, [])
            )

        # Fill the busiest machine first, then the oldest: sessions pack onto few machines and
        # the emptiest ones stay free to expire or to serve a session that needs a whole machine.
        candidates = sorted(
            await repository.list(self._pool_id, include_released=False),
            key=lambda machine: (-occupied(machine), machine.created_at),
        )
        for candidate in candidates:
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
            if (
                usable_slots(candidate, bindings.get(candidate.id, []), policy, datetime.now(UTC))
                < 1
            ):
                continue
            try:
                async with repository.operation(candidate.id):
                    lease = await repository.get(candidate.id)
                    # Re-check under the allocation lock: a concurrent start may have taken it.
                    if (
                        lease is None
                        or lease.state != LeaseState.IDLE
                        or usable_slots(
                            lease, await repository.slots(lease.id), policy, datetime.now(UTC)
                        )
                        < 1
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

    async def _start_runtime(
        self, allocation_id: UUID, binding_id: UUID, bootstrap: MachineBootstrap
    ) -> None:
        service, repository, _ = self._configured()
        async with repository.operation(allocation_id):
            machine = await repository.get(allocation_id)
            binding = await repository.get_slot(binding_id)
            if (
                machine is None
                or binding is None
                or machine.state not in {LeaseState.READY, LeaseState.IDLE}
                or binding.state not in {SlotState.STARTING, SlotState.BUSY}
            ):
                raise RuntimeError("VM claim changed before runtime startup")
            lease = SlotLease(allocation=machine, binding=binding)
            if bootstrap != await service.bootstrap_for(lease):
                raise ComputeLeaseBusyError("Allocation was rebound before runtime startup")
            runtime = await self._runtime_for_lease(lease)
            try:
                remaining = self._provisioning_timeout
                if binding.start_deadline is not None:
                    remaining = (binding.start_deadline - datetime.now(UTC)).total_seconds()
                session_id = binding.session_id
                async with asyncio.timeout(max(0, remaining)):
                    if lease.execution_plan is not None:
                        self._set_stage(
                            session_id, "host", "Preparing the host", StartupStageState.ACTIVE
                        )
                    machine = await self._ensure_host_prepared(machine, service, repository)
                    lease = SlotLease(allocation=machine, binding=binding)
                    if self._stage_pending(session_id, "guest"):
                        # Retries keep the reason the last attempt gave.
                        self._set_stage(
                            session_id,
                            "guest",
                            "Waiting for the guest",
                            StartupStageState.ACTIVE,
                            "Connecting to the guest",
                        )
                    await runtime.prepare(lease, bootstrap)
                    self._set_stage(
                        session_id,
                        "guest",
                        "Waiting for the guest",
                        StartupStageState.DONE,
                        "The guest answered",
                    )
                    if not isinstance(runtime, StartupReportingVmRuntime):
                        self._runtime_step(session_id, "guest_start", "Starting the runtime")
                    binding = binding.model_copy(update={"runtime_data_started": True})
                    await repository.save_slot(binding)
                    lease = SlotLease(allocation=machine, binding=binding)
                    await runtime.start(lease, bootstrap)
                    binding = binding.model_copy(update={"runtime_started": True})
                    await repository.save_slot(binding)
                    self._set_stage(
                        session_id,
                        "broker",
                        "Waiting for the broker",
                        StartupStageState.ACTIVE,
                    )
            except VmRuntimeUnavailableError:
                raise
            except Exception as error:
                detail = (
                    f"Runtime startup failed at stage {error.stage}; inspect guest and stop session"
                    if isinstance(error, VmRuntimeStageError)
                    else "Runtime startup failed; inspect guest and stop session"
                )
                # A runtime failure fails this session's slot, not the machine.
                await repository.save_slot(
                    binding.model_copy(update={"state": SlotState.FAILED, "error": detail})
                )
                raise

    async def _ensure_host_prepared(
        self,
        machine: ComputeLease,
        service: ComputeLeaseService,
        repository: ComputeLeaseRepository,
    ) -> ComputeLease:
        if machine.execution_plan is None:
            return machine
        plan, preparation = await self._preparation_for_lease(machine)
        if not machine.host_preparation_started:
            machine = machine.model_copy(update={"host_preparation_started": True})
            await repository.save(machine)
        machine_bootstrap = await service.machine_bootstrap_for(machine)
        if machine.host_preparation_result is None:
            result = await preparation.ensure(machine, machine_bootstrap, plan.host_recipe)
        else:
            result = await preparation.observe(machine, machine_bootstrap, plan.host_recipe)
        if result.recipe_digest != plan.host_recipe_digest:
            raise RuntimeError("Host preparation result does not match the pinned recipe")
        result.verify_requirements(plan.host_requirements)
        machine = machine.model_copy(
            update={"host_preparation_result": result.model_dump(mode="json")}
        )
        await repository.save(machine)
        return machine

    async def _recover_runtime(
        self, allocation_id: UUID, binding_id: UUID, bootstrap: MachineBootstrap
    ) -> None:
        try:
            await self._start_runtime(allocation_id, binding_id, bootstrap)
        except (VmRuntimeUnavailableError, ComputeLeaseBusyError):
            # Booting/busy claims are retried by the next reconciliation.
            return
        except Exception:
            # _start_runtime persists failure while holding the claim lock.
            # Do not log exception data, which may include adapter credentials.
            logger.error("VM runtime recovery failed for allocation %s", allocation_id)
        finally:
            self._recoveries.pop((allocation_id, binding_id), None)

    async def session_proxy_target(self, session: Session) -> SessionProxyTarget | None:
        service, repository, _ = self._configured()
        lease = await repository.active_for_session(self._pool_id, session.id)
        if lease is None or lease.binding.state not in {SlotState.STARTING, SlotState.BUSY}:
            return None
        if lease.allocation.state not in {LeaseState.READY, LeaseState.IDLE}:
            return None
        if not lease.machine or not lease.machine.addresses:
            return None
        runtime = await self._runtime_for_lease(lease)
        return await runtime.target(lease, await service.bootstrap_for(lease))

    async def broker_target(self, session: Session) -> SessionProxyTarget:
        """The guest broker, as the session proxy reaches it.

        The chat endpoint is Forge's own public ``/s/{id}/session`` route, which
        Forge cannot dial from inside its pod without leaving through the ingress.
        """
        try:
            target = await self.session_proxy_target(session)
        except VmRuntimeUnavailableError as exc:
            raise ValueError(f"Session {session.id} VM broker is unreachable: {exc}") from exc
        if target is None:
            raise ValueError(f"Session {session.id} has no ready VM broker")
        return target

    async def status(self, session: Session) -> SessionStatus:
        service, repository, _ = self._configured()
        view = await repository.active_for_session(self._pool_id, session.id)
        if view is None:
            # Session creation persists STARTING before its background task
            # reserves the durable compute lease.  A concurrent liveness sweep
            # must not interpret that normal admission window as a dead VM and
            # terminally reap the session.
            if session.status in {SessionStatus.STARTING, SessionStatus.PROVISIONING}:
                return SessionStatus.PROVISIONING
            return SessionStatus.STOPPED
        try:
            machine = await service.reconcile(view.id)
        except ComputeLeaseBusyError:
            # A runtime start/stop owns the claim; do not overwrite its session state.
            return session.status
        binding = await repository.get_slot(view.binding.id)
        if binding is None or binding.state in {SlotState.RETIRED, SlotState.RELEASED}:
            return SessionStatus.STOPPED
        lease = SlotLease(allocation=machine, binding=binding)
        if machine.state == LeaseState.FAILED or binding.state == SlotState.FAILED:
            return SessionStatus.FAILED
        if machine.state == LeaseState.RELEASED:
            return SessionStatus.STOPPED
        if machine.state not in {LeaseState.READY, LeaseState.IDLE}:
            return SessionStatus.PROVISIONING
        if binding.state not in {SlotState.STARTING, SlotState.BUSY}:
            return SessionStatus.PROVISIONING
        if not machine.machine or not machine.machine.addresses:
            return SessionStatus.PROVISIONING
        if lease.execution_plan is not None and binding.session_bootstrap_ref is None:
            return SessionStatus.PROVISIONING
        bootstrap = await service.bootstrap_for(lease)
        runtime = await self._runtime_for_lease(lease)
        if not binding.runtime_started:
            # A controller can die after allocation or any guest startup step.
            # Replay the persisted bootstrap under the same claim lock; start is
            # idempotent and never restores over an initialized allocation.
            key = (machine.id, binding.id)
            if key not in self._recoveries:
                self._recoveries[key] = asyncio.create_task(
                    self._recover_runtime(machine.id, binding.id, bootstrap)
                )
            return SessionStatus.PROVISIONING
        if not await runtime.ready(lease, bootstrap):
            return SessionStatus.PROVISIONING
        self._set_stage(session.id, "broker", "Waiting for the broker", StartupStageState.DONE)
        if binding.state is not SlotState.BUSY:
            try:
                await service.mark_busy(machine.id, binding.id)
            except ComputeLeaseBusyError:
                # Another operation holds the allocation. The runtime is ready,
                # so a read must not fail; the next status check records BUSY.
                pass
        return SessionStatus.RUNNING

    async def status_detail(self, session: Session) -> str | None:
        """Expose a provider-safe wait reason without treating it as failure."""
        _, repository, _ = self._configured()
        lease = await repository.active_for_session(self._pool_id, session.id)
        if lease is None or lease.binding.state != SlotState.STARTING:
            return None
        return self._wait_reasons.get(session.id) or lease.allocation.error

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

    @staticmethod
    def _slot_ended(
        machine: ComputeLease | None, binding: ComputeSlot | None, session: Session
    ) -> bool:
        """Whether this session's stop has nothing left to do."""
        return (
            machine is None
            or machine.state == LeaseState.RELEASED
            or binding is None
            or binding.session_id != session.id
            or binding.state in {SlotState.RETIRED, SlotState.RELEASED}
        )

    async def stop(self, session: Session) -> bool:
        service, repository, _ = self._configured()
        self._wait_reasons.pop(session.id, None)
        self._progress.pop(session.id, None)
        for runtime in self._runtimes():
            if isinstance(runtime, ForgeSessionVmRuntime):
                runtime.discard_forge_session(session.id)
        async with self._locks.setdefault(session.id, asyncio.Lock()):
            view = await repository.active_for_session(self._pool_id, session.id)
            if view is None:
                return True
            recovery = self._recoveries.get((view.id, view.binding.id))
            if recovery is not None:
                recovery.cancel()
                await asyncio.gather(recovery, return_exceptions=True)
            async with asyncio.timeout(self._cleanup_timeout):
                while True:
                    machine = await repository.get(view.id)
                    if machine is None:
                        raise RuntimeError("VM claim disappeared during stop")
                    binding = await repository.get_slot(view.binding.id)
                    if self._slot_ended(machine, binding, session):
                        return True
                    try:
                        if (
                            machine.state != LeaseState.DRAINING
                            and binding.state != SlotState.DRAINING
                        ):
                            # Archive failures deliberately prevent destructive VM cleanup.
                            async with repository.operation(machine.id):
                                machine = await repository.get(machine.id)
                                if machine is None:
                                    raise RuntimeError("VM claim disappeared during stop")
                                binding = await repository.get_slot(binding.id)
                                if self._slot_ended(machine, binding, session):
                                    return True
                                if (
                                    machine.state != LeaseState.DRAINING
                                    and binding.state != SlotState.DRAINING
                                ):
                                    binding = binding.model_copy(update={"stop_requested": True})
                                    await repository.save_slot(binding)
                                    machine, binding = await self._finish_stop(machine, binding)
                                    if binding.state in {SlotState.RETIRED, SlotState.RELEASED}:
                                        return True
                        if machine.state != LeaseState.DRAINING:
                            machine = await service.release(machine.id)
                        machine = await service.reconcile(machine.id)
                    except ComputeLeaseBusyError:
                        # Maintenance contention retains the claim. Retry within the
                        # existing cleanup deadline; capacity remains reserved.
                        pass
                    except MachineProviderError:
                        # Only a provider failure after archive and the durable DRAINING
                        # transition is retryable here. Bootstrap, configuration and
                        # runtime failures must remain visible immediately.
                        current = await repository.get(view.id)
                        if (
                            current is None
                            or current.state != LeaseState.DRAINING
                            or not service.provider_compatible(current)
                        ):
                            raise
                    await asyncio.sleep(self._poll)

    async def _finish_stop(
        self, machine: ComputeLease, binding: ComputeSlot
    ) -> tuple[ComputeLease, ComputeSlot]:
        if self._pool_service:
            return await self._pool_service.finish_stop(machine, binding)
        # Without a pool service there is no reuse policy: the machine always drains.
        service, _, _ = self._configured()
        view = SlotLease(allocation=machine, binding=binding)
        if binding.runtime_data_started:
            runtime = await self._runtime_for_lease(view)
            await runtime.stop(view, await service.bootstrap_for(view))
        return await service.retire_slot(machine, binding, drain=True)

    async def close(self) -> None:
        pending = list(self._recoveries.values())
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if not self._owns_runtime:
            return
        seen: set[int] = set()
        for runtime in self._runtimes():
            if id(runtime) in seen:
                continue
            seen.add(id(runtime))
            await runtime.close()
        for preparation in self._execution_preparations.values():
            if id(preparation) in seen:
                continue
            seen.add(id(preparation))
            await preparation.close()
