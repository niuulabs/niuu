"""Resident runtime ownership, profile, and durable-state service."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from volundr.domain.models import (
    Principal,
    ResidentCapability,
    ResidentCondition,
    ResidentDeploymentProfile,
    ResidentDesiredState,
    ResidentEndpoint,
    ResidentObservedState,
    ResidentRuntime,
)
from volundr.domain.ports import ResidentDeploymentProfileProvider, ResidentRuntimeRepository


class ResidentRuntimeNotFoundError(Exception):
    """Raised when a resident does not exist or is outside the caller's scope."""


class ResidentProfileNotFoundError(Exception):
    """Raised when a resident deployment profile is unavailable on this target."""


class ResidentRuntimeConflictError(Exception):
    """Raised when a resident conflicts with an existing owned runtime."""


class ResidentRuntimeValidationError(Exception):
    """Raised when profile-constrained resident input is invalid."""


class ResidentRuntimeAccessError(Exception):
    """Raised when a principal cannot mutate resident runtime state."""


class ResidentRuntimeService:
    """Manage resident records without performing backend deployment work."""

    def __init__(
        self,
        repository: ResidentRuntimeRepository,
        profiles: ResidentDeploymentProfileProvider,
    ) -> None:
        self._repository = repository
        self._profiles = profiles

    def list_profiles(self) -> list[ResidentDeploymentProfile]:
        """Return profiles that are actually enabled on this target."""
        return self._profiles.list()

    async def create_record(
        self,
        principal: Principal,
        *,
        name: str,
        profile_id: str,
        persona_name: str = "",
        model: str = "",
    ) -> ResidentRuntime:
        """Create the durable record used by a real deployment adapter."""
        self._require_write_role(principal)
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise ResidentProfileNotFoundError(f"Resident profile not found: {profile_id}")

        existing = await self._repository.get_by_owner_name(principal.user_id, name)
        if existing is not None:
            raise ResidentRuntimeConflictError(f"Resident already exists: {name}")

        resolved_model = model or profile.default_model
        if profile.allowed_models and resolved_model not in profile.allowed_models:
            raise ResidentRuntimeValidationError(
                f"Model {resolved_model!r} is not allowed by resident profile {profile_id}"
            )

        runtime = ResidentRuntime(
            owner_id=principal.user_id,
            tenant_id=principal.tenant_id,
            name=name,
            persona_name=persona_name,
            model=resolved_model,
            backend=profile.backend,
            engine=profile.engine,
            profile_id=profile.id,
            capabilities=profile.capabilities,
        )
        return await self._repository.create(runtime)

    async def list(self, principal: Principal) -> list[ResidentRuntime]:
        """List caller-owned residents, or tenant residents for an admin."""
        owner_id = None if self._is_admin(principal) else principal.user_id
        return await self._repository.list(
            tenant_id=principal.tenant_id,
            owner_id=owner_id,
        )

    async def get(self, principal: Principal, runtime_id: UUID) -> ResidentRuntime:
        """Return one visible resident without revealing cross-scope existence."""
        runtime = await self._repository.get(runtime_id)
        self._require_access(runtime, principal)
        return runtime

    async def set_desired_state(
        self,
        principal: Principal,
        runtime_id: UUID,
        desired_state: ResidentDesiredState,
    ) -> ResidentRuntime:
        """Persist an authorized lifecycle intent for a deployment adapter."""
        self._require_write_role(principal)
        runtime = await self.get(principal, runtime_id)
        self._require_lifecycle_capability(runtime, desired_state)
        updated = runtime.model_copy(
            update={
                "desired_state": desired_state,
                "updated_at": datetime.now(UTC),
            }
        )
        return await self._repository.update(updated)

    @staticmethod
    def _require_lifecycle_capability(
        runtime: ResidentRuntime,
        desired_state: ResidentDesiredState,
    ) -> None:
        uses_suspend = desired_state is ResidentDesiredState.SUSPENDED or (
            desired_state is ResidentDesiredState.RUNNING
            and (
                runtime.desired_state is ResidentDesiredState.SUSPENDED
                or runtime.observed_state is ResidentObservedState.SUSPENDED
            )
        )
        if uses_suspend and ResidentCapability.RUNTIME_SUSPEND not in runtime.capabilities:
            raise ResidentRuntimeValidationError(
                f"Resident profile {runtime.profile_id} does not support suspension"
            )

    async def update_observation(
        self,
        runtime_id: UUID,
        *,
        observed_state: ResidentObservedState,
        backend_ref: dict | None = None,
        endpoints: list[ResidentEndpoint] | None = None,
        capabilities: list[ResidentCapability] | None = None,
        conditions: list[ResidentCondition] | None = None,
    ) -> ResidentRuntime:
        """Persist real backend state during reconciliation."""
        runtime = await self._repository.get(runtime_id)
        if runtime is None:
            raise ResidentRuntimeNotFoundError(f"Resident runtime not found: {runtime_id}")

        updates: dict = {
            "observed_state": observed_state,
            "updated_at": datetime.now(UTC),
        }
        if backend_ref is not None:
            updates["backend_ref"] = backend_ref
        if endpoints is not None:
            updates["endpoints"] = endpoints
        if capabilities is not None:
            updates["capabilities"] = capabilities
        if conditions is not None:
            updates["conditions"] = conditions
        return await self._repository.update(runtime.model_copy(update=updates))

    async def delete_record(self, principal: Principal, runtime_id: UUID) -> bool:
        """Delete an authorized record after its deployment adapter has cleaned up."""
        self._require_write_role(principal)
        await self.get(principal, runtime_id)
        return await self._repository.delete(runtime_id)

    @staticmethod
    def _is_admin(principal: Principal) -> bool:
        return "volundr:admin" in principal.roles

    @classmethod
    def _require_write_role(cls, principal: Principal) -> None:
        if cls._is_admin(principal) or "volundr:developer" in principal.roles:
            return
        raise ResidentRuntimeAccessError("Principal cannot manage resident runtimes")

    @classmethod
    def _require_access(
        cls,
        runtime: ResidentRuntime | None,
        principal: Principal,
    ) -> None:
        if runtime is None:
            raise ResidentRuntimeNotFoundError("Resident runtime not found")
        if runtime.tenant_id != principal.tenant_id:
            raise ResidentRuntimeNotFoundError("Resident runtime not found")
        if runtime.owner_id == principal.user_id or cls._is_admin(principal):
            return
        raise ResidentRuntimeNotFoundError("Resident runtime not found")
