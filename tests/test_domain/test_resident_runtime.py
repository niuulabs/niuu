"""Domain tests for resident runtime ownership and profile constraints."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from niuu.domain.model_catalog import ManagedModel
from niuu.ports.session_proxy import SessionProxyTarget
from volundr.adapters.outbound.config_resident_profiles import (
    ConfigResidentDeploymentProfileProvider,
)
from volundr.adapters.outbound.pricing import HardcodedPricingProvider
from volundr.config import ResidentProfileConfig
from volundr.domain.models import (
    Principal,
    ResidentBackend,
    ResidentCapability,
    ResidentDesiredState,
    ResidentEndpoint,
    ResidentEngine,
    ResidentLogEntry,
    ResidentLogPage,
    ResidentObservedState,
    ResidentRuntime,
)
from volundr.domain.ports import (
    ResidentRuntimeLogReader,
    ResidentRuntimeObservation,
    ResidentRuntimeProxyTargetResolver,
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

    async def add_usage(self, runtime_id, *, tokens, cost, message_count):
        runtime = self.items.get(runtime_id)
        if runtime is None:
            return None
        updated = runtime.model_copy(
            update={
                "tokens_used": runtime.tokens_used + tokens,
                "cost": runtime.cost + type(runtime.cost)(str(cost)),
                "message_count": runtime.message_count + message_count,
            }
        )
        self.items[runtime_id] = updated
        return updated

    async def list_for_reconciliation(self) -> list[ResidentRuntime]:
        return list(self.items.values())

    async def delete(self, runtime_id: UUID) -> bool:
        return self.items.pop(runtime_id, None) is not None


class MemoryResidentRuntimeController:
    backend = ResidentBackend.OPENSHELL

    def __init__(self) -> None:
        self.actions: list[str] = []

    def supports(self, profile) -> bool:
        return profile.backend is self.backend

    @staticmethod
    def _observation(runtime: ResidentRuntime) -> ResidentRuntimeObservation:
        observed_state = ResidentObservedState.ACTIVE
        if runtime.desired_state is ResidentDesiredState.SUSPENDED:
            observed_state = ResidentObservedState.SUSPENDED
        return ResidentRuntimeObservation(
            observed_state=observed_state,
            backend_ref={"kind": "Sandbox", "name": str(runtime.id)},
            endpoints=[ResidentEndpoint(kind="chat", protocol="skuld-v1", url="/session")],
        )

    async def deploy(self, runtime, profile):
        self.actions.append("deploy")
        return self._observation(runtime)

    async def reconcile(self, runtime, profile):
        self.actions.append("reconcile")
        return self._observation(runtime)

    async def restart(self, runtime, profile):
        self.actions.append("restart")
        return self._observation(runtime)

    async def suspend(self, runtime):
        self.actions.append("suspend")
        return self._observation(runtime)

    async def resume(self, runtime):
        self.actions.append("resume")
        return self._observation(runtime)

    async def delete(self, runtime):
        self.actions.append("delete")
        return True

    async def close(self):
        self.actions.append("close")


class ProxyResidentRuntimeController(
    MemoryResidentRuntimeController,
    ResidentRuntimeProxyTargetResolver,
):
    def resident_proxy_target(self, runtime: ResidentRuntime) -> SessionProxyTarget | None:
        return SessionProxyTarget(
            service_url=f"http://resident-{runtime.id}.internal",
            connect_host="gateway.internal",
            connect_port=8080,
        )


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
                capabilities=[
                    ResidentCapability.CHAT,
                    ResidentCapability.LOGS,
                    ResidentCapability.FLOCK,
                ],
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


def test_profiles_resolve_model_options_from_bifrost_catalog() -> None:
    catalog = HardcodedPricingProvider(
        [
            ManagedModel(id="gpt-5.6-sol", name="Sol", vendor="openai"),
            ManagedModel(id="gpt-5.6-terra", name="Terra", vendor="openai"),
            ManagedModel(id="Qwen/Qwen3.6-35B-A3B-FP8", name="Qwen", vendor="local"),
        ]
    )
    profiles = ConfigResidentDeploymentProfileProvider(
        [
            ResidentProfileConfig(
                id="codex",
                display_name="Codex",
                backend=ResidentBackend.OPENSHELL,
                engine=ResidentEngine.RAVN,
                default_model="gpt-5.6-sol",
                catalog_vendors=["openai"],
            ),
            ResidentProfileConfig(
                id="hermes",
                display_name="Hermes",
                backend=ResidentBackend.OPENSHELL,
                engine=ResidentEngine.HERMES,
                default_model="niuu/gpt-5.6-sol",
                model_prefix="niuu/",
            ),
        ],
        catalog,
    )

    codex = profiles.get("codex")
    hermes = profiles.get("hermes")

    assert codex is not None
    assert codex.allowed_models == ["gpt-5.6-sol", "gpt-5.6-terra"]
    assert codex.default_model == "gpt-5.6-sol"
    assert hermes is not None
    assert hermes.allowed_models == [
        "niuu/gpt-5.6-sol",
        "niuu/gpt-5.6-terra",
        "niuu/Qwen/Qwen3.6-35B-A3B-FP8",
    ]


def test_profiles_intersect_explicit_constraints_with_bifrost_catalog() -> None:
    catalog = HardcodedPricingProvider(
        [ManagedModel(id="gpt-5.6-sol", name="Sol", vendor="openai")]
    )
    profiles = ConfigResidentDeploymentProfileProvider(
        [
            ResidentProfileConfig(
                id="hermes",
                display_name="Hermes",
                backend=ResidentBackend.OPENSHELL,
                engine=ResidentEngine.HERMES,
                default_model="niuu/gpt-5.6-sol",
                allowed_models=["niuu/gpt-5.6-sol", "niuu/missing"],
                model_prefix="niuu/",
            )
        ],
        catalog,
    )

    profile = profiles.get("hermes")

    assert profile is not None
    assert profile.allowed_models == ["niuu/gpt-5.6-sol"]


def test_profiles_fail_closed_until_default_model_is_available() -> None:
    profiles = ConfigResidentDeploymentProfileProvider(
        [
            ResidentProfileConfig(
                id="codex",
                display_name="Codex",
                backend=ResidentBackend.OPENSHELL,
                engine=ResidentEngine.RAVN,
                default_model="gpt-5.6-sol",
                catalog_vendors=["openai"],
            )
        ],
        HardcodedPricingProvider(),
    )

    assert profiles.get("codex") is None
    assert profiles.list() == []


@pytest.fixture
def runtime_service() -> tuple[ResidentRuntimeService, MemoryResidentRuntimeRepository]:
    repository = MemoryResidentRuntimeRepository()
    controller = MemoryResidentRuntimeController()
    return ResidentRuntimeService(repository, _profiles(), [controller]), repository


async def test_create_record_derives_identity_and_runtime_from_profile(runtime_service) -> None:
    service, _ = runtime_service
    flock_id = UUID("11111111-1111-1111-1111-111111111111")
    member_id = UUID("22222222-2222-2222-2222-222222222222")

    runtime = await service.create_record(
        _principal(),
        name="Muninn",
        profile_id="ravn-openshell",
        persona_name="product-steward",
        flock_id=flock_id,
        flock_member_id=member_id,
        flock_role="coordinator",
        flock_peer_id="ravn-coordinator",
    )

    assert runtime.owner_id == "user-a"
    assert runtime.tenant_id == "tenant-a"
    assert runtime.backend is ResidentBackend.OPENSHELL
    assert runtime.engine is ResidentEngine.RAVN
    assert runtime.model == "gpt-5.6"
    assert runtime.capabilities == [
        ResidentCapability.CHAT,
        ResidentCapability.LOGS,
        ResidentCapability.FLOCK,
    ]
    assert runtime.flock_id == flock_id
    assert runtime.flock_member_id == member_id
    assert runtime.flock_role == "coordinator"
    assert runtime.flock_peer_id == "ravn-coordinator"


async def test_create_deploys_and_persists_real_observation(runtime_service) -> None:
    service, repository = runtime_service

    runtime = await service.create(
        _principal(),
        name="Muninn",
        profile_id="ravn-openshell",
        persona_name="product-steward",
    )

    assert runtime.observed_state is ResidentObservedState.DEPLOYING
    assert runtime.backend_ref == {}

    await asyncio.gather(*tuple(service._deployment_tasks.values()))

    deployed = repository.items[runtime.id]
    assert deployed.observed_state is ResidentObservedState.ACTIVE
    assert deployed.backend_ref["kind"] == "Sandbox"


async def test_create_retains_failed_record_when_background_deployment_fails() -> None:
    repository = MemoryResidentRuntimeRepository()

    class FailingController(MemoryResidentRuntimeController):
        async def deploy(self, runtime, profile):
            self.actions.append("deploy")
            raise RuntimeError("gateway unavailable")

    controller = FailingController()
    service = ResidentRuntimeService(repository, _profiles(), [controller])

    runtime = await service.create(
        _principal(),
        name="Muninn",
        profile_id="ravn-openshell",
    )
    await asyncio.gather(*tuple(service._deployment_tasks.values()))

    failed = repository.items[runtime.id]
    assert failed.observed_state is ResidentObservedState.FAILED
    assert failed.conditions[0].reason == "DeploymentFailed"
    assert failed.conditions[0].message == "gateway unavailable"
    assert controller.actions == ["deploy"]


async def test_background_deployment_records_controller_loss() -> None:
    repository = MemoryResidentRuntimeRepository()
    service = ResidentRuntimeService(
        repository,
        _profiles(),
        [MemoryResidentRuntimeController()],
    )

    runtime = await service.create(
        _principal(),
        name="Muninn",
        profile_id="ravn-openshell",
    )
    service._controllers.clear()
    await asyncio.gather(*tuple(service._deployment_tasks.values()))

    failed = repository.items[runtime.id]
    assert failed.observed_state is ResidentObservedState.FAILED
    assert failed.conditions[0].reason == "DeploymentFailed"


async def test_reconcile_refreshes_capabilities_from_profile(runtime_service) -> None:
    service, repository = runtime_service
    runtime = await service.create_record(_principal(), name="Muninn", profile_id="ravn-openshell")
    repository.items[runtime.id] = runtime.model_copy(
        update={
            "observed_state": ResidentObservedState.ACTIVE,
            "backend_ref": {"kind": "Sandbox", "name": str(runtime.id)},
            "capabilities": [ResidentCapability.CHAT, ResidentCapability.METRICS],
        }
    )

    reconciled = await service.reconcile(runtime.id)

    assert reconciled.capabilities == [
        ResidentCapability.CHAT,
        ResidentCapability.LOGS,
        ResidentCapability.FLOCK,
    ]
    assert repository.items[runtime.id] == reconciled


async def test_reconcile_resumes_unfinished_background_deployment(runtime_service) -> None:
    service, repository = runtime_service
    runtime = await service.create_record(_principal(), name="Muninn", profile_id="ravn-openshell")

    reconciled = await service.reconcile(runtime.id)

    assert reconciled == runtime
    await asyncio.gather(*tuple(service._deployment_tasks.values()))
    assert repository.items[runtime.id].observed_state is ResidentObservedState.ACTIVE


async def test_reconcile_all_records_unavailable_controller_and_continues() -> None:
    repository = MemoryResidentRuntimeRepository()
    service = ResidentRuntimeService(repository, _profiles())
    runtime = await service.create_record(
        _principal(),
        name="Muninn",
        profile_id="ravn-openshell",
    )

    await service.reconcile_all()

    failed = repository.items[runtime.id]
    assert failed.conditions[0].reason == "ReconcileFailed"


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
    with pytest.raises(ResidentRuntimeValidationError, match="require flock_id"):
        await service.create_record(
            _principal(),
            name="Partial flock",
            profile_id="ravn-openshell",
            flock_peer_id="partial",
        )
    with pytest.raises(ResidentRuntimeValidationError, match="requires member id"):
        await service.create_record(
            _principal(),
            name="Missing member",
            profile_id="ravn-openshell",
            flock_id=UUID("33333333-3333-3333-3333-333333333333"),
        )


async def test_owner_name_is_unique(runtime_service) -> None:
    service, _ = runtime_service
    await service.create_record(_principal(), name="Muninn", profile_id="ravn-openshell")

    with pytest.raises(ResidentRuntimeConflictError):
        await service.create_record(_principal(), name="Muninn", profile_id="ravn-openshell")


async def test_database_uniqueness_race_becomes_domain_conflict() -> None:
    class RacingRepository(MemoryResidentRuntimeRepository):
        async def create(self, runtime):
            self.items[runtime.id] = runtime
            raise RuntimeError("unique constraint")

    repository = RacingRepository()
    service = ResidentRuntimeService(
        repository,
        _profiles(),
        [MemoryResidentRuntimeController()],
    )

    with pytest.raises(ResidentRuntimeConflictError, match="Muninn"):
        await service.create_record(
            _principal(),
            name="Muninn",
            profile_id="ravn-openshell",
        )


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
    assert await service.delete(principal, runtime.id)
    assert runtime.id not in repository.items


async def test_delete_record_removes_resident_trace_spans() -> None:
    repository = MemoryResidentRuntimeRepository()
    spans = AsyncMock()
    service = ResidentRuntimeService(repository, _profiles(), span_repository=spans)
    principal = _principal()
    runtime = await service.create_record(principal, name="Muninn", profile_id="ravn-openshell")

    assert await service.delete_record(principal, runtime.id)

    spans.delete_by_session.assert_awaited_once_with(runtime.id)


async def test_delete_removes_resident_trace_spans_after_backend_cleanup() -> None:
    repository = MemoryResidentRuntimeRepository()
    controller = MemoryResidentRuntimeController()
    spans = AsyncMock()
    service = ResidentRuntimeService(
        repository,
        _profiles(),
        [controller],
        span_repository=spans,
    )
    principal = _principal()
    runtime = await service.create_record(principal, name="Muninn", profile_id="ravn-openshell")

    assert await service.delete(principal, runtime.id)

    assert controller.actions == ["delete"]
    spans.delete_by_session.assert_awaited_once_with(runtime.id)


async def test_delete_waits_for_in_flight_deployment_before_cleanup() -> None:
    repository = MemoryResidentRuntimeRepository()
    deployment_started = asyncio.Event()
    finish_deployment = asyncio.Event()

    class BlockingController(MemoryResidentRuntimeController):
        async def deploy(self, runtime, profile):
            self.actions.append("deploy")
            deployment_started.set()
            await finish_deployment.wait()
            return self._observation(runtime)

    controller = BlockingController()
    service = ResidentRuntimeService(repository, _profiles(), [controller])
    principal = _principal()
    runtime = await service.create(
        principal,
        name="Muninn",
        profile_id="ravn-openshell",
    )
    await deployment_started.wait()

    deletion = asyncio.create_task(service.delete(principal, runtime.id))
    await asyncio.sleep(0)
    assert not deletion.done()

    finish_deployment.set()
    assert await deletion
    assert controller.actions == ["deploy", "delete"]
    assert runtime.id not in repository.items


async def test_reconcile_converges_through_runtime_profile(runtime_service) -> None:
    service, repository = runtime_service
    runtime = await service.create_record(
        _principal(),
        name="Muninn",
        profile_id="ravn-openshell",
    )
    repository.items[runtime.id] = runtime.model_copy(
        update={
            "observed_state": ResidentObservedState.ACTIVE,
            "backend_ref": {"kind": "Sandbox", "name": str(runtime.id)},
        }
    )

    reconciled = await service.reconcile(runtime.id)

    assert reconciled.observed_state is ResidentObservedState.ACTIVE
    assert repository.items[runtime.id] == reconciled


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


def test_duplicate_backend_controllers_fail_at_composition_boundary() -> None:
    with pytest.raises(ValueError, match="backends must be unique"):
        ResidentRuntimeService(
            MemoryResidentRuntimeRepository(),
            _profiles(),
            [MemoryResidentRuntimeController(), MemoryResidentRuntimeController()],
        )


async def test_logs_are_authorized_and_read_through_backend_port() -> None:
    class LogController(MemoryResidentRuntimeController, ResidentRuntimeLogReader):
        async def logs(self, runtime, *, lines, sources, min_level):
            assert lines == 25
            assert sources == ("sandbox",)
            assert min_level == "WARN"
            return ResidentLogPage(
                entries=[ResidentLogEntry(timestamp_ms=1, message=runtime.name)],
                buffer_total=1,
            )

    repository = MemoryResidentRuntimeRepository()
    service = ResidentRuntimeService(repository, _profiles(), [LogController()])
    runtime = await service.create_record(
        _principal(),
        name="Muninn",
        profile_id="ravn-openshell",
    )

    page = await service.logs(
        _principal(),
        runtime.id,
        lines=25,
        sources=("sandbox",),
        min_level="WARN",
    )

    assert page.entries[0].message == "Muninn"


async def test_proxy_target_is_resolved_through_runtime_backend_port() -> None:
    repository = MemoryResidentRuntimeRepository()
    service = ResidentRuntimeService(
        repository,
        _profiles(),
        [ProxyResidentRuntimeController()],
    )
    runtime = await service.create_record(
        _principal(),
        name="Muninn",
        profile_id="ravn-openshell",
    )

    target = await service.proxy_target(runtime.id)

    assert target is not None
    assert target.service_url == f"http://resident-{runtime.id}.internal"
    assert target.connect_host == "gateway.internal"


async def test_usage_is_atomically_recorded_on_resident() -> None:
    repository = MemoryResidentRuntimeRepository()
    service = ResidentRuntimeService(
        repository,
        _profiles(),
        [MemoryResidentRuntimeController()],
    )
    principal = _principal()
    runtime = await service.create_record(
        principal,
        name="Muninn",
        profile_id="ravn-openshell",
    )
    runtime = runtime.model_copy(
        update={"capabilities": [*runtime.capabilities, ResidentCapability.USAGE]}
    )
    await repository.update(runtime)

    updated = await service.record_usage(
        principal,
        runtime.id,
        tokens=123,
        cost=0.42,
        message_count=1,
    )

    assert updated.tokens_used == 123
    assert float(updated.cost) == pytest.approx(0.42)
    assert updated.message_count == 1
