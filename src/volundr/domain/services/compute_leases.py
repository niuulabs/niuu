"""Provider-neutral durable allocation lifecycle.

This layer tracks machines and the session slots bound to them, not runtime
readiness or guest credentials. Only confirmed deletion frees machine capacity. A
runtime may explicitly support returning a scrubbed slot to service after durable
session preservation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from niuu.domain.models import SecretType
from niuu.ports.credentials import CredentialStorePort
from volundr.domain.compute import (
    LIVE_SLOT_STATES,
    ComputeCapacityError,
    ComputeLease,
    ComputeLeaseBusyError,
    ComputeLeaseRepository,
    ComputeSlot,
    LeaseState,
    Machine,
    MachineBootstrap,
    MachineProfile,
    MachineProvider,
    MachineProviderError,
    MachineRequest,
    MachineState,
    SlotLease,
    SlotState,
    next_free_slot,
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
        session_id: UUID,
        tenant_id: str,
        owner_id: str,
        profile: str,
        bootstrap: MachineBootstrap | None = None,
        session_bootstrap: MachineBootstrap | None = None,
        execution_plan: ResolvedExecutionPlan | None = None,
        timeout_seconds: float | None = None,
        slots: int = 1,
        exclusive: bool = False,
    ) -> SlotLease:
        """Reserve a new machine with the session bound to its slot 0.

        ``exclusive``: the session holds root-equivalent grants, so its machine never
        serves another session (``usable_slots``).
        """
        if not tenant_id or not owner_id:
            raise ValueError("Compute claims require tenant_id and owner_id")
        machine, binding = await self._acquire(
            session_id=session_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            profile=profile,
            bootstrap=bootstrap,
            session_bootstrap=session_bootstrap,
            execution_plan=execution_plan,
            timeout_seconds=timeout_seconds,
            slots=slots,
            exclusive=exclusive,
        )
        return SlotLease(allocation=machine, binding=binding)

    async def acquire_standby(
        self,
        *,
        profile: str,
        bootstrap: MachineBootstrap | None = None,
        execution_plan: ResolvedExecutionPlan | None = None,
        timeout_seconds: float | None = None,
        slots: int = 1,
    ) -> ComputeLease:
        """Reserve a new machine with no session, to be warmed for later sessions."""
        machine, _ = await self._acquire(
            session_id=None,
            tenant_id="",
            owner_id="",
            profile=profile,
            bootstrap=bootstrap,
            session_bootstrap=None,
            execution_plan=execution_plan,
            timeout_seconds=timeout_seconds,
            slots=slots,
            exclusive=False,
        )
        return machine

    async def _acquire(
        self,
        *,
        session_id: UUID | None,
        tenant_id: str,
        owner_id: str,
        profile: str,
        bootstrap: MachineBootstrap | None,
        session_bootstrap: MachineBootstrap | None,
        execution_plan: ResolvedExecutionPlan | None,
        timeout_seconds: float | None,
        slots: int,
        exclusive: bool,
    ) -> tuple[ComputeLease, ComputeSlot | None]:
        if bootstrap is not None and self._bootstrap_store is None:
            raise ValueError("Per-session bootstrap requires a durable credential store")
        if session_bootstrap is not None and self._bootstrap_store is None:
            raise ValueError("Session bootstrap requires a session and durable credential store")
        if execution_plan is not None:
            self._validate_execution_plan(execution_plan, profile)
        allocation_id = uuid4()
        machine_bootstrap = bootstrap or self._bootstrap
        profile_revision = await self.profile_revision(profile)
        provider_binding = self._provider_binding if execution_plan is not None else ""
        provider_fingerprint = self._provider_fingerprint if execution_plan is not None else ""
        fingerprint = self._request_fingerprint(
            profile=profile,
            profile_revision=profile_revision,
            bootstrap=machine_bootstrap,
            provider_binding=provider_binding,
            provider_fingerprint=provider_fingerprint,
        )
        deadline = datetime.now(UTC) + timedelta(
            seconds=timeout_seconds or self._provisioning_timeout
        )
        binding = None
        if session_id is not None:
            binding = ComputeSlot(
                id=uuid4(),
                allocation_id=allocation_id,
                slot=0,
                pool_id=self._pool_id,
                session_id=session_id,
                tenant_id=tenant_id,
                owner_id=owner_id,
                execution_plan=execution_plan,
                session_bootstrap_ref=(
                    self._session_reference(allocation_id, session_id)
                    if session_bootstrap is not None
                    else None
                ),
                start_deadline=deadline,
                exclusive=exclusive,
            )
        lease, binding = await self._repository.reserve(
            ComputeLease(
                id=allocation_id,
                pool_id=self._pool_id,
                profile=profile,
                profile_revision=profile_revision,
                request_fingerprint=fingerprint,
                provider_binding=provider_binding,
                provider_fingerprint=provider_fingerprint,
                execution_plan=execution_plan,
                slots=slots,
                provision_deadline=deadline,
                bootstrap_ref=str(allocation_id) if bootstrap is not None else None,
                bootstrap_owner=str(allocation_id) if bootstrap is not None else None,
            ),
            self._limit,
            binding,
        )
        if binding is not None and binding.execution_plan != execution_plan:
            raise ValueError("Session already owns a different resolved execution plan")
        if bootstrap is not None or session_bootstrap is not None:
            async with self._repository.operation(lease.id):
                lease = await self._get(lease.id)
                if lease.state != LeaseState.PROVISIONING:
                    # Only a session's existing claim is past provisioning: a new machine
                    # always starts there. Verify its bootstrap instead of rewriting it.
                    await self.bootstrap_for(SlotLease(allocation=lease, binding=binding))
                    return lease, binding
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
                        binding.session_bootstrap_ref,
                    )
                    if stored_session is None:
                        await self._bootstrap_store.store(
                            "compute",
                            lease.bootstrap_owner or self._pool_id,
                            binding.session_bootstrap_ref,
                            SecretType.GENERIC,
                            {"bootstrap": session_bootstrap.model_dump_json()},
                        )
        if lease.retry_after is not None:
            async with self._repository.operation(lease.id):
                lease = await self._get(lease.id)
                await self._repository.save(lease.model_copy(update={"retry_after": None}))
        lease = await self.reconcile(lease.id)
        if binding is not None:
            binding = await self._get_slot(binding.id, lease.id)
        return lease, binding

    @staticmethod
    def _session_reference(allocation_id: UUID, session_id: UUID) -> str:
        return f"{allocation_id}-session-{session_id}"

    async def bootstrap_for(self, lease: SlotLease) -> MachineBootstrap:
        """The session's bootstrap, else its machine's when none was attached yet."""
        reference = lease.binding.session_bootstrap_ref
        if reference is not None:
            if self._bootstrap_store is None:
                raise MachineProviderError("Compute bootstrap credential store is unavailable")
            value = await self._bootstrap_store.get_value(
                "compute", lease.allocation.bootstrap_owner or self._pool_id, reference
            )
            if value is None:
                raise MachineProviderError(
                    "Session bootstrap is missing; restore it before retrying"
                )
            return MachineBootstrap.model_validate_json(value["bootstrap"])
        return await self.machine_bootstrap_for(lease.allocation)

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

    async def _get_slot(self, slot_id: UUID, allocation_id: UUID) -> ComputeSlot:
        binding = await self._repository.get_slot(slot_id)
        if binding is None or binding.allocation_id != allocation_id:
            raise LookupError("Compute slot not found on this allocation")
        return binding

    async def _live_bindings(self, allocation_id: UUID) -> list[ComputeSlot]:
        return [
            binding
            for binding in await self._repository.slots(allocation_id)
            if binding.state in LIVE_SLOT_STATES
        ]

    async def reconcile(self, lease_id: UUID) -> ComputeLease:
        """Advance a machine toward its provider state and expire overdue starts."""
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
            now = datetime.now(UTC)
            deadline = lease.provision_deadline or (
                lease.created_at + timedelta(seconds=self._provisioning_timeout)
            )
            live = await self._live_bindings(lease.id)
            expired_machine = (
                lease.state == LeaseState.PROVISIONING
                or (lease.state == LeaseState.READY and not live)
            ) and now >= deadline
            if expired_machine:
                lease = lease.model_copy(
                    update={
                        "state": LeaseState.FAILED,
                        "error": "Provisioning expired; preserve data and release the allocation",
                    }
                )
                await self._repository.save(lease)
                return lease
            if lease.state in {LeaseState.READY, LeaseState.IDLE}:
                overdue = [
                    binding
                    for binding in live
                    if binding.state is SlotState.STARTING
                    and binding.start_deadline is not None
                    and now >= binding.start_deadline
                ]
                for binding in overdue:
                    await self._repository.save_slot(
                        binding.model_copy(
                            update={
                                "state": SlotState.FAILED,
                                "error": (
                                    "Provisioning expired; preserve data and release the allocation"
                                ),
                            }
                        )
                    )
                if overdue:
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
                    for binding in live:
                        if binding.session_bootstrap_ref is not None:
                            # A session secret reference is part of the durable
                            # allocation intent. Never create the machine until the
                            # referenced payload also exists, even though it is not
                            # included in MachineRequest.
                            await self.bootstrap_for(SlotLease(allocation=lease, binding=binding))
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

    async def mark_busy(self, allocation_id: UUID, binding_id: UUID) -> SlotLease:
        """Called only after the runtime controller has verified session readiness."""
        async with self._repository.operation(allocation_id):
            lease = await self._get(allocation_id)
            binding = await self._get_slot(binding_id, allocation_id)
            if lease.state not in {LeaseState.READY, LeaseState.IDLE} or binding.state not in {
                SlotState.STARTING,
                SlotState.BUSY,
            }:
                raise ValueError("Only a ready compute claim can become busy")
            binding = binding.model_copy(update={"state": SlotState.BUSY})
            await self._repository.save_slot(binding)
            if lease.state == LeaseState.READY:
                # A machine in service is idle; the slot carries the session's state.
                lease = lease.model_copy(update={"state": LeaseState.IDLE})
                await self._repository.save(lease)
            return SlotLease(allocation=lease, binding=binding)

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
                references = [
                    reference
                    for reference in (
                        lease.bootstrap_ref,
                        *(
                            binding.session_bootstrap_ref
                            for binding in await self._repository.slots(lease.id)
                        ),
                    )
                    if reference is not None
                ]
                if references:
                    if self._bootstrap_store is None:
                        raise MachineProviderError(
                            "Compute bootstrap credential store is unavailable"
                        )
                    for reference in references:
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
                # Saving a released machine retires its live bindings in the same commit.
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
        exclusive: bool = False,
    ) -> SlotLease:
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
        slot = next_free_slot(lease, await self._repository.slots(lease.id))
        reference = self._session_reference(lease.id, session_id)
        await self._bootstrap_store.store(
            "compute",
            lease.bootstrap_owner or self._pool_id,
            reference,
            SecretType.GENERIC,
            {"bootstrap": bootstrap.model_dump_json()},
        )
        binding = ComputeSlot(
            id=uuid4(),
            allocation_id=lease.id,
            slot=slot,
            pool_id=lease.pool_id,
            session_id=session_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            execution_plan=execution_plan,
            session_bootstrap_ref=reference,
            start_deadline=datetime.now(UTC) + timedelta(seconds=timeout_seconds),
            exclusive=exclusive,
        )
        # An uncertain DB commit must retain its bootstrap for restart recovery.
        try:
            return await self._repository.bind(lease, binding)
        except (ComputeCapacityError, ComputeLeaseBusyError):
            await self._bootstrap_store.delete(
                "compute", lease.bootstrap_owner or self._pool_id, reference
            )
            raise

    async def release_slot(self, lease: ComputeLease, binding: ComputeSlot) -> ComputeLease:
        """Free a slot for reuse, under the allocation lock and after the runtime was scrubbed.

        A machine left with no live binding becomes an empty, idle spare.
        """
        released = binding.model_copy(
            update={
                "state": SlotState.RELEASED,
                "runtime_data_started": False,
                "runtime_started": False,
                "stop_requested": False,
                "error": None,
            }
        )
        await self._repository.save_slot(released)
        updated = lease
        if not await self._live_bindings(lease.id):
            updated = lease.model_copy(
                update={
                    "state": LeaseState.IDLE,
                    "idle_since": datetime.now(UTC),
                    "provision_deadline": None,
                    "error": None,
                    "retry_after": None,
                    "failures": 0,
                }
            )
            await self._repository.save(updated)
        # The binding is ended before its secret is removed. A crash must never
        # leave a bound session without the bootstrap needed to retry cleanup.
        if binding.session_bootstrap_ref is not None:
            await self._bootstrap_store.delete(
                "compute", lease.bootstrap_owner or self._pool_id, binding.session_bootstrap_ref
            )
        return updated

    async def retire_slot(
        self, lease: ComputeLease, binding: ComputeSlot, *, drain: bool
    ) -> tuple[ComputeLease, ComputeSlot]:
        """End a session's slot without reusing it; ``drain`` also deletes the machine.

        Called under the allocation lock, after the session's data was archived. The machine
        is marked first, so a crash between the two writes still deletes it on retry.
        """
        if drain:
            lease = lease.model_copy(update={"state": LeaseState.DRAINING})
            await self._repository.save(lease)
        binding = binding.model_copy(
            update={"state": SlotState.DRAINING if drain else SlotState.RETIRED}
        )
        await self._repository.save_slot(binding)
        return lease, binding

    async def attach_session_bootstrap(
        self,
        allocation_id: UUID,
        binding_id: UUID,
        session_id: UUID,
        bootstrap: MachineBootstrap,
    ) -> SlotLease:
        """Persist session-only bootstrap without changing provider create input."""
        if self._bootstrap_store is None:
            raise ValueError("Session bootstrap requires a durable credential store")
        async with self._repository.operation(allocation_id):
            lease = await self._get(allocation_id)
            binding = await self._get_slot(binding_id, allocation_id)
            if binding.session_id != session_id:
                raise ValueError("Session bootstrap does not match the compute claim")
            if binding.session_bootstrap_ref is not None:
                view = SlotLease(allocation=lease, binding=binding)
                await self.bootstrap_for(view)
                return view
            reference = self._session_reference(lease.id, session_id)
            await self._bootstrap_store.store(
                "compute",
                lease.bootstrap_owner or self._pool_id,
                reference,
                SecretType.GENERIC,
                {"bootstrap": bootstrap.model_dump_json()},
            )
            binding = binding.model_copy(update={"session_bootstrap_ref": reference})
            await self._repository.save_slot(binding)
            return SlotLease(allocation=lease, binding=binding)

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
