"""The Forge keeps one record per discovered resident and never deploys one."""

from __future__ import annotations

import pytest

from niuu.domain.discovered_residents import discovered_resident_id
from tests.test_domain.test_resident_runtime import MemoryResidentRuntimeRepository
from volundr.adapters.outbound.config_resident_profiles import (
    ConfigResidentDeploymentProfileProvider,
)
from volundr.config import ResidentProfileConfig
from volundr.domain.models import (
    Principal,
    ResidentBackend,
    ResidentCapability,
    ResidentEngine,
    ResidentObservedState,
)
from volundr.domain.ports import (
    DiscoveredResident,
    ResidentDiscoverySource,
    ResidentRuntimeObservation,
)
from volundr.domain.services.resident_runtime import (
    ResidentRuntimeService,
    ResidentRuntimeValidationError,
)

_OWNER = Principal(
    user_id="user-a",
    email="a@example.test",
    tenant_id="tenant-a",
    roles=["volundr:developer"],
)


class _Source(ResidentDiscoverySource):
    backend = ResidentBackend.DISCOVERED

    def __init__(self, residents: list[DiscoveredResident]) -> None:
        self.residents = residents

    def supports(self, profile) -> bool:
        return profile.backend is self.backend

    async def discovered_residents(self) -> list[DiscoveredResident]:
        return list(self.residents)

    async def reconcile(self, runtime, profile):
        return ResidentRuntimeObservation(
            observed_state=ResidentObservedState.ACTIVE,
            backend_ref={"key": runtime.backend_ref["key"]},
        )


def _resident(key: str = "valkyrie-glitnir-k8s", **overrides) -> DiscoveredResident:
    fields = {
        "key": key,
        "name": "Hildr",
        "owner_id": "user-a",
        "tenant_id": "tenant-a",
        "profile_id": "discovered-ravn",
        "persona_name": "k8s-valkyrie",
    }
    fields.update(overrides)
    return DiscoveredResident(**fields)


def _profiles(backend: ResidentBackend = ResidentBackend.DISCOVERED):
    return ConfigResidentDeploymentProfileProvider(
        [
            ResidentProfileConfig(
                id="discovered-ravn",
                display_name="Discovered Ravn",
                backend=backend,
                engine=ResidentEngine.RAVN,
                capabilities=[ResidentCapability.CHAT],
                default_model="qwen",
            )
        ]
    )


def _service(source: _Source, *, backend: ResidentBackend = ResidentBackend.DISCOVERED):
    repository = MemoryResidentRuntimeRepository()
    service = ResidentRuntimeService(
        repository,
        _profiles(backend),
        controllers=[source],  # type: ignore[list-item]
    )
    return service, repository


async def test_a_discovered_resident_gets_a_record_named_by_its_key():
    source = _Source([_resident()])
    service, repository = _service(source)

    await service.reconcile_all()

    runtime = repository.items[discovered_resident_id("valkyrie-glitnir-k8s")]
    assert runtime.name == "Hildr"
    assert (runtime.owner_id, runtime.tenant_id) == ("user-a", "tenant-a")
    assert runtime.backend is ResidentBackend.DISCOVERED
    assert runtime.engine is ResidentEngine.RAVN
    assert runtime.model == "qwen"
    assert runtime.backend_ref == {"key": "valkyrie-glitnir-k8s"}
    assert runtime.observed_state is ResidentObservedState.ACTIVE
    assert [r.name for r in await service.list(_OWNER)] == ["Hildr"]


async def test_sync_follows_the_source_and_keeps_the_same_record():
    source = _Source([_resident()])
    service, repository = _service(source)
    await service.sync_discovered()
    runtime_id = discovered_resident_id("valkyrie-glitnir-k8s")

    source.residents = [_resident(name="Hildr the Second", model="gpt")]
    await service.sync_discovered()
    renamed = repository.items[runtime_id]
    await service.sync_discovered()

    assert renamed.name == "Hildr the Second"
    assert renamed.model == "gpt"
    assert repository.items[runtime_id] == renamed

    source.residents = []
    await service.sync_discovered()
    assert runtime_id not in repository.items


async def test_a_discovered_resident_on_another_backends_profile_fails_loudly():
    service, _ = _service(_Source([_resident()]), backend=ResidentBackend.OPENSHELL)

    with pytest.raises(ResidentRuntimeValidationError, match="for the openshell backend"):
        await service.sync_discovered()


async def test_a_discovered_profile_is_never_offered_or_deployed():
    service, _ = _service(_Source([]))

    assert service.list_profiles() == []
    with pytest.raises(ResidentRuntimeValidationError, match="deploys none"):
        await service.create(_OWNER, name="Mine", profile_id="discovered-ravn")


async def test_the_ravn_session_controller_gives_a_discovered_resident_channels():
    import httpx

    from volundr.adapters.outbound.discovered_resident_runtime import (
        DiscoveredResidentRuntimeController,
    )
    from volundr.adapters.outbound.ravn_gateway import RavnResidentSessionController

    controller = DiscoveredResidentRuntimeController(
        residents=[
            {
                "key": "valkyrie-glitnir-k8s",
                "name": "Hildr",
                "owner_id": "user-a",
                "tenant_id": "tenant-a",
                "shim_url": "https://hildr.glitnir.example/openclaw",
            }
        ],
        shim_tokens={"valkyrie-glitnir-k8s": "tok"},
        transport=httpx.MockTransport(lambda _: httpx.Response(200)),
    )
    sessions = RavnResidentSessionController(controller, credential_store=object())  # type: ignore[arg-type]
    repository = MemoryResidentRuntimeRepository()
    service = ResidentRuntimeService(
        repository,
        _profiles(),
        controllers=[controller],
        session_controllers=[sessions],
    )

    await service.reconcile_all()
    await controller.close()

    runtime = repository.items[discovered_resident_id("valkyrie-glitnir-k8s")]
    assert runtime.observed_state is ResidentObservedState.ACTIVE
    assert {
        ResidentCapability.SESSION_LIST,
        ResidentCapability.SESSION_CREATE,
        ResidentCapability.SESSION_DELETE,
    } <= set(runtime.capabilities)
    assert service.list_profiles() == []
