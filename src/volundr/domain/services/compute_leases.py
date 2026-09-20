"""Provider-neutral durable allocation lifecycle.

This layer tracks machines, not runtime readiness or guest credentials. Only
confirmed deletion frees machine capacity. A runtime may explicitly support
returning a scrubbed guest to standby after durable session preservation.
"""

from __future__ import annotations

import hashlib
import json
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
    Machine,
    MachineBootstrap,
    MachineProfile,
    MachineProvider,
    MachineProviderError,
    MachineRequest,
    MachineState,
)
from volundr.domain.execution_catalog import ResolvedExecutionPlan


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
        provider_binding: str = "",
        provider_fingerprint: str = "",
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
        self._bootstrap_store = bootstrap_store
        self._provider_binding = provider_binding
        self._provider_fingerprint = provider_fingerprint
        self._provisioning_timeout = provisioning_timeout_seconds
        self._retry_interval = retry_interval_seconds
        self._retry_max = retry_max_seconds

    async def validated_profiles(self) -> tuple[MachineProfile, ...]:
        profiles = tuple(await self._provider.profiles())
        if not profiles:
            raise ValueError("Compute provider must configure at least one machine profile")
        names = [profile.name for profile in profiles]
        if len(names) != len(set(names)):
            raise ValueError("Compute provider machine profile names must be unique")
        revisions = [profile.revision for profile in profiles]
        if len(revisions) != len(set(revisions)):
            raise ValueError("Compute provider machine profile revisions must be unique")
        return profiles

    async def profile_revision(self, name: str) -> str:
        for profile in await self.validated_profiles():
            if profile.name == name:
                return profile.revision
        raise ValueError("Machine profile is no longer configured by the provider adapter")

    async def validate_execution_plan(self, plan: ResolvedExecutionPlan) -> str:
        """Validate a plan against this deployment before contributors or allocation."""
        self._validate_execution_plan(plan, plan.provider_profile)
        return await self.profile_revision(plan.provider_profile)

    async def compatible(self, lease: ComputeLease) -> bool:
        if not self.provider_compatible(lease):
            return False
        return any(
            profile.name == lease.profile and profile.revision == lease.profile_revision
            for profile in await self.validated_profiles()
        )

    def provider_compatible(self, lease: ComputeLease) -> bool:
        if lease.provider_binding and lease.provider_binding != self._provider_binding:
            return False
        if lease.provider_fingerprint and lease.provider_fingerprint != self._provider_fingerprint:
            return False
        return True

    async def quarantine(self, machine: Machine, profile: str) -> ComputeLease:
        """Record discovered provider inventory with its deployment identity."""
        lease = ComputeLease(
            id=machine.allocation_id,
            pool_id=self._pool_id,
            session_id=None,
            owner_id="",
            tenant_id="",
            profile=profile,
            profile_revision=await self.profile_revision(profile),
            request_fingerprint="",
            provider_binding=self._provider_binding,
            provider_fingerprint=self._provider_fingerprint,
            machine=machine,
            state=LeaseState.QUARANTINED,
            error="Unrecorded owned machine; inspect data before disposal",
        )
        await self._repository.quarantine(lease)
        return lease

    async def acquire(
        self,
        *,
        session_id: UUID | None,
        tenant_id: str,
        owner_id: str,
        profile: str,
        bootstrap: MachineBootstrap | None = None,
        session_bootstrap: MachineBootstrap | None = None,
        execution_plan: ResolvedExecutionPlan | None = None,
        timeout_seconds: float | None = None,
    ) -> ComputeLease:
        if session_id is not None and (not tenant_id or not owner_id):
            raise ValueError("Compute claims require tenant_id and owner_id")
        if bootstrap is not None and self._bootstrap_store is None:
            raise ValueError("Per-session bootstrap requires a durable credential store")
        if session_bootstrap is not None and (session_id is None or self._bootstrap_store is None):
            raise ValueError("Session bootstrap requires a session and durable credential store")
        if execution_plan is not None:
            self._validate_execution_plan(execution_plan, profile)
        allocation_id = uuid4()
        machine_bootstrap = bootstrap or self._bootstrap
        profile_revision = await self.profile_revision(profile)
        provider_binding = self._provider_binding if execution_plan is not None else ""
        provider_fingerprint = self._provider_fingerprint if execution_plan is not None else ""
        session_bootstrap_ref = (
            f"{allocation_id}-session-{session_id}" if session_bootstrap is not None else None
        )
        fingerprint = self._request_fingerprint(
            profile=profile,
            profile_revision=profile_revision,
            bootstrap=machine_bootstrap,
            provider_binding=provider_binding,
            provider_fingerprint=provider_fingerprint,
        )
        lease = await self._repository.reserve(
            ComputeLease(
                id=allocation_id,
                pool_id=self._pool_id,
                session_id=session_id,
                tenant_id=tenant_id,
                owner_id=owner_id,
                profile=profile,
                profile_revision=profile_revision,
                request_fingerprint=fingerprint,
                provider_binding=provider_binding,
                provider_fingerprint=provider_fingerprint,
                execution_plan=execution_plan,
                provision_deadline=datetime.now(UTC)
                + timedelta(seconds=timeout_seconds or self._provisioning_timeout),
                bootstrap_ref=str(allocation_id) if bootstrap is not None else None,
                bootstrap_owner=str(allocation_id) if bootstrap is not None else None,
                session_bootstrap_ref=session_bootstrap_ref,
            ),
            self._limit,
        )
        if lease.execution_plan != execution_plan:
            raise ValueError("Session already owns a different resolved execution plan")
        if bootstrap is not None or session_bootstrap is not None:
            async with self._repository.operation(lease.id):
                lease = await self._get(lease.id)
                if lease.state != LeaseState.PROVISIONING:
                    await self.bootstrap_for(lease)
                    return lease
                if bootstrap is not None:
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
                if session_bootstrap is not None:
                    stored_session = await self._bootstrap_store.get_value(
                        "compute",
                        lease.bootstrap_owner or self._pool_id,
                        lease.session_bootstrap_ref,
                    )
                    if stored_session is None:
                        await self._bootstrap_store.store(
                            "compute",
                            lease.bootstrap_owner or self._pool_id,
                            lease.session_bootstrap_ref,
                            SecretType.GENERIC,
                            {"bootstrap": session_bootstrap.model_dump_json()},
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
            self._request_fingerprint(
                profile=lease.profile,
                profile_revision=lease.profile_revision,
                bootstrap=bootstrap,
                provider_binding=lease.provider_binding,
                provider_fingerprint=lease.provider_fingerprint,
            )
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
            if not self.provider_compatible(lease):
                raise MachineProviderError(
                    "Pinned compute provider binding is unavailable or changed; "
                    "restore its configured binding before recovery"
                )
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
                    if not await self.compatible(lease):
                        updated = lease.model_copy(
                            update={
                                "state": LeaseState.FAILED,
                                "error": (
                                    "Machine profile changed during provisioning; "
                                    "cleanup is required"
                                ),
                            }
                        )
                        await self._repository.save(updated)
                        return updated
                    bootstrap = await self.machine_bootstrap_for(lease)
                    if lease.session_bootstrap_ref is not None:
                        # A session secret reference is part of the durable
                        # allocation intent. Never create the machine until the
                        # referenced payload also exists, even though it is not
                        # included in MachineRequest.
                        await self.bootstrap_for(lease)
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
                            "error": machine.status_detail
                            or f"Machine entered {machine.state.value}; cleanup is required",
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
                            "error": machine.status_detail,
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
            if not self.provider_compatible(lease):
                raise MachineProviderError(
                    "Pinned compute provider binding is unavailable or changed; "
                    "restore it before deleting this allocation"
                )
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
        execution_plan: ResolvedExecutionPlan | None = None,
    ) -> ComputeLease:
        """Called under the allocation operation lock; secrets precede binding commit."""
        if not owner_id or not tenant_id or self._bootstrap_store is None:
            raise ValueError("Binding requires session ownership and a durable credential store")
        if execution_plan is not None:
            self._validate_execution_plan(execution_plan, lease.profile)
            if lease.execution_plan is None:
                raise ValueError("A legacy standby lease cannot bind a resolved execution plan")
            if (
                lease.execution_plan.host_compatibility_digest
                != execution_plan.host_compatibility_digest
            ):
                raise ValueError("Standby host is incompatible with the resolved execution plan")
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
                "execution_plan": execution_plan or lease.execution_plan,
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

    async def return_idle(self, lease: ComputeLease) -> ComputeLease:
        """Called under the allocation lock, only after the runtime was scrubbed."""
        updated = lease.model_copy(
            update={
                "session_id": None,
                "owner_id": "",
                "tenant_id": "",
                "session_bootstrap_ref": None,
                "runtime_data_started": False,
                "runtime_started": False,
                "stop_requested": False,
                "state": LeaseState.IDLE,
                "idle_since": datetime.now(UTC),
                "provision_deadline": None,
                "error": None,
                "retry_after": None,
                "failures": 0,
            }
        )
        await self._repository.save(updated)
        # The binding is cleared before removing its secret. A crash must never
        # leave a bound session without the bootstrap needed to retry cleanup.
        if lease.session_bootstrap_ref is not None:
            await self._bootstrap_store.delete(
                "compute", lease.bootstrap_owner or self._pool_id, lease.session_bootstrap_ref
            )
        return updated

    async def attach_session_bootstrap(
        self,
        lease_id: UUID,
        session_id: UUID,
        bootstrap: MachineBootstrap,
    ) -> ComputeLease:
        """Persist session-only bootstrap without changing provider create input."""
        if self._bootstrap_store is None:
            raise ValueError("Session bootstrap requires a durable credential store")
        async with self._repository.operation(lease_id):
            lease = await self._get(lease_id)
            if lease.session_id != session_id:
                raise ValueError("Session bootstrap does not match the compute claim")
            if lease.session_bootstrap_ref is not None:
                await self.bootstrap_for(lease)
                return lease
            reference = f"{lease.id}-session-{session_id}"
            await self._bootstrap_store.store(
                "compute",
                lease.bootstrap_owner or self._pool_id,
                reference,
                SecretType.GENERIC,
                {"bootstrap": bootstrap.model_dump_json()},
            )
            updated = lease.model_copy(update={"session_bootstrap_ref": reference})
            await self._repository.save(updated)
            return updated

    def _retry(self, lease: ComputeLease) -> dict:
        delay = min(self._retry_max, self._retry_interval * 2 ** min(lease.failures, 20))
        return {
            "failures": lease.failures + 1,
            "retry_after": datetime.now(UTC) + timedelta(seconds=delay),
        }

    def _validate_execution_plan(self, plan: ResolvedExecutionPlan, profile: str) -> None:
        if plan.pool_id != self._pool_id:
            raise ValueError("Resolved execution plan targets a different compute pool")
        if not self._provider_binding or plan.provider_binding != self._provider_binding:
            raise ValueError("Resolved execution plan targets a different provider binding")
        if not self._provider_fingerprint:
            raise ValueError("Resolved execution requires a configured provider fingerprint")
        if plan.provider_profile != profile:
            raise ValueError("Resolved execution plan and machine profile disagree")

    def _request_fingerprint(
        self,
        *,
        profile: str,
        profile_revision: str,
        bootstrap: MachineBootstrap,
        provider_binding: str | None = None,
        provider_fingerprint: str | None = None,
    ) -> str:
        effective_binding = self._provider_binding if provider_binding is None else provider_binding
        effective_fingerprint = (
            self._provider_fingerprint if provider_fingerprint is None else provider_fingerprint
        )
        if not effective_binding and not effective_fingerprint:
            return hashlib.sha256(bootstrap.model_dump_json().encode()).hexdigest()
        payload = json.dumps(
            {
                "bootstrap": bootstrap.model_dump(mode="json"),
                "profile": profile,
                "profile_revision": profile_revision,
                "provider_binding": effective_binding,
                "provider_fingerprint": effective_fingerprint,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()
