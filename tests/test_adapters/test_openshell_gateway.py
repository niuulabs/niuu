"""Tests for the native OpenShell gateway PodManager."""

from __future__ import annotations

import importlib
import io
import sys
import tarfile
import types
from uuid import uuid4

import pytest

from volundr.domain.models import GitSource, PodSpecAdditions, Session, SessionSpec, SessionStatus


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


class _FakeCredentialStore:
    def __init__(self, values: dict[str, dict[str, str]]) -> None:
        self.values = values
        self.gets: list[tuple[str, str, str]] = []

    async def get_value(self, owner_type: str, owner_id: str, name: str):
        self.gets.append((owner_type, owner_id, name))
        return self.values.get(name)

    async def get(self, owner_type: str, owner_id: str, name: str):
        values = self.values.get(name)
        if values is None:
            return None
        return types.SimpleNamespace(keys=tuple(values))


class _FakeSessionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    async def get(self, session_id):
        return self.session if session_id == self.session.id else None


def _session() -> Session:
    return Session(
        id=uuid4(),
        name="OpenShell Forge",
        model="claude-sonnet-4-20250514",
        source=GitSource(repo="https://github.com/niuulabs/volundr", branch="dev"),
        owner_id="owner-1",
        tenant_id="tenant-1",
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
        grant["config"]["volundr_session_id"] == str(session.id)
        for grant in client.provider_grants
    )

    assert await manager.stop(session) is True


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
async def test_start_projects_openbao_file_mapping_into_sandbox_home(
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = _import_adapter(monkeypatch)
    session = _session()
    client = _FakeOpenShellGatewayClient(adapter)
    manager = adapter.OpenShellGatewayPodManager(client=client, ready_timeout=0.1)
    manager.set_credential_store(
        _FakeCredentialStore({"claude-home": {"credentials.json": '{"oauthAccount":{}}'}})
    )
    spec = SessionSpec(
        values={
            "openshell": {
                "credentialMappings": [
                    {
                        "credentialName": "claude-home",
                        "fileMappings": {
                            "/home/volundr/.claude/.credentials.json": "credentials.json"
                        },
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
            "files": {
                "/sandbox/.claude/.credentials.json": b'{"oauthAccount":{}}'
            },
        }
    ]


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
