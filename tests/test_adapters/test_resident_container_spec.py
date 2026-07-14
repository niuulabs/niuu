"""Shared resident container mesh configuration tests."""

from uuid import uuid4

from volundr.adapters.outbound.resident_container_spec import (
    resident_flock_environment,
    resident_flock_labels,
    resident_flock_profile_configured,
    resident_flock_runtime_config,
    resident_flock_skuld_config,
    resident_mesh_pod_metadata,
)
from volundr.domain.models import (
    ResidentBackend,
    ResidentCapability,
    ResidentDeploymentProfile,
    ResidentEngine,
    ResidentRuntime,
)


def _runtime(*, flock: bool = True) -> ResidentRuntime:
    return ResidentRuntime(
        id=uuid4(),
        owner_id="user-a",
        tenant_id="tenant-a",
        name="Muninn",
        persona_name="product-steward",
        backend=ResidentBackend.OPENSHELL,
        engine=ResidentEngine.RAVN,
        profile_id="ravn-openshell",
        capabilities=[ResidentCapability.CHAT, ResidentCapability.FLOCK],
        flock_id=uuid4() if flock else None,
        flock_member_id=uuid4() if flock else None,
        flock_role="coordinator" if flock else "",
        flock_peer_id="ravn-coordinator" if flock else "",
    )


def _profile(
    *,
    engine: ResidentEngine = ResidentEngine.RAVN,
    capabilities: list[ResidentCapability] | None = None,
) -> ResidentDeploymentProfile:
    return ResidentDeploymentProfile(
        id="resident",
        display_name="Resident",
        backend=ResidentBackend.OPENSHELL,
        engine=engine,
        capabilities=capabilities or [ResidentCapability.CHAT],
    )


def _flock_values() -> dict:
    return {
        "resident": {
            "flock": {
                "mesh": {
                    "adapters": ["ravn.adapters.mesh.sleipnir_mesh.SleipnirMeshAdapter"],
                    "nats": {"servers": ["nats://nats:4222"]},
                },
                "discovery": {
                    "adapters": [
                        "ravn.adapters.discovery.event_bus.EventBusDiscoveryAdapter"
                    ]
                },
            }
        }
    }


def test_flock_identity_is_shared_by_container_and_kubernetes_backends() -> None:
    plain = _runtime(flock=False)
    assert resident_flock_labels(plain, prefix="volundr.niuu.io") == {}
    assert resident_flock_environment(plain) == {}
    assert resident_mesh_pod_metadata(plain) == ({}, {})

    runtime = _runtime()
    labels = resident_flock_labels(runtime, prefix="volundr.niuu.io")
    environment = resident_flock_environment(runtime)
    pod_labels, annotations = resident_mesh_pod_metadata(runtime)

    assert labels == {
        "volundr.niuu.io/flock-id": str(runtime.flock_id),
        "volundr.niuu.io/flock-member-id": str(runtime.flock_member_id),
        "volundr.niuu.io/flock-role": "coordinator",
        "volundr.niuu.io/flock-peer-id": "ravn-coordinator",
    }
    assert environment == {
        "NIUU_FLOCK_ID": str(runtime.flock_id),
        "NIUU_FLOCK_MEMBER_ID": str(runtime.flock_member_id),
        "NIUU_FLOCK_ROLE": "coordinator",
        "NIUU_FLOCK_PEER_ID": "ravn-coordinator",
    }
    assert pod_labels == {
        "ravn.niuu.world/realm": str(runtime.flock_id),
        "ravn.niuu.world/role": "agent",
    }
    assert annotations == {
        "ravn.niuu.world/peer-id": "ravn-coordinator",
        "ravn.niuu.world/persona": "product-steward",
        "ravn.niuu.world/capabilities": "chat,flock",
        "ravn.niuu.world/permission-mode": "permissive",
    }


def test_profile_selected_transport_configures_ravn_and_skuld() -> None:
    runtime = _runtime()
    values = _flock_values()
    ravn_config = {
        "mesh": {"adapter": "legacy"},
        "discovery": {"adapters": ["local"]},
    }
    skuld_config = {
        "mesh": {
            "adapters": ["local"],
            "discovery_adapters": ["local"],
        }
    }

    resident_flock_runtime_config(ravn_config, runtime, values)
    resident_flock_skuld_config(skuld_config, runtime, values)

    assert ravn_config["mesh"] == {
        "adapters": ["ravn.adapters.mesh.sleipnir_mesh.SleipnirMeshAdapter"],
        "nats": {"servers": ["nats://nats:4222"]},
        "own_peer_id": "ravn-coordinator",
    }
    assert ravn_config["discovery"] == {
        "adapters": [
            "local",
            "ravn.adapters.discovery.event_bus.EventBusDiscoveryAdapter",
        ],
        "realm_id": str(runtime.flock_id),
    }
    assert skuld_config["mesh"] == {
        "adapters": ["ravn.adapters.mesh.sleipnir_mesh.SleipnirMeshAdapter"],
        "discovery_adapters": [
            "local",
            "ravn.adapters.discovery.event_bus.EventBusDiscoveryAdapter",
        ],
        "nats": {"servers": ["nats://nats:4222"]},
        "realm_id": str(runtime.flock_id),
    }


def test_flock_profile_requires_real_ravn_transport_configuration() -> None:
    flock_profile = _profile(capabilities=[ResidentCapability.CHAT, ResidentCapability.FLOCK])

    assert not resident_flock_profile_configured(flock_profile, {})
    assert not resident_flock_profile_configured(
        flock_profile,
        {"resident": {"flock": {"mesh": {"adapters": ["mesh"]}}}},
    )
    assert resident_flock_profile_configured(flock_profile, _flock_values())
    assert resident_flock_profile_configured(_profile(), {})
    assert resident_flock_profile_configured(
        _profile(
            engine=ResidentEngine.HERMES,
            capabilities=[ResidentCapability.CHAT, ResidentCapability.FLOCK],
        ),
        {},
    )


def test_non_flock_runtime_leaves_runtime_configs_unchanged() -> None:
    runtime = _runtime(flock=False)
    ravn_config = {"mesh": {"adapter": "local"}, "discovery": {"adapters": []}}
    skuld_config = {"mesh": {"adapters": ["local"]}}

    resident_flock_runtime_config(ravn_config, runtime, _flock_values())
    resident_flock_skuld_config(skuld_config, runtime, _flock_values())

    assert ravn_config == {"mesh": {"adapter": "local"}, "discovery": {"adapters": []}}
    assert skuld_config == {"mesh": {"adapters": ["local"]}}
