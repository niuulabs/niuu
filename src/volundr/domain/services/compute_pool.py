"""Provider-independent standby maintenance, recovery and pool administration."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID

from niuu.observability import get_observability
from volundr.domain.compute import (
    ComputeCapacityError,
    ComputeLease,
    ComputeLeaseBusyError,
    ComputeLeaseRepository,
    ComputePoolPolicy,
    LeaseState,
    MachineBootstrap,
    MachineProvider,
)
from volundr.domain.execution_catalog import ResolvedExecutionPlan
from volundr.domain.host_preparation import HostPreparation
from volundr.domain.services.compute_leases import ComputeLeaseService
from volundr.domain.services.execution_catalog import ExecutionPlanResolver
from volundr.domain.vm_runtime import ProfileAwareVmRuntime, VmRuntime, VmRuntimeUnavailableError

logger = logging.getLogger(__name__)


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

    async def policy(self) -> ComputePoolPolicy:
        policy = await self.repository.policy(self.pool_id, self.defaults)
        if policy.reuse_policy == "reuse" and not self.runtime.supports_reuse:
            raise ValueError("Configured runtime does not support in-place VM reuse")
        return policy

    async def validate_policy(self, policy: ComputePoolPolicy) -> ComputePoolPolicy:
        if policy.profile not in {
            profile.name for profile in await self.leases.validated_profiles()
        }:
            raise ValueError("Select a machine profile configured by the provider adapter")
        if policy.reuse_policy == "reuse" and not self.runtime.supports_reuse:
            raise ValueError("Configured runtime does not support in-place VM reuse")
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
        leases = await self.repository.list(self.pool_id, include_released=False)
        return {
            "pool_id": self.pool_id,
            "profiles": [
                profile.model_dump(exclude={"revision"})
                for profile in await self.leases.validated_profiles()
            ],
            "policy": (await self.policy()).model_dump(),
            "counts": dict(
                Counter(lease.state.value for lease in leases if lease.state != LeaseState.RELEASED)
            ),
            "allocations": [
                lease.model_dump(
                    mode="json",
                    exclude={
                        "bootstrap_ref",
                        "bootstrap_owner",
                        "session_bootstrap_ref",
                        "request_fingerprint",
                        "profile_revision",
                        "provider_binding",
                        "provider_fingerprint",
                        "execution_plan",
                    },
                )
                for lease in leases
                if lease.state != LeaseState.RELEASED
            ],
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
        known = {lease.id for lease in leases if lease.state != LeaseState.RELEASED}
        # Inventory is authoritative for owned but unrecorded infrastructure. Never
        # destroy unknown data automatically: expose quarantine and charge capacity.
        for machine in await self.provider.list():
            if machine.allocation_id not in known:
                await self.leases.quarantine(machine, policy.profile)
        results = await asyncio.gather(
            *(self._maintain_lease(lease, policy, default_plan) for lease in leases),
            return_exceptions=True,
        )
        failures = [
            result
            for result in results
            if isinstance(result, Exception)
            and not isinstance(result, ComputeLeaseBusyError | VmRuntimeUnavailableError)
        ]
        leases = await self.repository.list(self.pool_id, include_released=False)
        # Retire surplus spares after policy reductions, without touching bound guests.
        spares = [lease for lease in leases if lease.state == LeaseState.IDLE]
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
        warm = sum(
            lease.session_id is None
            and lease.state in {LeaseState.PROVISIONING, LeaseState.READY, LeaseState.IDLE}
            and lease.profile == policy.profile
            and (
                (default_plan is None and lease.execution_plan is None)
                or (
                    default_plan is not None
                    and lease.execution_plan is not None
                    and lease.execution_plan.host_compatibility_digest
                    == default_plan.host_compatibility_digest
                )
            )
            for lease in leases
        )
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
                    await self.leases.acquire(
                        session_id=None,
                        tenant_id="",
                        owner_id="",
                        profile=policy.profile,
                        bootstrap=(
                            machine_bootstrap
                            if default_plan is not None
                            or isinstance(self.runtime, ProfileAwareVmRuntime)
                            else self.runtime.machine_bootstrap(self.bootstrap)
                        ),
                        execution_plan=default_plan,
                        timeout_seconds=policy.provisioning_timeout_seconds,
                    )
                except ComputeCapacityError:
                    break  # Atomic admission bounds concurrent controllers and pending cleanup.
        counts = Counter(
            lease.state
            for lease in await self.repository.list(self.pool_id, include_released=False)
        )
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
        policy: ComputePoolPolicy,
        default_plan: ResolvedExecutionPlan | None,
    ) -> None:
        if lease.state == LeaseState.DRAINING:
            await self.leases.reconcile(lease.id)
            return
        if lease.stop_requested and lease.state != LeaseState.RELEASED:
            async with self.repository.operation(lease.id):
                current = await self.repository.get(lease.id)
                if (
                    current is None
                    or not current.stop_requested
                    or current.session_id != lease.session_id
                    or current.state in {LeaseState.RELEASED, LeaseState.DRAINING}
                ):
                    return
                current = await self.finish_stop(current)
                if current.state == LeaseState.IDLE:
                    return
            await self.leases.reconcile(lease.id)
            return
        if lease.session_id is not None or lease.state in {
            LeaseState.RELEASED,
            LeaseState.QUARANTINED,
        }:
            return
        if not await self.leases.compatible(lease):
            await self.dispose(lease.id)
            return
        if not self._host_compatible(lease, default_plan):
            await self.dispose(lease.id)
            return
        lease = await self.leases.reconcile(lease.id)
        expired = (
            lease.idle_since is not None
            and (datetime.now(UTC) - lease.idle_since).total_seconds()
            >= policy.idle_timeout_seconds
        )
        if (
            policy.drain
            or expired
            or lease.profile != policy.profile
            or lease.state == LeaseState.FAILED
        ):
            await self.dispose(lease.id)
            return
        if lease.state != LeaseState.READY or not lease.machine or not lease.machine.addresses:
            return
        async with self.repository.operation(lease.id):
            lease = await self.repository.get(lease.id)
            if lease is None or lease.session_id is not None or lease.state != LeaseState.READY:
                return
            bootstrap = await self.leases.bootstrap_for(lease)
            # Use the original persisted deadline, including time spent before restart.
            deadline = lease.provision_deadline or (
                lease.created_at + timedelta(seconds=policy.provisioning_timeout_seconds)
            )
            remaining = (deadline - datetime.now(UTC)).total_seconds()
            async with asyncio.timeout(max(0, remaining)):
                lease = await self._ensure_host_prepared(lease)
                await self._runtime_for_lease(lease).warm(lease, bootstrap)
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

    async def finish_stop(self, lease: ComputeLease) -> ComputeLease:
        """Archive and reset or dispose, under the caller's allocation lock."""
        runtime = self._runtime_for_lease(lease)
        if lease.runtime_data_started:
            await runtime.stop(lease, await self.leases.bootstrap_for(lease))
        policy = await self.policy()
        if (
            policy.reuse_policy == "reuse"
            and not policy.drain
            and lease.runtime_started
            and lease.state in {LeaseState.READY, LeaseState.BUSY}
            and await self.leases.compatible(lease)
        ):
            await runtime.recycle(lease, await self.leases.bootstrap_for(lease))
            return await self.leases.return_idle(lease)
        lease = lease.model_copy(update={"state": LeaseState.DRAINING})
        await self.repository.save(lease)
        return lease

    def _resolved_plan_for_lease(self, lease: ComputeLease) -> ResolvedExecutionPlan:
        if lease.execution_plan is None or self._execution_resolver is None:
            raise ValueError("Resolved compute lease requires its execution catalog")
        if not self.leases.provider_compatible(lease):
            raise RuntimeError("Pinned compute provider binding is unavailable or changed")
        return self._execution_resolver.resolve_pinned(
            lease.execution_plan.execution,
            lease.execution_plan.plan_digest,
        )

    def _runtime_for_lease(self, lease: ComputeLease) -> VmRuntime:
        if lease.execution_plan is None:
            return self.runtime
        plan = self._resolved_plan_for_lease(lease)
        runtime = self._execution_runtimes.get(plan.plan_digest)
        if runtime is None:
            raise RuntimeError("Pinned execution runtime binding is unavailable")
        return runtime

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
            plan = self._resolved_plan_for_lease(lease)
        except Exception:
            return False
        return plan.host_compatibility_digest == default_plan.host_compatibility_digest

    async def _ensure_host_prepared(self, lease: ComputeLease) -> ComputeLease:
        if lease.execution_plan is None:
            return lease
        plan = self._resolved_plan_for_lease(lease)
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
            if lease.session_id is not None:
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
