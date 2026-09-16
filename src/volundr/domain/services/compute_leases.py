"""Provider-neutral durable allocation lifecycle.

This layer tracks machines, not runtime readiness or guest credentials. Only
confirmed deletion frees capacity. Standby guests bind once; used guests are
replaced after preservation, so reset never depends on a filesystem scrub.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from niuu.domain.models import SecretType
from niuu.ports.credentials import CredentialStorePort
from volundr.domain.compute import (
    ComputeCapacityError,
    ComputeLease,
    ComputeLeaseBusyError,
    ComputeLeaseRepository,
    LeaseState,
    MachineBootstrap,
    MachineProvider,
    MachineProviderError,
    MachineRequest,
    MachineState,
)


class ComputeLeaseService:
    def __init__(
        self,
        repository: ComputeLeaseRepository,
        provider: MachineProvider,
        *,
        pool_id: str,
        max_machines: int,
        bootstrap: MachineBootstrap,
        bootstrap_store: CredentialStorePort | None = None,
        provisioning_timeout_seconds: float = 600,
        retry_interval_seconds: float = 10,
        retry_max_seconds: float = 300,
    ):
        if not pool_id or max_machines < 1:
            raise ValueError("Compute pool_id and positive max_machines are required")
        self._repository = repository
        self._provider = provider
        self._pool_id = pool_id
        self._limit = max_machines
        self._bootstrap = bootstrap
        self._fingerprint = hashlib.sha256(bootstrap.model_dump_json().encode()).hexdigest()
        self._bootstrap_store = bootstrap_store
        self._provisioning_timeout = provisioning_timeout_seconds
        self._retry_interval = retry_interval_seconds
        self._retry_max = retry_max_seconds

    async def acquire(
        self,
        *,
        session_id: UUID | None,
        tenant_id: str,
        owner_id: str,
        profile: str,
        bootstrap: MachineBootstrap | None = None,
        timeout_seconds: float | None = None,
    ) -> ComputeLease:
        if session_id is not None and (not tenant_id or not owner_id):
            raise ValueError("Compute claims require tenant_id and owner_id")
        if bootstrap is not None and self._bootstrap_store is None:
            raise ValueError("Per-session bootstrap requires a durable credential store")
        allocation_id = uuid4()
        fingerprint = self._fingerprint
        if bootstrap is not None:
            fingerprint = hashlib.sha256(bootstrap.model_dump_json().encode()).hexdigest()
        lease = await self._repository.reserve(
            ComputeLease(
                id=allocation_id,
                pool_id=self._pool_id,
                session_id=session_id,
                tenant_id=tenant_id,
                owner_id=owner_id,
                profile=profile,
                request_fingerprint=fingerprint,
                provision_deadline=datetime.now(UTC)
                + timedelta(seconds=timeout_seconds or self._provisioning_timeout),
                bootstrap_ref=str(allocation_id) if bootstrap is not None else None,
                bootstrap_owner=str(allocation_id) if bootstrap is not None else None,
            ),
            self._limit,
        )
        if bootstrap is not None:
            async with self._repository.operation(lease.id):
                lease = await self._get(lease.id)
                if lease.state != LeaseState.PROVISIONING:
                    await self.bootstrap_for(lease)
                    return lease
                stored = await self._bootstrap_store.get_value(
                    "compute", lease.bootstrap_owner or self._pool_id, lease.bootstrap_ref
                )
                if stored is None:
                    await self._bootstrap_store.store(
                        "compute",
                        lease.bootstrap_owner or self._pool_id,
                        lease.bootstrap_ref,
                        SecretType.GENERIC,
                        {"bootstrap": bootstrap.model_dump_json()},
                    )
        if lease.retry_after is not None:
            async with self._repository.operation(lease.id):
                lease = await self._get(lease.id)
                await self._repository.save(lease.model_copy(update={"retry_after": None}))
        return await self.reconcile(lease.id)

    async def bootstrap_for(self, lease: ComputeLease) -> MachineBootstrap:
        if lease.session_bootstrap_ref is not None:
            if self._bootstrap_store is None:
                raise MachineProviderError("Compute bootstrap credential store is unavailable")
            value = await self._bootstrap_store.get_value(
                "compute", lease.bootstrap_owner or self._pool_id, lease.session_bootstrap_ref
            )
            if value is None:
                raise MachineProviderError(
                    "Session bootstrap is missing; restore it before retrying"
                )
            return MachineBootstrap.model_validate_json(value["bootstrap"])
        return await self.machine_bootstrap_for(lease)

    async def machine_bootstrap_for(self, lease: ComputeLease) -> MachineBootstrap:
        if lease.bootstrap_ref is None:
            bootstrap = self._bootstrap
        else:
            if self._bootstrap_store is None:
                raise MachineProviderError("Compute bootstrap credential store is unavailable")
            value = await self._bootstrap_store.get_value(
                "compute", lease.bootstrap_owner or self._pool_id, lease.bootstrap_ref
            )
            if value is None:
                raise MachineProviderError(
                    "Compute bootstrap credential is missing; restore it before retrying"
                )
            bootstrap = MachineBootstrap.model_validate_json(value["bootstrap"])
        if (
            hashlib.sha256(bootstrap.model_dump_json().encode()).hexdigest()
            != lease.request_fingerprint
        ):
            raise MachineProviderError("Bootstrap configuration changed during provisioning")
        return bootstrap

    async def _get(self, lease_id: UUID) -> ComputeLease:
        lease = await self._repository.get(lease_id)
        if lease is None or lease.pool_id != self._pool_id:
            raise LookupError("Compute lease not found in this pool")
        return lease

    async def reconcile(self, lease_id: UUID) -> ComputeLease:
        async with self._repository.operation(lease_id):
            lease = await self._get(lease_id)
            if lease.state in {LeaseState.RELEASED, LeaseState.FAILED, LeaseState.QUARANTINED}:
                return lease
            if lease.retry_after is not None and datetime.now(UTC) < lease.retry_after:
                return lease
            if lease.state == LeaseState.DRAINING:
                return await self._delete(lease)
            deadline = lease.provision_deadline or (
                lease.created_at + timedelta(seconds=self._provisioning_timeout)
            )
            if (
                lease.state in {LeaseState.PROVISIONING, LeaseState.READY}
                and datetime.now(UTC) >= deadline
            ):
                lease = lease.model_copy(
                    update={
                        "state": LeaseState.FAILED,
                        "error": "Provisioning expired; preserve data and release the allocation",
                    }
                )
                await self._repository.save(lease)
                return lease
            try:
                machine = await self._provider.get(lease.id)
                if lease.state == LeaseState.PROVISIONING:
                    bootstrap = await self.machine_bootstrap_for(lease)
                    # create is idempotent and repairs partially completed allocation operations.
                    machine = await self._provider.create(
                        MachineRequest(
                            allocation_id=lease.id, profile=lease.profile, bootstrap=bootstrap
                        )
                    )
                if machine is None:
                    updated = lease.model_copy(
                        update={
                            "state": LeaseState.FAILED,
                            "machine": None,
                            "error": "Machine missing; release the claim to confirm cleanup",
                        }
                    )
                elif machine.state in {
                    MachineState.FAILED,
                    MachineState.STOPPED,
                    MachineState.DELETING,
                }:
                    updated = lease.model_copy(
                        update={
                            "state": LeaseState.FAILED,
                            "machine": machine,
                            "error": f"Machine entered {machine.state.value}; cleanup is required",
                        }
                    )
                else:
                    next_state = lease.state
                    if (
                        machine.state == MachineState.RUNNING
                        and lease.state == LeaseState.PROVISIONING
                    ):
                        next_state = LeaseState.READY
                    updated = lease.model_copy(
                        update={
                            "state": next_state,
                            "machine": machine,
                            "retry_after": None,
                            "failures": 0,
                            "error": None,
                        }
                    )
                await self._repository.save(updated)
                return updated
            except Exception:
                # Keep uncertain creates resumable and charged to capacity, without storing secrets.
                await self._repository.save(
                    lease.model_copy(
                        update={
                            **self._retry(lease),
                            "error": "Reconciliation failed; retry this allocation or release it",
                        }
                    )
                )
                raise

    async def mark_busy(self, lease_id: UUID) -> ComputeLease:
        """Called only after the runtime controller has verified session readiness."""
        async with self._repository.operation(lease_id):
            lease = await self._get(lease_id)
            if lease.state not in {LeaseState.READY, LeaseState.BUSY}:
                raise ValueError("Only a ready compute claim can become busy")
            lease = lease.model_copy(update={"state": LeaseState.BUSY})
            await self._repository.save(lease)
            return lease

    async def release(self, lease_id: UUID) -> ComputeLease:
        """Dispose an allocation after the caller preserves durable session data."""
        async with self._repository.operation(lease_id):
            lease = await self._get(lease_id)
            if lease.state == LeaseState.RELEASED:
                return lease
            lease = lease.model_copy(
                update={"state": LeaseState.DRAINING, "error": None, "retry_after": None}
            )
            await self._repository.save(lease)
            return await self._delete(lease)

    async def _delete(self, lease: ComputeLease) -> ComputeLease:
        try:
            if await self._provider.delete(lease.id):
                if lease.bootstrap_ref is not None or lease.session_bootstrap_ref is not None:
                    if self._bootstrap_store is None:
                        raise MachineProviderError(
                            "Compute bootstrap credential store is unavailable"
                        )
                    for reference in (lease.bootstrap_ref, lease.session_bootstrap_ref):
                        if reference is not None:
                            await self._bootstrap_store.delete(
                                "compute", lease.bootstrap_owner or self._pool_id, reference
                            )
                if self._bootstrap_store is not None:
                    # A controller can die between storing session bootstrap and
                    # committing its binding. Reclaim those credentials on disposal too.
                    for credential in await self._bootstrap_store.list(
                        "compute", lease.bootstrap_owner or self._pool_id
                    ):
                        if credential.name.startswith(f"{lease.id}-session-"):
                            await self._bootstrap_store.delete(
                                "compute", lease.bootstrap_owner or self._pool_id, credential.name
                            )
                lease = lease.model_copy(
                    update={
                        "state": LeaseState.RELEASED,
                        "machine": None,
                        "error": None,
                    }
                )
                await self._repository.save(lease)
            return lease
        except Exception:
            await self._repository.save(
                lease.model_copy(
                    update={
                        **self._retry(lease),
                        "error": "Deletion failed; capacity reserved until cleanup succeeds",
                    }
                )
            )
            raise

    async def bind(
        self,
        lease: ComputeLease,
        session_id: UUID,
        owner_id: str,
        tenant_id: str,
        bootstrap: MachineBootstrap,
        timeout_seconds: float,
    ) -> ComputeLease:
        """Called under the allocation operation lock; secrets precede binding commit."""
        if not owner_id or not tenant_id or self._bootstrap_store is None:
            raise ValueError("Binding requires session ownership and a durable credential store")
        reference = f"{lease.id}-session-{session_id}"
        await self._bootstrap_store.store(
            "compute",
            lease.bootstrap_owner or self._pool_id,
            reference,
            SecretType.GENERIC,
            {"bootstrap": bootstrap.model_dump_json()},
        )
        updated = lease.model_copy(
            update={
                "session_id": session_id,
                "owner_id": owner_id,
                "tenant_id": tenant_id,
                "session_bootstrap_ref": reference,
                "state": LeaseState.READY,
                "idle_since": None,
                "provision_deadline": datetime.now(UTC) + timedelta(seconds=timeout_seconds),
            }
        )
        # An uncertain DB commit must retain its bootstrap for restart recovery.
        try:
            return await self._repository.bind(updated)
        except (ComputeCapacityError, ComputeLeaseBusyError):
            await self._bootstrap_store.delete(
                "compute", lease.bootstrap_owner or self._pool_id, reference
            )
            raise

    def _retry(self, lease: ComputeLease) -> dict:
        delay = min(self._retry_max, self._retry_interval * 2 ** min(lease.failures, 20))
        return {
            "failures": lease.failures + 1,
            "retry_after": datetime.now(UTC) + timedelta(seconds=delay),
        }
