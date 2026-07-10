"""Domain tests for resident runtime ownership and profile constraints."""

from __future__ import annotations

from uuid import UUID

import pytest

from volundr.adapters.outbound.config_resident_profiles import (
    ConfigResidentDeploymentProfileProvider,
)
from volundr.config import ResidentProfileConfig
from volundr.domain.models import (
    Principal,
    ResidentBackend,
    ResidentCapability,
    ResidentDesiredState,
    ResidentEndpoint,
    ResidentEngine,
    ResidentObservedState,
    ResidentRuntime,
)
from volundr.domain.services.resident_runtime import (
    ResidentProfileNotFoundError,
    ResidentRuntimeAccessError,
    ResidentRuntimeConflictError,
    ResidentRuntimeNotFoundError,
    ResidentRuntimeService,
    ResidentRuntimeValidationError,
)


class MemoryResidentRuntimeRepository:
    def __init__(self) -> None:
        self.items: dict[UUID, ResidentRuntime] = {}

    async def create(self, runtime: ResidentRuntime) -> ResidentRuntime:
        self.items[runtime.id] = runtime
        return runtime

    async def get(self, runtime_id: UUID) -> ResidentRuntime | None:
        return self.items.get(runtime_id)

    async def get_by_owner_name(self, owner_id: str, name: str) -> ResidentRuntime | None:
        return next(
            (
                runtime
                for runtime in self.items.values()
                if runtime.owner_id == owner_id and runtime.name == name
            ),
            None,
        )

    async def list(
        self,
        *,
        tenant_id: str,
        owner_id: str | None = None,
    ) -> list[ResidentRuntime]:
        return [
            runtime
            for runtime in self.items.values()
            if runtime.tenant_id == tenant_id and (owner_id is None or runtime.owner_id == owner_id)
        ]

    async def update(self, runtime: ResidentRuntime) -> ResidentRuntime:
        self.items[runtime.id] = runtime
        return runtime

    async def delete(self, runtime_id: UUID) -> bool:
        return self.items.pop(runtime_id, None) is not None


def _principal(
    user_id: str = "user-a",
    tenant_id: str = "tenant-a",
    roles: list[str] | None = None,
) -> Principal:
    return Principal(
        user_id=user_id,
        email=f"{user_id}@example.test",
        tenant_id=tenant_id,
        roles=roles or ["volundr:developer"],
    )


def _profiles() -> ConfigResidentDeploymentProfileProvider:
    return ConfigResidentDeploymentProfileProvider(
        [
            ResidentProfileConfig(
                id="ravn-openshell",
                display_name="Ravn on OpenShell",
                backend=ResidentBackend.OPENSHELL,
                engine=ResidentEngine.RAVN,
                capabilities=[ResidentCapability.CHAT, ResidentCapability.LOGS],
                default_model="gpt-5.6",
                allowed_models=["gpt-5.6"],
                deployment={"image": "ghcr.io/niuulabs/niuu@sha256:real"},
            ),
            ResidentProfileConfig(
                id="disabled",
                enabled=False,
                display_name="Disabled",
                backend=ResidentBackend.HELMRELEASE,
                engine=ResidentEngine.RAVN,
            ),
        ]
    )


@pytest.fixture
def runtime_service() -> tuple[ResidentRuntimeService, MemoryResidentRuntimeRepository]:
    repository = MemoryResidentRuntimeRepository()
    return ResidentRuntimeService(repository, _profiles()), repository


async def test_create_record_derives_identity_and_runtime_from_profile(runtime_service) -> None:
    service, _ = runtime_service

    runtime = await service.create_record(
        _principal(),
        name="Muninn",
        profile_id="ravn-openshell",
        persona_name="product-steward",
    )

    assert runtime.owner_id == "user-a"
    assert runtime.tenant_id == "tenant-a"
    assert runtime.backend is ResidentBackend.OPENSHELL
    assert runtime.engine is ResidentEngine.RAVN
    assert runtime.model == "gpt-5.6"
    assert runtime.capabilities == [ResidentCapability.CHAT, ResidentCapability.LOGS]


async def test_create_record_rejects_unavailable_profile_and_model(runtime_service) -> None:
    service, _ = runtime_service

    with pytest.raises(ResidentProfileNotFoundError):
        await service.create_record(_principal(), name="Muninn", profile_id="disabled")
    with pytest.raises(ResidentRuntimeValidationError):
        await service.create_record(
            _principal(),
            name="Muninn",
            profile_id="ravn-openshell",
            model="not-allowed",
        )


