"""Provider-independent standby maintenance, recovery and pool administration."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID

from niuu.observability import get_observability
from volundr.domain.compute import (
    LIVE_SLOT_STATES,
    ComputeCapacityError,
    ComputeLease,
    ComputeLeaseBusyError,
    ComputeLeaseRepository,
    ComputePoolPolicy,
    ComputeSlot,
    LeaseState,
    MachineBootstrap,
    MachineProvider,
    SlotLease,
    SlotState,
    display_state,
    session_limit,
    usable_slots,
)
from volundr.domain.execution_catalog import ResolvedExecutionPlan
from volundr.domain.host_preparation import HostPreparation
from volundr.domain.services.compute_leases import ComputeLeaseService
from volundr.domain.services.execution_catalog import ExecutionPlanResolver
from volundr.domain.vm_runtime import ProfileAwareVmRuntime, VmRuntime, VmRuntimeUnavailableError

logger = logging.getLogger(__name__)


def _live(bindings: Iterable[ComputeSlot]) -> list[ComputeSlot]:
    return [binding for binding in bindings if binding.state in LIVE_SLOT_STATES]


def _by_allocation(bindings: Iterable[ComputeSlot]) -> dict[UUID, list[ComputeSlot]]:
    grouped: dict[UUID, list[ComputeSlot]] = {}
    for binding in bindings:
        grouped.setdefault(binding.allocation_id, []).append(binding)
    return grouped


class ComputePoolService:
    def __init__(
        self,
        repository: ComputeLeaseRepository,
        leases: ComputeLeaseService,
        provider: MachineProvider,
        runtime: VmRuntime,
        *,
        pool_id: str,
        defaults: ComputePoolPolicy,
        bootstrap: MachineBootstrap,
        interval_seconds: float = 10,
    ):
        self.repository, self.leases, self.provider, self.runtime = (
            repository,
            leases,
            provider,
            runtime,
        )
        self.pool_id, self.defaults, self.bootstrap = pool_id, defaults, bootstrap
        self.interval = interval_seconds
        self.last_error: str | None = None
        self.last_reconciled: datetime | None = None
        self._execution_resolver: ExecutionPlanResolver | None = None
        self._execution_runtimes: dict[str, VmRuntime] = {}
        self._execution_preparations: dict[str, HostPreparation] = {}

    def configure_execution(
        self,
        resolver: ExecutionPlanResolver,
        runtimes: Mapping[str, VmRuntime],
        preparations: Mapping[str, HostPreparation],
    ) -> None:
        required = {plan.plan_digest for plan in resolver.plans()}
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

    def _configured_runtimes(self) -> list[VmRuntime]:
        unique = {
            id(runtime): runtime for runtime in (self.runtime, *self._execution_runtimes.values())
        }
        return list(unique.values())

    def _require_slot_support(self, policy: ComputePoolPolicy) -> None:
        """Refuse a policy the configured runtimes cannot honour; never run it degraded."""
        if policy.slots_per_machine > 1:
            for runtime in self._configured_runtimes():
                if not runtime.supports_slots:
                    raise ValueError(
                        f"Compute pool {self.pool_id} sets slots_per_machine="
                        f"{policy.slots_per_machine}, but runtime {type(runtime).__name__} "
                        "does not support slots; set compute.slots_per_machine and the "
                        "pool's 'Sessions per machine' setting to 1, or configure a runtime "
                        "that supports slots"
                    )
        limit = policy.max_sessions_per_machine
        if policy.reuse_policy != "replace" or limit is None or limit <= policy.slots_per_machine:
            return
        for runtime in self._configured_runtimes():
            if not runtime.supports_reuse:
                raise ValueError(
                    f"Compute pool {self.pool_id} sets max_sessions_per_machine={limit} above "
                    f"slots_per_machine={policy.slots_per_machine}, which reuses cleaned slots, "
                    f"but runtime {type(runtime).__name__} does not support in-place reuse; "
                    "set 'Sessions before replacement' to the number of slots or lower, or "
                    "configure a runtime that supports reuse"
                )

    async def policy(self) -> ComputePoolPolicy:
        # Check the defaults before the repository stores them: a policy no runtime can honour
        # must never become the durable policy.
        self._require_slot_support(self.defaults)
        policy = await self.repository.policy(self.pool_id, self.defaults)
        if policy.reuse_policy == "reuse" and not self.runtime.supports_reuse:
            raise ValueError("Configured runtime does not support in-place VM reuse")
        self._require_slot_support(policy)
        return policy

    async def validate_policy(self, policy: ComputePoolPolicy) -> ComputePoolPolicy:
        if policy.profile not in {
            profile.name for profile in await self.leases.validated_profiles()
        }:
            raise ValueError("Select a machine profile configured by the provider adapter")
        if policy.reuse_policy == "reuse" and not self.runtime.supports_reuse:
            raise ValueError("Configured runtime does not support in-place VM reuse")
        self._require_slot_support(policy)
        if self._execution_resolver is not None:
            plan = self._execution_resolver.resolve()
            await self.leases.validate_execution_plan(plan)
            if policy.profile != plan.provider_profile:
                raise ValueError(
                    "Compute pool policy profile must match the default execution plan"
                )
        return policy

    async def configure(self, policy: ComputePoolPolicy) -> ComputePoolPolicy:
        await self.validate_policy(policy)
        await self.repository.set_policy(self.pool_id, policy)
        return policy

    async def snapshot(self) -> dict:
        machines = await self.repository.list(self.pool_id, include_released=False)
        bindings = _by_allocation(await self.repository.list_slots(self.pool_id, live_only=True))
        shown = [machine for machine in machines if machine.state != LeaseState.RELEASED]
        states = {m.id: display_state(m, bindings.get(m.id, [])) for m in shown}
        allocations = []
        for machine in shown:
            allocation = machine.model_dump(
                mode="json",
                exclude={
                    "bootstrap_ref",
                    "bootstrap_owner",
                    "request_fingerprint",
                    "profile_revision",
                    "provider_binding",
                    "provider_fingerprint",
                    "execution_plan",
                },
            )
            allocation["state"] = states[machine.id].value
            allocation["sessions"] = [
                {
                    "slot": binding.slot,
                    "session_id": str(binding.session_id),
                    "state": binding.state.value,
                    "error": binding.error,
                }
                for binding in bindings.get(machine.id, [])
            ]
            allocations.append(allocation)
        return {
            "pool_id": self.pool_id,
            "profiles": [
                profile.model_dump(exclude={"revision"})
                for profile in await self.leases.validated_profiles()
            ],
            "policy": (await self.policy()).model_dump(),
            "counts": dict(Counter(state.value for state in states.values())),
            "allocations": allocations,
            "last_error": self.last_error,
            "last_reconciled": self.last_reconciled,
        }

    async def maintain(self) -> None:
        started = time.monotonic()
        policy = await self.policy()
        default_plan = (
            self._execution_resolver.resolve() if self._execution_resolver is not None else None
        )
        if default_plan is not None:
            await self.leases.validate_execution_plan(default_plan)
            if policy.profile != default_plan.provider_profile:
                raise ValueError(
                    "Compute pool policy profile must match the default execution plan"
                )
        leases = await self.repository.list(self.pool_id, include_released=False)
        bindings = _by_allocation(await self.repository.list_slots(self.pool_id, live_only=False))
        known = {lease.id for lease in leases if lease.state != LeaseState.RELEASED}
        # Inventory is authoritative for owned but unrecorded infrastructure. Never
        # destroy unknown data automatically: expose quarantine and charge capacity.
        for machine in await self.provider.list():
            if machine.allocation_id not in known:
                await self.leases.quarantine(machine, policy.profile)
        results = await asyncio.gather(
            *(
                self._maintain_lease(lease, bindings.get(lease.id, []), policy, default_plan)
                for lease in leases
            ),
            return_exceptions=True,
        )
        failures = [
            result
            for result in results
            if isinstance(result, Exception)
            and not isinstance(result, ComputeLeaseBusyError | VmRuntimeUnavailableError)
        ]
        leases = await self.repository.list(self.pool_id, include_released=False)
        bindings = _by_allocation(await self.repository.list_slots(self.pool_id, live_only=False))
        # Retire surplus spares after policy reductions, without touching bound guests.
        spares = [
            lease
            for lease in leases
            if lease.state == LeaseState.IDLE and not _live(bindings.get(lease.id, []))
        ]
        surplus = max(
            len(spares) - policy.warm_min if policy.reuse_policy == "replace" else 0,
            sum(lease.state != LeaseState.RELEASED for lease in leases) - policy.max_machines,
        )
        for spare in spares[: max(0, surplus)]:
            try:
                await self.dispose(spare.id)
            except (ComputeLeaseBusyError, ValueError):
                pass  # Concurrent assignment owns the guest now; never dispose its data.
        leases = await self.repository.list(self.pool_id, include_released=False)
        bindings = _by_allocation(await self.repository.list_slots(self.pool_id, live_only=False))
        # Idle expiry only trims capacity above the warm minimum. The configured
        # spares stay warm indefinitely; churning them would just reprovision.
        now = datetime.now(UTC)
        expired = sorted(
            (
                lease
                for lease in leases
                if lease.state == LeaseState.IDLE
                and lease.idle_since is not None
                and (now - lease.idle_since).total_seconds() >= policy.idle_timeout_seconds
                and self._is_warm_spare(lease, bindings.get(lease.id, []), policy, default_plan)
            ),
            key=lambda lease: lease.idle_since or now,
        )
        warm = sum(
            self._is_warm_spare(lease, bindings.get(lease.id, []), policy, default_plan)
            for lease in leases
        )
        for lease in expired[: max(0, warm - policy.warm_min)]:
            try:
                await self.dispose(lease.id)
            except (ComputeLeaseBusyError, ValueError):
                continue  # Concurrent assignment owns the guest now; never dispose its data.
            warm -= 1
        if not failures and not policy.paused and not policy.drain and warm < policy.warm_min:
            for _ in range(policy.warm_min - warm):
                try:
                    machine_bootstrap = self.bootstrap
                    if default_plan is not None:
                        machine_bootstrap = self._execution_preparations[
                            default_plan.plan_digest
                        ].machine_bootstrap(default_plan.host_recipe, self.bootstrap)
                    elif isinstance(self.runtime, ProfileAwareVmRuntime):
                        machine_bootstrap = self.runtime.machine_bootstrap_for(
                            policy.profile, self.bootstrap
                        )
                    await self.leases.acquire_standby(
                        profile=policy.profile,
                        bootstrap=(
                            machine_bootstrap
                            if default_plan is not None
                            or isinstance(self.runtime, ProfileAwareVmRuntime)
                            else self.runtime.machine_bootstrap(self.bootstrap)
                        ),
                        execution_plan=default_plan,
                        timeout_seconds=policy.provisioning_timeout_seconds,
                        slots=policy.slots_per_machine,
                    )
                except ComputeCapacityError:
                    break  # Atomic admission bounds concurrent controllers and pending cleanup.
        leases = await self.repository.list(self.pool_id, include_released=False)
        bindings = _by_allocation(await self.repository.list_slots(self.pool_id, live_only=True))
        counts = Counter(display_state(lease, bindings.get(lease.id, [])) for lease in leases)
        for state in LeaseState:
            get_observability().gauge(
                "volundr.compute.machines",
                counts[state],
                attributes={"pool": self.pool_id, "state": state.value},
            )
        get_observability().duration(
            "volundr.compute.reconcile.duration",
            time.monotonic() - started,
            attributes={"pool": self.pool_id},
        )
        if failures:
            raise failures[0]
        self.last_reconciled = datetime.now(UTC)
        self.last_error = None

    async def _maintain_lease(
        self,
        lease: ComputeLease,
        bindings: list[ComputeSlot],
        policy: ComputePoolPolicy,
        default_plan: ResolvedExecutionPlan | None,
    ) -> None:
        if lease.state == LeaseState.DRAINING:
            await self.leases.reconcile(lease.id)
            return
        live = _live(bindings)
        for binding in live:
            if binding.stop_requested and binding.state != SlotState.DRAINING:
                await self._finish_requested_stop(lease, binding)
        if live or lease.state in {LeaseState.RELEASED, LeaseState.QUARANTINED}:
            return
        if not await self.leases.compatible(lease):
            await self.dispose(lease.id)
            return
        if not self._host_compatible(lease, default_plan):
            await self.dispose(lease.id)
            return
        lease = await self.leases.reconcile(lease.id)
        if (
            policy.drain
            or lease.profile != policy.profile
            or lease.state == LeaseState.FAILED
            or usable_slots(lease, bindings, policy, datetime.now(UTC)) == 0
        ):
            await self.dispose(lease.id)
            return
        if lease.state != LeaseState.READY or not lease.machine or not lease.machine.addresses:
            return
        async with self.repository.operation(lease.id):
            lease = await self.repository.get(lease.id)
            if (
                lease is None
                or lease.state != LeaseState.READY
                or _live(await self.repository.slots(lease.id))
            ):
                return
            bootstrap = await self.leases.machine_bootstrap_for(lease)
            # Use the original persisted deadline, including time spent before restart.
            deadline = lease.provision_deadline or (
                lease.created_at + timedelta(seconds=policy.provisioning_timeout_seconds)
            )
            remaining = (deadline - datetime.now(UTC)).total_seconds()
            async with asyncio.timeout(max(0, remaining)):
                lease = await self._ensure_host_prepared(lease)
                await self._runtime_for_machine(lease).warm(lease, bootstrap)
            get_observability().duration(
                "volundr.compute.warm.duration",
                (datetime.now(UTC) - lease.created_at).total_seconds(),
                attributes={"pool": self.pool_id},
            )
            await self.repository.save(
                lease.model_copy(
                    update={
                        "state": LeaseState.IDLE,
                        "idle_since": datetime.now(UTC),
                        "error": None,
                    }
                )
            )

    async def _finish_requested_stop(self, lease: ComputeLease, binding: ComputeSlot) -> None:
        """Complete a stop whose archive or cleanup an earlier attempt did not finish."""
        async with self.repository.operation(lease.id):
            current = await self.repository.get(lease.id)
            slot = await self.repository.get_slot(binding.id)
            if (
                current is None
                or slot is None
                or not slot.stop_requested
                or slot.state not in LIVE_SLOT_STATES
                or slot.state == SlotState.DRAINING
                or current.state in {LeaseState.RELEASED, LeaseState.DRAINING}
            ):
                return
            current, slot = await self.finish_stop(current, slot)
            if current.state != LeaseState.DRAINING:
                return
        await self.leases.reconcile(lease.id)

    async def finish_stop(
        self, lease: ComputeLease, binding: ComputeSlot
    ) -> tuple[ComputeLease, ComputeSlot]:
        """Archive, then reuse the slot or retire it, under the caller's allocation lock.

        Returns the machine and binding as saved. The last live binding of a machine that
        is not reused drains the machine.
        """
        view = SlotLease(allocation=lease, binding=binding)
        runtime = self._runtime_for_slot(view)
        if binding.runtime_data_started:
            await runtime.stop(view, await self.leases.bootstrap_for(view))
        policy = await self.policy()
        bindings = await self.repository.slots(lease.id)
        limit = session_limit(policy, lease)
        if (
            not policy.drain
            and lease.state in {LeaseState.READY, LeaseState.IDLE}
            and binding.runtime_started
            and binding.state in {SlotState.STARTING, SlotState.BUSY}
            and runtime.supports_reuse
            and (limit is None or len(bindings) < limit)
            and await self.leases.compatible(lease)
        ):
            await runtime.recycle(view, await self.leases.bootstrap_for(view))
            lease = await self.leases.release_slot(lease, binding)
            return lease, binding.model_copy(update={"state": SlotState.RELEASED})
        others = [other for other in _live(bindings) if other.id != binding.id]
        return await self.leases.retire_slot(lease, binding, drain=not others)

    def _resolved_plan(
        self, execution_plan: ResolvedExecutionPlan | None, machine: ComputeLease
    ) -> ResolvedExecutionPlan:
        if execution_plan is None or self._execution_resolver is None:
            raise ValueError("Resolved compute lease requires its execution catalog")
        if not self.leases.provider_compatible(machine):
            raise RuntimeError("Pinned compute provider binding is unavailable or changed")
        return self._execution_resolver.resolve_pinned(
            execution_plan.execution,
            execution_plan.plan_digest,
        )

    def _runtime_for(
        self, execution_plan: ResolvedExecutionPlan | None, machine: ComputeLease
    ) -> VmRuntime:
        if execution_plan is None:
            return self.runtime
        plan = self._resolved_plan(execution_plan, machine)
        runtime = self._execution_runtimes.get(plan.plan_digest)
        if runtime is None:
            raise RuntimeError("Pinned execution runtime binding is unavailable")
        return runtime

    def _runtime_for_machine(self, machine: ComputeLease) -> VmRuntime:
        """The runtime of the plan the machine was provisioned for."""
        return self._runtime_for(machine.execution_plan, machine)

    def _runtime_for_slot(self, view: SlotLease) -> VmRuntime:
        """The runtime of the plan the session was started with."""
        return self._runtime_for(view.execution_plan, view.allocation)

    def _is_warm_spare(
        self,
        lease: ComputeLease,
        bindings: list[ComputeSlot],
        policy: ComputePoolPolicy,
        default_plan: ResolvedExecutionPlan | None,
    ) -> bool:
        if _live(bindings):
            return False
        if lease.state not in {LeaseState.PROVISIONING, LeaseState.READY, LeaseState.IDLE}:
            return False
        if lease.profile != policy.profile:
            return False
        if default_plan is None or lease.execution_plan is None:
            return default_plan is None and lease.execution_plan is None
        return (
            lease.execution_plan.host_compatibility_digest == default_plan.host_compatibility_digest
        )

    def _host_compatible(
        self,
        lease: ComputeLease,
        default_plan: ResolvedExecutionPlan | None,
    ) -> bool:
        if lease.execution_plan is None:
            return default_plan is None
        if default_plan is None:
            return False
        try:
            plan = self._resolved_plan(lease.execution_plan, lease)
        except Exception:
            return False
        return plan.host_compatibility_digest == default_plan.host_compatibility_digest

    async def _ensure_host_prepared(self, lease: ComputeLease) -> ComputeLease:
        if lease.execution_plan is None:
            return lease
        plan = self._resolved_plan(lease.execution_plan, lease)
        preparation = self._execution_preparations.get(plan.plan_digest)
        if preparation is None:
            raise RuntimeError("Pinned host preparation binding is unavailable")
        if not lease.host_preparation_started:
            lease = lease.model_copy(update={"host_preparation_started": True})
            await self.repository.save(lease)
        bootstrap = await self.leases.machine_bootstrap_for(lease)
        if lease.host_preparation_result is None:
            result = await preparation.ensure(lease, bootstrap, plan.host_recipe)
        else:
            result = await preparation.observe(lease, bootstrap, plan.host_recipe)
        if result.recipe_digest != plan.host_recipe_digest:
            raise RuntimeError("Host preparation result does not match the pinned recipe")
        result.verify_requirements(plan.host_requirements)
        lease = lease.model_copy(update={"host_preparation_result": result.model_dump(mode="json")})
        await self.repository.save(lease)
        return lease

    async def dispose(self, allocation_id: UUID) -> None:
        async with self.repository.operation(allocation_id):
            lease = await self.repository.get(allocation_id)
            if lease is None or lease.pool_id != self.pool_id:
                raise LookupError("Allocation not found in this pool")
            if _live(await self.repository.slots(allocation_id)):
                raise ValueError(
                    "Stop the session to preserve its data before disposing its machine"
                )
            await self.repository.save(lease.model_copy(update={"state": LeaseState.DRAINING}))
        await self.leases.reconcile(allocation_id)

    async def run(self) -> None:
        while True:
            try:
                await self.maintain()
            except Exception:
                get_observability().count(
                    "volundr.compute.reconcile.errors", attributes={"pool": self.pool_id}
                )
                # Preserve a visible failure and retry the same operations. Exception
                # strings may contain provider credentials and must not be exposed.
                self.last_error = (
                    "Pool reconciliation failed; inspect allocation state and provider connectivity"
                )
                logger.error("Compute pool reconciliation failed for %s", self.pool_id)
            await asyncio.sleep(self.interval)
