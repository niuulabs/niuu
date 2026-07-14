"""Contract tests for the Docker-backed local resident runtime adapter."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from niuu.adapters.memory_credential_store import MemoryCredentialStore
from volundr.adapters.outbound import local_resident_runtime as local_runtime
from volundr.adapters.outbound.local_resident_runtime import (
    LocalContainerResidentRuntimeController,
)
from volundr.adapters.outbound.resident_container_spec import _resident_hermes_config
from volundr.domain.models import (
    ResidentBackend,
    ResidentCapability,
    ResidentDeploymentProfile,
    ResidentDesiredState,
    ResidentEngine,
    ResidentObservedState,
    ResidentRuntime,
)


class _Container:
    def __init__(self, collection, kwargs) -> None:
        self._collection = collection
        self.kwargs = kwargs
        self.id = "container-id"
        self.name = kwargs["name"]
        self.labels = kwargs["labels"]
        self.status = "running"
        self.attrs = {
            "NetworkSettings": {
                "Ports": {"9200/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49152"}]}
            }
        }

    def reload(self) -> None:
        return None

    def pause(self) -> None:
        self.status = "paused"

    def unpause(self) -> None:
        self.status = "running"

    def start(self) -> None:
        self.status = "running"

    def restart(self, *, timeout: int) -> None:
        assert timeout > 0
        self.status = "running"

    def remove(self, *, force: bool) -> None:
        assert force
        self._collection.container = None

    def logs(self, **_kwargs) -> bytes:
        return b"2026-07-12T12:00:00Z [skuld] ready\n"

    def exec_run(self, command, *, environment) -> SimpleNamespace:
        assert command[:3] == ["openclaw", "devices", "approve"]
        assert environment["OPENCLAW_GATEWAY_TOKEN"]
        return SimpleNamespace(exit_code=0, output=b"")


class _Containers:
    def __init__(self) -> None:
        self.container = None

    def get(self, name: str):
        if self.container is None or self.container.name != name:
            raise local_runtime.NotFound("missing")
        return self.container

    def run(self, **kwargs):
        self.container = _Container(self, kwargs)
        return self.container


class _DockerClient:
    def __init__(self) -> None:
        self.containers = _Containers()
        self.images = SimpleNamespace(pull=lambda _image: None)
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _SkuldRegistry:
    def __init__(self) -> None:
        self.ports: dict[str, int] = {}

    def register(self, session_id: str, port: int) -> None:
        self.ports[session_id] = port

    def unregister(self, session_id: str) -> None:
        self.ports.pop(session_id, None)


def _profile(engine: ResidentEngine = ResidentEngine.RAVN) -> ResidentDeploymentProfile:
    runtime = {"service": {"name": "skuld", "port": 9200}}
    if engine is ResidentEngine.OPENCLAW:
        runtime.update(
            {
                "processMode": "replace",
                "processes": [
                    {
                        "name": "openclaw",
                        "command": ["openclaw", "gateway", "run"],
                        "files": {
                            "/sandbox/workspace/.openclaw/openclaw.json": json.dumps(
                                {
                                    "models": {
                                        "providers": {"niuu": {"baseUrl": "http://bifrost.test/v1"}}
                                    }
                                }
                            )
                        },
                    }
                ],
            }
        )
    return ResidentDeploymentProfile(
        id=f"{engine.value}-local",
        display_name=f"{engine.value} local",
        backend=ResidentBackend.LOCAL,
        engine=engine,
        capabilities=[ResidentCapability.LOGS, ResidentCapability.RUNTIME_SUSPEND],
        default_model="gpt-5.6-sol",
        deployment={"values": {"image": "example.test/resident@sha256:123", "runtime": runtime}},
    )


def _runtime(engine: ResidentEngine = ResidentEngine.RAVN) -> ResidentRuntime:
    return ResidentRuntime(
        owner_id="local-user",
        tenant_id="local",
        name="Local resident",
        persona_name="product-steward",
        model="gpt-5.6-sol",
        backend=ResidentBackend.LOCAL,
        engine=engine,
        profile_id=f"{engine.value}-local",
    )


@pytest.fixture
def docker_client(monkeypatch) -> _DockerClient:
    client = _DockerClient()
    monkeypatch.setattr(local_runtime.docker, "from_env", lambda: client)
    monkeypatch.setattr(local_runtime, "_service_ready", lambda _port: True)
    return client


@pytest.mark.asyncio
async def test_local_resident_lifecycle_uses_existing_runtime_contract(
    tmp_path,
    docker_client: _DockerClient,
) -> None:
    controller = LocalContainerResidentRuntimeController(residents_dir=str(tmp_path))
    registry = _SkuldRegistry()
    controller.set_skuld_registry(registry)
    runtime = _runtime()
    profile = _profile()

    deployed = await controller.deploy(runtime, profile)

    assert deployed.observed_state is ResidentObservedState.ACTIVE
    assert deployed.backend_ref["kind"] == "DockerContainer"
    assert deployed.backend_ref["host_port"] == 49152
    assert deployed.endpoints[0].url == f"/s/{runtime.id}/session"
    assert registry.ports[str(runtime.id)] == 49152
    assert docker_client.containers.container.kwargs["ports"] == {"9200/tcp": ("127.0.0.1", None)}
    assert (tmp_path / str(runtime.id) / "sandbox" / "config" / "skuld.yaml").is_file()
    assert (tmp_path / str(runtime.id) / "sandbox" / "config" / "ravn.yaml").is_file()

    assert (
        controller._spec_hash(runtime, await controller._materialize(runtime, profile))
        == (docker_client.containers.container.labels[local_runtime.SPEC_HASH_LABEL])
    )

    runtime = runtime.model_copy(update={"backend_ref": deployed.backend_ref})
    suspended = await controller.suspend(runtime)
    assert suspended.observed_state is ResidentObservedState.SUSPENDED
    assert str(runtime.id) not in registry.ports

    resumed = await controller.resume(runtime)
    assert resumed.observed_state is ResidentObservedState.ACTIVE
    assert registry.ports[str(runtime.id)] == 49152

    restarted = await controller.restart(runtime, profile)
    assert restarted.observed_state is ResidentObservedState.ACTIVE

    page = await controller.logs(runtime, lines=20, sources=(), min_level="info")
    assert [(entry.source, entry.message) for entry in page.entries] == [("skuld", "ready")]
    target = controller.resident_proxy_target(runtime)
    assert target is not None
    assert target.connect_port == 49152

    assert await controller.delete(runtime) is True
    assert str(runtime.id) not in registry.ports
    assert not (tmp_path / str(runtime.id)).exists()
    await controller.close()
    assert docker_client.closed is True


@pytest.mark.asyncio
async def test_local_openclaw_uses_machine_credentials_and_device_approval(
    tmp_path,
    docker_client: _DockerClient,
) -> None:
    controller = LocalContainerResidentRuntimeController(
        residents_dir=str(tmp_path),
        mount_agent_credentials=False,
    )
    store = MemoryCredentialStore()
    controller.set_credential_store(store)
    runtime = _runtime(ResidentEngine.OPENCLAW).model_copy(
        update={"model": "niuu/nvidia/nemotron-3-super"}
    )

    deployed = await controller.deploy(runtime, _profile(ResidentEngine.OPENCLAW))

    environment = docker_client.containers.container.kwargs["environment"]
    assert environment["OPENCLAW_GATEWAY_TOKEN"]
    assert docker_client.containers.container.labels[local_runtime.SPEC_HASH_LABEL] == (
        controller._spec_hash(
            runtime,
            await controller._materialize(runtime, _profile(ResidentEngine.OPENCLAW)),
            {key: value for key, value in environment.items() if key.startswith("OPENCLAW_")},
        )
    )
    assert await store.get_value("resident", str(runtime.id), "openclaw-gateway")
    openclaw_config = json.loads(
        (
            tmp_path / str(runtime.id) / "sandbox" / "workspace" / ".openclaw" / "openclaw.json"
        ).read_text()
    )
    assert openclaw_config["models"]["providers"]["niuu"]["headers"] == {
        "X-Agent-ID": str(runtime.id),
        "X-Tenant-ID": "local",
        "X-Session-ID": str(runtime.id),
    }
    assert openclaw_config["tools"]["exec"] == {"security": "full", "ask": "off"}
    runtime = runtime.model_copy(update={"backend_ref": deployed.backend_ref})
    await controller.approve_resident_device(
        runtime,
        request_id="pairing-request",
        gateway_token=environment["OPENCLAW_GATEWAY_TOKEN"],
    )
    await controller.delete(runtime)
    assert await store.get_value("resident", str(runtime.id), "openclaw-gateway") is None


def test_local_controller_rejects_profiles_owned_by_other_backends(
    tmp_path,
    docker_client: _DockerClient,
) -> None:
    controller = LocalContainerResidentRuntimeController(residents_dir=str(tmp_path))
    profile = _profile().model_copy(update={"backend": ResidentBackend.OPENSHELL})

    assert controller.supports(profile) is False


def test_local_hermes_uses_native_yolo_mode() -> None:
    config = _resident_hermes_config(
        _runtime(ResidentEngine.HERMES),
        {"resident": {"llm": {"provider": {"kwargs": {"base_url": "http://bifrost.test/v1"}}}}},
        18789,
    )

    assert config["approvals"] == {"mode": "off"}


@pytest.mark.asyncio
async def test_reconcile_applies_the_persisted_desired_state(
    tmp_path,
    docker_client: _DockerClient,
) -> None:
    controller = LocalContainerResidentRuntimeController(residents_dir=str(tmp_path))
    runtime = _runtime()
    profile = _profile()
    deployed = await controller.deploy(runtime, profile)
    runtime = runtime.model_copy(
        update={
            "backend_ref": deployed.backend_ref,
            "desired_state": ResidentDesiredState.SUSPENDED,
        }
    )

    observation = await controller.reconcile(runtime, profile)

    assert observation.observed_state is ResidentObservedState.SUSPENDED


def test_local_controller_supports_only_complete_local_profiles(
    tmp_path,
    docker_client: _DockerClient,
) -> None:
    controller = LocalContainerResidentRuntimeController(residents_dir=str(tmp_path))
    no_image = _profile().model_copy(update={"deployment": {"values": {"runtime": {}}}})
    flock_without_transport = _profile().model_copy(
        update={
            "capabilities": [ResidentCapability.FLOCK],
            "deployment": {
                "values": {
                    "image": "resident",
                    "runtime": {},
                }
            },
        }
    )
    malformed = _profile(ResidentEngine.OPENCLAW).model_copy(
        update={
            "deployment": {
                "values": {
                    "image": "resident",
                    "runtime": {
                        "processMode": "replace",
                        "processes": ["invalid"],
                    },
                }
            }
        }
    )

    assert controller.backend is ResidentBackend.LOCAL
    assert controller.supports(no_image) is False
    assert controller.supports(flock_without_transport) is False
    assert controller.supports(malformed) is False


@pytest.mark.asyncio
async def test_deploy_reuses_or_replaces_container_by_spec_hash(
    tmp_path,
    docker_client: _DockerClient,
) -> None:
    controller = LocalContainerResidentRuntimeController(residents_dir=str(tmp_path))
    runtime = _runtime()
    profile = _profile()
    await controller.deploy(runtime, profile)
    original = docker_client.containers.container

    original.status = "paused"
    reused = await controller.deploy(runtime, profile)
    assert reused.observed_state is ResidentObservedState.ACTIVE
    assert docker_client.containers.container is original

    original.labels[local_runtime.SPEC_HASH_LABEL] = "stale"
    replaced = await controller.deploy(runtime, profile)
    assert replaced.observed_state is ResidentObservedState.ACTIVE
    assert docker_client.containers.container is not original


@pytest.mark.asyncio
async def test_reconcile_deploys_missing_and_replaces_changed_container(
    tmp_path,
    docker_client: _DockerClient,
) -> None:
    controller = LocalContainerResidentRuntimeController(residents_dir=str(tmp_path))
    runtime = _runtime()
    profile = _profile()

    deployed = await controller.reconcile(runtime, profile)
    assert deployed.observed_state is ResidentObservedState.ACTIVE
    original = docker_client.containers.container

    changed = runtime.model_copy(update={"model": "gpt-5.6-terra"})
    reconciled = await controller.reconcile(changed, profile)
    assert reconciled.observed_state is ResidentObservedState.ACTIVE
    assert docker_client.containers.container is not original


@pytest.mark.asyncio
async def test_local_lifecycle_handles_nonrunning_and_missing_containers(
    tmp_path,
    docker_client: _DockerClient,
) -> None:
    controller = LocalContainerResidentRuntimeController(
        residents_dir=str(tmp_path),
        retain_data_on_delete=True,
    )
    runtime = _runtime()
    profile = _profile()
    deployed = await controller.deploy(runtime, profile)
    runtime = runtime.model_copy(update={"backend_ref": deployed.backend_ref})
    container = docker_client.containers.container

    container.status = "created"
    resumed = await controller.resume(runtime)
    assert resumed.observed_state is ResidentObservedState.ACTIVE

    container.status = "paused"
    suspended = await controller.suspend(runtime)
    assert suspended.observed_state is ResidentObservedState.SUSPENDED

    container.remove(force=True)
    assert await controller.delete(runtime) is False
    with pytest.raises(RuntimeError, match="does not exist"):
        await controller.restart(runtime, profile)


@pytest.mark.asyncio
async def test_local_readiness_reports_exit_and_timeout(
    tmp_path,
    docker_client: _DockerClient,
) -> None:
    runtime = _runtime()
    profile = _profile()
    controller = LocalContainerResidentRuntimeController(
        residents_dir=str(tmp_path),
        ready_timeout=0,
    )
    spec = await controller._materialize(runtime, profile)
    container = _Container(docker_client.containers, {"name": "resident", "labels": {}})

    with pytest.raises(TimeoutError, match="not ready"):
        await controller._wait_for_ready(runtime, container, spec)

    controller._ready_timeout = 1
    container.status = "exited"
    with pytest.raises(RuntimeError, match="exited before readiness"):
        await controller._wait_for_ready(runtime, container, spec)


@pytest.mark.asyncio
async def test_local_device_failure_and_missing_machine_store_are_explicit(
    tmp_path,
    docker_client: _DockerClient,
) -> None:
    controller = LocalContainerResidentRuntimeController(residents_dir=str(tmp_path))
    openclaw = _runtime(ResidentEngine.OPENCLAW)
    hermes = _runtime(ResidentEngine.HERMES)

    with pytest.raises(RuntimeError, match="require a credential store"):
        await controller._machine_environment(openclaw)
    with pytest.raises(RuntimeError, match="require a credential store"):
        await controller._machine_environment(hermes)

    runtime = _runtime()
    await controller.deploy(runtime, _profile())
    container = docker_client.containers.container
    container.exec_run = lambda *_args, **_kwargs: SimpleNamespace(
        exit_code=1,
        output=b"approval denied",
    )
    with pytest.raises(RuntimeError, match="approval denied"):
        await controller.approve_resident_device(
            runtime,
            request_id="pairing-request",
            gateway_token="token",
        )


def test_local_runtime_helpers_cover_states_paths_and_log_filters(tmp_path) -> None:
    root = tmp_path / "sandbox"
    assert (
        local_runtime._host_runtime_path(
            root,
            "/sandbox/workspace/project/file.txt",
        )
        == root / "workspace" / "project" / "file.txt"
    )
    with pytest.raises(RuntimeError, match="not backed"):
        local_runtime._host_runtime_path(root, "/tmp/file")

    assert local_runtime._observed_state("running") is ResidentObservedState.ACTIVE
    assert local_runtime._observed_state("paused") is ResidentObservedState.SUSPENDED
    assert local_runtime._observed_state("created") is ResidentObservedState.DEPLOYING
    assert local_runtime._observed_state("dead") is ResidentObservedState.FAILED
    assert local_runtime._observed_state("unknown") is ResidentObservedState.PENDING
    assert local_runtime._published_port(SimpleNamespace(attrs={}), 9200) == 0

    entries = local_runtime._parse_logs(
        "\n".join(
            [
                "invalid-time [api] error details",
                "2026-07-12T12:00:00Z [api] WARNING retrying",
                "2026-07-12T12:00:01Z [api] fatal failure",
                "2026-07-12T12:00:02Z [other] error ignored",
            ]
        ),
        ("api",),
        "warning",
    )
    assert [(entry.level, entry.message) for entry in entries] == [
        ("error", "error details"),
        ("warning", "WARNING retrying"),
        ("critical", "fatal failure"),
    ]
    assert local_runtime._log_level("exception raised") == "error"
    assert local_runtime._log_level("warn soon") == "warning"
    assert local_runtime._log_level("debug details") == "debug"
    assert local_runtime._log_level("ready") == "info"