async def test_owner_name_is_unique(runtime_service) -> None:
    service, _ = runtime_service
    await service.create_record(_principal(), name="Muninn", profile_id="ravn-openshell")

    with pytest.raises(ResidentRuntimeConflictError):
        await service.create_record(_principal(), name="Muninn", profile_id="ravn-openshell")


async def test_viewer_cannot_create_or_mutate_resident(runtime_service) -> None:
    service, _ = runtime_service
    viewer = _principal(roles=["volundr:viewer"])
    runtime = await service.create_record(_principal(), name="Muninn", profile_id="ravn-openshell")

    with pytest.raises(ResidentRuntimeAccessError):
        await service.create_record(viewer, name="Huginn", profile_id="ravn-openshell")
    with pytest.raises(ResidentRuntimeAccessError):
        await service.set_desired_state(viewer, runtime.id, ResidentDesiredState.SUSPENDED)


async def test_list_and_get_enforce_owner_tenant_and_admin_scope(runtime_service) -> None:
    service, _ = runtime_service
    mine = await service.create_record(
        _principal("user-a"), name="Muninn", profile_id="ravn-openshell"
    )
    theirs = await service.create_record(
        _principal("user-b"), name="Huginn", profile_id="ravn-openshell"
    )
    await service.create_record(
        _principal("user-c", "tenant-b"), name="Other", profile_id="ravn-openshell"
    )

    assert [runtime.id for runtime in await service.list(_principal("user-a"))] == [mine.id]
    admin = _principal("admin", roles=["volundr:admin"])
    assert {runtime.id for runtime in await service.list(admin)} == {mine.id, theirs.id}
    assert await service.get(admin, theirs.id) == theirs
    with pytest.raises(ResidentRuntimeNotFoundError):
        await service.get(_principal("user-a"), theirs.id)


async def test_lifecycle_observation_and_delete_use_one_record(runtime_service) -> None:
    service, repository = runtime_service
    principal = _principal()
    runtime = await service.create_record(principal, name="Muninn", profile_id="ravn-openshell")

    observed = await service.update_observation(
        runtime.id,
        observed_state=ResidentObservedState.ACTIVE,
        backend_ref={"kind": "Sandbox", "name": "muninn"},
        endpoints=[ResidentEndpoint(kind="chat", protocol="skuld-v1", url="/s/muninn/session")],
        capabilities=[ResidentCapability.CHAT, ResidentCapability.RUNTIME_SUSPEND],
    )
    desired = await service.set_desired_state(
        principal,
        runtime.id,
        ResidentDesiredState.SUSPENDED,
    )
    resumed = await service.set_desired_state(
        principal,
        runtime.id,
        ResidentDesiredState.RUNNING,
    )

    assert desired.desired_state is ResidentDesiredState.SUSPENDED
    assert resumed.desired_state is ResidentDesiredState.RUNNING
    assert observed.observed_state is ResidentObservedState.ACTIVE
    assert observed.backend_ref["kind"] == "Sandbox"
    assert await service.delete_record(principal, runtime.id)
    assert runtime.id not in repository.items


async def test_lifecycle_rejects_suspend_without_backend_capability(runtime_service) -> None:
    service, _ = runtime_service
    principal = _principal()
    runtime = await service.create_record(principal, name="Muninn", profile_id="ravn-openshell")

    with pytest.raises(ResidentRuntimeValidationError, match="does not support suspension"):
        await service.set_desired_state(
            principal,
            runtime.id,
            ResidentDesiredState.SUSPENDED,
        )

    await service.update_observation(
        runtime.id,
        observed_state=ResidentObservedState.SUSPENDED,
    )
    with pytest.raises(ResidentRuntimeValidationError, match="does not support suspension"):
        await service.set_desired_state(
            principal,
            runtime.id,
            ResidentDesiredState.RUNNING,
        )


def test_profile_provider_hides_disabled_and_keeps_deployment_private() -> None:
    provider = _profiles()

    assert [profile.id for profile in provider.list()] == ["ravn-openshell"]
    profile = provider.get("ravn-openshell")
    assert profile is not None
    assert profile.deployment["image"].endswith("@sha256:real")
    assert "deployment" not in profile.model_dump()
