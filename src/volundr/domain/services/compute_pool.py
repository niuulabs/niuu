"""Provider-independent standby maintenance, recovery and pool administration."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
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
from volundr.domain.services.compute_leases import ComputeLeaseService
from volundr.domain.vm_runtime import VmRuntime, VmRuntimeUnavailableError

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

    async def policy(self) -> ComputePoolPolicy:
        return await self.repository.policy(self.pool_id, self.defaults)

    async def configure(self, policy: ComputePoolPolicy) -> ComputePoolPolicy:
        if policy.profile not in {profile.name for profile in await self.provider.profiles()}:
            raise ValueError("Select a machine profile configured by the provider adapter")
        await self.repository.set_policy(self.pool_id, policy)
        return policy

    async def snapshot(self) -> dict:
        leases = await self.repository.list(self.pool_id, include_released=False)
        return {
            "pool_id": self.pool_id,
            "profiles": [profile.model_dump() for profile in await self.provider.profiles()],
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
        leases = await self.repository.list(self.pool_id, include_released=False)
        known = {lease.id for lease in leases if lease.state != LeaseState.RELEASED}
        # Inventory is authoritative for owned but unrecorded infrastructure. Never
        # destroy unknown data automatically: expose quarantine and charge capacity.
        for machine in await self.provider.list():
            if machine.allocation_id not in known:
                await self.repository.quarantine(
                    ComputeLease(
                        id=machine.allocation_id,
                        pool_id=self.pool_id,
                        session_id=None,
                        owner_id="",
                        tenant_id="",
                        profile=policy.profile,
                        request_fingerprint="",
                        machine=machine,
                        state=LeaseState.QUARANTINED,
                        error="Unrecorded owned machine; inspect data before disposal",
                    )
                )
        results = await asyncio.gather(
            *(self._maintain_lease(lease, policy) for lease in leases),
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
            len(spares) - policy.warm_min,
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
            for lease in leases
        )
        if not failures and not policy.paused and not policy.drain and warm < policy.warm_min:
            for _ in range(policy.warm_min - warm):
                try:
                    await self.leases.acquire(
                        session_id=None,
                        tenant_id="",
                        owner_id="",
                        profile=policy.profile,
                        bootstrap=self.runtime.machine_bootstrap(self.bootstrap),
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

    async def _maintain_lease(self, lease: ComputeLease, policy: ComputePoolPolicy) -> None:
        if lease.state == LeaseState.DRAINING:
            await self.leases.reconcile(lease.id)
            return
        if lease.stop_requested and lease.state != LeaseState.RELEASED:
            async with self.repository.operation(lease.id):
                current = await self.repository.get(lease.id)
                if current is None or current.state in {LeaseState.RELEASED, LeaseState.DRAINING}:
                    return
                if current.runtime_data_started:
                    await self.runtime.stop(current, await self.leases.bootstrap_for(current))
                await self.repository.save(
                    current.model_copy(update={"state": LeaseState.DRAINING})
                )
            await self.leases.reconcile(lease.id)
            return
        if lease.session_id is not None or lease.state in {
            LeaseState.RELEASED,
            LeaseState.QUARANTINED,
        }:
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
                await self.runtime.warm(lease, bootstrap)
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
