"""Shared resident container mesh configuration tests."""

import json
from uuid import uuid4

import pytest
import yaml

from volundr.adapters.outbound.resident_container_spec import (
    image_from_values,
    materialize_resident_container,
    resident_attribution_headers,
    resident_flock_environment,
    resident_flock_labels,
    resident_flock_profile_configured,
    resident_flock_runtime_config,
    resident_flock_skuld_config,
    resident_mesh_pod_metadata,
    resident_process_files,
    resident_profile_values,
    resident_runtime_section,
    resident_service,
    runtime_processes_from_values,
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
                    "adapters": ["ravn.adapters.discovery.event_bus.EventBusDiscoveryAdapter"]
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


def test_profile_image_runtime_and_service_helpers_cover_supported_shapes() -> None:
    values = {"runtime": {"service": {"name": "resident", "port": 9000}}}
    assert resident_profile_values("profile", {"values": values}) is values
    with pytest.raises(RuntimeError, match="deployment.values"):
        resident_profile_values("profile", {})

    assert resident_runtime_section(values) == values["runtime"]
    assert resident_runtime_section({"openshell": {"service": {}}}) == {"service": {}}
    assert resident_runtime_section({"runtime": [], "openshell": []}) == {}

    assert image_from_values({"image": "example.test/resident"}) == "example.test/resident"
    assert image_from_values({"image": ""}, default="fallback") == "fallback"
    assert image_from_values({"image": 42}, default="fallback") == "fallback"
    assert image_from_values({"image": {"tag": "v1"}}, default="fallback") == "fallback"
    assert image_from_values({"image": {"repository": "repo", "tag": "v1"}}) == "repo:v1"
    assert image_from_values({"image": {"repository": "repo"}}) == "repo"

    assert resident_service({}, "skuld", 9200) == ("skuld", 9200)
    assert resident_service(values, "skuld", 9200) == ("resident", 9000)
    with pytest.raises(RuntimeError, match="service configuration"):
        resident_service({"runtime": {"service": {"name": "resident", "port": 70000}}}, "x", 1)


def test_runtime_process_parser_materializes_valid_processes() -> None:
    values = {
        "runtime": {
            "processes": [
                {
                    "name": "api",
                    "command": ["python", "-m", "resident"],
                    "env": {"MODE": "permissive", "EMPTY": ""},
                    "files": {"/sandbox/workspace/config.json": "{}"},
                    "log_path": "/sandbox/.volundr/api.log",
                }
            ]
        }
    }

    processes = runtime_processes_from_values(values)

    assert len(processes) == 1
    assert processes[0].name == "api"
    assert processes[0].command == ("python", "-m", "resident")
    assert processes[0].env == {"MODE": "permissive"}
    assert processes[0].files == {"/sandbox/workspace/config.json": b"{}"}
    assert processes[0].log_path == "/sandbox/.volundr/api.log"
    assert runtime_processes_from_values({}) == ()


@pytest.mark.parametrize(
    ("processes", "message"),
    [
        (["invalid"], "entries must be objects"),
        ([{"name": "bad name", "command": ["run"]}], "invalid name"),
        (
            [
                {"name": "run"},
            ],
            "has no command",
        ),
        ([{"name": "run", "command": ["bad\x00command"]}], "invalid command"),
        (
            [{"name": "run", "command": ["run"], "files": ["invalid"]}],
            "files must be an object",
        ),
        (
            [
                {"name": "run", "command": ["run"]},
                {"name": "run", "command": ["run"]},
            ],
            "invalid name",
        ),
        (
            [
                {
                    "name": "run",
                    "command": ["run"],
                    "files": {"../outside": "bad"},
                }
            ],
            "file path is invalid",
        ),
        (
            [
                {
                    "name": "run",
                    "command": ["run"],
                    "files": {"/etc/passwd": "bad"},
                }
            ],
            "outside /sandbox",
        ),
        (
            [
                {
                    "name": "run",
                    "command": ["run"],
                    "files": {"/sandbox": "bad"},
                }
            ],
            "must name a file",
        ),
    ],
)
def test_runtime_process_parser_rejects_invalid_contracts(
    processes: list,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        runtime_processes_from_values({"runtime": {"processes": processes}})


def test_openclaw_process_files_apply_attribution_and_permissions() -> None:
    runtime = _runtime().model_copy(
        update={"engine": ResidentEngine.OPENCLAW, "model": "niuu/nemotron"}
    )
    path = "/sandbox/workspace/.openclaw/openclaw.json"
    files = {
        "/sandbox/workspace/readme.txt": b"untouched",
        path: json.dumps(
            {"models": {"providers": {"niuu": {"baseUrl": "http://bifrost/v1"}}}}
        ).encode(),
    }

    materialized = resident_process_files(runtime, files)
    config = json.loads(materialized[path])

    assert config["tools"]["exec"] == {"security": "full", "ask": "off"}
    assert config["models"]["providers"]["niuu"]["headers"] == resident_attribution_headers(runtime)
    assert materialized["/sandbox/workspace/readme.txt"] == b"untouched"
    assert resident_process_files(runtime.model_copy(update={"model": "nemotron"}), files) == files


@pytest.mark.parametrize(
    "config",
    [
        b"not-json",
        b"{}",
        json.dumps({"models": {"providers": {"niuu": []}}}).encode(),
        json.dumps({"models": {"providers": {"niuu": {}}}, "tools": []}).encode(),
        json.dumps({"models": {"providers": {"niuu": {}}}, "tools": {"exec": []}}).encode(),
        json.dumps({"models": {"providers": {"niuu": {"headers": []}}}}).encode(),
    ],
)
def test_openclaw_process_files_reject_invalid_config(config: bytes) -> None:
    runtime = _runtime().model_copy(
        update={"engine": ResidentEngine.OPENCLAW, "model": "niuu/nemotron"}
    )
    with pytest.raises(RuntimeError):
        resident_process_files(
            runtime,
            {"/sandbox/workspace/.openclaw/openclaw.json": config},
        )


def test_materialize_hermes_container_uses_provider_and_permissive_runtime() -> None:
    runtime = _runtime().model_copy(
        update={"engine": ResidentEngine.HERMES, "model": "niuu/nemotron"}
    )
    values = {
        "image": "example.test/hermes@sha256:123",
        "env": {"EXTRA": "yes"},
        "broker": {
            "cliType": "hermes",
            "transport": "api",
            "approvalPolicy": "never",
            "skipPermissions": True,
        },
        "resident": {"llm": {"provider": {"kwargs": {"base_url": "http://bifrost/v1"}}}},
        "runtime": {
            "processMode": "replace",
            "service": {"name": "hermes", "port": 8642},
            "processes": [{"name": "hermes", "command": ["hermes", "api"]}],
        },
    }

    spec = materialize_resident_container(
        runtime,
        values,
        default_image="",
        default_service_name="resident",
        default_service_port=9200,
        volundr_api_url="http://volundr",
        sandbox_command=("skuld", "serve"),
    )
    config = yaml.safe_load(spec.files["/sandbox/workspace/.hermes/config.yaml"])

    assert spec.image == "example.test/hermes@sha256:123"
    assert spec.service_name == "hermes"
    assert spec.environment["HERMES_HOME"] == "/sandbox/workspace/.hermes"
    assert spec.environment["EXTRA"] == "yes"
    assert spec.environment["SKULD__SKIP_PERMISSIONS"] == "true"
    assert config["model"]["base_url"] == "http://bifrost/v1"
    assert config["approvals"] == {"mode": "off"}


def test_materialize_ravn_container_extends_shared_mesh_configuration() -> None:
    runtime = _runtime()
    values = _flock_values() | {
        "image": "example.test/ravn@sha256:123",
        "resident": {
            **_flock_values()["resident"],
            "llm": {"temperature": 0.2},
            "wakefulness": {"enabled": True},
            "platform": {"baseUrl": "http://platform"},
        },
        "runtime": {"service": {"name": "skuld", "port": 9200}},
    }

    spec = materialize_resident_container(
        runtime,
        values,
        default_image="",
        default_service_name="resident",
        default_service_port=9000,
        volundr_api_url="http://volundr",
        sandbox_command=("skuld", "serve"),
    )
    ravn_config = yaml.safe_load(spec.files["/sandbox/.volundr/ravn.yaml"])
    skuld_config = yaml.safe_load(spec.files["/sandbox/.volundr/skuld.yaml"])

    assert [process.name for process in spec.processes] == ["skuld", "ravn"]
    assert ravn_config["llm"] == {"temperature": 0.2}
    assert ravn_config["wakefulness"] == {"enabled": True}
    assert ravn_config["mesh"]["own_peer_id"] == runtime.flock_peer_id
    assert skuld_config["mesh"]["realm_id"] == str(runtime.flock_id)


@pytest.mark.parametrize(
    ("runtime", "values", "message"),
    [
        (_runtime(), {"image": ""}, "image is required"),
        (
            _runtime().model_copy(update={"engine": ResidentEngine.OPENCLAW}),
            {"image": "resident", "runtime": {"processMode": "replace"}},
            "requires at least one process",
        ),
        (
            _runtime().model_copy(update={"engine": ResidentEngine.OPENCLAW}),
            {"image": "resident"},
            "requires a process",
        ),
        (
            _runtime().model_copy(update={"engine": ResidentEngine.HERMES}),
            {
                "image": "resident",
                "runtime": {
                    "processMode": "replace",
                    "processes": [{"name": "hermes", "command": ["hermes"]}],
                },
            },
            "require resident.llm.provider.kwargs.base_url",
        ),
    ],
)
def test_materialize_resident_container_rejects_incomplete_profiles(
    runtime: ResidentRuntime,
    values: dict,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        materialize_resident_container(
            runtime,
            values,
            default_image="",
            default_service_name="resident",
            default_service_port=9200,
            volundr_api_url="http://volundr",
            sandbox_command=("skuld", "serve"),
        )
