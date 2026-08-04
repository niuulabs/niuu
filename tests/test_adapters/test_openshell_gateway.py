"""Tests for the native OpenShell gateway PodManager."""

from __future__ import annotations

import base64
import importlib
import io
import json
import socket
import sys
import tarfile
import time
import types
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import jwt
import pytest

from niuu.ports.workload_identity import IssuedWorkloadToken
from volundr.adapters.outbound.hermes_gateway import (
    HERMES_CREDENTIAL_NAME,
    HERMES_LEGACY_CREDENTIAL_NAME,
)
from volundr.domain.models import (
    CredentialEnrollment,
    CredentialEnrollmentState,
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

        def HasField(self, name):  # noqa: N802 - protobuf compatibility shim.
            return getattr(self, name, None) is not None

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
        "CreateSshSessionRequest",
        "RevokeSshSessionRequest",
        "TcpForwardFrame",
        "TcpForwardInit",
        "TcpRelayTarget",
        "ExecSandboxRequest",
        "ProviderProfileCredential",
        "ProviderCredentialTokenGrant",
        "ProviderProfile",
        "ProviderProfileImportItem",
        "ImportProviderProfilesRequest",
        "CreateProviderRequest",
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
        self.forwarded: list[dict] = []
        self.closed_forwarders: list[dict] = []
        self.deleted: list[str] = []
        self.deleted_services: list[dict] = []
        self.provider_grants: list[dict] = []
        self.deleted_grants: list[object] = []
        self.delete_polls_remaining = 0
        self.cleanup_events: list[str] = []
        self.written_files: list[dict] = []
        self.providers_v2_enabled = False
        self.service_url = "http://openshell.example/proxy/session-1"
        self.grant_sandbox = None
        self.grant_provider = None
        self.grant_profile = None
        self.provider_environment = {}
        self.closed = False
        self.sandbox_exists = True
        self.sandbox_labels: dict[str, str] = {}

    def create_sandbox(self, **kwargs):
        self.created = kwargs
        self.sandbox_exists = True
        self.sandbox_labels = dict(kwargs.get("labels") or {})
        return self._adapter.OpenShellSandbox(
            id="sandbox-id",
            name=kwargs["name"],
            phase=self._adapter.openshell_pb2.SANDBOX_PHASE_PROVISIONING,
        )

    def get_sandbox(self, name: str):
        if name in self.deleted:
            if self.delete_polls_remaining:
                self.delete_polls_remaining -= 1
                self.cleanup_events.append("sandbox-present")
            else:
                self.cleanup_events.append("sandbox-gone")
                return None
        if not self.sandbox_exists:
            return None
        return self._adapter.OpenShellSandbox(
            id="sandbox-id",
            name=name,
            phase=self._adapter.openshell_pb2.SANDBOX_PHASE_READY,
            ready=True,
            labels=self.sandbox_labels,
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

    def start_tcp_forward(self, **kwargs):
        self.forwarded.append(kwargs)
        closed_forwarders = self.closed_forwarders

        class _Forwarder:
            host = "127.0.0.1"
            port = 43210

            def close(self) -> None:
                closed_forwarders.append(kwargs)

        return _Forwarder()

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
        self.cleanup_events.append("grant-deleted")
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
        self.deletes: list[tuple[str, str, str]] = []

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

    async def delete(self, owner_type: str, owner_id: str, name: str) -> bool:
        self.deletes.append((owner_type, owner_id, name))
        self.values.pop(name, None)
        return True


def test_native_tcp_forward_uses_authenticated_sandbox_relay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    requests: list[object] = []

    def create_ssh_session(request, **_kwargs):
        requests.append(request)
        return types.SimpleNamespace(token="short-lived-forward-token")

    def forward_tcp(frames, **_kwargs):
        requests.extend(list(frames))
        return [adapter.openshell_pb2.TcpForwardFrame(data=b"HTTP/1.1 204 OK\r\n\r\n")]

    def revoke_ssh_session(request, **_kwargs):
        requests.append(request)

    client = object.__new__(adapter.OpenShellGatewayClient)
    client._stub = types.SimpleNamespace(
        CreateSshSession=create_ssh_session,
        ForwardTcp=forward_tcp,
        RevokeSshSession=revoke_ssh_session,
    )
    client._timeout = 30.0
    client._metadata = lambda: (("authorization", "Bearer oidc"),)
    bridge, caller = socket.socketpair()
    caller.sendall(b"GET /health HTTP/1.1\r\nHost: hermes\r\n\r\n")
    caller.shutdown(socket.SHUT_WR)

    client._forward_tcp_connection(bridge, sandbox_id="sandbox-id", target_port=18789)

    assert caller.recv(1024) == b"HTTP/1.1 204 OK\r\n\r\n"
    init = requests[1].init
    assert init.sandbox_id == "sandbox-id"
    assert init.tcp.host == "127.0.0.1"
    assert init.tcp.port == 18789
    assert init.authorization_token == "short-lived-forward-token"
    assert requests[2].data.startswith(b"GET /health")
    assert requests[3].token == "short-lived-forward-token"
    caller.close()
    bridge.close()


def test_openshell_uses_shared_kubernetes_credential_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    brokered = importlib.import_module("volundr.adapters.outbound.brokered_credentials")

    assert issubclass(
        adapter.OpenShellGatewayPodManager,
        brokered.BrokeredCredentialPodManager,
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


def _credential_enrollment() -> CredentialEnrollment:
    now = datetime.now(UTC)
    return CredentialEnrollment(
        id=uuid4(),
        connection_id=str(uuid4()),
        owner_id="owner-1",
        tenant_id="tenant-1",
        provider_slug="codex",
        credential_name="codex-credentials",
        method="codex_device",
        state=CredentialEnrollmentState.PENDING,
        runner_ref={},
        verification_uri="",
        user_code="",
        expires_at=now + timedelta(minutes=15),
        error_code="",
        created_at=now,
        updated_at=now,
    )


def test_parses_codex_device_login_challenge_without_ansi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    output = (
        "\x1b[1mOpen https://auth.openai.com/codex/device in your browser\x1b[0m\n"
        "Enter code ABCD-EFGH\n"
    )

    assert adapter._parse_codex_device_challenge(output) == (
        "https://auth.openai.com/codex/device",
        "ABCD-EFGH",
    )


def test_codex_challenge_ignores_the_codex_home_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex names its CODEX_HOME before printing the code."""
    adapter = _import_adapter(monkeypatch)
    output = (
        "WARNING: proceeding, even though we could not create PATH aliases: "
        'Refusing to create helper binaries under temporary dir "/tmp" '
        '(codex_home: AbsolutePathBuf("/tmp/niuu-codex-enrollment"))\n'
        "\n"
        "Follow these steps to sign in with ChatGPT using device code authorization:\n"
        "\n"
        "1. Open this link in your browser and sign in to your account\n"
        "   \x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n"
        "\n"
        "2. Enter this one-time code \x1b[90m(expires in 15 minutes)\x1b[0m\n"
        "   \x1b[94m78YQ-3RG20\x1b[0m\n"
    )

    assert adapter._parse_codex_device_challenge(output) == (
        "https://auth.openai.com/codex/device",
        "78YQ-3RG20",
    )


@pytest.mark.asyncio
async def test_codex_device_enrollment_runs_in_workspace_free_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    client = _FakeOpenShellGatewayClient(adapter)
    outputs = iter(
        [
            (0, ""),
            (
                0,
                "Open https://auth.openai.com/codex/device\nEnter code ABCD-EFGH\n",
            ),
        ]
    )

    def exec_script(**kwargs):
        client.bootstrap_execs.append(kwargs)
        return next(outputs)

    client.exec_script = exec_script
    manager = adapter.OpenShellGatewayPodManager(client=client)
    enrollment = _credential_enrollment()

    started = await manager.start_enrollment(enrollment)

    assert started.state == CredentialEnrollmentState.AWAITING_USER
    assert started.verification_uri == "https://auth.openai.com/codex/device"
    assert started.user_code == "ABCD-EFGH"
    assert client.created is not None
    assert client.created["providers"] == (started.runner_ref["provider_name"],)
    assert "workspace" not in client.created
    assert "owner-1" not in str(client.created["labels"])
    assert client.provider_grants[0]["profile"].credentials == []
    assert client.execs[0]["command"] == ("codex", "login", "--device-auth")


@pytest.mark.asyncio
async def test_codex_device_enrollment_returns_auth_document_only_when_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    client = _FakeOpenShellGatewayClient(adapter)
    auth_document = _codex_auth_document()
    encoded = base64.b64encode(auth_document.encode()).decode()
    client.exec_script = lambda **_kwargs: (0, f"complete\n{encoded}")
    manager = adapter.OpenShellGatewayPodManager(client=client)
    enrollment = replace(
        _credential_enrollment(),
        state=CredentialEnrollmentState.AWAITING_USER,
        runner_ref={
            "sandbox_id": "sandbox-id",
            "sandbox_name": "enroll-1",
            "provider_name": "provider-1",
            "profile_id": "provider-1",
        },
    )

    result = await manager.poll_enrollment(enrollment)

    assert result.state == CredentialEnrollmentState.COMPLETE
    assert result.credential_data == {"auth.json": auth_document}

    await manager.cancel_enrollment(enrollment)

    assert client.deleted == ["enroll-1"]
    assert len(client.deleted_grants) == 1


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
                    "skipPermissions": True,
                },
                "session": {"reasoningEffort": "high"},
                "openshell": {},
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


def test_resident_ravn_config_uses_profile_selected_flock_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    flock_id = uuid4()
    member_id = uuid4()
    runtime = _resident_runtime().model_copy(
        update={
            "flock_id": flock_id,
            "flock_member_id": member_id,
            "flock_role": "coordinator",
            "flock_peer_id": f"ravn-{member_id}",
            "capabilities": [*_resident_runtime().capabilities, ResidentCapability.FLOCK],
        }
    )
    values = _resident_profile().deployment["values"]
    values["resident"]["flock"] = {
        "mesh": {
            "adapters": [{"adapter": "sleipnir", "transport": "nats"}],
            "nats": {
                "servers": ["tls://nats.example.test:4222"],
                "user_env": "RAVN_NATS_USER",
                "password_env": "RAVN_NATS_PASSWORD",
                "tls_ca_pem": "-----BEGIN CERTIFICATE-----\nproof\n-----END CERTIFICATE-----\n",
            },
        },
        "discovery": {
            "adapters": [{"adapter": "event_bus", "transport": "nats"}],
        },
    }
    manager = adapter.OpenShellGatewayPodManager(client=_FakeOpenShellGatewayClient(adapter))
    missing_transport = _resident_profile().model_copy(
        update={"capabilities": [*_resident_profile().capabilities, ResidentCapability.FLOCK]}
    )
    configured = missing_transport.model_copy(update={"deployment": {"values": values}})

    config = adapter._resident_ravn_config(runtime, values, 9200)
    skuld_config = adapter._resident_skuld_config(
        runtime,
        values,
        9200,
        "https://volundr.example.test",
    )

    assert manager.supports(missing_transport) is False
    assert manager.supports(configured) is True
    assert config["mesh"]["own_peer_id"] == f"ravn-{member_id}"
    assert config["mesh"]["adapters"] == [{"adapter": "sleipnir", "transport": "nats"}]
    assert config["mesh"]["nats"]["user_env"] == "RAVN_NATS_USER"
    assert config["mesh"]["nats"]["tls_ca_pem"].startswith("-----BEGIN CERTIFICATE-----")
    assert config["discovery"]["realm_id"] == str(flock_id)
    assert config["discovery"]["adapters"][-1] == {
        "adapter": "event_bus",
        "transport": "nats",
    }
    assert skuld_config["mesh"]["realm_id"] == str(flock_id)
    assert skuld_config["mesh"]["adapters"] == [{"adapter": "sleipnir", "transport": "nats"}]
    assert skuld_config["mesh"]["discovery_adapters"][-1] == {
        "adapter": "event_bus",
        "transport": "nats",
    }
    assert skuld_config["mesh"]["nats"]["user_env"] == "RAVN_NATS_USER"
    assert "max_participants" not in skuld_config["room"]


def _hermes_runtime() -> ResidentRuntime:
    return _resident_runtime().model_copy(
        update={
            "engine": ResidentEngine.HERMES,
            "profile_id": "nemohermes-openshell",
            "capabilities": [
                ResidentCapability.CHAT,
                ResidentCapability.SESSION_LIST,
                ResidentCapability.SESSION_CREATE,
                ResidentCapability.SESSION_DELETE,
                ResidentCapability.STEER,
                ResidentCapability.INTERRUPT,
                ResidentCapability.APPROVALS,
                ResidentCapability.RUNTIME_RESTART,
                ResidentCapability.LOGS,
                ResidentCapability.USAGE,
            ],
        }
    )


def _hermes_profile() -> ResidentDeploymentProfile:
    return ResidentDeploymentProfile(
        id="nemohermes-openshell",
        display_name="Hermes on OpenShell",
        backend=ResidentBackend.OPENSHELL,
        engine=ResidentEngine.HERMES,
        capabilities=_hermes_runtime().capabilities,
        default_model="gpt-5.6-sol",
        allowed_models=["gpt-5.6-sol"],
        deployment={
            "values": {
                "image": (
                    "ghcr.io/nvidia/nemoclaw/hermes-sandbox-base@"
                    "sha256:7e9378c50f291e6dd80b922e8b89e0e7edf21e4e3a80b8c2664be01976f59aa8"
                ),
                "resident": {
                    "llm": {
                        "provider": {
                            "kwargs": {"base_url": "http://bifrost.volundr.svc.cluster.local/v1"}
                        }
                    },
                    "platform": {
                        "enabled": True,
                        "baseUrl": "https://platform.example.test",
                    },
                },
                "openshell": {
                    "processMode": "replace",
                    "service": {"name": "hermes", "port": 18789},
                    "processes": [
                        {
                            "name": "hermes",
                            "command": [
                                "/opt/hermes/.venv/bin/python",
                                "/opt/hermes/.venv/bin/hermes",
                                "dashboard",
                                "--host",
                                "127.0.0.1",
                                "--port",
                                "18789",
                                "--skip-build",
                                "--no-open",
                                "--tui",
                            ],
                            "logPath": "/sandbox/.volundr/hermes.log",
                        }
                    ],
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


def test_openclaw_profile_requires_complete_process_and_service_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    manager = adapter.OpenShellGatewayPodManager(client=_FakeOpenShellGatewayClient(adapter))
    profile = ResidentDeploymentProfile(
        id="nemoclaw-openshell",
        display_name="NemoClaw",
        backend=ResidentBackend.OPENSHELL,
        engine=ResidentEngine.OPENCLAW,
        capabilities=[ResidentCapability.CHAT, ResidentCapability.SESSION_LIST],
        deployment={"values": {"openshell": {}}},
    )
    assert manager.supports(profile) is False

    complete = profile.model_copy(
        update={
            "deployment": {
                "values": {
                    "openshell": {
                        "processMode": "replace",
                        "service": {"name": "openclaw", "port": 18789},
                        "processes": [
                            {
                                "name": "openclaw",
                                "command": ["openclaw", "gateway", "run"],
                                "logPath": "/sandbox/.volundr/openclaw.log",
                            }
                        ],
                    }
                }
            }
        }
    )
    assert manager.supports(complete) is True
    assert adapter._resident_platform_binaries(
        _resident_runtime().model_copy(update={"engine": ResidentEngine.OPENCLAW})
    ) == ("/usr/bin/node",)


def test_hermes_profile_requires_complete_replace_process_and_service_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    manager = adapter.OpenShellGatewayPodManager(client=_FakeOpenShellGatewayClient(adapter))
    profile = _hermes_profile()

    assert manager.supports(profile) is True
    assert (
        manager.supports(profile.model_copy(update={"deployment": {"values": {"openshell": {}}}}))
        is False
    )
    assert adapter._resident_platform_binaries(_hermes_runtime()) == (
        "/opt/hermes/**",
        "/usr/bin/python3",
    )


@pytest.mark.asyncio
async def test_hermes_deploy_uses_persisted_process_only_credential_and_generic_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    runtime = _hermes_runtime()
    profile = _hermes_profile()
    client = _FakeOpenShellGatewayClient(adapter)
    client.sandbox_exists = False
    store = _FakeCredentialStore({})
    manager = adapter.OpenShellGatewayPodManager(
        client=client,
        ready_timeout=0.1,
        volundr_api_url="https://volundr.example.test",
    )
    manager.set_credential_store(store)
    manager.set_workload_token_issuer(_FakeWorkloadTokenIssuer())

    observation = await manager.deploy(runtime, profile)

    assert observation.observed_state is ResidentObservedState.ACTIVE
    assert observation.endpoints[0].protocol == "hermes-api-server-v1"
    assert observation.backend_ref["process_names"] == ["hermes"]
    assert client.created is not None
    assert client.created["policy"] is manager._sandbox_policy
    assert client.created["env"]["HERMES_HOME"] == "/sandbox/workspace/.hermes"
    assert adapter.HERMES_API_SERVER_KEY_ENV not in client.created["env"]
    assert client.execs[0]["env"][adapter.HERMES_API_SERVER_KEY_ENV]
    assert client.execs[0]["env"]["HERMES_HOME"] == "/sandbox/workspace/.hermes"
    assert client.execs[0]["pid_path"] == "/sandbox/.volundr/hermes.pid"
    assert client.exposed is None
    assert store.stores[0]["name"] == HERMES_CREDENTIAL_NAME
    assert store.stores[0]["owner_id"] == str(runtime.id)
    hermes_config = client.written_files[0]["files"][
        "/sandbox/workspace/.hermes/config.yaml"
    ].decode()
    assert "provider: custom:niuu" in hermes_config
    assert "default: gpt-5.6-sol" in hermes_config
    assert "model: gpt-5.6-sol" in hermes_config
    assert "niuu/gpt-5.6-sol" not in hermes_config
    assert "key_env: NIUU_VOLUNDR_ACCESS_TOKEN" in hermes_config
    assert "api_key:" not in hermes_config
    assert f"X-Agent-ID: {runtime.id}" in hermes_config
    assert "X-Tenant-ID: tenant-1" in hermes_config
    assert f"X-Session-ID: {runtime.id}" in hermes_config
    assert "api_server:" in hermes_config
    assert "port: 18789" in hermes_config
    assert "approvals:\n  mode: 'off'" in hermes_config
    assert any(
        binary.path == "/opt/hermes/**"
        for grant in client.provider_grants
        for binary in grant["profile"].binaries
    )
    health = client.bootstrap_execs[-1]["script"]
    assert "/sandbox/.volundr/hermes.pid" in health
    assert "/:4965$/" in health

    deployed_runtime = runtime.model_copy(update={"backend_ref": observation.backend_ref})
    target = manager.resident_proxy_target(deployed_runtime)
    assert target == adapter.SessionProxyTarget(
        service_url=adapter.HERMES_INTERNAL_SERVICE_URL,
        connect_host="127.0.0.1",
        connect_port=43210,
    )
    assert client.forwarded == [{"sandbox_id": "sandbox-id", "target_port": 18789}]


@pytest.mark.asyncio
async def test_hermes_restart_reuses_machine_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    runtime = _hermes_runtime().model_copy(
        update={
            "backend_ref": {
                "service_url": "https://hermes.example.test",
                "service_name": "hermes",
                "process_names": ["hermes"],
            }
        }
    )
    client = _FakeOpenShellGatewayClient(adapter)
    token = "persisted-hermes-token"
    manager = adapter.OpenShellGatewayPodManager(client=client, ready_timeout=0.1)
    manager.set_credential_store(_FakeCredentialStore({HERMES_CREDENTIAL_NAME: {"api_key": token}}))

    observation = await manager.restart(runtime, _hermes_profile())

    assert observation.observed_state is ResidentObservedState.ACTIVE
    assert client.deleted == []
    assert client.bootstrap_execs[0]["script"] == adapter._resident_stop_script(("hermes",))
    assert client.execs[0]["env"][adapter.HERMES_API_SERVER_KEY_ENV] == token


@pytest.mark.asyncio
async def test_hermes_rollback_and_delete_cleanup_machine_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    runtime = _hermes_runtime()
    profile = _hermes_profile()
    client = _FakeOpenShellGatewayClient(adapter)
    client.sandbox_exists = False
    client.exec_detached = lambda **_kwargs: 1
    store = _FakeCredentialStore({})
    manager = adapter.OpenShellGatewayPodManager(client=client, ready_timeout=0.1)
    manager.set_credential_store(store)

    with pytest.raises(RuntimeError, match="Hermes residents require"):
        await adapter.OpenShellGatewayPodManager(client=client).deploy(runtime, profile)
    with pytest.raises(RuntimeError, match="failed with exit 1"):
        await manager.deploy(runtime, profile)

    credential_key = ("resident", str(runtime.id), HERMES_CREDENTIAL_NAME)
    assert store.deletes == [credential_key]

    client.exec_detached = _FakeOpenShellGatewayClient.exec_detached.__get__(client)
    client.deleted.clear()
    client.created = {"providers": ()}
    store.values[HERMES_CREDENTIAL_NAME] = {"api_key": "delete-me"}
    deleted = await manager.delete(runtime)
    assert deleted
    assert store.deletes == [
        credential_key,
        credential_key,
        ("resident", str(runtime.id), HERMES_LEGACY_CREDENTIAL_NAME),
    ]


@pytest.mark.asyncio
async def test_hermes_logs_default_to_hermes_process_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    client = _FakeOpenShellGatewayClient(adapter)
    client.created = {"providers": ()}
    manager = adapter.OpenShellGatewayPodManager(client=client)

    await manager.logs(_hermes_runtime(), lines=20, sources=(), min_level="INFO")

    assert "__VOLUNDR_LOG_SOURCE__=hermes" in client.bootstrap_execs[0]["script"]


def test_platform_provider_adds_resident_engine_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)

    profile = adapter._platform_provider_profile(
        profile_id="resident-platform",
        token_endpoint="https://volundr.example.test/token",
        api_urls=("http://niuu-bifrost.volundr.svc.cluster.local/v1",),
        additional_binaries=("/usr/bin/node",),
    )

    assert [binary.path for binary in profile.binaries] == [
        "/opt/niuu/**",
        "/sandbox/.uv/python/**",
        "/usr/bin/node",
    ]


def test_dynamic_provider_accepts_configured_tcp_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)

    profile = adapter._provider_profile(
        profile_id="resident-nats",
        env_name="RAVN_NATS_PASSWORD",
        token_endpoint="https://volundr.example.test/token",
        target_config={
            "endpoints": [
                {
                    "host": "nats-noatun.nats.svc.cluster.local",
                    "port": 4222,
                    "tls": "skip",
                    "allowed_ips": ["10.191.72.34"],
                }
            ],
            "binaries": ["/opt/niuu/bin/python"],
        },
    )

    assert profile.endpoints[0].host == "nats-noatun.nats.svc.cluster.local"
    assert profile.endpoints[0].port == 4222
    assert profile.endpoints[0].protocol == ""
    assert profile.endpoints[0].tls == "skip"
    assert list(profile.endpoints[0].allowed_ips) == ["10.191.72.34"]
    assert [binary.path for binary in profile.binaries] == ["/opt/niuu/bin/python"]


@pytest.mark.asyncio
async def test_resident_controller_deploys_real_sandbox_and_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    runtime = _resident_runtime()
    profile = _resident_profile()
    client = _FakeOpenShellGatewayClient(adapter)
    client.sandbox_exists = False
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
    assert observation.endpoints[0].url == f"/s/{runtime.id}/session"
    assert client.created is not None
    assert client.created["image"] == ("ghcr.io/niuulabs/openshell:niu-1099-openshell-resident")
    assert client.created["labels"] == {
        "app.kubernetes.io/managed-by": "volundr",
        "volundr.niuu.io/resident": str(runtime.id),
        "volundr.niuu.io/runtime": "ravn",
    }
    assert len(client.provider_grants) == 1
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
    skuld_config = projected["/sandbox/.volundr/skuld.yaml"].decode()
    assert "gpt-5.6-sol" in ravn_config
    assert "mimir-yggdrasil" in ravn_config
    assert "token_env: NIUU_VOLUNDR_ACCESS_TOKEN" in ravn_config
    assert "Saga Planning" in ravn_config
    assert "skip_permissions: true" in skuld_config
    assert [process["pid_path"] for process in client.execs] == [
        "/sandbox/.volundr/skuld.pid",
        "/sandbox/.volundr/ravn.pid",
    ]
    assert client.execs[0]["env"]["SKULD_BOOTSTRAP_FOREGROUND"] == "true"
    assert client.execs[0]["env"]["NIUU_CONFIG"] == "/sandbox/.volundr/skuld.yaml"
    assert client.execs[0]["env"]["SKULD__CODEX_AUTH__ADAPTER"] == (
        "skuld.codex_auth.VolundrCodexAuthProvider"
    )
    assert client.execs[0]["env"]["SKULD__CODEX_AUTH__KWARGS"] == "{}"
    assert "SKULD_CONFIG" not in client.execs[0]["env"]
    assert client.execs[1]["env"]["SKULD__TRANSPORT_ADAPTER"] == (
        "skuld.transports.codex_ws.CodexWebSocketTransport"
    )
    assert client.execs[1]["env"]["SKULD__SKIP_PERMISSIONS"] == "true"


@pytest.mark.asyncio
async def test_resident_deploy_resumes_owned_sandbox_and_launches_missing_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    runtime = _resident_runtime()
    profile = _resident_profile()
    client = _FakeOpenShellGatewayClient(adapter)
    client.sandbox_labels = {"volundr.niuu.io/resident": str(runtime.id)}
    health_results = iter((0, 1, 0))

    def exec_script(**kwargs):
        client.bootstrap_execs.append(kwargs)
        return next(health_results), ""

    client.exec_script = exec_script
    manager = adapter.OpenShellGatewayPodManager(
        client=client,
        volundr_api_url="https://volundr.example.test",
        workload_audiences=["volundr-api", "mimir", "guild"],
        ready_timeout=0.1,
    )
    manager.set_credential_store(
        _FakeCredentialStore({"codex-credentials": {"auth.json": _codex_auth_document()}})
    )
    manager.set_workload_token_issuer(_FakeWorkloadTokenIssuer())

    observation = await manager.deploy(runtime, profile)

    assert observation.observed_state is ResidentObservedState.ACTIVE
    assert client.created is None
    assert [process["pid_path"] for process in client.execs] == ["/sandbox/.volundr/ravn.pid"]


@pytest.mark.asyncio
async def test_resident_deploy_rejects_unowned_existing_sandbox_without_deleting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    runtime = _resident_runtime()
    client = _FakeOpenShellGatewayClient(adapter)

    with pytest.raises(RuntimeError, match="is not owned by resident"):
        await adapter.OpenShellGatewayPodManager(client=client).deploy(
            runtime,
            _resident_profile(),
        )

    assert client.deleted == []


@pytest.mark.asyncio
async def test_resident_reconcile_recovers_missing_service_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    runtime = _resident_runtime().model_copy(
        update={
            "backend_ref": {
                "kind": "OpenShellSandbox",
                "id": "sandbox-id",
                "name": "resident-existing",
                "service_url": "",
            }
        }
    )
    client = _FakeOpenShellGatewayClient(adapter)
    manager = adapter.OpenShellGatewayPodManager(client=client)

    observation = await manager.reconcile(runtime, _resident_profile())

    assert client.exposed == {
        "sandbox_name": f"resident-{runtime.id.hex[:19]}",
        "target_port": 9200,
        "service": "skuld",
    }
    assert observation.backend_ref["service_url"] == client.service_url
    assert observation.endpoints[0].url == f"/s/{runtime.id}/session"


@pytest.mark.asyncio
async def test_hermes_reconcile_recovers_internal_service_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    runtime = _hermes_runtime()
    client = _FakeOpenShellGatewayClient(adapter)
    manager = adapter.OpenShellGatewayPodManager(client=client)

    observation = await manager.reconcile(runtime, _hermes_profile())

    assert client.exposed is None
    assert observation.backend_ref["service_url"] == adapter.HERMES_INTERNAL_SERVICE_URL
    assert observation.endpoints[0].kind == "sessions"


@pytest.mark.asyncio
async def test_resident_materializes_raw_protocol_credential_from_openbao(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    runtime = _resident_runtime()
    profile = _resident_profile()
    values = profile.deployment["values"]
    values["openshell"]["credentialMappings"] = [
        {
            "credentialName": "nats-flock-noatun",
            "envMappings": {"RAVN_NATS_PASSWORD": "password"},
            "materializeEnvironment": True,
            "provider": {
                "endpoints": [
                    {
                        "host": "10.191.72.34",
                        "port": 4222,
                        "tls": "skip",
                        "allowed_ips": ["10.191.72.34"],
                    }
                ],
                "binaries": ["/opt/niuu/bin/python"],
            },
        }
    ]
    client = _FakeOpenShellGatewayClient(adapter)
    client.sandbox_exists = False
    manager = adapter.OpenShellGatewayPodManager(client=client, ready_timeout=0.1)
    manager.set_credential_store(
        _FakeCredentialStore(
            {
                "codex-credentials": {"auth.json": _codex_auth_document()},
                "nats-flock-noatun": {"password": "nats-from-openbao"},
            }
        )
    )

    await manager.deploy(runtime, profile)

    assert client.created is not None
    assert "RAVN_NATS_PASSWORD" not in client.created["env"]
    assert client.execs[1]["env"]["RAVN_NATS_PASSWORD"] == "nats-from-openbao"
    assert any(
        grant["profile"].credentials[0].env_vars == ["RAVN_NATS_PASSWORD"]
        for grant in client.provider_grants
    )


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
    manager = adapter.OpenShellGatewayPodManager(client=client, ready_timeout=0.1)
    manager.set_credential_store(
        _FakeCredentialStore({"codex-credentials": {"auth.json": _codex_auth_document()}})
    )
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
    assert client.bootstrap_execs[0]["script"] == adapter._resident_stop_script(
        ("skuld", "ravn", "sidecar")
    )
    assert len(client.written_files) == 1
    assert set(client.written_files[0]["files"]) == {
        "/sandbox/.volundr/ravn.yaml",
        "/sandbox/.volundr/skuld.yaml",
    }
    assert client.execs[-1]["env"]["SKULD__CODEX_AUTH__ADAPTER"] == (
        "skuld.codex_auth.VolundrCodexAuthProvider"
    )
    assert client.execs[-1]["env"]["SKULD__CODEX_AUTH__KWARGS"] == "{}"
    assert client.execs[-1]["env"]["RESIDENT_MODE"] == "active"
    assert client.execs[-1]["env"]["SKULD__TRANSPORT_ADAPTER"] == (
        "skuld.transports.codex_ws.CodexWebSocketTransport"
    )
    assert client.execs[-1]["env"]["SKULD__SKIP_PERMISSIONS"] == "true"
    assert [process["pid_path"] for process in client.execs] == [
        "/sandbox/.volundr/skuld.pid",
        "/sandbox/.volundr/ravn.pid",
        "/sandbox/.volundr/sidecar.pid",
    ]


def test_resident_process_lifecycle_uses_in_sandbox_process_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)

    health = adapter._resident_health_script()
    stop = adapter._resident_stop_script()

    assert "/sandbox/.volundr/skuld.pid" in health
    assert "/sandbox/.volundr/ravn.pid" in health
    assert 'kill -0 "$(cat ' in health
    assert 'kill -9 "$(cat "$pid_file")"' in stop


def test_resident_process_health_requires_declared_service_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)

    health = adapter._resident_health_script(("openclaw",), service_port=18789)

    assert "/sandbox/.volundr/openclaw.pid" in health
    assert "/:4965$/" in health
    assert '$4 == "0A"' in health


@pytest.mark.asyncio
async def test_resident_delete_removes_service_sandbox_and_provider_grants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    runtime = _resident_runtime()
    client = _FakeOpenShellGatewayClient(adapter)
    client.created = {"providers": ("volundr-provider",)}
    client.delete_polls_remaining = 2
    monkeypatch.setattr(adapter.asyncio, "sleep", AsyncMock())
    manager = adapter.OpenShellGatewayPodManager(client=client)

    deleted = await manager.delete(runtime)
    assert deleted
    assert client.deleted_services == [
        {"sandbox_name": f"resident-{runtime.id.hex[:19]}", "service": "skuld"}
    ]
    assert client.deleted == [f"resident-{runtime.id.hex[:19]}"]
    assert [grant.provider_name for grant in client.deleted_grants] == ["volundr-provider"]
    assert client.cleanup_events == [
        "sandbox-present",
        "sandbox-present",
        "sandbox-gone",
        "grant-deleted",
    ]


@pytest.mark.asyncio
async def test_resident_delete_retains_provider_grants_until_sandbox_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    runtime = _resident_runtime()
    client = _FakeOpenShellGatewayClient(adapter)
    client.created = {"providers": ("volundr-provider",)}
    client.delete_polls_remaining = 1
    manager = adapter.OpenShellGatewayPodManager(
        client=client,
        resource_delete_timeout=0,
    )

    with pytest.raises(RuntimeError, match="was not deleted within 0s"):
        await manager.delete(runtime)

    assert client.deleted_grants == []
    assert client.cleanup_events == ["sandbox-present"]


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


@pytest.mark.asyncio
async def test_resident_logs_merge_process_files_through_gateway_exec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)
    runtime = _resident_runtime()
    client = _FakeOpenShellGatewayClient(adapter)
    client.created = {"providers": ()}
    client.exec_script = lambda **kwargs: (
        0,
        "\n".join(
            (
                "__VOLUNDR_LOG_SOURCE__=ravn",
                '{"time":"2026-07-11T10:00:00Z","level":"INFO","message":"turn started"}',
                '{"time":"2026-07-11T10:00:01Z","level":"ERROR","message":"turn failed"}',
            )
        ),
    )
    manager = adapter.OpenShellGatewayPodManager(client=client)

    page = await manager.logs(
        runtime,
        lines=50,
        sources=("ravn",),
        min_level="ERROR",
    )

    assert [(entry.source, entry.level, entry.message) for entry in page.entries] == [
        ("sandbox", "OCSF", "PROC:LAUNCH ravn"),
        ("ravn", "ERROR", "turn failed"),
    ]


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


def test_resident_proxy_target_preserves_service_route_and_uses_gateway(
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = _import_adapter(monkeypatch)
    runtime = _resident_runtime().model_copy(
        update={
            "backend_ref": {"service_url": "ws://resident-example--skuld.openshell.localhost:8080"}
        }
    )
    manager = adapter.OpenShellGatewayPodManager(
        client=_FakeOpenShellGatewayClient(adapter),
        gateway_endpoint="openshell.openshell.svc.cluster.local:8080",
    )

    target = manager.resident_proxy_target(runtime)

    assert target is not None
    assert target.service_url == ("ws://resident-example--skuld.openshell.localhost:8080")
    assert target.connect_host == "openshell.openshell.svc.cluster.local"
    assert target.connect_port == 8080


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
async def test_start_uses_central_codex_broker_without_openshell_token_grant(
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
            "broker": {
                "codexAuth": {
                    "kwargs": {
                        "credential_name": "codex-credentials",
                        "credential_field": "auth.json",
                    }
                }
            },
            "openshell": {
                "credentialMappings": [
                    {
                        "credentialName": "github-credential",
                        "envMappings": {"GITHUB_PERSONAL_ACCESS_TOKEN": "token"},
                    },
                ],
            },
        },
        pod_spec=PodSpecAdditions(),
    )

    await manager.start(session, spec)

    assert client.written_files == []
    assert client.created is not None
    assert client.created["env"]["SKULD__CODEX_AUTH__ADAPTER"] == (
        "skuld.codex_auth.VolundrCodexAuthProvider"
    )
    assert json.loads(client.created["env"]["SKULD__CODEX_AUTH__KWARGS"]) == {
        "credential_name": "codex-credentials",
        "credential_field": "auth.json",
    }
    assert len(client.provider_grants) == 1
    assert client.provider_grants[0]["profile"].credentials[0].env_vars == [
        "GITHUB_PERSONAL_ACCESS_TOKEN"
    ]


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

    class _Data:
        data = b"one\ntwo\n"

    class _Event:
        exit = _Exit()
        stdout = _Data()

        def __init__(self, field: str) -> None:
            self._field = field

        def HasField(self, name: str) -> bool:  # noqa: N802 - protobuf shim.
            return name == self._field

    class _Stub:
        def ExecSandbox(self, request, **_kwargs):  # noqa: N802 - protobuf shim.
            recorded["request"] = request
            return [_Event("stdout"), _Event("exit")]

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
    assert output == "one\ntwo\n"
    assert request.command == ["sh", "-s"]
    assert request.stdin == b"echo one\necho two\n"
    assert all("\n" not in arg and "\r" not in arg for arg in request.command)
    assert request.environment == {"A": "B"}


def test_write_files_includes_projection_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _import_adapter(monkeypatch)
    recorded = {}

    class _Data:
        data = b"tar: cannot create sandbox/.volundr: permission denied\n"

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


def test_sandbox_policy_is_built_from_adapter_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)

    policy = adapter._sandbox_policy_from_config(
        {
            "version": 1,
            "filesystem": {
                "include_workdir": True,
                "read_only": ["/usr"],
                "read_write": ["/sandbox", "/tmp", "/dev/null"],
            },
            "process": {"run_as_user": "sandbox", "run_as_group": "sandbox"},
            "network_policies": {
                "npm_https": {
                    "name": "npm-https",
                    "endpoints": [
                        {
                            "host": "registry.npmjs.org",
                            "port": 443,
                            "protocol": "rest",
                            "tls": "terminate",
                            "enforcement": "enforce",
                            "access": "full",
                        }
                    ],
                    "binaries": [{"path": "/usr/bin/node"}],
                }
            },
        }
    )

    assert "/dev/null" in policy.filesystem.read_write
    assert "/tmp" in policy.filesystem.read_write
    assert "/usr" in policy.filesystem.read_only
    npm = policy.network_policies["npm_https"]
    assert [endpoint.host for endpoint in npm.endpoints] == ["registry.npmjs.org"]
    assert [binary.path for binary in npm.binaries] == ["/usr/bin/node"]


def test_sandbox_policy_requires_supported_version(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _import_adapter(monkeypatch)

    with pytest.raises(ValueError, match="version must be 1"):
        adapter._sandbox_policy_from_config({})


def test_real_gateway_client_requires_sandbox_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _import_adapter(monkeypatch)

    with pytest.raises(ValueError, match="sandbox_policy configuration is required"):
        adapter.OpenShellGatewayPodManager()


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


def test_provider_grant_names_a_credential_slot_the_gateway_accepts(monkeypatch) -> None:
    """A provider with no credentials at all is rejected by the gateway."""
    adapter = _import_adapter(monkeypatch)
    client = adapter.OpenShellGatewayClient(
        endpoint="openshell.openshell.svc.cluster.local:8080",
        token_provider=types.SimpleNamespace(token=lambda: "token", close=lambda: None),
    )
    created: list[object] = []

    class _Stub:
        def ImportProviderProfiles(self, _request, **_kwargs):  # noqa: N802 - gRPC stub shim.
            return types.SimpleNamespace(imported=True, diagnostics=[])

        def CreateProvider(self, request, **_kwargs):  # noqa: N802 - gRPC stub shim.
            created.append(request)

    client._stub = _Stub()
    monkeypatch.setattr(client, "get_provider_profile", lambda _id: None)
    monkeypatch.setattr(client, "get_provider", lambda _name: None)

    network_only = adapter._codex_enrollment_profile("volundr-enroll-1")
    client.create_provider_grant(
        profile=network_only,
        provider_name="volundr-enroll-1",
        config={"volundr_enrollment_id": "1"},
    )

    slots = created[0].provider.credentials
    assert slots == {adapter.PROVIDER_NETWORK_ONLY_CREDENTIAL: ""}


def test_provider_grant_leaves_declared_credentials_for_the_token_grant(monkeypatch) -> None:
    adapter = _import_adapter(monkeypatch)
    profile = types.SimpleNamespace(
        id="platform",
        credentials=[types.SimpleNamespace(name="access_token")],
    )

    assert adapter._provider_credential_slots(profile) == {"access_token": ""}


def test_provider_credential_slots_are_named_per_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The provisioner treats a credential slot name as an env key shared
    across every provider on a sandbox. Both profiles used to call theirs
    "access_token", so attaching a platform provider alongside any connection
    provider failed the whole session:

      credential env key 'access_token' is provided by both provider
      'volundr-...-39c40789e41f' and provider 'volundr-...-93f7c83ce773';
      use provider-specific env names
    """
    adapter = _import_adapter(monkeypatch)

    platform = adapter._platform_provider_profile(
        profile_id="resident-platform",
        token_endpoint="https://volundr.example.test/token",
        api_urls=("http://niuu-bifrost.volundr.svc.cluster.local/v1",),
    )
    connection = adapter._provider_profile(
        profile_id="resident-noatun",
        env_name="NOATUN_ACCESS_TOKEN",
        token_endpoint="https://volundr.example.test/token",
        target_config={
            "endpoints": [{"host": "example.test", "port": 443, "tls": "skip"}],
            "binaries": ["/opt/niuu/bin/python"],
        },
    )

    platform_slots = set(adapter._provider_credential_slots(platform))
    connection_slots = set(adapter._provider_credential_slots(connection))

    assert platform_slots & connection_slots == set()
    assert platform_slots == {adapter.PLATFORM_ACCESS_TOKEN_ENV}
    assert connection_slots == {"NOATUN_ACCESS_TOKEN"}


def test_two_connection_providers_do_not_collide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch)

    first = adapter._provider_profile(
        profile_id="p1",
        env_name="ONE_TOKEN",
        token_endpoint="https://volundr.example.test/token",
        target_config={
            "endpoints": [{"host": "example.test", "port": 443, "tls": "skip"}],
            "binaries": ["/opt/niuu/bin/python"],
        },
    )
    second = adapter._provider_profile(
        profile_id="p2",
        env_name="TWO_TOKEN",
        token_endpoint="https://volundr.example.test/token",
        target_config={
            "endpoints": [{"host": "example.test", "port": 443, "tls": "skip"}],
            "binaries": ["/opt/niuu/bin/python"],
        },
    )

    assert set(adapter._provider_credential_slots(first)) != set(
        adapter._provider_credential_slots(second)
    )
