"""Resident runtime ownership, profile, and durable-state service."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from volundr.domain.models import (
    Principal,
    ResidentCapability,
    ResidentCondition,
    ResidentDeploymentProfile,
    ResidentDesiredState,
    ResidentEndpoint,
    ResidentLogPage,
    ResidentObservedState,
    ResidentRuntime,
)
from volundr.domain.ports import (
    ResidentDeploymentProfileProvider,
    ResidentRuntimeController,
    ResidentRuntimeLogReader,
    ResidentRuntimeObservation,
    ResidentRuntimeRepository,
)

logger = logging.getLogger(__name__)


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


class ResidentRuntimeDeploymentError(Exception):
    """Raised when a resident backend operation fails."""


class ResidentRuntimeService:
    """Authorize and orchestrate durable resident runtime lifecycle state."""

    def __init__(
        self,
        repository: ResidentRuntimeRepository,
        profiles: ResidentDeploymentProfileProvider,
        controllers: list[ResidentRuntimeController] | None = None,
    ) -> None:
        self._repository = repository
        self._profiles = profiles
        self._controllers = {controller.backend: controller for controller in controllers or []}
        if len(self._controllers) != len(controllers or []):
            raise ValueError("Resident runtime controller backends must be unique")

    def list_profiles(self) -> list[ResidentDeploymentProfile]:
        """Return profiles that are actually enabled on this target."""
        return [
            profile
            for profile in self._profiles.list()
            if (controller := self._controllers.get(profile.backend))
            and controller.supports(profile)
        ]

    async def create(
        self,
        principal: Principal,
        *,
        name: str,
        profile_id: str,
        persona_name: str = "",
        model: str = "",
    ) -> ResidentRuntime:
        """Create one durable record and its real backend deployment."""
        profile = self._require_profile(profile_id)
        controller = self._require_controller(profile)
        runtime = await self.create_record(
            principal,
            name=name,
            profile_id=profile_id,
            persona_name=persona_name,
            model=model,
        )
        try:
            observation = await controller.deploy(runtime, profile)
        except Exception as exc:
            try:
                await controller.delete(runtime)
            except Exception:
                logger.exception("Failed to clean up backend for resident %s", runtime.id)
            try:
                await self._repository.delete(runtime.id)
            except Exception:
                logger.exception("Failed to roll back resident record %s", runtime.id)
            raise ResidentRuntimeDeploymentError(
                f"Failed to deploy resident {runtime.name}: {exc}"
            ) from exc
        return await self._apply_observation(runtime, observation)

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
        profile = self._require_profile(profile_id)

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
        try:
            return await self._repository.create(runtime)
        except Exception as exc:
            # The database uniqueness constraint closes the race between the
            # ownership lookup above and concurrent launch requests.
            existing = await self._repository.get_by_owner_name(principal.user_id, name)
            if existing is not None:
                raise ResidentRuntimeConflictError(f"Resident already exists: {name}") from exc
            raise

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
        if desired_state is ResidentDesiredState.DELETED:
            raise ResidentRuntimeValidationError("Use delete for resident removal")
        controller = self._require_controller_for_runtime(runtime)
        updated = runtime.model_copy(
            update={
                "desired_state": desired_state,
                "updated_at": datetime.now(UTC),
            }
        )
        updated = await self._repository.update(updated)
        try:
            if desired_state is ResidentDesiredState.SUSPENDED:
                observation = await controller.suspend(updated)
            else:
                observation = await controller.resume(updated)
        except Exception as exc:
            await self._record_backend_failure(updated, "LifecycleFailed", str(exc))
            raise ResidentRuntimeDeploymentError(
                f"Failed to set resident {runtime.name} to {desired_state.value}: {exc}"
            ) from exc
        return await self._apply_observation(updated, observation)

    async def restart(self, principal: Principal, runtime_id: UUID) -> ResidentRuntime:
        """Restart an authorized resident through its configured backend."""
        self._require_write_role(principal)
        runtime = await self.get(principal, runtime_id)
        if ResidentCapability.RUNTIME_RESTART not in runtime.capabilities:
            raise ResidentRuntimeValidationError(
                f"Resident profile {runtime.profile_id} does not support restart"
            )
        if runtime.desired_state is not ResidentDesiredState.RUNNING:
            raise ResidentRuntimeValidationError("Only running residents can be restarted")
        profile = self._require_profile(runtime.profile_id)
        controller = self._require_controller(profile)
        try:
            observation = await controller.restart(runtime, profile)
        except Exception as exc:
            await self._record_backend_failure(runtime, "RestartFailed", str(exc))
            raise ResidentRuntimeDeploymentError(
                f"Failed to restart resident {runtime.name}: {exc}"
            ) from exc
        return await self._apply_observation(runtime, observation)

    async def reconcile(self, runtime_id: UUID) -> ResidentRuntime:
        """Refresh one resident from its owning backend."""
        runtime = await self._repository.get(runtime_id)
        if runtime is None:
            raise ResidentRuntimeNotFoundError(f"Resident runtime not found: {runtime_id}")
        try:
            profile = self._require_profile(runtime.profile_id)
            controller = self._require_controller(profile)
            observation = await controller.reconcile(runtime, profile)
        except Exception as exc:
            await self._record_backend_failure(runtime, "ReconcileFailed", str(exc))
            raise ResidentRuntimeDeploymentError(
                f"Failed to reconcile resident {runtime.name}: {exc}"
            ) from exc
        return await self._apply_observation(runtime, observation)

    async def reconcile_all(self) -> None:
        """Refresh every durable resident without letting one failure stop the pass."""
        for runtime in await self._repository.list_for_reconciliation():
            try:
                await self.reconcile(runtime.id)
            except ResidentRuntimeDeploymentError:
                continue

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

    async def delete(self, principal: Principal, runtime_id: UUID) -> bool:
        """Delete backend resources before removing the durable record."""
        self._require_write_role(principal)
        runtime = await self.get(principal, runtime_id)
        controller = self._require_controller_for_runtime(runtime)
        deleting = runtime.model_copy(
            update={
                "desired_state": ResidentDesiredState.DELETED,
                "observed_state": ResidentObservedState.DELETING,
                "updated_at": datetime.now(UTC),
            }
        )
        await self._repository.update(deleting)
        try:
            existed = await controller.delete(deleting)
        except Exception as exc:
            await self._record_backend_failure(deleting, "DeleteFailed", str(exc))
            raise ResidentRuntimeDeploymentError(
                f"Failed to delete resident {runtime.name}: {exc}"
            ) from exc
        await self._repository.delete(runtime_id)
        return existed

    async def logs(
        self,
        principal: Principal,
        runtime_id: UUID,
        *,
        lines: int,
        sources: tuple[str, ...] = (),
        min_level: str = "",
    ) -> ResidentLogPage:
        """Read backend-native logs for an authorized resident."""
        runtime = await self.get(principal, runtime_id)
        if ResidentCapability.LOGS not in runtime.capabilities:
            raise ResidentRuntimeValidationError(
                f"Resident profile {runtime.profile_id} does not provide logs"
            )
        controller = self._require_controller_for_runtime(runtime)
        if not isinstance(controller, ResidentRuntimeLogReader):
            raise ResidentRuntimeDeploymentError(
                f"Resident backend does not implement logs: {runtime.backend.value}"
            )
        try:
            return await controller.logs(
                runtime,
                lines=lines,
                sources=sources,
                min_level=min_level,
            )
        except Exception as exc:
            raise ResidentRuntimeDeploymentError(
                f"Failed to read resident {runtime.name} logs: {exc}"
            ) from exc

    async def record_usage(
        self,
        principal: Principal,
        runtime_id: UUID,
        *,
        tokens: int,
        cost: float,
        message_count: int,
    ) -> ResidentRuntime:
        """Atomically add real engine usage to an authorized resident."""
        runtime = await self.get(principal, runtime_id)
        if ResidentCapability.USAGE not in runtime.capabilities:
            raise ResidentRuntimeValidationError(
                f"Resident profile {runtime.profile_id} does not report usage"
            )
        updated = await self._repository.add_usage(
            runtime.id,
            tokens=tokens,
            cost=cost,
            message_count=message_count,
        )
        if updated is None:
            raise ResidentRuntimeNotFoundError(f"Resident runtime not found: {runtime_id}")
        return updated

    def _require_profile(self, profile_id: str) -> ResidentDeploymentProfile:
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise ResidentProfileNotFoundError(f"Resident profile not found: {profile_id}")
        return profile

    def _require_controller(
        self,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeController:
        controller = self._controllers.get(profile.backend)
        if controller is None or not controller.supports(profile):
            raise ResidentProfileNotFoundError(
                f"Resident profile is not deployable on this target: {profile.id}"
            )
        return controller

    def _require_controller_for_runtime(
        self,
        runtime: ResidentRuntime,
    ) -> ResidentRuntimeController:
        controller = self._controllers.get(runtime.backend)
        if controller is None:
            raise ResidentRuntimeDeploymentError(
                f"Resident backend is unavailable on this target: {runtime.backend.value}"
            )
        return controller

    async def _apply_observation(
        self,
        runtime: ResidentRuntime,
        observation: ResidentRuntimeObservation,
    ) -> ResidentRuntime:
        return await self._repository.update(
            runtime.model_copy(
                update={
                    "observed_state": observation.observed_state,
                    "backend_ref": observation.backend_ref,
                    "endpoints": observation.endpoints,
                    "conditions": observation.conditions,
                    "updated_at": datetime.now(UTC),
                }
            )
        )

    async def _record_backend_failure(
        self,
        runtime: ResidentRuntime,
        reason: str,
        message: str,
    ) -> ResidentRuntime:
        condition = ResidentCondition(
            type="BackendReady",
            status="unknown",
            reason=reason,
            message=message,
        )
        return await self._repository.update(
            runtime.model_copy(
                update={
                    "conditions": [condition],
                    "updated_at": datetime.now(UTC),
                }
            )
        )

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
