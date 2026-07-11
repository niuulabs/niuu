"""Tests for the native OpenShell gateway PodManager."""

from __future__ import annotations

import importlib
import io
import json
import sys
import tarfile
import time
import types
from uuid import uuid4

import jwt
import pytest

from niuu.ports.workload_identity import IssuedWorkloadToken
from volundr.domain.models import (
    GitSource,
    PodSpecAdditions,
    ResidentBackend,
    ResidentCapability,
    ResidentDeploymentProfile,
    ResidentEngine,
    ResidentObservedState,
    ResidentRuntime,
    SecretType,
    Session,
    SessionSpec,
    SessionStatus,
)


def _import_adapter(monkeypatch: pytest.MonkeyPatch):
    """Import the adapter with lightweight OpenShell/grpc modules for unit tests."""

    sys.modules.pop("volundr.adapters.outbound.openshell_gateway", None)

    grpc_mod = types.ModuleType("grpc")

    class RpcError(Exception):
        def code(self):
            return None

    grpc_mod.RpcError = RpcError
    grpc_mod.StatusCode = types.SimpleNamespace(
        ALREADY_EXISTS="already-exists",
        NOT_FOUND="not-found",
    )
    grpc_mod.insecure_channel = lambda _endpoint: types.SimpleNamespace(close=lambda: None)
    grpc_mod.secure_channel = lambda _endpoint, _credentials: types.SimpleNamespace(
        close=lambda: None
    )
    grpc_mod.ssl_channel_credentials = lambda: object()

    google_mod = types.ModuleType("google")
    protobuf_mod = types.ModuleType("google.protobuf")
    struct_pb2_mod = types.ModuleType("google.protobuf.struct_pb2")

    class Struct(dict):
        def update(self, value):  # type: ignore[override]
            super().update(value)

        def CopyFrom(self, value):  # noqa: N802 - protobuf compatibility shim.
            self.clear()
            self.update(value)

    struct_pb2_mod.Struct = Struct
    protobuf_mod.struct_pb2 = struct_pb2_mod
    google_mod.protobuf = protobuf_mod

    openshell_mod = types.ModuleType("openshell")
    proto_mod = types.ModuleType("openshell._proto")
    datamodel_pb2_mod = types.ModuleType("openshell._proto.datamodel_pb2")
    openshell_pb2_mod = types.ModuleType("openshell._proto.openshell_pb2")
    openshell_pb2_grpc_mod = types.ModuleType("openshell._proto.openshell_pb2_grpc")
    sandbox_pb2_mod = types.ModuleType("openshell._proto.sandbox_pb2")

    openshell_pb2_mod.SANDBOX_PHASE_UNSPECIFIED = 0
    openshell_pb2_mod.SANDBOX_PHASE_PROVISIONING = 1
    openshell_pb2_mod.SANDBOX_PHASE_READY = 2
    openshell_pb2_mod.SANDBOX_PHASE_ERROR = 3
    openshell_pb2_mod.SANDBOX_PHASE_DELETING = 4
    openshell_pb2_mod.PROVIDER_PROFILE_CATEGORY_AGENT = 3
    openshell_pb2_mod.PROVIDER_PROFILE_CATEGORY_SOURCE_CONTROL = 4

    class _Proto:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    for name in (
        "Provider",
        "ObjectMeta",
    ):
        setattr(datamodel_pb2_mod, name, _Proto)

    for name in (
        "SandboxTemplate",
        "SandboxSpec",
        "CreateSandboxRequest",
        "GetSandboxRequest",
        "DeleteSandboxRequest",
        "ExposeServiceRequest",
        "ExecSandboxRequest",
        "ProviderProfileCredential",
        "ProviderCredentialTokenGrant",
        "ProviderProfile",
    ):
        setattr(openshell_pb2_mod, name, _Proto)

    openshell_pb2_grpc_mod.OpenShellStub = lambda _channel: object()

    for name in (
        "SandboxPolicy",
        "FilesystemPolicy",
        "LandlockPolicy",
        "ProcessPolicy",
        "NetworkPolicyRule",
        "NetworkEndpoint",
        "NetworkBinary",
    ):
        setattr(sandbox_pb2_mod, name, _Proto)

    proto_mod.datamodel_pb2 = datamodel_pb2_mod
    proto_mod.openshell_pb2 = openshell_pb2_mod
    proto_mod.openshell_pb2_grpc = openshell_pb2_grpc_mod
    proto_mod.sandbox_pb2 = sandbox_pb2_mod
    openshell_mod._proto = proto_mod

    for name, module in {
        "grpc": grpc_mod,
        "google": google_mod,
        "google.protobuf": protobuf_mod,
        "google.protobuf.struct_pb2": struct_pb2_mod,
        "openshell": openshell_mod,
        "openshell._proto": proto_mod,
        "openshell._proto.datamodel_pb2": datamodel_pb2_mod,
        "openshell._proto.openshell_pb2": openshell_pb2_mod,
        "openshell._proto.openshell_pb2_grpc": openshell_pb2_grpc_mod,
        "openshell._proto.sandbox_pb2": sandbox_pb2_mod,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    return importlib.import_module("volundr.adapters.outbound.openshell_gateway")


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeHttpClient:
    def __init__(self) -> None:
        self.posts: list[dict] = []

    def post(self, url: str, data: dict):
        self.posts.append({"url": url, "data": data})
        return _FakeResponse({"access_token": "token-1", "expires_in": 3600})


class _FakeOpenShellGatewayClient:
    def __init__(self, adapter_module) -> None:
        self._adapter = adapter_module
        self.created: dict | None = None
        self.bootstrap_execs: list[dict] = []
        self.execs: list[dict] = []
        self.exposed: dict | None = None
        self.deleted: list[str] = []
        self.deleted_services: list[dict] = []
        self.provider_grants: list[dict] = []
        self.deleted_grants: list[object] = []
        self.written_files: list[dict] = []
        self.providers_v2_enabled = False
        self.service_url = "http://openshell.example/proxy/session-1"
        self.grant_sandbox = None
        self.grant_provider = None
        self.grant_profile = None
        self.provider_environment = {}
        self.closed = False

    def create_sandbox(self, **kwargs):
        self.created = kwargs
        return self._adapter.OpenShellSandbox(
            id="sandbox-id",
            name=kwargs["name"],
            phase=self._adapter.openshell_pb2.SANDBOX_PHASE_PROVISIONING,
        )

    def get_sandbox(self, name: str):
        return self._adapter.OpenShellSandbox(
            id="sandbox-id",
            name=name,
            phase=self._adapter.openshell_pb2.SANDBOX_PHASE_READY,
            ready=True,
            providers=tuple(self.created.get("providers", ())) if self.created else (),
        )

    def exec_detached(self, **kwargs) -> int:
        self.execs.append(kwargs)
        return 0

    def exec_script(self, **kwargs) -> tuple[int, str]:
        self.bootstrap_execs.append(kwargs)
        return 0, "Workspace ready"

    def expose_service(self, **kwargs) -> str:
        self.exposed = kwargs
        return self.service_url

    def delete_sandbox(self, name: str) -> bool:
        self.deleted.append(name)
        return True

    def delete_service(self, **kwargs) -> bool:
        self.deleted_services.append(kwargs)
        return True

    def ensure_providers_v2(self) -> None:
        self.providers_v2_enabled = True

    def create_provider_grant(self, **kwargs) -> None:
        self.provider_grants.append(kwargs)

    def delete_provider_grant(self, grant) -> None:
        self.deleted_grants.append(grant)

    def write_files(self, **kwargs) -> None:
        self.written_files.append(kwargs)

    def get_sandbox_by_id(self, _sandbox_id: str):
        return self.grant_sandbox

    def get_provider(self, _name: str):
        return self.grant_provider

    def get_provider_profile(self, _profile_id: str):
        return self.grant_profile

    def get_provider_environment(self, _sandbox_id: str):
        return dict(self.provider_environment)

    def get_sandbox_logs(self, _sandbox_id: str, **_kwargs):
        return self._adapter.ResidentLogPage(
            entries=[
                self._adapter.ResidentLogEntry(
                    timestamp_ms=1234,
                    level="OCSF",
                    source="sandbox",
                    target="ocsf",
                    message="PROC:LAUNCH ravn",
                    fields={"process": "ravn"},
                )
            ],
            buffer_total=1,
        )

    def close(self) -> None:
        self.closed = True


class _FakeCredentialStore:
    def __init__(self, values: dict[str, dict[str, str]]) -> None:
        self.values = values
        self.gets: list[tuple[str, str, str]] = []
        self.stores: list[dict] = []

    async def store(self, owner_type, owner_id, name, secret_type, data, metadata=None):
        self.values[name] = dict(data)
        self.stores.append(
            {
                "owner_type": owner_type,
                "owner_id": owner_id,
                "name": name,
                "secret_type": secret_type,
                "data": dict(data),
                "metadata": metadata or {},
            }
        )
        return types.SimpleNamespace(
            keys=tuple(data),
            secret_type=secret_type,
            metadata=metadata or {},
        )

    async def get_value(self, owner_type: str, owner_id: str, name: str):
        self.gets.append((owner_type, owner_id, name))
        return self.values.get(name)

    async def get(self, owner_type: str, owner_id: str, name: str):
        values = self.values.get(name)
        if values is None:
            return None
        return types.SimpleNamespace(
            keys=tuple(values),
            secret_type=SecretType.GENERIC,
            metadata={},
        )


class _FakeWorkloadTokenIssuer:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    @property
    def enabled(self) -> bool:
        return True

    def issue_token(self, **kwargs) -> IssuedWorkloadToken:
        self.requests.append(kwargs)
        return IssuedWorkloadToken(
            token="volundr-workload-token",
            expires_at=int(time.time()) + 900,
        )


def _codex_auth_document(*, expires_in: int = 3600) -> str:
    access_token = jwt.encode(
        {"sub": "user", "exp": int(time.time()) + expires_in},
        key="",
        algorithm="none",
    )
    return json.dumps(
        {
            "auth_mode": "chatgpt",
            "tokens": {
                "access_token": access_token,
                "refresh_token": "refresh-from-openbao",
                "account_id": "account-from-openbao",
                "id_token": "id-token-from-openbao",
            },
        }
    )


class _FakeSessionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    async def get(self, session_id):
        return self.session if session_id == self.session.id else None


class _FakeResidentRuntimeRepository:
    def __init__(self, runtime: ResidentRuntime) -> None:
        self.runtime = runtime

    async def get(self, runtime_id):
        return self.runtime if runtime_id == self.runtime.id else None


def _session() -> Session:
    return Session(
        id=uuid4(),
        name="OpenShell Forge",
        model="claude-sonnet-4-20250514",
        source=GitSource(repo="https://github.com/niuulabs/volundr", branch="dev"),
        owner_id="owner-1",
        tenant_id="tenant-1",
    )


def _resident_runtime() -> ResidentRuntime:
    return ResidentRuntime(
        name="OpenShell Muninn",
        owner_id="owner-1",
        tenant_id="tenant-1",
        persona_name="product-steward",
        model="gpt-5.6-sol",
        backend=ResidentBackend.OPENSHELL,
        engine=ResidentEngine.RAVN,
        profile_id="ravn-openshell",
        capabilities=[
            ResidentCapability.CHAT,
            ResidentCapability.RUNTIME_RESTART,
            ResidentCapability.LOGS,
            ResidentCapability.USAGE,
        ],
    )


def _resident_profile() -> ResidentDeploymentProfile:
    return ResidentDeploymentProfile(
        id="ravn-openshell",
        display_name="Resident Ravn on OpenShell",
        backend=ResidentBackend.OPENSHELL,
        engine=ResidentEngine.RAVN,
        capabilities=[
            ResidentCapability.CHAT,
            ResidentCapability.RUNTIME_RESTART,
            ResidentCapability.LOGS,
            ResidentCapability.USAGE,
        ],
        default_model="gpt-5.6-sol",
        allowed_models=["gpt-5.6-sol"],
        deployment={
            "values": {
                "image": {
                    "repository": "ghcr.io/niuulabs/openshell",
                    "tag": "niu-1099-openshell-resident",
                    "pullPolicy": "Always",
                },
                "broker": {
                    "cliType": "codex-ws",
                    "transportAdapter": "skuld.transports.codex_ws.CodexWebSocketTransport",
                },
                "session": {"reasoningEffort": "high"},
                "openshell": {
                    "codexAuth": {
                        "credentialName": "codex-credentials",
                        "authField": "auth.json",
                    }
                },
                "resident": {
                    "dailyBudgetUsd": "100.0",
                    "platform": {
                        "enabled": True,
                        "baseUrl": "https://yggdrasil.example.test",
                        "workflowAliases": {"planning": {"name": "Saga Planning"}},
                    },
                    "llm": {
                        "provider": {
                            "adapter": "ravn.adapters.llm.bifrost.BifrostAdapter",
                            "kwargs": {"base_url": "http://bifrost.example.test"},
                        }
                    },
                    "wakefulness": {"enabled": True},
                },
                "mimir": {
                    "instances": [
                        {
                            "name": "mimir-yggdrasil",
                            "role": "shared",
                            "url": "https://mimir.example.test/api/v1",
                            "auth": {"type": "workload", "audiences": ["mimir"]},
                        }
                    ]
                },
            }
        },
    )


@pytest.mark.asyncio
async def test_start_uses_gateway_client_without_host_cli(monkeypatch: pytest.MonkeyPatch):
    adapter = _import_adapter(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-from-volundr-env")
    session = _session()
    client = _FakeOpenShellGatewayClient(adapter)
    spec = SessionSpec(
        values={
            "broker": {"cliType": "codex", "approvalPolicy": "on-request"},
            "env": {"CUSTOM_ENV": "yes"},
            "resources": {
                "requests": {"cpu": "500m", "memory": "1Gi"},
                "limits": {"cpu": "1", "memory": "2Gi"},
            },
            "nodeSelector": {"workload": "openshell"},
            "runtimeClassName": "nvidia",
            "tolerations": [{"key": "nvidia.com/gpu", "operator": "Exists"}],
        },
        pod_spec=PodSpecAdditions(
            labels={"volundr.niuu.io/workload": "forge"},
            annotations={
                "volundr.niuu.io/source": "test",
                "vault.hashicorp.com/agent-inject": "true",
            },
            env=(
                {"name": "POD_ENV", "value": "from-pod-spec"},
                {"name": "OPENAI_API_KEY", "value": "sk-placeholder"},
            ),
        ),
    )
    manager = adapter.OpenShellGatewayPodManager(
        client=client,
        sandbox_command=["skuld", "serve"],
        service_port=9200,
        ready_timeout=0.1,
    )

    result = await manager.start(session, spec)

    expected_sandbox_name = f"forge-{session.id.hex[:22]}"
    assert len(expected_sandbox_name) == 28
    assert result.pod_name == expected_sandbox_name
    assert result.chat_endpoint == "ws://openshell.example/proxy/session-1/session"
    assert result.code_endpoint == "http://openshell.example/proxy/session-1/"
    assert client.created is not None
    assert client.created["name"] == expected_sandbox_name
    assert client.created["image"] == adapter.DEFAULT_SANDBOX_IMAGE
    assert client.created["labels"]["volundr.niuu.io/workload"] == "forge"
    assert client.created["annotations"]["volundr.niuu.io/source"] == "test"
    assert "vault.hashicorp.com/agent-inject" not in client.created["annotations"]
    assert client.created["env"]["SKULD__SESSION__ID"] == str(session.id)
    assert client.created["env"]["SKULD__SESSION__WORKSPACE_DIR"] == "/sandbox/workspace"
    assert client.created["env"]["SKULD__CLI_TYPE"] == "codex"
    assert client.created["env"]["CUSTOM_ENV"] == "yes"
    assert client.created["env"]["POD_ENV"] == "from-pod-spec"
    assert "OPENAI_API_KEY" not in client.created["env"]
    assert client.created["resources"]["requests"]["cpu"] == "500m"
    assert client.created["resources"]["limits"]["memory"] == "2Gi"
    assert client.created["driver_config"]["pod"]["node_selector"] == {"workload": "openshell"}
    assert client.created["driver_config"]["pod"]["runtime_class_name"] == "nvidia"
    assert len(client.bootstrap_execs) == 1
    bootstrap = client.bootstrap_execs[0]
    assert bootstrap["sandbox_id"] == "sandbox-id"
    assert "WORKSPACE=/sandbox/workspace" in bootstrap["script"]
    assert "CLONE_URL=https://github.com/niuulabs/volundr" in bootstrap["script"]
    assert "BRANCH=dev" in bootstrap["script"]
    assert "safe.directory" in bootstrap["script"]
    assert 'attempt" -ge 20' in bootstrap["script"]
    assert bootstrap["env"] == client.created["env"]
    assert client.execs == [
        {
            "sandbox_id": "sandbox-id",
            "command": ["skuld", "serve"],
            "env": client.created["env"],
            "log_path": "/sandbox/.volundr/skuld.log",
        }
    ]
    assert client.exposed == {
        "sandbox_name": expected_sandbox_name,
        "target_port": 9200,
        "service": "skuld",
    }


@pytest.mark.asyncio
async def test_resident_controller_deploys_real_sandbox_and_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    runtime = _resident_runtime()
    profile = _resident_profile()
    client = _FakeOpenShellGatewayClient(adapter)
    store = _FakeCredentialStore({"codex-credentials": {"auth.json": _codex_auth_document()}})
    issuer = _FakeWorkloadTokenIssuer()
    manager = adapter.OpenShellGatewayPodManager(
        client=client,
        volundr_api_url="https://volundr.example.test",
        workload_audiences=["volundr-api", "mimir", "guild"],
        ready_timeout=0.1,
    )
    manager.set_credential_store(store)
    manager.set_workload_token_issuer(issuer)

    observation = await manager.deploy(runtime, profile)

    assert manager.backend is ResidentBackend.OPENSHELL
    assert manager.supports(profile)
    assert observation.observed_state is ResidentObservedState.ACTIVE
    assert observation.backend_ref["kind"] == "OpenShellSandbox"
    expected_name = f"resident-{runtime.id.hex[:19]}"
    assert observation.backend_ref["name"] == expected_name
    assert len(expected_name) == adapter.MAX_SANDBOX_ROUTING_NAME_LENGTH
    assert observation.endpoints[0].url == "ws://openshell.example/proxy/session-1/session"
    assert client.created is not None
    assert client.created["image"] == ("ghcr.io/niuulabs/openshell:niu-1099-openshell-resident")
    assert client.created["labels"] == {
        "app.kubernetes.io/managed-by": "volundr",
        "volundr.niuu.io/resident": str(runtime.id),
        "volundr.niuu.io/runtime": "ravn",
    }
    assert len(client.provider_grants) == 2
    assert all(
        grant["config"]["volundr_subject_kind"] == "resident"
        and grant["config"]["volundr_subject_id"] == str(runtime.id)
        for grant in client.provider_grants
    )
    assert len(client.written_files) == 1
    projected = client.written_files[0]["files"]
    assert "/sandbox/.volundr/skuld.yaml" in projected
    assert "/sandbox/.volundr/ravn.yaml" in projected
    assert _codex_auth_document().encode() not in projected.values()
    ravn_config = projected["/sandbox/.volundr/ravn.yaml"].decode()
    assert "gpt-5.6-sol" in ravn_config
    assert "mimir-yggdrasil" in ravn_config
    assert "token_env: NIUU_VOLUNDR_ACCESS_TOKEN" in ravn_config
    assert "Saga Planning" in ravn_config
    assert [process["pid_path"] for process in client.execs] == [
        "/sandbox/.volundr/skuld.pid",
        "/sandbox/.volundr/ravn.pid",
    ]


@pytest.mark.asyncio
async def test_resident_restart_reuses_sandbox_and_dynamic_provider_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    runtime = _resident_runtime().model_copy(
        update={
            "backend_ref": {
                "kind": "OpenShellSandbox",
                "id": "sandbox-id",
                "name": "resident-existing",
                "service_url": "https://resident.example.test",
            }
        }
    )
    client = _FakeOpenShellGatewayClient(adapter)
    client.created = {"providers": ()}
    client.provider_environment = {
        adapter.PLATFORM_ACCESS_TOKEN_ENV: "openshell:resolve:env:platform"
    }
    manager = adapter.OpenShellGatewayPodManager(client=client, ready_timeout=0.1)
    profile = _resident_profile()
    values = profile.deployment["values"]
    values["env"] = {"RESIDENT_MODE": "active"}
    values["openshell"]["processes"] = [
        {
            "name": "sidecar",
            "command": ["sleep", "3600"],
            "logPath": "/sandbox/.volundr/sidecar.log",
        }
    ]

    observation = await manager.restart(runtime, profile)

    assert observation.observed_state is ResidentObservedState.ACTIVE
    assert client.deleted == []
    assert client.bootstrap_execs[0]["script"] == adapter._resident_stop_script()
    assert client.execs[-1]["env"][adapter.PLATFORM_ACCESS_TOKEN_ENV].startswith(
        "openshell:resolve"
    )
    assert client.execs[-1]["env"]["RESIDENT_MODE"] == "active"
    assert [process["pid_path"] for process in client.execs] == [
        "/sandbox/.volundr/skuld.pid",
        "/sandbox/.volundr/ravn.pid",
        "/sandbox/.volundr/sidecar.pid",
    ]


@pytest.mark.asyncio
async def test_resident_delete_removes_service_sandbox_and_provider_grants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    runtime = _resident_runtime()
    client = _FakeOpenShellGatewayClient(adapter)
    client.created = {"providers": ("volundr-provider",)}
    manager = adapter.OpenShellGatewayPodManager(client=client)

    assert await manager.delete(runtime)
    assert client.deleted_services == [
        {"sandbox_name": f"resident-{runtime.id.hex[:19]}", "service": "skuld"}
    ]
    assert client.deleted == [f"resident-{runtime.id.hex[:19]}"]
    assert [grant.provider_name for grant in client.deleted_grants] == ["volundr-provider"]


@pytest.mark.asyncio
async def test_resident_logs_use_native_gateway_log_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    runtime = _resident_runtime()
    client = _FakeOpenShellGatewayClient(adapter)
    client.created = {"providers": ()}
    manager = adapter.OpenShellGatewayPodManager(client=client)

    page = await manager.logs(
        runtime,
        lines=50,
        sources=("sandbox",),
        min_level="INFO",
    )

    assert page.buffer_total == 1
    assert page.entries[0].message == "PROC:LAUNCH ravn"


def test_session_proxy_target_preserves_service_route_and_uses_gateway(
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = _import_adapter(monkeypatch)
    session = _session().model_copy(
        update={"chat_endpoint": ("ws://forge-example--skuld.openshell.localhost:8080/session")}
    )
    manager = adapter.OpenShellGatewayPodManager(
        client=_FakeOpenShellGatewayClient(adapter),
        gateway_endpoint="openshell.openshell.svc.cluster.local:8080",
    )

    target = manager.session_proxy_target(session)

    assert target is not None
    assert target.service_url == ("ws://forge-example--skuld.openshell.localhost:8080")
    assert target.connect_host == "openshell.openshell.svc.cluster.local"
    assert target.connect_port == 8080
    assert target.connect_secure is False


def test_workspace_bootstrap_strips_embedded_git_credentials(
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = _import_adapter(monkeypatch)
    session = _session().model_copy(
        update={
            "source": GitSource(
                repo="https://token-value@github.com/niuulabs/volundr",
                branch="dev",
            )
        }
    )
    client = _FakeOpenShellGatewayClient(adapter)
    manager = adapter.OpenShellGatewayPodManager(client=client)

    script = manager._workspace_bootstrap_script(  # noqa: SLF001
        session,
        SessionSpec(values={}, pod_spec=None),
    )

    assert "token-value" not in script
    assert "CLONE_URL=https://github.com/niuulabs/volundr" in script


@pytest.mark.asyncio
async def test_start_fails_and_rolls_back_without_exposed_service_url(
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = _import_adapter(monkeypatch)
    session = _session()
    client = _FakeOpenShellGatewayClient(adapter)
    client.service_url = ""
    manager = adapter.OpenShellGatewayPodManager(client=client, ready_timeout=0.1)

    with pytest.raises(RuntimeError, match="did not return an exposed service URL"):
        await manager.start(session, SessionSpec(values={}, pod_spec=None))

    assert client.deleted == [f"forge-{session.id.hex[:22]}"]


@pytest.mark.asyncio
async def test_start_creates_dynamic_openbao_providers_without_secret_environment(
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = _import_adapter(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-from-process-env")
    session = _session()
    client = _FakeOpenShellGatewayClient(adapter)
    manager = adapter.OpenShellGatewayPodManager(client=client, ready_timeout=0.1)
    manager.set_credential_store(
        _FakeCredentialStore(
            {
                "openai-cred": {"api_key": "sk-openai-from-openbao"},
                "github-cred": {"token": "ghp-from-openbao"},
            }
        )
    )
    spec = SessionSpec(
        values={
            "env": {"GITHUB_TOKEN": "literal-should-not-launch"},
            "openshell": {
                "credentialMappings": [
                    {
                        "credentialName": "openai-cred",
                        "envMappings": {"OPENAI_API_KEY": "api_key"},
                    },
                    {
                        "credentialName": "github-cred",
                        "envMappings": {"GITHUB_PERSONAL_ACCESS_TOKEN": "token"},
                    },
                ],
            },
        },
        pod_spec=PodSpecAdditions(
            env=(
                {"name": "OPENAI_API_KEY", "value": "placeholder"},
                {"name": "GITHUB_PERSONAL_ACCESS_TOKEN", "value": "placeholder"},
            ),
        ),
    )

    await manager.start(session, spec)

    assert client.created is not None
    assert "OPENAI_API_KEY" not in client.created["env"]
    assert "GITHUB_TOKEN" not in client.created["env"]
    assert "GITHUB_PERSONAL_ACCESS_TOKEN" not in client.created["env"]
    assert "OPENAI_API_KEY" not in client.bootstrap_execs[0]["env"]
    assert "GITHUB_PERSONAL_ACCESS_TOKEN" not in client.bootstrap_execs[0]["env"]
    assert "secret_env" not in client.bootstrap_execs[0]
    assert "OPENAI_API_KEY" not in client.execs[0]["env"]
    assert "GITHUB_PERSONAL_ACCESS_TOKEN" not in client.execs[0]["env"]
    assert "secret_env" not in client.execs[0]
    assert client.providers_v2_enabled is True
    assert len(client.provider_grants) == 2
    assert {grant["profile"].credentials[0].env_vars[0] for grant in client.provider_grants} == {
        "OPENAI_API_KEY",
        "GITHUB_PERSONAL_ACCESS_TOKEN",
    }
    assert all(not grant["profile"].credentials[0].required for grant in client.provider_grants)
    assert all(
        grant["config"]["volundr_session_id"] == str(session.id) for grant in client.provider_grants
    )

    assert await manager.stop(session) is True


@pytest.mark.asyncio
async def test_start_attaches_session_bound_platform_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    session = _session()
    client = _FakeOpenShellGatewayClient(adapter)
    issuer = _FakeWorkloadTokenIssuer()
    manager = adapter.OpenShellGatewayPodManager(
        client=client,
        ready_timeout=0.1,
        volundr_api_url="http://niuu-volundr.volundr.svc.cluster.local",
    )
    manager.set_workload_token_issuer(issuer)

    await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))

    assert client.created is not None
    assert client.created["env"]["SKULD__VOLUNDR_API_URL"] == (
        "http://niuu-volundr.volundr.svc.cluster.local"
    )
    assert len(client.provider_grants) == 1
    grant = client.provider_grants[0]
    credential = grant["profile"].credentials[0]
    assert credential.env_vars == [adapter.PLATFORM_ACCESS_TOKEN_ENV]
    assert credential.token_grant.audience.startswith(adapter.PLATFORM_GRANT_AUDIENCE_PREFIX)
    assert grant["profile"].endpoints[0].host == "niuu-volundr.volundr.svc.cluster.local"
    assert grant["profile"].endpoints[0].port == 80
    assert grant["config"] == {
        "volundr_session_id": str(session.id),
        "volundr_grant_kind": "platform",
    }
    assert client.created["providers"] == (grant["provider_name"],)
    assert adapter.PLATFORM_ACCESS_TOKEN_ENV not in client.created["env"]


@pytest.mark.asyncio
async def test_start_rejects_platform_reporting_without_workload_issuer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    client = _FakeOpenShellGatewayClient(adapter)
    manager = adapter.OpenShellGatewayPodManager(
        client=client,
        ready_timeout=0.1,
        volundr_api_url="http://niuu-volundr.volundr.svc.cluster.local",
    )

    with pytest.raises(RuntimeError, match="workload token issuer"):
        await manager.start(_session(), SessionSpec(values={}, pod_spec=PodSpecAdditions()))

    assert client.created is None


@pytest.mark.asyncio
async def test_start_uses_dynamic_codex_grant_without_projecting_oauth_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    session = _session()
    client = _FakeOpenShellGatewayClient(adapter)
    manager = adapter.OpenShellGatewayPodManager(client=client, ready_timeout=0.1)
    manager.set_credential_store(
        _FakeCredentialStore(
            {
                "codex-credentials": {"auth.json": _codex_auth_document()},
                "openai-credential": {"api_key": "sk-openai-from-openbao"},
                "github-credential": {"token": "ghp-from-openbao"},
            }
        )
    )
    spec = SessionSpec(
        values={
            "openshell": {
                "codexAuth": {
                    "credentialName": "codex-credentials",
                    "authField": "auth.json",
                },
                "credentialMappings": [
                    {
                        "credentialName": "codex-credentials",
                        "envMappings": {
                            "CODEX_CREDENTIALS_AUTH.JSON": "auth.json",
                            "CODEX_CREDENTIALS_CONFIG.TOML": "config.toml",
                        },
                    },
                    {
                        "credentialName": "openai-credential",
                        "envMappings": {"OPENAI_API_KEY": "api_key"},
                    },
                    {
                        "credentialName": "github-credential",
                        "envMappings": {"GITHUB_PERSONAL_ACCESS_TOKEN": "token"},
                    },
                ],
            }
        },
        pod_spec=PodSpecAdditions(),
    )

    await manager.start(session, spec)

    assert client.written_files == []
    assert client.created is not None
    assert client.created["env"][adapter.CODEX_ACCESS_TOKEN_ENV] == (
        adapter.CODEX_ACCESS_TOKEN_REFERENCE
    )
    assert client.created["env"][adapter.CODEX_ACCOUNT_ID_ENV] == "account-from-openbao"
    assert len(client.provider_grants) == 2
    assert {grant["profile"].credentials[0].env_vars[0] for grant in client.provider_grants} == {
        adapter.CODEX_ACCESS_TOKEN_ENV,
        "GITHUB_PERSONAL_ACCESS_TOKEN",
    }
    grant = next(
        grant
        for grant in client.provider_grants
        if grant["profile"].credentials[0].env_vars == [adapter.CODEX_ACCESS_TOKEN_ENV]
    )
    assert grant["profile"].credentials[0].env_vars == [adapter.CODEX_ACCESS_TOKEN_ENV]
    assert grant["profile"].credentials[0].token_grant.cache_ttl_seconds == 0
    assert {endpoint.host for endpoint in grant["profile"].endpoints} >= {
        "api.openai.com",
        "auth.openai.com",
        "chatgpt.com",
    }
    assert grant["config"]["volundr_credential_format"] == adapter.CODEX_AUTH_FORMAT
    assert grant["config"]["volundr_credential_field"] == "auth.json"


@pytest.mark.asyncio
async def test_start_fails_when_openshell_credential_mapping_is_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = _import_adapter(monkeypatch)
    session = _session()
    client = _FakeOpenShellGatewayClient(adapter)
    manager = adapter.OpenShellGatewayPodManager(client=client, ready_timeout=0.1)
    manager.set_credential_store(_FakeCredentialStore({}))
    spec = SessionSpec(
        values={
            "openshell": {
                "credentialMappings": [
                    {
                        "credentialName": "openai-cred",
                        "envMappings": {"OPENAI_API_KEY": "api_key"},
                    },
                ],
            },
        },
        pod_spec=PodSpecAdditions(),
    )

    with pytest.raises(RuntimeError, match="openai-cred"):
        await manager.start(session, spec)

    assert client.created is None
    assert client.execs == []


@pytest.mark.asyncio
async def test_start_projects_non_agent_file_mapping_into_sandbox(
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = _import_adapter(monkeypatch)
    session = _session()
    client = _FakeOpenShellGatewayClient(adapter)
    manager = adapter.OpenShellGatewayPodManager(client=client, ready_timeout=0.1)
    manager.set_credential_store(
        _FakeCredentialStore({"tool-config": {"credentials.json": '{"token":"opaque"}'}})
    )
    spec = SessionSpec(
        values={
            "openshell": {
                "credentialMappings": [
                    {
                        "credentialName": "tool-config",
                        "fileMappings": {"/run/secrets/tool/credentials.json": "credentials.json"},
                    },
                ],
            },
        },
        pod_spec=PodSpecAdditions(),
    )

    await manager.start(session, spec)

    assert client.written_files == [
        {
            "sandbox_id": "sandbox-id",
            "files": {"/run/secrets/tool/credentials.json": b'{"token":"opaque"}'},
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "destination",
    (
        "/home/volundr/.codex/auth.json",
        "/home/volundr/.claude/.credentials.json",
    ),
)
async def test_start_rejects_agent_auth_file_projection(
    monkeypatch: pytest.MonkeyPatch,
    destination: str,
) -> None:
    adapter = _import_adapter(monkeypatch)
    session = _session()
    client = _FakeOpenShellGatewayClient(adapter)
    manager = adapter.OpenShellGatewayPodManager(client=client, ready_timeout=0.1)
    manager.set_credential_store(
        _FakeCredentialStore({"agent-auth": {"auth": '{"token":"secret"}'}})
    )
    spec = SessionSpec(
        values={
            "openshell": {
                "credentialMappings": [
                    {
                        "credentialName": "agent-auth",
                        "fileMappings": {destination: "auth"},
                    }
                ]
            }
        },
        pod_spec=PodSpecAdditions(),
    )

    with pytest.raises(RuntimeError, match="must use a dynamic provider"):
        await manager.start(session, spec)

    assert client.created is None


@pytest.mark.asyncio
async def test_start_launches_structured_flock_processes_inside_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    session = _session()
    client = _FakeOpenShellGatewayClient(adapter)
    manager = adapter.OpenShellGatewayPodManager(client=client, ready_timeout=0.1)
    spec = SessionSpec(
        values={
            "openshell": {
                "processes": [
                    {
                        "name": "ravn-coder",
                        "command": [
                            "/opt/niuu/bin/python",
                            "-m",
                            "ravn",
                            "daemon",
                            "--config",
                            "/sandbox/.volundr/flock/coder.yaml",
                        ],
                        "env": {"RAVN_PERSONA": "coder"},
                        "files": {
                            "/sandbox/.volundr/flock/coder.yaml": "persona:\n  name: coder\n"
                        },
                        "logPath": "/sandbox/.volundr/flock/coder.log",
                    }
                ]
            }
        },
        pod_spec=PodSpecAdditions(),
    )

    await manager.start(session, spec)

    assert client.written_files[0]["files"] == {
        "/sandbox/.volundr/flock/coder.yaml": b"persona:\n  name: coder\n"
    }
    assert client.execs[0]["command"][:4] == (
        "/opt/niuu/bin/python",
        "-m",
        "ravn",
        "daemon",
    )
    assert client.execs[0]["env"]["RAVN_PERSONA"] == "coder"
    assert client.execs[0]["log_path"] == "/sandbox/.volundr/flock/coder.log"
    assert client.execs[1]["command"] == adapter.DEFAULT_SANDBOX_COMMAND


@pytest.mark.asyncio
async def test_stop_and_status_map_sandbox_lifecycle(monkeypatch: pytest.MonkeyPatch):
    adapter = _import_adapter(monkeypatch)
    session = _session()
    client = _FakeOpenShellGatewayClient(adapter)
    manager = adapter.OpenShellGatewayPodManager(client=client)

    assert await manager.status(session) == SessionStatus.RUNNING
    assert await manager.stop(session) is True
    assert client.deleted == [f"forge-{session.id.hex[:22]}"]


def test_token_provider_uses_keycloak_client_credentials_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    http_client = _FakeHttpClient()
    provider = adapter.ClientCredentialsTokenProvider(
        token_url="https://keycloak.example/realms/volundr/protocol/openid-connect/token",
        client_id="openshell-volundr-agent",
        client_secret="secret",
        client=http_client,
    )

    assert provider.token() == "token-1"
    assert provider.token() == "token-1"
    assert http_client.posts == [
        {
            "url": "https://keycloak.example/realms/volundr/protocol/openid-connect/token",
            "data": {
                "grant_type": "client_credentials",
                "client_id": "openshell-volundr-agent",
                "client_secret": "secret",
            },
        }
    ]


def test_exec_script_sends_multiline_script_over_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _import_adapter(monkeypatch)
    recorded = {}

    class _Exit:
        exit_code = 0

    class _Event:
        exit = _Exit()

        def HasField(self, name: str) -> bool:  # noqa: N802 - protobuf shim.
            return name == "exit"

    class _Stub:
        def ExecSandbox(self, request, **_kwargs):  # noqa: N802 - protobuf shim.
            recorded["request"] = request
            return [_Event()]

    adapter.openshell_pb2_grpc.OpenShellStub = lambda _channel: _Stub()
    client = adapter.OpenShellGatewayClient(
        endpoint="openshell.example:8080",
        token_provider=type("TokenProvider", (), {"token": lambda self: "token"})(),
    )

    exit_code, output = client.exec_script(
        sandbox_id="sandbox-id",
        script="echo one\necho two\n",
        env={"A": "B"},
    )

    request = recorded["request"]
    assert exit_code == 0
    assert output == ""
    assert request.command == ["sh", "-s"]
    assert request.stdin == b"echo one\necho two\n"
    assert all("\n" not in arg and "\r" not in arg for arg in request.command)
    assert request.environment == {"A": "B"}


def test_write_files_includes_projection_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _import_adapter(monkeypatch)
    recorded = {}

    class _Data:
        data = "tar: cannot create sandbox/.volundr: permission denied\n"

    class _Exit:
        exit_code = 2

    class _Event:
        stderr = _Data()
        exit = _Exit()

        def __init__(self, field: str) -> None:
            self._field = field

        def HasField(self, name: str) -> bool:  # noqa: N802 - protobuf shim.
            return name == self._field

    class _Stub:
        def ExecSandbox(self, request, **_kwargs):  # noqa: N802 - protobuf shim.
            recorded["request"] = request
            return [_Event("stderr"), _Event("exit")]

    adapter.openshell_pb2_grpc.OpenShellStub = lambda _channel: _Stub()
    client = adapter.OpenShellGatewayClient(
        endpoint="openshell.example:8080",
        token_provider=type("TokenProvider", (), {"token": lambda self: "token"})(),
    )

    with pytest.raises(RuntimeError, match="permission denied"):
        client.write_files(
            sandbox_id="sandbox-id",
            files={"/sandbox/.volundr/skuld.yaml": b"session: {}\n"},
        )
    assert recorded["request"].command == ["tar", "-xf", "-", "-C", "/sandbox"]


def test_exec_detached_sends_only_process_command_over_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    recorded = {}

    class _Exit:
        exit_code = 0

    class _Event:
        exit = _Exit()

        def HasField(self, name: str) -> bool:  # noqa: N802 - protobuf shim.
            return name == "exit"

    class _Stub:
        def ExecSandbox(self, request, **_kwargs):  # noqa: N802 - protobuf shim.
            recorded["request"] = request
            return [_Event()]

    adapter.openshell_pb2_grpc.OpenShellStub = lambda _channel: _Stub()
    client = adapter.OpenShellGatewayClient(
        endpoint="openshell.example:8080",
        token_provider=type("TokenProvider", (), {"token": lambda self: "token"})(),
    )

    exit_code = client.exec_detached(
        sandbox_id="sandbox-id",
        command=["skuld", "serve"],
        env={"SKULD__PORT": "9200"},
        log_path="/sandbox/.volundr/skuld.log",
    )

    request = recorded["request"]
    assert exit_code == 0
    assert request.command == ["sh", "-s"]
    assert request.environment == {"SKULD__PORT": "9200"}
    assert b"nohup skuld serve" in request.stdin
    assert b"OPENAI_API_KEY" not in request.stdin


def test_default_policy_allows_bootstrap_and_codex_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)

    policy = adapter._default_policy()

    assert "/dev/null" in policy.filesystem.read_write
    assert "/tmp" in policy.filesystem.read_write
    assert "/usr" in policy.filesystem.read_only

    github = policy.network_policies["github_https"]
    assert {endpoint.host for endpoint in github.endpoints} >= {
        "github.com",
        "api.github.com",
        "codeload.github.com",
    }
    assert {endpoint.protocol for endpoint in github.endpoints} == {"rest"}
    assert {endpoint.tls for endpoint in github.endpoints} == {"terminate"}
    assert {binary.path for binary in github.binaries} >= {
        "/usr/bin/git",
        "/usr/lib/git-core/git-remote-http",
    }

    openai = policy.network_policies["openai_https"]
    assert [endpoint.host for endpoint in openai.endpoints] == ["api.openai.com"]
    assert [endpoint.protocol for endpoint in openai.endpoints] == ["rest"]
    assert [endpoint.tls for endpoint in openai.endpoints] == ["terminate"]
    assert {binary.path for binary in openai.binaries} >= {
        "/usr/local/bin/codex",
        "/usr/bin/node",
    }


def test_credential_file_archive_uses_private_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _import_adapter(monkeypatch)

    payload = adapter._credential_file_archive(
        {
            "/sandbox/.codex/auth.json": b'{"tokens":{}}',
            "/home/volundr/.claude/.credentials.json": b'{"oauthAccount":{}}',
        }
    )

    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        auth = archive.getmember("sandbox/.codex/auth.json")
        claude = archive.getmember("sandbox/.claude/.credentials.json")
        assert auth.mode == 0o600
        assert claude.mode == 0o600
        assert archive.getmember("sandbox/.codex").mode == 0o700
        assert archive.extractfile(auth).read() == b'{"tokens":{}}'


def test_credential_file_archives_extract_within_writable_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)

    payloads = dict(
        adapter._credential_file_archives(
            {
                "/sandbox/.volundr/skuld.yaml": b"session: {}\n",
                "/run/secrets/tool/token": b"secret",
            }
        )
    )

    with tarfile.open(fileobj=io.BytesIO(payloads["/sandbox"]), mode="r:") as archive:
        assert archive.getnames() == [".volundr", ".volundr/skuld.yaml"]
    with tarfile.open(fileobj=io.BytesIO(payloads["/run/secrets"]), mode="r:") as archive:
        assert archive.getnames() == ["tool", "tool/token"]


def test_credential_file_path_rejects_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _import_adapter(monkeypatch)

    with pytest.raises(RuntimeError, match="outside allowed roots"):
        adapter._sandbox_credential_path("/etc/shadow")
    with pytest.raises(RuntimeError, match="invalid"):
        adapter._sandbox_credential_path("/sandbox/../../etc/shadow")


@pytest.mark.asyncio
async def test_credential_grant_binds_svid_sandbox_provider_session_and_openbao(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    session = _session()
    sandbox_id = str(uuid4())
    provider_name = f"volundr-{session.id.hex[:12]}-0123456789ab"
    audience = f"{adapter.GRANT_AUDIENCE_PREFIX}{provider_name}"
    client = _FakeOpenShellGatewayClient(adapter)
    client.grant_sandbox = adapter.OpenShellSandbox(
        id=sandbox_id,
        name="forge-test",
        phase=adapter.openshell_pb2.SANDBOX_PHASE_READY,
        labels={"volundr.niuu.io/session": str(session.id)},
        providers=(provider_name,),
    )
    client.grant_provider = types.SimpleNamespace(
        type=provider_name,
        config={
            "volundr_session_id": str(session.id),
            "volundr_credential_name": "openai-cred",
            "volundr_credential_field": "api_key",
        },
    )

    class Credential:
        token_grant = types.SimpleNamespace(audience=audience)

        def HasField(self, name: str) -> bool:  # noqa: N802
            return name == "token_grant"

    client.grant_profile = types.SimpleNamespace(credentials=[Credential()])
    manager = adapter.OpenShellGatewayPodManager(client=client)
    manager.set_session_repository(_FakeSessionRepository(session))
    manager.set_credential_store(
        _FakeCredentialStore({"openai-cred": {"api_key": "sk-from-openbao"}})
    )

    class Verifier:
        async def verify(self, _token: str):
            return {"sub": f"{adapter.DEFAULT_SPIFFE_SUBJECT_PREFIX}{sandbox_id}"}

    manager._spiffe_verifier = Verifier()

    token = await manager.exchange_credential_grant(
        client_assertion="signed-svid",
        client_assertion_type=adapter.OAUTH_CLIENT_ASSERTION_TYPE,
        grant_type="client_credentials",
        audience=audience,
        scope="",
    )

    assert token.access_token == "sk-from-openbao"
    assert token.expires_in == 300


@pytest.mark.asyncio
async def test_platform_grant_mints_session_bound_workload_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    session = _session()
    sandbox_id = str(uuid4())
    provider_name = f"volundr-{session.id.hex[:12]}-platform"
    audience = f"{adapter.PLATFORM_GRANT_AUDIENCE_PREFIX}{provider_name}"
    client = _FakeOpenShellGatewayClient(adapter)
    client.grant_sandbox = adapter.OpenShellSandbox(
        id=sandbox_id,
        name="forge-test",
        phase=adapter.openshell_pb2.SANDBOX_PHASE_READY,
        labels={"volundr.niuu.io/session": str(session.id)},
        providers=(provider_name,),
    )
    client.grant_provider = types.SimpleNamespace(
        type=provider_name,
        config={
            "volundr_session_id": str(session.id),
            "volundr_grant_kind": "platform",
        },
    )

    class Credential:
        token_grant = types.SimpleNamespace(audience=audience)

        def HasField(self, name: str) -> bool:  # noqa: N802
            return name == "token_grant"

    client.grant_profile = types.SimpleNamespace(credentials=[Credential()])
    issuer = _FakeWorkloadTokenIssuer()
    manager = adapter.OpenShellGatewayPodManager(client=client)
    manager.set_session_repository(_FakeSessionRepository(session))
    manager.set_workload_token_issuer(issuer)

    class Verifier:
        async def verify(self, _token: str):
            return {"sub": f"{adapter.DEFAULT_SPIFFE_SUBJECT_PREFIX}{sandbox_id}"}

    manager._spiffe_verifier = Verifier()

    token = await manager.exchange_credential_grant(
        client_assertion="signed-svid",
        client_assertion_type=adapter.OAUTH_CLIENT_ASSERTION_TYPE,
        grant_type="client_credentials",
        audience=audience,
        scope="",
    )

    assert token.access_token == "volundr-workload-token"
    assert token.expires_in > 800
    assert issuer.requests[0]["principal"].user_id == session.owner_id
    assert issuer.requests[0]["token_use"] == "openshell_session"
    assert issuer.requests[0]["claims"] == {
        "session_id": str(session.id),
        "sandbox_id": sandbox_id,
    }


@pytest.mark.asyncio
async def test_platform_grant_mints_resident_bound_workload_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    runtime = _resident_runtime()
    sandbox_id = str(uuid4())
    provider_name = f"volundr-{runtime.id.hex[:12]}-platform"
    audience = f"{adapter.PLATFORM_GRANT_AUDIENCE_PREFIX}{provider_name}"
    client = _FakeOpenShellGatewayClient(adapter)
    client.grant_sandbox = adapter.OpenShellSandbox(
        id=sandbox_id,
        name="resident-test",
        phase=adapter.openshell_pb2.SANDBOX_PHASE_READY,
        labels={"volundr.niuu.io/resident": str(runtime.id)},
        providers=(provider_name,),
    )
    client.grant_provider = types.SimpleNamespace(
        type=provider_name,
        config={
            "volundr_subject_kind": "resident",
            "volundr_subject_id": str(runtime.id),
            "volundr_grant_kind": "platform",
        },
    )

    class Credential:
        token_grant = types.SimpleNamespace(audience=audience)

        def HasField(self, name: str) -> bool:  # noqa: N802
            return name == "token_grant"

    client.grant_profile = types.SimpleNamespace(credentials=[Credential()])
    issuer = _FakeWorkloadTokenIssuer()
    manager = adapter.OpenShellGatewayPodManager(
        client=client,
        workload_audiences=["volundr-api", "mimir", "guild"],
    )
    manager.set_resident_runtime_repository(_FakeResidentRuntimeRepository(runtime))
    manager.set_workload_token_issuer(issuer)

    class Verifier:
        async def verify(self, _token: str):
            return {"sub": f"{adapter.DEFAULT_SPIFFE_SUBJECT_PREFIX}{sandbox_id}"}

    manager._spiffe_verifier = Verifier()

    token = await manager.exchange_credential_grant(
        client_assertion="signed-svid",
        client_assertion_type=adapter.OAUTH_CLIENT_ASSERTION_TYPE,
        grant_type="client_credentials",
        audience=audience,
        scope="",
    )

    assert token.access_token == "volundr-workload-token"
    assert issuer.requests[0]["principal"].user_id == runtime.owner_id
    assert issuer.requests[0]["audiences"] == ["volundr-api", "mimir", "guild"]
    assert issuer.requests[0]["token_use"] == "openshell_resident"
    assert issuer.requests[0]["claims"] == {
        "resident_id": str(runtime.id),
        "sandbox_id": sandbox_id,
    }


@pytest.mark.asyncio
async def test_codex_credential_grant_reads_access_token_from_openbao_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    session = _session()
    sandbox_id = str(uuid4())
    provider_name = f"volundr-{session.id.hex[:12]}-codexgrant"
    audience = f"{adapter.GRANT_AUDIENCE_PREFIX}{provider_name}"
    client = _FakeOpenShellGatewayClient(adapter)
    client.grant_sandbox = adapter.OpenShellSandbox(
        id=sandbox_id,
        name="forge-test",
        phase=adapter.openshell_pb2.SANDBOX_PHASE_READY,
        labels={"volundr.niuu.io/session": str(session.id)},
        providers=(provider_name,),
    )
    client.grant_provider = types.SimpleNamespace(
        type=provider_name,
        config={
            "volundr_session_id": str(session.id),
            "volundr_credential_name": "codex-credentials",
            "volundr_credential_field": "auth.json",
            "volundr_credential_format": adapter.CODEX_AUTH_FORMAT,
        },
    )

    class Credential:
        token_grant = types.SimpleNamespace(audience=audience)

        def HasField(self, name: str) -> bool:  # noqa: N802
            return name == "token_grant"

    client.grant_profile = types.SimpleNamespace(credentials=[Credential()])
    store = _FakeCredentialStore(
        {"codex-credentials": {"auth.json": _codex_auth_document(expires_in=3600)}}
    )
    manager = adapter.OpenShellGatewayPodManager(client=client)
    manager.set_session_repository(_FakeSessionRepository(session))
    manager.set_credential_store(store)

    class Verifier:
        async def verify(self, _token: str):
            return {"sub": f"{adapter.DEFAULT_SPIFFE_SUBJECT_PREFIX}{sandbox_id}"}

    manager._spiffe_verifier = Verifier()
    expected_access_token = json.loads(store.values["codex-credentials"]["auth.json"])["tokens"][
        "access_token"
    ]

    token = await manager.exchange_credential_grant(
        client_assertion="signed-svid",
        client_assertion_type=adapter.OAUTH_CLIENT_ASSERTION_TYPE,
        grant_type="client_credentials",
        audience=audience,
        scope="",
    )

    assert token.access_token == expected_access_token
    assert token.expires_in > 3000
    assert store.stores == []


@pytest.mark.asyncio
async def test_codex_credential_grant_refreshes_and_persists_rotated_oauth_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    session = _session()
    sandbox_id = str(uuid4())
    provider_name = f"volundr-{session.id.hex[:12]}-codexgrant"
    audience = f"{adapter.GRANT_AUDIENCE_PREFIX}{provider_name}"
    client = _FakeOpenShellGatewayClient(adapter)
    client.grant_sandbox = adapter.OpenShellSandbox(
        id=sandbox_id,
        name="forge-test",
        phase=adapter.openshell_pb2.SANDBOX_PHASE_READY,
        labels={"volundr.niuu.io/session": str(session.id)},
        providers=(provider_name,),
    )
    client.grant_provider = types.SimpleNamespace(
        type=provider_name,
        config={
            "volundr_session_id": str(session.id),
            "volundr_credential_name": "codex-credentials",
            "volundr_credential_field": "auth.json",
            "volundr_credential_format": adapter.CODEX_AUTH_FORMAT,
        },
    )

    class Credential:
        token_grant = types.SimpleNamespace(audience=audience)

        def HasField(self, name: str) -> bool:  # noqa: N802
            return name == "token_grant"

    client.grant_profile = types.SimpleNamespace(credentials=[Credential()])
    store = _FakeCredentialStore(
        {
            "codex-credentials": {
                "auth.json": _codex_auth_document(expires_in=-60),
                "config.toml": 'model = "gpt-5"',
            }
        }
    )
    refreshed_access_token = jwt.encode(
        {"sub": "user", "exp": int(time.time()) + 7200},
        key="",
        algorithm="none",
    )

    class OAuthResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "access_token": refreshed_access_token,
                "refresh_token": "rotated-refresh-token",
                "id_token": "rotated-id-token",
            }

    class OAuthClient:
        def __init__(self) -> None:
            self.posts: list[dict] = []

        async def post(self, url: str, data: dict):
            self.posts.append({"url": url, "data": dict(data)})
            return OAuthResponse()

    oauth_client = OAuthClient()
    manager = adapter.OpenShellGatewayPodManager(
        client=client,
        codex_oauth_client=oauth_client,
    )
    manager.set_session_repository(_FakeSessionRepository(session))
    manager.set_credential_store(store)

    class Verifier:
        async def verify(self, _token: str):
            return {"sub": f"{adapter.DEFAULT_SPIFFE_SUBJECT_PREFIX}{sandbox_id}"}

    manager._spiffe_verifier = Verifier()

    token = await manager.exchange_credential_grant(
        client_assertion="signed-svid",
        client_assertion_type=adapter.OAUTH_CLIENT_ASSERTION_TYPE,
        grant_type="client_credentials",
        audience=audience,
        scope="",
    )

    assert token.access_token == refreshed_access_token
    assert token.expires_in > 7000
    assert oauth_client.posts == [
        {
            "url": adapter.DEFAULT_CODEX_OAUTH_TOKEN_URL,
            "data": {
                "grant_type": "refresh_token",
                "client_id": adapter.DEFAULT_CODEX_OAUTH_CLIENT_ID,
                "refresh_token": "refresh-from-openbao",
            },
        }
    ]
    assert len(store.stores) == 1
    persisted = json.loads(store.stores[0]["data"]["auth.json"])
    assert persisted["tokens"]["access_token"] == refreshed_access_token
    assert persisted["tokens"]["refresh_token"] == "rotated-refresh-token"
    assert persisted["tokens"]["id_token"] == "rotated-id-token"
    assert store.stores[0]["data"]["config.toml"] == 'model = "gpt-5"'


@pytest.mark.asyncio
async def test_credential_grant_rejects_provider_not_attached_to_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    sandbox_id = str(uuid4())
    client = _FakeOpenShellGatewayClient(adapter)
    client.grant_sandbox = adapter.OpenShellSandbox(
        id=sandbox_id,
        name="forge-test",
        phase=adapter.openshell_pb2.SANDBOX_PHASE_READY,
        providers=(),
    )
    manager = adapter.OpenShellGatewayPodManager(client=client)

    class Verifier:
        async def verify(self, _token: str):
            return {"sub": f"{adapter.DEFAULT_SPIFFE_SUBJECT_PREFIX}{sandbox_id}"}

    manager._spiffe_verifier = Verifier()

    with pytest.raises(ValueError, match="not attached"):
        await manager.exchange_credential_grant(
            client_assertion="signed-svid",
            client_assertion_type=adapter.OAUTH_CLIENT_ASSERTION_TYPE,
            grant_type="client_credentials",
            audience=f"{adapter.GRANT_AUDIENCE_PREFIX}volundr-session-grant",
            scope="",
        )
