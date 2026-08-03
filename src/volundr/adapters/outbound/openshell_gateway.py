"""Native OpenShell gateway adapter for Kubernetes-backed Forge sessions.

It mints a Keycloak client-credentials token and talks to the OpenShell gateway
gRPC API directly. The old OpenShell CLI shell-out adapter was intentionally
removed; service/runtime auth belongs at this gateway boundary.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import re
import shlex
import socket
import tarfile
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse, urlunparse
from uuid import UUID

import grpc
import httpx
import yaml
from google.protobuf import struct_pb2
from openshell._proto import datamodel_pb2, openshell_pb2, openshell_pb2_grpc, sandbox_pb2

from niuu.adapters.workload_identity.jwt import JwtWorkloadIdentityVerifier
from niuu.domain.models import Principal
from niuu.domain.services.token_scope import (
    OPENSHELL_RESIDENT_TOKEN_USE,
    OPENSHELL_SESSION_TOKEN_USE,
)
from niuu.ports.session_proxy import SessionProxyTarget
from niuu.ports.workload_identity import WorkloadTokenIssuer
from volundr.adapters.outbound.brokered_credentials import BrokeredCredentialPodManager
from volundr.adapters.outbound.local_process import LocalProcessPodManager
from volundr.adapters.outbound.resident_container_spec import (
    image_from_values as _shared_image_from_values,
)
from volundr.adapters.outbound.resident_container_spec import (
    resident_attribution_headers as _shared_resident_attribution_headers,
)
from volundr.adapters.outbound.resident_container_spec import (
    resident_flock_environment,
    resident_flock_labels,
    resident_flock_profile_configured,
    resident_flock_runtime_config,
    resident_flock_skuld_config,
    resident_mesh_pod_metadata,
)
from volundr.adapters.outbound.resident_container_spec import (
    resident_process_files as _shared_resident_process_files,
)
from volundr.adapters.outbound.resident_container_spec import (
    resident_profile_values as _shared_resident_profile_values,
)
from volundr.adapters.outbound.resident_container_spec import (
    resident_service as _shared_resident_service,
)
from volundr.adapters.outbound.resident_container_spec import (
    runtime_processes_from_values as _shared_runtime_processes_from_values,
)
from volundr.domain.models import (
    CredentialEnrollment,
    CredentialEnrollmentPoll,
    CredentialEnrollmentState,
    ResidentBackend,
    ResidentCapability,
    ResidentCondition,
    ResidentConditionStatus,
    ResidentDeploymentProfile,
    ResidentEndpoint,
    ResidentEngine,
    ResidentLogEntry,
    ResidentLogPage,
    ResidentObservedState,
    ResidentRuntime,
    Session,
    SessionSpec,
    SessionStatus,
)
from volundr.domain.ports import (
    CredentialStorePort,
    OpenShellCredentialGrantPort,
    OpenShellCredentialGrantToken,
    PodManager,
    PodStartResult,
    ResidentDeviceApprover,
    ResidentRuntimeController,
    ResidentRuntimeLogReader,
    ResidentRuntimeObservation,
    ResidentRuntimeProxyTargetResolver,
    ResidentRuntimeRepository,
    SessionRepository,
)

logger = logging.getLogger(__name__)

DEFAULT_GATEWAY_ENDPOINT = "openshell.openshell.svc.cluster.local:8080"
DEFAULT_TOKEN_URL = "https://keycloak.niuu.world/realms/volundr/protocol/openid-connect/token"
DEFAULT_CLIENT_ID = "openshell-volundr-agent"
DEFAULT_SANDBOX_IMAGE = "ghcr.io/niuulabs/skuld:openshell-codex-openbao-20260709-7"
DEFAULT_SANDBOX_COMMAND = ["/usr/local/bin/openshell-run-installed-skuld"]
DEFAULT_SERVICE_PORT = 9200
READY_POLL_INTERVAL = 1.0
DEFAULT_RESOURCE_DELETE_TIMEOUT_SECONDS = 30.0
TCP_FORWARD_BUFFER_BYTES = 64 * 1024
BOOTSTRAP_TIMEOUT_SECONDS = 600
BOOTSTRAP_GIT_ATTEMPTS = 20
MAX_SANDBOX_ROUTING_NAME_LENGTH = 28
OAUTH_CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-spiffe"
GRANT_AUDIENCE_PREFIX = "niuu:credential:"
PLATFORM_GRANT_AUDIENCE_PREFIX = "niuu:platform:"
PLATFORM_ACCESS_TOKEN_ENV = "NIUU_VOLUNDR_ACCESS_TOKEN"
PROVIDERS_V2_SETTING = "providers_v2_enabled"
DEFAULT_CREDENTIAL_TOKEN_ENDPOINT = (
    "http://niuu-volundr.volundr.svc.cluster.local/api/v1/internal/openshell/credential-token"
)
DEFAULT_SPIFFE_JWKS_URI = (
    "https://spire-spiffe-oidc-discovery-provider.spire.svc.cluster.local/keys"
)
DEFAULT_SPIFFE_ISSUER = "https://spire-spiffe-oidc-discovery-provider.spire.svc.cluster.local"
DEFAULT_SPIFFE_AUDIENCE = DEFAULT_CREDENTIAL_TOKEN_ENDPOINT
DEFAULT_SPIFFE_SUBJECT_PREFIX = "spiffe://niuu.world/openshell/sandbox/"
DEFAULT_CREDENTIAL_ENROLLMENT_CHALLENGE_TIMEOUT_SECONDS = 30.0
CODEX_DEVICE_ENROLLMENT_METHOD = "codex_device"
CODEX_ENROLLMENT_HOME = "/tmp/niuu-codex-enrollment"
CODEX_ENROLLMENT_AUTH_PATH = f"{CODEX_ENROLLMENT_HOME}/auth.json"
CODEX_ENROLLMENT_LOG_PATH = "/tmp/niuu-codex-enrollment.log"
CODEX_ENROLLMENT_PID_PATH = "/tmp/niuu-codex-enrollment.pid"
PROVIDER_NETWORK_ONLY_CREDENTIAL = "network_only"
CODEX_DEVICE_CODE_PATTERN = re.compile(r"[A-Z0-9]{4,8}(?:-[A-Z0-9]{4,8})+")
HERMES_API_SERVER_KEY_ENV = "API_SERVER_KEY"
HERMES_API_SERVER_DEFAULT_PORT = 8642
HERMES_INTERNAL_SERVICE_URL = "http://hermes-api.internal"
SECRET_ENV_KEYS = {
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GIT_TOKEN",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GITHUB_PERSONAL_ACCESS_TOKEN",
    "CODEX_AUTH_ACCESS_TOKEN",
    "CODEX_AUTH_REFRESH_TOKEN",
    "CODEX_AUTH_ACCOUNT_ID",
    "CODEX_AUTH_ID_TOKEN",
    PLATFORM_ACCESS_TOKEN_ENV,
    HERMES_API_SERVER_KEY_ENV,
}
SECRET_INJECTION_ANNOTATION_PREFIXES = ("vault.hashicorp.com/",)
AGENT_AUTH_FILE_SUFFIXES = (
    "/.codex/auth.json",
    "/.claude/.credentials.json",
)


@dataclass(frozen=True)
class OpenShellSandbox:
    id: str
    name: str
    phase: int
    ready: bool = False
    labels: dict[str, str] | None = None
    providers: tuple[str, ...] = ()


@dataclass(frozen=True)
class OpenShellCredentialContext:
    files: dict[str, bytes]
    providers: tuple[str, ...]
    environment: dict[str, str]
    process_environment: dict[str, str]


@dataclass(frozen=True)
class OpenShellProviderGrant:
    provider_name: str
    profile_id: str


@dataclass(frozen=True)
class OpenShellRuntimeProcess:
    name: str
    command: tuple[str, ...]
    env: dict[str, str]
    files: dict[str, bytes]
    log_path: str


@dataclass(frozen=True)
class OpenShellWorkloadSubject:
    """Authenticated owner binding shared by Forge and resident workloads."""

    id: UUID
    kind: str
    name: str
    owner_id: str
    tenant_id: str


class ClientCredentialsTokenProvider:
    """Small synchronous client-credentials token cache for gRPC metadata."""

    def __init__(
        self,
        *,
        token_url: str = DEFAULT_TOKEN_URL,
        client_id: str = DEFAULT_CLIENT_ID,
        client_secret: str = "",
        timeout: float = 10.0,
        refresh_skew_seconds: int = 60,
        client: httpx.Client | None = None,
    ) -> None:
        if not client_secret:
            raise RuntimeError("OpenShell OIDC client secret is required")
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = float(timeout)
        self._refresh_skew_seconds = int(refresh_skew_seconds)
        self._client = client or httpx.Client(timeout=self._timeout)
        self._owns_client = client is None
        self._lock = threading.Lock()
        self._token = ""
        self._expires_at = 0.0

    def token(self) -> str:
        with self._lock:
            now = time.time()
            if self._token and now < self._expires_at - self._refresh_skew_seconds:
                return self._token
            response = self._client.post(
                self._token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
            response.raise_for_status()
            payload = response.json()
            token = str(payload.get("access_token") or "")
            if not token:
                raise RuntimeError("OpenShell OIDC token response did not include access_token")
            expires_in = int(payload.get("expires_in") or 300)
            self._token = token
            self._expires_at = now + max(expires_in, 1)
            return token

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


class OpenShellGatewayClient:
    """Native gRPC client for the OpenShell gateway calls Volundr needs."""

    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_GATEWAY_ENDPOINT,
        token_provider: ClientCredentialsTokenProvider,
        plaintext: bool = True,
        timeout: float = 30.0,
    ) -> None:
        self._endpoint = _endpoint_hostport(endpoint)
        self._token_provider = token_provider
        self._timeout = float(timeout)
        channel = (
            grpc.insecure_channel(self._endpoint)
            if plaintext
            else grpc.secure_channel(self._endpoint, grpc.ssl_channel_credentials())
        )
        self._channel = channel
        self._stub = openshell_pb2_grpc.OpenShellStub(channel)

    def close(self) -> None:
        self._channel.close()
        self._token_provider.close()

    def create_sandbox(
        self,
        *,
        name: str,
        image: str,
        env: dict[str, str],
        labels: dict[str, str],
        annotations: dict[str, str] | None = None,
        resources: dict[str, Any] | None = None,
        driver_config: dict[str, Any] | None = None,
        providers: Sequence[str] = (),
        policy: Any | None = None,
    ) -> OpenShellSandbox:
        template = openshell_pb2.SandboxTemplate(image=image)
        template.labels.update(labels)
        if annotations:
            template.annotations.update(annotations)
        template.environment.update(env)
        if resources:
            template.resources.CopyFrom(_protobuf_struct(resources))
        if driver_config:
            template.driver_config.CopyFrom(_protobuf_struct(driver_config))
        spec = openshell_pb2.SandboxSpec(
            environment=env,
            template=template,
            policy=policy,
            providers=list(providers),
        )
        request = openshell_pb2.CreateSandboxRequest(spec=spec, name=name, labels=labels)
        response = self._stub.CreateSandbox(
            request,
            timeout=self._timeout,
            metadata=self._metadata(),
        )
        return _sandbox_from_proto(response.sandbox)

    def get_sandbox(self, name: str) -> OpenShellSandbox | None:
        try:
            response = self._stub.GetSandbox(
                openshell_pb2.GetSandboxRequest(name=name),
                timeout=self._timeout,
                metadata=self._metadata(),
            )
        except grpc.RpcError as exc:
            if exc.code() == grpc.StatusCode.NOT_FOUND:
                return None
            raise
        return _sandbox_from_proto(response.sandbox)

    def get_sandbox_by_id(self, sandbox_id: str) -> OpenShellSandbox | None:
        offset = 0
        while True:
            response = self._stub.ListSandboxes(
                openshell_pb2.ListSandboxesRequest(limit=100, offset=offset),
                timeout=self._timeout,
                metadata=self._metadata(),
            )
            for sandbox in response.sandboxes:
                if str(sandbox.metadata.id) == sandbox_id:
                    return _sandbox_from_proto(sandbox)
            if len(response.sandboxes) < 100:
                return None
            offset += len(response.sandboxes)

    def delete_sandbox(self, name: str) -> bool:
        try:
            response = self._stub.DeleteSandbox(
                openshell_pb2.DeleteSandboxRequest(name=name),
                timeout=self._timeout,
                metadata=self._metadata(),
            )
        except grpc.RpcError as exc:
            if exc.code() == grpc.StatusCode.NOT_FOUND:
                return False
            raise
        return bool(response.deleted)

    def delete_service(self, *, sandbox_name: str, service: str) -> bool:
        try:
            response = self._stub.DeleteService(
                openshell_pb2.DeleteServiceRequest(sandbox=sandbox_name, service=service),
                timeout=self._timeout,
                metadata=self._metadata(),
            )
        except grpc.RpcError as exc:
            if exc.code() == grpc.StatusCode.NOT_FOUND:
                return False
            raise
        return bool(response.deleted)

    def ensure_providers_v2(self) -> None:
        self._stub.UpdateConfig(
            openshell_pb2.UpdateConfigRequest(
                setting_key=PROVIDERS_V2_SETTING,
                setting_value=sandbox_pb2.SettingValue(bool_value=True),
                **{"global": True},
            ),
            timeout=self._timeout,
            metadata=self._metadata(),
        )

    def get_provider(self, name: str) -> Any | None:
        try:
            response = self._stub.GetProvider(
                openshell_pb2.GetProviderRequest(name=name),
                timeout=self._timeout,
                metadata=self._metadata(),
            )
        except grpc.RpcError as exc:
            if exc.code() == grpc.StatusCode.NOT_FOUND:
                return None
            raise
        return response.provider

    def get_provider_profile(self, profile_id: str) -> Any | None:
        try:
            response = self._stub.GetProviderProfile(
                openshell_pb2.GetProviderProfileRequest(id=profile_id),
                timeout=self._timeout,
                metadata=self._metadata(),
            )
        except grpc.RpcError as exc:
            if exc.code() == grpc.StatusCode.NOT_FOUND:
                return None
            raise
        return response.profile

    def create_provider_grant(
        self,
        *,
        profile: Any,
        provider_name: str,
        config: dict[str, str],
    ) -> None:
        existing_profile = self.get_provider_profile(str(profile.id))
        if existing_profile is not None and not _profiles_equivalent(existing_profile, profile):
            raise RuntimeError(f"OpenShell provider profile {profile.id!r} does not match")
        if existing_profile is None:
            response = self._stub.ImportProviderProfiles(
                openshell_pb2.ImportProviderProfilesRequest(
                    profiles=[
                        openshell_pb2.ProviderProfileImportItem(
                            profile=profile,
                            source="volundr",
                        )
                    ]
                ),
                timeout=self._timeout,
                metadata=self._metadata(),
            )
            errors = [d.message for d in response.diagnostics if str(d.severity).lower() == "error"]
            if errors or not response.imported:
                raise RuntimeError(
                    "OpenShell provider profile import failed: "
                    + ("; ".join(errors) if errors else str(profile.id))
                )

        existing_provider = self.get_provider(provider_name)
        if existing_provider is not None:
            provider_matches = (
                str(existing_provider.type) == str(profile.id)
                and dict(existing_provider.config) == config
            )
            if not provider_matches:
                raise RuntimeError(f"OpenShell provider {provider_name!r} does not match")
            return
        try:
            self._stub.CreateProvider(
                openshell_pb2.CreateProviderRequest(
                    provider=datamodel_pb2.Provider(
                        metadata=datamodel_pb2.ObjectMeta(name=provider_name),
                        type=str(profile.id),
                        config=config,
                        credentials=_provider_credential_slots(profile),
                    )
                ),
                timeout=self._timeout,
                metadata=self._metadata(),
            )
        except grpc.RpcError as exc:
            if exc.code() != grpc.StatusCode.ALREADY_EXISTS:
                raise

    def delete_provider_grant(self, grant: OpenShellProviderGrant) -> None:
        try:
            self._stub.DeleteProvider(
                openshell_pb2.DeleteProviderRequest(name=grant.provider_name),
                timeout=self._timeout,
                metadata=self._metadata(),
            )
        except grpc.RpcError as exc:
            if exc.code() != grpc.StatusCode.NOT_FOUND:
                raise
        try:
            self._stub.DeleteProviderProfile(
                openshell_pb2.DeleteProviderProfileRequest(id=grant.profile_id),
                timeout=self._timeout,
                metadata=self._metadata(),
            )
        except grpc.RpcError as exc:
            if exc.code() != grpc.StatusCode.NOT_FOUND:
                raise

    def expose_service(self, *, sandbox_name: str, target_port: int, service: str = "") -> str:
        response = self._stub.ExposeService(
            openshell_pb2.ExposeServiceRequest(
                sandbox=sandbox_name,
                service=service,
                target_port=int(target_port),
                domain=True,
            ),
            timeout=self._timeout,
            metadata=self._metadata(),
        )
        return str(response.url or "")

    def start_tcp_forward(self, *, sandbox_id: str, target_port: int) -> OpenShellTcpForwarder:
        """Open a local listener backed by OpenShell's authenticated ForwardTcp RPC."""
        return OpenShellTcpForwarder(self, sandbox_id=sandbox_id, target_port=target_port)

    def _forward_tcp_connection(
        self,
        connection: socket.socket,
        *,
        sandbox_id: str,
        target_port: int,
    ) -> None:
        session = self._stub.CreateSshSession(
            openshell_pb2.CreateSshSessionRequest(sandbox_id=sandbox_id),
            timeout=self._timeout,
            metadata=self._metadata(),
        )
        token = str(session.token)

        def frames():
            yield openshell_pb2.TcpForwardFrame(
                init=openshell_pb2.TcpForwardInit(
                    sandbox_id=sandbox_id,
                    service_id=f"volundr-resident:{sandbox_id}:{target_port}",
                    tcp=openshell_pb2.TcpRelayTarget(host="127.0.0.1", port=target_port),
                    authorization_token=token,
                )
            )
            while True:
                data = connection.recv(TCP_FORWARD_BUFFER_BYTES)
                if not data:
                    return
                yield openshell_pb2.TcpForwardFrame(data=data)

        try:
            responses = self._stub.ForwardTcp(frames(), metadata=self._metadata())
            for response in responses:
                if response.HasField("data"):
                    connection.sendall(response.data)
        finally:
            try:
                self._stub.RevokeSshSession(
                    openshell_pb2.RevokeSshSessionRequest(token=token),
                    timeout=self._timeout,
                    metadata=self._metadata(),
                )
            except grpc.RpcError:
                logger.debug("OpenShell TCP forward session revocation failed", exc_info=True)

    def exec_detached(
        self,
        *,
        sandbox_id: str,
        command: Sequence[str],
        env: dict[str, str],
        log_path: str,
        pid_path: str = "",
    ) -> int:
        command_line = shlex.join([str(part) for part in command])
        log_dir = shlex.quote(str(Path(log_path).parent))
        quoted_log_path = shlex.quote(log_path)
        pid_write = f"echo $! > {shlex.quote(pid_path)}\n" if pid_path else ""
        script = f"mkdir -p {log_dir}\nnohup {command_line} >{quoted_log_path} 2>&1 &\n{pid_write}"
        stream = self._stub.ExecSandbox(
            openshell_pb2.ExecSandboxRequest(
                sandbox_id=sandbox_id,
                command=["sh", "-s"],
                environment=env,
                stdin=script.encode(),
                timeout_seconds=10,
            ),
            timeout=self._timeout,
            metadata=self._metadata(),
        )
        exit_code = 0
        for event in stream:
            if event.HasField("exit"):
                exit_code = int(event.exit.exit_code)
        return exit_code

    def get_provider_environment(self, sandbox_id: str) -> dict[str, str]:
        response = self._stub.GetSandboxProviderEnvironment(
            openshell_pb2.GetSandboxProviderEnvironmentRequest(sandbox_id=sandbox_id),
            timeout=self._timeout,
            metadata=self._metadata(),
        )
        return {str(key): str(value) for key, value in response.environment.items()}

    def get_sandbox_logs(
        self,
        sandbox_id: str,
        *,
        lines: int,
        sources: Sequence[str],
        min_level: str,
    ) -> ResidentLogPage:
        response = self._stub.GetSandboxLogs(
            openshell_pb2.GetSandboxLogsRequest(
                sandbox_id=sandbox_id,
                lines=lines,
                sources=list(sources),
                min_level=min_level,
            ),
            timeout=self._timeout,
            metadata=self._metadata(),
        )
        return ResidentLogPage(
            entries=[
                ResidentLogEntry(
                    timestamp_ms=int(entry.timestamp_ms),
                    level=str(entry.level),
                    source=str(entry.source),
                    target=str(entry.target),
                    message=str(entry.message),
                    fields={str(key): str(value) for key, value in entry.fields.items()},
                )
                for entry in response.logs
            ],
            buffer_total=int(response.buffer_total),
        )

    def exec_script(
        self,
        *,
        sandbox_id: str,
        script: str,
        env: dict[str, str],
    ) -> tuple[int, str]:
        stdin = script.encode()
        stream = self._stub.ExecSandbox(
            openshell_pb2.ExecSandboxRequest(
                sandbox_id=sandbox_id,
                command=["sh", "-s"],
                environment=env,
                stdin=stdin,
                timeout_seconds=BOOTSTRAP_TIMEOUT_SECONDS,
            ),
            timeout=max(self._timeout, float(BOOTSTRAP_TIMEOUT_SECONDS)),
            metadata=self._metadata(),
        )
        exit_code = 0
        output: list[str] = []
        for event in stream:
            if event.HasField("stdout"):
                output.append(_exec_output_text(event.stdout.data))
            elif event.HasField("stderr"):
                output.append(_exec_output_text(event.stderr.data))
            elif event.HasField("exit"):
                exit_code = int(event.exit.exit_code)
        return exit_code, "".join(output)

    def write_files(self, *, sandbox_id: str, files: dict[str, bytes]) -> None:
        for extraction_root, archive in _credential_file_archives(files):
            stream = self._stub.ExecSandbox(
                openshell_pb2.ExecSandboxRequest(
                    sandbox_id=sandbox_id,
                    command=["tar", "-xf", "-", "-C", extraction_root],
                    stdin=archive,
                    timeout_seconds=30,
                ),
                timeout=max(self._timeout, 30.0),
                metadata=self._metadata(),
            )
            output: list[str] = []
            exit_code = 0
            for event in stream:
                if event.HasField("stdout"):
                    output.append(_exec_output_text(event.stdout.data))
                elif event.HasField("stderr"):
                    output.append(_exec_output_text(event.stderr.data))
                elif event.HasField("exit"):
                    exit_code = int(event.exit.exit_code)
            if exit_code != 0:
                detail = _redact_secret_url("".join(output).strip())
                suffix = f": {detail}" if detail else ""
                raise RuntimeError(
                    "OpenShell credential file projection failed "
                    f"for {extraction_root} with exit {exit_code}{suffix}"
                )

    def _metadata(self) -> tuple[tuple[str, str], ...]:
        return (("authorization", f"Bearer {self._token_provider.token()}"),)


class OpenShellTcpForwarder:
    """Local TCP listener bridged to one sandbox loopback port over gRPC."""

    def __init__(
        self,
        client: OpenShellGatewayClient,
        *,
        sandbox_id: str,
        target_port: int,
    ) -> None:
        self._client = client
        self._sandbox_id = sandbox_id
        self._target_port = target_port
        self._closed = threading.Event()
        self._connections: set[socket.socket] = set()
        self._connection_lock = threading.Lock()
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen()
        self.host, self.port = self._listener.getsockname()
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self) -> None:
        while not self._closed.is_set():
            try:
                connection, _ = self._listener.accept()
            except OSError:
                return
            with self._connection_lock:
                self._connections.add(connection)
            threading.Thread(
                target=self._bridge,
                args=(connection,),
                daemon=True,
            ).start()

    def _bridge(self, connection: socket.socket) -> None:
        try:
            self._client._forward_tcp_connection(
                connection,
                sandbox_id=self._sandbox_id,
                target_port=self._target_port,
            )
        except (OSError, grpc.RpcError):
            logger.debug("OpenShell TCP forward closed", exc_info=True)
        finally:
            with self._connection_lock:
                self._connections.discard(connection)
            connection.close()

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._listener.close()
        with self._connection_lock:
            connections = tuple(self._connections)
            self._connections.clear()
        for connection in connections:
            connection.close()
        self._thread.join(timeout=1)


class OpenShellGatewayPodManager(
    BrokeredCredentialPodManager,
    PodManager,
    OpenShellCredentialGrantPort,
    ResidentRuntimeController,
    ResidentRuntimeLogReader,
    ResidentRuntimeProxyTargetResolver,
    ResidentDeviceApprover,
):
    """Kubernetes OpenShell PodManager using OIDC and native gRPC."""

    def __init__(
        self,
        *,
        gateway_endpoint: str = DEFAULT_GATEWAY_ENDPOINT,
        gateway_public_url: str = "",
        token_url: str = DEFAULT_TOKEN_URL,
        client_id: str = DEFAULT_CLIENT_ID,
        client_secret: str = "",
        sandbox_image: str = DEFAULT_SANDBOX_IMAGE,
        sandbox_command: list[str] | str | None = None,
        sandbox_workspace: str = "/sandbox/workspace",
        sandbox_home: str = "/sandbox",
        service_port: int = DEFAULT_SERVICE_PORT,
        service_name: str = "skuld",
        command_log_path: str = "/sandbox/.volundr/skuld.log",
        cpu: str = "",
        memory: str = "",
        plaintext: bool = True,
        command_timeout: float = 30.0,
        ready_timeout: float = 300.0,
        resource_delete_timeout: float = DEFAULT_RESOURCE_DELETE_TIMEOUT_SECONDS,
        credential_token_endpoint: str = DEFAULT_CREDENTIAL_TOKEN_ENDPOINT,
        volundr_api_url: str = "",
        workload_audience: str = "volundr-api",
        workload_audiences: list[str] | None = None,
        workload_roles: list[str] | None = None,
        spiffe_jwks_uri: str = DEFAULT_SPIFFE_JWKS_URI,
        spiffe_issuer: str = DEFAULT_SPIFFE_ISSUER,
        spiffe_audience: str = DEFAULT_SPIFFE_AUDIENCE,
        spiffe_subject_prefix: str = DEFAULT_SPIFFE_SUBJECT_PREFIX,
        spiffe_ca_cert_path: str = "",
        credential_enrollment_challenge_timeout_seconds: float = (
            DEFAULT_CREDENTIAL_ENROLLMENT_CHALLENGE_TIMEOUT_SECONDS
        ),
        codex_auth_adapter: str = "skuld.codex_auth.VolundrCodexAuthProvider",
        codex_auth_kwargs: dict | None = None,
        sandbox_policy: dict[str, Any] | None = None,
        client: OpenShellGatewayClient | None = None,
        **_extra: object,
    ) -> None:
        if sandbox_policy is None and client is None:
            raise ValueError("OpenShell sandbox_policy configuration is required")
        self._gateway_public_url = gateway_public_url.rstrip("/")
        gateway_hostport = _endpoint_hostport(gateway_endpoint)
        gateway_host, separator, gateway_port = gateway_hostport.rpartition(":")
        if not separator or not gateway_host or not gateway_port.isdigit():
            raise ValueError("OpenShell gateway endpoint must include a host and port")
        self._gateway_connect_host = gateway_host.strip("[]")
        self._gateway_connect_port = int(gateway_port)
        self._gateway_connect_secure = not plaintext
        self._sandbox_image = sandbox_image
        self._sandbox_command = _normalize_command(sandbox_command) or DEFAULT_SANDBOX_COMMAND
        self._sandbox_workspace = sandbox_workspace
        self._sandbox_home = sandbox_home.rstrip("/")
        self._sandbox_policy = _sandbox_policy_from_config(sandbox_policy or {"version": 1})
        self._service_port = int(service_port)
        self._service_name = service_name
        self._command_log_path = command_log_path
        self._cpu = cpu
        self._memory = memory
        self._ready_timeout = float(ready_timeout)
        self._resource_delete_timeout = float(resource_delete_timeout)
        self._credential_token_endpoint = credential_token_endpoint
        self._volundr_api_url = volundr_api_url.rstrip("/")
        self._workload_audience = workload_audience
        self._workload_audiences = tuple(workload_audiences or [workload_audience])
        self._workload_roles = tuple(workload_roles or ["volundr:developer"])
        self._credential_enrollment_challenge_timeout_seconds = float(
            credential_enrollment_challenge_timeout_seconds
        )
        self._configure_brokered_credentials(
            codex_auth_adapter=codex_auth_adapter,
            codex_auth_kwargs=codex_auth_kwargs,
        )
        self._spiffe_subject_prefix = spiffe_subject_prefix.rstrip("/") + "/"
        self._spiffe_verifier = JwtWorkloadIdentityVerifier(
            issuer=spiffe_issuer,
            audiences=[spiffe_audience],
            jwks_uri=spiffe_jwks_uri,
            ca_cert_path=spiffe_ca_cert_path,
            algorithms=["RS256"],
        )
        self._client = client or OpenShellGatewayClient(
            endpoint=gateway_endpoint,
            token_provider=ClientCredentialsTokenProvider(
                token_url=token_url,
                client_id=client_id,
                client_secret=client_secret,
            ),
            plaintext=plaintext,
            timeout=command_timeout,
        )
        self._service_urls: dict[str, str] = {}
        self._resident_forwarders: dict[str, OpenShellTcpForwarder] = {}
        self._provider_grants: dict[str, tuple[OpenShellProviderGrant, ...]] = {}
        self._credential_store: CredentialStorePort | None = None
        self._session_repository: SessionRepository | None = None
        self._resident_runtime_repository: ResidentRuntimeRepository | None = None
        self._workload_token_issuer: WorkloadTokenIssuer | None = None

    def set_credential_store(self, store: CredentialStorePort) -> None:
        """Inject credential store for resolving OpenShell launch credentials."""
        self._credential_store = store

    def set_session_repository(self, repository: SessionRepository) -> None:
        """Inject session persistence for sandbox-to-owner grant authorization."""
        self._session_repository = repository

    def set_resident_runtime_repository(
        self,
        repository: ResidentRuntimeRepository,
    ) -> None:
        """Inject resident persistence for sandbox grant authorization."""
        self._resident_runtime_repository = repository

    def set_workload_token_issuer(self, issuer: WorkloadTokenIssuer) -> None:
        """Inject the configured issuer for session-bound platform tokens."""
        self._workload_token_issuer = issuer

    def supports_enrollment(self, method: str) -> bool:
        """Return whether this OpenShell runner implements an enrollment method."""
        return method == CODEX_DEVICE_ENROLLMENT_METHOD

    async def start_enrollment(
        self,
        enrollment: CredentialEnrollment,
    ) -> CredentialEnrollment:
        """Start a Codex device-code login in a workspace-free OpenShell sandbox."""
        if not self.supports_enrollment(enrollment.method):
            raise ValueError("unsupported credential enrollment method")

        sandbox_name = f"enroll-{enrollment.id.hex[:21]}"
        provider_name = f"volundr-enroll-{enrollment.id.hex[:12]}"
        profile = _codex_enrollment_profile(provider_name)
        grant = OpenShellProviderGrant(provider_name=provider_name, profile_id=provider_name)
        labels = {
            "app.kubernetes.io/managed-by": "volundr",
            "volundr.niuu.io/credential-enrollment": str(enrollment.id),
            "volundr.niuu.io/integration-connection": enrollment.connection_id,
        }
        environment = {
            "CODEX_HOME": CODEX_ENROLLMENT_HOME,
            "HOME": "/tmp/niuu-enrollment-home",
            "NO_COLOR": "1",
        }
        try:
            await asyncio.to_thread(self._client.ensure_providers_v2)
            await asyncio.to_thread(
                self._client.create_provider_grant,
                profile=profile,
                provider_name=provider_name,
                config={"volundr_enrollment_id": str(enrollment.id)},
            )
            sandbox = await asyncio.to_thread(
                self._client.create_sandbox,
                name=sandbox_name,
                image=self._sandbox_image,
                env=environment,
                labels=labels,
                providers=(provider_name,),
                policy=self._sandbox_policy,
            )
            ready = await self._wait_for_sandbox_name(sandbox.name, self._ready_timeout)
            config_exit, _ = await asyncio.to_thread(
                self._client.exec_script,
                sandbox_id=ready.id,
                script=(
                    "umask 077\n"
                    f"mkdir -p {shlex.quote(CODEX_ENROLLMENT_HOME)} "
                    f"{shlex.quote(environment['HOME'])}\n"
                    f"printf '%s\\n' 'cli_auth_credentials_store = \"file\"' > "
                    f"{shlex.quote(f'{CODEX_ENROLLMENT_HOME}/config.toml')}\n"
                ),
                env=environment,
            )
            if config_exit != 0:
                raise RuntimeError("Codex enrollment home initialization failed")
            launch_exit = await asyncio.to_thread(
                self._client.exec_detached,
                sandbox_id=ready.id,
                command=("codex", "login", "--device-auth"),
                env=environment,
                log_path=CODEX_ENROLLMENT_LOG_PATH,
                pid_path=CODEX_ENROLLMENT_PID_PATH,
            )
            if launch_exit != 0:
                raise RuntimeError("Codex device login failed to start")
            verification_uri, user_code = await self._wait_for_codex_device_challenge(
                ready.id,
                environment,
            )
        except Exception:
            await self._cleanup_credential_enrollment(sandbox_name, grant)
            raise

        return replace(
            enrollment,
            state=CredentialEnrollmentState.AWAITING_USER,
            runner_ref={
                "sandbox_id": ready.id,
                "sandbox_name": sandbox_name,
                "provider_name": provider_name,
                "profile_id": provider_name,
            },
            verification_uri=verification_uri,
            user_code=user_code,
            updated_at=datetime.now(UTC),
        )

    async def poll_enrollment(
        self,
        enrollment: CredentialEnrollment,
    ) -> CredentialEnrollmentPoll:
        """Inspect a Codex enrollment and return its secret only on completion."""
        if not self.supports_enrollment(enrollment.method):
            return CredentialEnrollmentPoll(
                state=CredentialEnrollmentState.FAILED,
                error_code="unsupported_method",
            )
        sandbox_id = enrollment.runner_ref.get("sandbox_id", "")
        if not sandbox_id:
            return CredentialEnrollmentPoll(
                state=CredentialEnrollmentState.FAILED,
                error_code="runner_missing",
            )
        script = (
            f"if [ -s {shlex.quote(CODEX_ENROLLMENT_AUTH_PATH)} ]; then\n"
            "  printf 'complete\\n'\n"
            f"  base64 {shlex.quote(CODEX_ENROLLMENT_AUTH_PATH)} | tr -d '\\n'\n"
            f"elif [ -s {shlex.quote(CODEX_ENROLLMENT_PID_PATH)} ] "
            f'&& kill -0 "$(cat {shlex.quote(CODEX_ENROLLMENT_PID_PATH)})" 2>/dev/null; then\n'
            "  printf 'awaiting_user\\n'\n"
            "else\n"
            "  printf 'failed\\n'\n"
            "fi\n"
        )
        exit_code, output = await asyncio.to_thread(
            self._client.exec_script,
            sandbox_id=sandbox_id,
            script=script,
            env={"CODEX_HOME": CODEX_ENROLLMENT_HOME},
        )
        if exit_code != 0:
            return CredentialEnrollmentPoll(
                state=CredentialEnrollmentState.FAILED,
                error_code="runner_unavailable",
            )
        state, _, payload = output.partition("\n")
        if state.strip() == "awaiting_user":
            return CredentialEnrollmentPoll(state=CredentialEnrollmentState.AWAITING_USER)
        if state.strip() != "complete" or not payload.strip():
            return CredentialEnrollmentPoll(
                state=CredentialEnrollmentState.FAILED,
                error_code="provider_login_failed",
            )
        try:
            auth_json = base64.b64decode(payload.strip(), validate=True).decode("utf-8")
            _parse_codex_auth_document(auth_json)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("Codex enrollment returned an invalid credential document") from exc
        return CredentialEnrollmentPoll(
            state=CredentialEnrollmentState.COMPLETE,
            credential_data={"auth.json": auth_json},
        )

    async def cancel_enrollment(self, enrollment: CredentialEnrollment) -> None:
        """Destroy the enrollment sandbox and its network-only provider grant."""
        sandbox_name = enrollment.runner_ref.get("sandbox_name", "")
        provider_name = enrollment.runner_ref.get("provider_name", "")
        profile_id = enrollment.runner_ref.get("profile_id", provider_name)
        if not sandbox_name:
            return
        grant = OpenShellProviderGrant(provider_name=provider_name, profile_id=profile_id)
        await self._cleanup_credential_enrollment(sandbox_name, grant)

    async def _wait_for_codex_device_challenge(
        self,
        sandbox_id: str,
        environment: dict[str, str],
    ) -> tuple[str, str]:
        deadline = time.monotonic() + self._credential_enrollment_challenge_timeout_seconds
        while time.monotonic() < deadline:
            exit_code, output = await asyncio.to_thread(
                self._client.exec_script,
                sandbox_id=sandbox_id,
                script=(
                    f"if [ -f {shlex.quote(CODEX_ENROLLMENT_LOG_PATH)} ]; then "
                    f"sed -n '1,120p' {shlex.quote(CODEX_ENROLLMENT_LOG_PATH)}; fi\n"
                ),
                env=environment,
            )
            if exit_code == 0:
                challenge = _parse_codex_device_challenge(output)
                if challenge is not None:
                    return challenge
            await asyncio.sleep(READY_POLL_INTERVAL)
        raise TimeoutError("Codex device login did not produce a challenge in time")

    async def _cleanup_credential_enrollment(
        self,
        sandbox_name: str,
        grant: OpenShellProviderGrant,
    ) -> None:
        try:
            await asyncio.to_thread(self._client.delete_sandbox, sandbox_name)
            await self._wait_for_sandbox_deleted(sandbox_name)
        finally:
            if grant.provider_name:
                await asyncio.to_thread(self._client.delete_provider_grant, grant)

    @staticmethod
    def _session_subject(session: Session) -> OpenShellWorkloadSubject:
        return OpenShellWorkloadSubject(
            id=session.id,
            kind="session",
            name=session.name,
            owner_id=session.owner_id or "",
            tenant_id=session.tenant_id or "default",
        )

    @staticmethod
    def _resident_subject(runtime: ResidentRuntime) -> OpenShellWorkloadSubject:
        return OpenShellWorkloadSubject(
            id=runtime.id,
            kind="resident",
            name=runtime.name,
            owner_id=runtime.owner_id,
            tenant_id=runtime.tenant_id,
        )

    @staticmethod
    def _grant_binding(
        subject: OpenShellWorkloadSubject,
        **values: str,
    ) -> dict[str, str]:
        if subject.kind == "session":
            return {"volundr_session_id": str(subject.id), **values}
        return {
            "volundr_subject_kind": subject.kind,
            "volundr_subject_id": str(subject.id),
            **values,
        }

    async def _grant_subject(
        self,
        config: dict[str, Any],
        labels: dict[str, str],
    ) -> OpenShellWorkloadSubject:
        kind = str(config.get("volundr_subject_kind") or "session")
        subject_id = str(config.get("volundr_subject_id") or config.get("volundr_session_id") or "")
        if kind not in {"session", "resident"}:
            raise ValueError("credential provider workload kind is invalid")
        try:
            workload_id = UUID(subject_id)
        except ValueError as exc:
            raise ValueError("credential provider workload binding is invalid") from exc
        if labels.get(f"volundr.niuu.io/{kind}") != subject_id:
            raise ValueError("credential provider workload binding does not match sandbox")

        if kind == "session":
            if self._session_repository is None:
                raise ValueError("credential grant session repository is unavailable")
            session = await self._session_repository.get(workload_id)
            if session is None or not session.owner_id:
                raise ValueError("credential grant session does not exist")
            return self._session_subject(session)

        if self._resident_runtime_repository is None:
            raise ValueError("credential grant resident repository is unavailable")
        runtime = await self._resident_runtime_repository.get(workload_id)
        if runtime is None:
            raise ValueError("credential grant resident does not exist")
        return self._resident_subject(runtime)

    def initial_chat_endpoint(self, session: Session) -> str | None:
        return None

    def session_proxy_target(self, session: Session) -> SessionProxyTarget | None:
        """Resolve the OpenShell service route used by Niuu's session proxy."""
        base = self._service_urls.get(str(session.id))
        if not base and session.chat_endpoint:
            parsed = urlparse(session.chat_endpoint)
            if parsed.hostname and parsed.hostname.endswith(".openshell.localhost"):
                path = parsed.path.removesuffix("/session")
                base = urlunparse(parsed._replace(path=path, params="", query="", fragment=""))
        return self._proxy_target(base)

    def resident_proxy_target(self, runtime: ResidentRuntime) -> SessionProxyTarget | None:
        """Resolve the resident service using its engine-supported OpenShell transport."""
        if runtime.engine is ResidentEngine.HERMES:
            return self._hermes_proxy_target(runtime)
        base = self._service_urls.get(str(runtime.id)) or str(
            runtime.backend_ref.get("service_url") or ""
        )
        return self._proxy_target(base)

    def _hermes_proxy_target(self, runtime: ResidentRuntime) -> SessionProxyTarget | None:
        runtime_id = str(runtime.id)
        forwarder = self._resident_forwarders.get(runtime_id)
        if forwarder is None:
            sandbox_id = str(runtime.backend_ref.get("id") or "")
            service_port = int(
                runtime.backend_ref.get("service_port") or HERMES_API_SERVER_DEFAULT_PORT
            )
            if not sandbox_id:
                return None
            forwarder = self._client.start_tcp_forward(
                sandbox_id=sandbox_id,
                target_port=service_port,
            )
            self._resident_forwarders[runtime_id] = forwarder
        return SessionProxyTarget(
            service_url=HERMES_INTERNAL_SERVICE_URL,
            connect_host=forwarder.host,
            connect_port=forwarder.port,
        )

    def _proxy_target(self, base: str | None) -> SessionProxyTarget | None:
        if not base:
            return None
        return SessionProxyTarget(
            service_url=base.rstrip("/"),
            connect_host=self._gateway_connect_host,
            connect_port=self._gateway_connect_port,
            connect_secure=self._gateway_connect_secure,
        )

    async def start(self, session: Session, spec: SessionSpec) -> PodStartResult:
        spec = self._with_brokered_credentials(spec)
        sandbox_name = self._sandbox_name(session)
        session_id = str(session.id)
        env = self._build_env(session, spec)
        credential_context = OpenShellCredentialContext(
            files={}, providers=(), environment={}, process_environment={}
        )
        grants: tuple[OpenShellProviderGrant, ...] = ()
        for env_key in SECRET_ENV_KEYS:
            env.pop(env_key, None)
        labels = {
            "app.kubernetes.io/managed-by": "volundr",
            "volundr.niuu.io/session": session_id,
            "volundr.niuu.io/runtime": self._runtime_from_spec(spec),
        }
        annotations: dict[str, str] = {}
        if spec.pod_spec:
            labels.update({str(key): str(value) for key, value in spec.pod_spec.labels.items()})
            annotations.update(self._supported_annotations_from_pod_spec(spec.pod_spec.annotations))
        self._warn_unsupported_pod_spec(session, spec)
        try:
            platform_providers = await self._resolve_platform_provider(session)
            grants = tuple(
                OpenShellProviderGrant(provider_name=name, profile_id=name)
                for name in platform_providers
            )
            credential_context = await self._resolve_credential_env(session, spec)
            env.update(credential_context.environment)
            process_env = {**env, **credential_context.process_environment}
            runtime_processes = _runtime_processes_from_spec(spec)
            provider_names = (*platform_providers, *credential_context.providers)
            grants = tuple(
                OpenShellProviderGrant(provider_name=name, profile_id=name)
                for name in provider_names
            )
            if grants:
                await asyncio.to_thread(self._client.ensure_providers_v2)
            sandbox = await asyncio.to_thread(
                self._client.create_sandbox,
                name=sandbox_name,
                image=self._sandbox_image,
                env=env,
                labels=labels,
                annotations=annotations,
                resources=self._resources_from_spec(spec),
                driver_config=self._driver_config_from_spec(spec),
                providers=provider_names,
                policy=self._sandbox_policy,
            )
            ready = await self._wait_for_sandbox_name(sandbox.name, self._ready_timeout)
            projected_files = dict(credential_context.files)
            for process in runtime_processes:
                projected_files.update(process.files)
            if projected_files:
                await asyncio.to_thread(
                    self._client.write_files,
                    sandbox_id=ready.id,
                    files=projected_files,
                )
            await self._bootstrap_workspace(
                ready.id,
                session,
                spec,
                env,
            )
            for process in runtime_processes:
                process_exit = await asyncio.to_thread(
                    self._client.exec_detached,
                    sandbox_id=ready.id,
                    command=process.command,
                    env={**process_env, **process.env},
                    log_path=process.log_path,
                )
                if process_exit != 0:
                    raise RuntimeError(
                        f"OpenShell runtime process {process.name!r} failed with exit "
                        f"{process_exit}"
                    )
            exit_code = await asyncio.to_thread(
                self._client.exec_detached,
                sandbox_id=ready.id,
                command=self._sandbox_command,
                env=process_env,
                log_path=self._command_log_path,
            )
            if exit_code != 0:
                raise RuntimeError(
                    f"OpenShell session command bootstrap failed with exit {exit_code}"
                )
            service_url = await asyncio.to_thread(
                self._client.expose_service,
                sandbox_name=sandbox_name,
                target_port=self._service_port,
                service=self._service_name,
            )
            if not service_url and not self._gateway_public_url:
                raise RuntimeError(
                    "OpenShell did not return an exposed service URL for the session"
                )
        except Exception:
            try:
                await self._cleanup_resources(sandbox_name, grants)
            except Exception:
                logger.exception("OpenShell launch rollback failed for session %s", session.id)
            raise
        if service_url:
            self._service_urls[session_id] = service_url.rstrip("/")
        self._provider_grants[session_id] = grants

        return PodStartResult(
            chat_endpoint=self._chat_endpoint(session),
            code_endpoint=self._code_endpoint(session),
            pod_name=sandbox_name,
        )

    async def stop(self, session: Session) -> bool:
        self._service_urls.pop(str(session.id), None)
        sandbox_name = self._sandbox_name(session)
        grants = self._provider_grants.pop(str(session.id), ())
        if not grants:
            sandbox = await asyncio.to_thread(self._client.get_sandbox, sandbox_name)
            if sandbox is not None:
                grants = tuple(
                    OpenShellProviderGrant(provider_name=name, profile_id=name)
                    for name in sandbox.providers
                    if name.startswith("volundr-")
                )
        return await self._cleanup_resources(sandbox_name, grants)

    async def status(self, session: Session) -> SessionStatus:
        sandbox = await asyncio.to_thread(self._client.get_sandbox, self._sandbox_name(session))
        if sandbox is None:
            return SessionStatus.STOPPED
        return _status_from_sandbox(sandbox)

    @property
    def backend(self) -> ResidentBackend:
        return ResidentBackend.OPENSHELL

    def supports(self, profile: ResidentDeploymentProfile) -> bool:
        """Run declared resident engines without unsupported sandbox suspension."""
        if (
            profile.backend is not ResidentBackend.OPENSHELL
            or ResidentCapability.RUNTIME_SUSPEND in profile.capabilities
        ):
            return False
        if profile.engine is ResidentEngine.RAVN:
            try:
                return resident_flock_profile_configured(profile, _resident_profile_values(profile))
            except RuntimeError:
                return False
        if profile.engine not in {ResidentEngine.OPENCLAW, ResidentEngine.HERMES}:
            return False
        try:
            values = _resident_profile_values(profile)
            openshell = values.get("openshell")
            return (
                isinstance(openshell, dict)
                and openshell.get("processMode") == "replace"
                and bool(_runtime_processes_from_values(values))
                and isinstance(openshell.get("service"), dict)
                and int(openshell["service"].get("port") or 0) > 0
            )
        except (RuntimeError, TypeError, ValueError):
            return False

    async def deploy(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        if not self.supports(profile):
            raise RuntimeError(f"OpenShell does not support resident profile {profile.id!r}")
        values = self._with_brokered_credential_values(_resident_profile_values(profile))
        subject = self._resident_subject(runtime)
        sandbox_name = self._resident_sandbox_name(runtime)
        env = self._resident_environment(runtime, values)
        env.update(resident_flock_environment(runtime))
        service_name, service_port = _resident_service(
            values, self._service_name, self._service_port
        )
        machine_credential: dict[str, str] | None = None
        if runtime.engine is ResidentEngine.OPENCLAW and self._credential_store is None:
            raise RuntimeError("OpenClaw residents require the configured credential store")
        if runtime.engine is ResidentEngine.HERMES and self._credential_store is None:
            raise RuntimeError("Hermes residents require the configured credential store")
        credential_context = OpenShellCredentialContext(
            files={}, providers=(), environment={}, process_environment={}
        )
        grants: tuple[OpenShellProviderGrant, ...] = ()
        sandbox = await asyncio.to_thread(self._client.get_sandbox, sandbox_name)
        if sandbox is not None:
            resident_label = dict(sandbox.labels or {}).get("volundr.niuu.io/resident")
            if resident_label != str(runtime.id):
                raise RuntimeError(
                    f"OpenShell sandbox {sandbox_name!r} is not owned by resident {runtime.id}"
                )
        resumed_deployment = sandbox is not None
        try:
            if runtime.engine is ResidentEngine.OPENCLAW:
                from volundr.adapters.outbound.openclaw_gateway import (
                    ensure_openclaw_machine_credential,
                )

                machine_credential = await ensure_openclaw_machine_credential(
                    self._credential_store, runtime
                )
                env.setdefault("OPENCLAW_STATE_DIR", f"{self._sandbox_workspace}/.openclaw")
            if runtime.engine is ResidentEngine.HERMES:
                from volundr.adapters.outbound.hermes_gateway import (
                    ensure_hermes_api_key,
                )

                machine_credential = {
                    "api_key": await ensure_hermes_api_key(self._credential_store, runtime)
                }
            platform_providers = await self._resolve_platform_provider(
                subject,
                api_urls=_resident_api_urls(values),
                binaries=_resident_platform_binaries(runtime),
            )
            credential_context = await self._resolve_credential_context(subject, values)
            provider_names = (*platform_providers, *credential_context.providers)
            grants = tuple(
                OpenShellProviderGrant(provider_name=name, profile_id=name)
                for name in provider_names
            )
            if grants:
                await asyncio.to_thread(self._client.ensure_providers_v2)
            env.update(credential_context.environment)
            mesh_labels, mesh_annotations = resident_mesh_pod_metadata(runtime)
            if not resumed_deployment:
                sandbox = await asyncio.to_thread(
                    self._client.create_sandbox,
                    name=sandbox_name,
                    image=_image_from_values(values, default=self._sandbox_image),
                    env=env,
                    labels={
                        "app.kubernetes.io/managed-by": "volundr",
                        "volundr.niuu.io/resident": str(runtime.id),
                        "volundr.niuu.io/runtime": runtime.engine.value,
                        **resident_flock_labels(runtime, prefix="volundr.niuu.io"),
                        **mesh_labels,
                    },
                    annotations=mesh_annotations,
                    resources=_resources_from_values(values, cpu=self._cpu, memory=self._memory),
                    driver_config=_driver_config_from_values(values),
                    providers=provider_names,
                    policy=self._sandbox_policy,
                )
            ready = await self._wait_for_sandbox_name(sandbox.name, self._ready_timeout)
            files = {
                **credential_context.files,
                **self._resident_config_files(runtime, values),
            }
            processes = self._resident_processes(runtime, values)
            for process in processes:
                files.update(_shared_resident_process_files(runtime, process.files))
            await asyncio.to_thread(
                self._client.write_files,
                sandbox_id=ready.id,
                files=files,
            )
            process_env = {**env, **credential_context.process_environment}
            if runtime.engine is ResidentEngine.OPENCLAW and machine_credential is not None:
                process_env["OPENCLAW_GATEWAY_TOKEN"] = machine_credential["gateway_token"]
            if runtime.engine is ResidentEngine.HERMES and machine_credential is not None:
                process_env[HERMES_API_SERVER_KEY_ENV] = machine_credential["api_key"]
            if resumed_deployment:
                await self._launch_missing_resident_processes(ready.id, process_env, processes)
            else:
                await self._launch_resident_processes(ready.id, process_env, processes)
            await self._wait_for_resident_processes(
                ready.id,
                processes,
                service_port=service_port,
            )
            if runtime.engine is ResidentEngine.HERMES:
                service_url = HERMES_INTERNAL_SERVICE_URL
            else:
                service_url = await asyncio.to_thread(
                    self._client.expose_service,
                    sandbox_name=sandbox_name,
                    target_port=service_port,
                    service=service_name,
                )
                if not service_url and not self._gateway_public_url:
                    raise RuntimeError("OpenShell did not return a resident service URL")
        except Exception:
            try:
                await self._cleanup_resources(sandbox_name, grants, service_name=service_name)
            except Exception:
                logger.exception("OpenShell resident rollback failed for %s", runtime.id)
            if runtime.engine is ResidentEngine.OPENCLAW and self._credential_store is not None:
                await self._credential_store.delete("resident", str(runtime.id), "openclaw-gateway")
            if runtime.engine is ResidentEngine.HERMES and self._credential_store is not None:
                from volundr.adapters.outbound.hermes_gateway import HERMES_CREDENTIAL_NAME

                await self._credential_store.delete(
                    "resident", str(runtime.id), HERMES_CREDENTIAL_NAME
                )
            raise

        resolved_service_url = (service_url or self._gateway_public_url).rstrip("/")
        self._service_urls[str(runtime.id)] = resolved_service_url
        self._provider_grants[str(runtime.id)] = grants
        return self._resident_observation(
            runtime,
            ready,
            service_url=resolved_service_url,
            processes_ready=True,
            service_name=service_name,
            service_port=service_port,
            process_names=tuple(process.name for process in processes),
        )

    async def reconcile(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        if not self.supports(profile):
            raise RuntimeError(f"OpenShell does not support resident profile {profile.id!r}")
        sandbox = await asyncio.to_thread(
            self._client.get_sandbox,
            self._resident_sandbox_name(runtime),
        )
        if sandbox is None:
            return ResidentRuntimeObservation(
                observed_state=ResidentObservedState.FAILED,
                backend_ref=dict(runtime.backend_ref),
                conditions=[
                    ResidentCondition(
                        type="SandboxReady",
                        status=ResidentConditionStatus.FALSE,
                        reason="NotFound",
                        message="OpenShell sandbox does not exist",
                    )
                ],
            )
        processes_ready = False
        values = self._with_brokered_credential_values(_resident_profile_values(profile))
        processes = self._resident_processes(runtime, values)
        service_name, service_port = _resident_service(
            values, self._service_name, self._service_port
        )
        if sandbox.ready:
            processes_ready = await self._resident_processes_ready(
                sandbox.id,
                processes,
                service_port=service_port,
            )
        service_url = str(runtime.backend_ref.get("service_url") or "")
        if not service_url and runtime.engine is ResidentEngine.HERMES:
            service_url = HERMES_INTERNAL_SERVICE_URL
        if not service_url:
            service_url = await asyncio.to_thread(
                self._client.expose_service,
                sandbox_name=self._resident_sandbox_name(runtime),
                target_port=service_port,
                service=service_name,
            )
            service_url = (service_url or self._gateway_public_url).rstrip("/")
            if service_url:
                self._service_urls[str(runtime.id)] = service_url
        return self._resident_observation(
            runtime,
            sandbox,
            service_url=service_url,
            processes_ready=processes_ready,
            service_name=service_name,
            service_port=service_port,
            process_names=tuple(process.name for process in processes),
        )

    async def restart(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        if not self.supports(profile):
            raise RuntimeError(f"OpenShell does not support resident profile {profile.id!r}")
        values = self._with_brokered_credential_values(_resident_profile_values(profile))
        sandbox = await asyncio.to_thread(
            self._client.get_sandbox,
            self._resident_sandbox_name(runtime),
        )
        if sandbox is None or not sandbox.ready:
            raise RuntimeError("OpenShell resident sandbox is not ready")
        processes = self._resident_processes(runtime, values)
        exit_code, output = await asyncio.to_thread(
            self._client.exec_script,
            sandbox_id=sandbox.id,
            script=_resident_stop_script(tuple(process.name for process in processes)),
            env={},
        )
        if exit_code != 0:
            raise RuntimeError(f"OpenShell resident process stop failed: {output.strip()}")
        files = self._resident_config_files(runtime, values)
        for process in processes:
            files.update(_shared_resident_process_files(runtime, process.files))
        await asyncio.to_thread(
            self._client.write_files,
            sandbox_id=sandbox.id,
            files=files,
        )
        env = self._resident_environment(runtime, values)
        if runtime.engine is ResidentEngine.OPENCLAW:
            if self._credential_store is None:
                raise RuntimeError("OpenClaw residents require the configured credential store")
            machine = await self._credential_store.get_value(
                "resident", str(runtime.id), "openclaw-gateway"
            )
            if not machine or not machine.get("gateway_token"):
                raise RuntimeError("OpenClaw resident machine credential is unavailable")
            env["OPENCLAW_GATEWAY_TOKEN"] = machine["gateway_token"]
        if runtime.engine is ResidentEngine.HERMES:
            from volundr.adapters.outbound.hermes_gateway import ensure_hermes_api_key

            if self._credential_store is None:
                raise RuntimeError("Hermes residents require the configured credential store")
            env[HERMES_API_SERVER_KEY_ENV] = await ensure_hermes_api_key(
                self._credential_store,
                runtime,
            )
        await self._launch_resident_processes(sandbox.id, env, processes)
        service_name, service_port = _resident_service(
            values, self._service_name, self._service_port
        )
        await self._wait_for_resident_processes(
            sandbox.id,
            processes,
            service_port=service_port,
        )
        return self._resident_observation(
            runtime,
            sandbox,
            service_url=str(runtime.backend_ref.get("service_url") or ""),
            processes_ready=True,
            service_name=service_name,
            service_port=service_port,
            process_names=tuple(process.name for process in processes),
        )

    async def suspend(self, runtime: ResidentRuntime) -> ResidentRuntimeObservation:
        raise RuntimeError("OpenShell resident suspension is unsupported")

    async def resume(self, runtime: ResidentRuntime) -> ResidentRuntimeObservation:
        raise RuntimeError("OpenShell resident suspension is unsupported")

    async def delete(self, runtime: ResidentRuntime) -> bool:
        runtime_id = str(runtime.id)
        self._service_urls.pop(runtime_id, None)
        forwarder = self._resident_forwarders.pop(runtime_id, None)
        if forwarder is not None:
            await asyncio.to_thread(forwarder.close)
        sandbox_name = self._resident_sandbox_name(runtime)
        grants = self._provider_grants.pop(runtime_id, ())
        if not grants:
            sandbox = await asyncio.to_thread(self._client.get_sandbox, sandbox_name)
            if sandbox is not None:
                grants = tuple(
                    OpenShellProviderGrant(provider_name=name, profile_id=name)
                    for name in sandbox.providers
                    if name.startswith("volundr-")
                )
        deleted = await self._cleanup_resources(
            sandbox_name,
            grants,
            service_name=str(runtime.backend_ref.get("service_name") or self._service_name),
        )
        if runtime.engine is ResidentEngine.OPENCLAW and self._credential_store is not None:
            await self._credential_store.delete("resident", runtime_id, "openclaw-gateway")
        if runtime.engine is ResidentEngine.HERMES and self._credential_store is not None:
            from volundr.adapters.outbound.hermes_gateway import (
                HERMES_CREDENTIAL_NAME,
                HERMES_LEGACY_CREDENTIAL_NAME,
            )

            await self._credential_store.delete("resident", runtime_id, HERMES_CREDENTIAL_NAME)
            await self._credential_store.delete(
                "resident", runtime_id, HERMES_LEGACY_CREDENTIAL_NAME
            )
        return deleted

    async def logs(
        self,
        runtime: ResidentRuntime,
        *,
        lines: int,
        sources: tuple[str, ...],
        min_level: str,
    ) -> ResidentLogPage:
        sandbox = await asyncio.to_thread(
            self._client.get_sandbox,
            self._resident_sandbox_name(runtime),
        )
        if sandbox is None:
            raise RuntimeError("OpenShell resident sandbox does not exist")
        native_page = await asyncio.to_thread(
            self._client.get_sandbox_logs,
            sandbox.id,
            lines=lines,
            sources=sources,
            min_level=min_level,
        )
        configured_names = runtime.backend_ref.get("process_names")
        if not configured_names and runtime.engine is ResidentEngine.OPENCLAW:
            configured_names = ("openclaw",)
        if not configured_names and runtime.engine is ResidentEngine.HERMES:
            configured_names = ("hermes",)
        if not configured_names:
            configured_names = ("skuld", "ravn")
        process_names = tuple(
            str(source) for source in configured_names if not sources or str(source) in sources
        )
        process_sources = process_names
        if not process_sources:
            return native_page
        exit_code, output = await asyncio.to_thread(
            self._client.exec_script,
            sandbox_id=sandbox.id,
            script=_resident_process_log_script(lines, process_sources),
            env={},
        )
        if exit_code != 0:
            raise RuntimeError("OpenShell resident process logs could not be read")
        process_entries = _resident_process_log_entries(output, min_level=min_level)
        entries = [*native_page.entries, *process_entries]
        entries.sort(key=lambda entry: entry.timestamp_ms)
        return ResidentLogPage(
            entries=entries[-lines:],
            buffer_total=native_page.buffer_total + len(process_entries),
        )

    async def close(self) -> None:
        forwarders = tuple(self._resident_forwarders.values())
        self._resident_forwarders.clear()
        for forwarder in forwarders:
            await asyncio.to_thread(forwarder.close)
        await asyncio.to_thread(self._client.close)

    async def _launch_resident_processes(
        self,
        sandbox_id: str,
        env: dict[str, str],
        processes: Sequence[OpenShellRuntimeProcess],
    ) -> None:
        for process in processes:
            exit_code = await asyncio.to_thread(
                self._client.exec_detached,
                sandbox_id=sandbox_id,
                command=process.command,
                env={**env, **process.env},
                log_path=process.log_path,
                pid_path=f"/sandbox/.volundr/{process.name}.pid",
            )
            if exit_code != 0:
                raise RuntimeError(
                    f"OpenShell resident process {process.name!r} failed with exit {exit_code}"
                )

    async def _launch_missing_resident_processes(
        self,
        sandbox_id: str,
        env: dict[str, str],
        processes: Sequence[OpenShellRuntimeProcess],
    ) -> None:
        for process in processes:
            if await self._resident_processes_ready(sandbox_id, (process,)):
                continue
            await self._launch_resident_processes(sandbox_id, env, (process,))

    def _resident_processes(
        self,
        runtime: ResidentRuntime,
        values: dict[str, Any],
    ) -> tuple[OpenShellRuntimeProcess, ...]:
        configured = _runtime_processes_from_values(values)
        openshell = values.get("openshell")
        replace = isinstance(openshell, dict) and openshell.get("processMode") == "replace"
        if replace:
            if not configured:
                raise RuntimeError("Replacing resident processes requires at least one process")
            return configured
        defaults = (
            OpenShellRuntimeProcess(
                name="skuld",
                command=tuple(self._sandbox_command),
                env={
                    "NIUU_CONFIG": "/sandbox/.volundr/skuld.yaml",
                    "SKULD_BOOTSTRAP_FOREGROUND": "true",
                },
                files={},
                log_path="/sandbox/.volundr/skuld.log",
            ),
            OpenShellRuntimeProcess(
                name="ravn",
                command=(
                    "sh",
                    "-lc",
                    'export RAVN__GATEWAY__PLATFORM__PAT_TOKEN="$NIUU_VOLUNDR_ACCESS_TOKEN"; '
                    "exec /opt/niuu/bin/python -m ravn daemon "
                    "--config /sandbox/.volundr/ravn.yaml "
                    f"--persona {shlex.quote(runtime.persona_name or 'product-steward')}",
                ),
                env={},
                files={},
                log_path="/sandbox/.volundr/ravn.log",
            ),
        )
        return (*defaults, *configured)

    async def _resident_processes_ready(
        self,
        sandbox_id: str,
        processes: Sequence[OpenShellRuntimeProcess],
        *,
        service_port: int | None = None,
    ) -> bool:
        exit_code, _ = await asyncio.to_thread(
            self._client.exec_script,
            sandbox_id=sandbox_id,
            script=_resident_health_script(
                tuple(process.name for process in processes),
                service_port=service_port,
            ),
            env={},
        )
        return exit_code == 0

    async def _wait_for_resident_processes(
        self,
        sandbox_id: str,
        processes: Sequence[OpenShellRuntimeProcess],
        *,
        service_port: int | None = None,
    ) -> None:
        deadline = time.monotonic() + self._ready_timeout
        while time.monotonic() < deadline:
            if await self._resident_processes_ready(
                sandbox_id,
                processes,
                service_port=service_port,
            ):
                return
            await asyncio.sleep(READY_POLL_INTERVAL)
        raise TimeoutError(
            f"OpenShell resident processes were not ready within {self._ready_timeout}s"
        )

    def _resident_config_files(
        self,
        runtime: ResidentRuntime,
        values: dict[str, Any],
    ) -> dict[str, bytes]:
        if runtime.engine is ResidentEngine.HERMES:
            return {
                f"{self._sandbox_workspace}/.hermes/config.yaml": yaml.safe_dump(
                    _resident_hermes_config(runtime, values),
                    sort_keys=False,
                ).encode()
            }
        if runtime.engine is not ResidentEngine.RAVN:
            return {}
        return {
            "/sandbox/.volundr/skuld.yaml": yaml.safe_dump(
                _resident_skuld_config(
                    runtime,
                    values,
                    self._service_port,
                    self._volundr_api_url,
                ),
                sort_keys=False,
            ).encode(),
            "/sandbox/.volundr/ravn.yaml": yaml.safe_dump(
                _resident_ravn_config(runtime, values, self._service_port),
                sort_keys=False,
            ).encode(),
        }

    def _resident_environment(
        self,
        runtime: ResidentRuntime,
        values: dict[str, Any],
    ) -> dict[str, str]:
        env = {
            "HOME": self._sandbox_home,
            "CODEX_HOME": f"{self._sandbox_home}/.codex",
            "CLAUDE_CONFIG_DIR": f"{self._sandbox_home}/.claude",
            "SKULD__SESSION__ID": str(runtime.id),
            "SKULD__SESSION__NAME": runtime.name,
            "SKULD__SESSION__OWNER_ID": runtime.owner_id,
            "SKULD__SESSION__TENANT_ID": runtime.tenant_id,
            "SKULD__SESSION__MODEL": runtime.model,
            "SKULD__SESSION__WORKSPACE_DIR": self._sandbox_workspace,
            "SKULD__PERSISTENCE_MOUNT_PATH": self._sandbox_workspace,
            "SKULD__HOST": "0.0.0.0",
            "SKULD__PORT": str(self._service_port),
            "RAVN_STATE_DIR": f"{self._sandbox_workspace}/.ravn",
        }
        if runtime.engine is ResidentEngine.HERMES:
            env["HERMES_HOME"] = f"{self._sandbox_workspace}/.hermes"
        broker = values.get("broker")
        if isinstance(broker, dict):
            env.update(_resident_broker_environment(broker))
        env.update(self._brokered_credential_environment_values(values))
        extra_env = values.get("env")
        if isinstance(extra_env, dict):
            env.update(_string_dict(extra_env))
        for secret_key in SECRET_ENV_KEYS:
            env.pop(secret_key, None)
        return env

    def _resident_observation(
        self,
        runtime: ResidentRuntime,
        sandbox: OpenShellSandbox,
        *,
        service_url: str,
        processes_ready: bool,
        service_name: str,
        service_port: int,
        process_names: Sequence[str],
    ) -> ResidentRuntimeObservation:
        observed_state = _resident_state_from_sandbox(sandbox, processes_ready)
        conditions = [
            ResidentCondition(
                type="SandboxReady",
                status=(
                    ResidentConditionStatus.TRUE if sandbox.ready else ResidentConditionStatus.FALSE
                ),
                reason="Ready" if sandbox.ready else "Provisioning",
            ),
            ResidentCondition(
                type="ProcessesReady",
                status=(
                    ResidentConditionStatus.TRUE
                    if processes_ready
                    else ResidentConditionStatus.FALSE
                ),
                reason="Healthy" if processes_ready else "Unavailable",
            ),
        ]
        endpoints = []
        if service_url:
            if runtime.engine is ResidentEngine.RAVN:
                endpoints.append(
                    ResidentEndpoint(
                        kind="chat",
                        protocol="skuld-v1",
                        url=f"/s/{runtime.id}/session",
                    )
                )
            elif runtime.engine is ResidentEngine.OPENCLAW:
                endpoints.append(
                    ResidentEndpoint(
                        kind="sessions",
                        protocol="openclaw-gateway-v4",
                        url=f"/api/v1/forge/resident-runtimes/{runtime.id}/sessions",
                    )
                )
            else:
                endpoints.append(
                    ResidentEndpoint(
                        kind="sessions",
                        protocol="hermes-api-server-v1",
                        url=f"/api/v1/forge/resident-runtimes/{runtime.id}/sessions",
                    )
                )
        return ResidentRuntimeObservation(
            observed_state=observed_state,
            backend_ref={
                "kind": "OpenShellSandbox",
                "id": sandbox.id,
                "name": sandbox.name,
                "service_url": service_url,
                "service_name": service_name,
                "service_port": service_port,
                "process_names": list(process_names),
            },
            endpoints=endpoints,
            conditions=conditions,
        )

    async def approve_resident_device(
        self,
        runtime: ResidentRuntime,
        *,
        request_id: str,
        gateway_token: str,
    ) -> None:
        """Approve one challenged Volundr device from inside its owning sandbox."""
        sandbox = await asyncio.to_thread(
            self._client.get_sandbox, self._resident_sandbox_name(runtime)
        )
        if sandbox is None or not sandbox.ready:
            raise RuntimeError("OpenClaw resident sandbox is not ready for device pairing")
        exit_code, output = await asyncio.to_thread(
            self._client.exec_script,
            sandbox_id=sandbox.id,
            script=f"openclaw devices approve {shlex.quote(request_id)}",
            env={
                "OPENCLAW_GATEWAY_TOKEN": gateway_token,
                "OPENCLAW_STATE_DIR": f"{self._sandbox_workspace}/.openclaw",
            },
        )
        if exit_code != 0:
            raise RuntimeError(f"OpenClaw device pairing failed: {output.strip()}")

    @staticmethod
    def _resident_sandbox_name(runtime: ResidentRuntime) -> str:
        prefix = "resident-"
        suffix_length = MAX_SANDBOX_ROUTING_NAME_LENGTH - len(prefix)
        return f"{prefix}{runtime.id.hex[:suffix_length]}"

    async def exchange_credential_grant(
        self,
        *,
        client_assertion: str,
        client_assertion_type: str,
        grant_type: str,
        audience: str,
        scope: str,
    ) -> OpenShellCredentialGrantToken:
        if grant_type != "client_credentials":
            raise ValueError("unsupported grant_type")
        if client_assertion_type != OAUTH_CLIENT_ASSERTION_TYPE:
            raise ValueError("unsupported client_assertion_type")
        if not client_assertion:
            raise ValueError("missing client_assertion")
        is_platform_grant = audience.startswith(PLATFORM_GRANT_AUDIENCE_PREFIX)
        if not is_platform_grant and not audience.startswith(GRANT_AUDIENCE_PREFIX):
            raise ValueError("unsupported credential audience")
        prefix = PLATFORM_GRANT_AUDIENCE_PREFIX if is_platform_grant else GRANT_AUDIENCE_PREFIX
        provider_name = audience.removeprefix(prefix)
        if not provider_name.startswith("volundr-"):
            raise ValueError("invalid credential audience")

        try:
            claims = await self._spiffe_verifier.verify(client_assertion)
        except Exception as exc:
            raise ValueError("client_assertion is not a valid SPIFFE JWT-SVID") from exc
        subject = str(claims.get("sub") or "")
        if not subject.startswith(self._spiffe_subject_prefix):
            raise ValueError("JWT-SVID subject is not an OpenShell sandbox")
        sandbox_id = subject.removeprefix(self._spiffe_subject_prefix)
        try:
            UUID(sandbox_id)
        except ValueError as exc:
            raise ValueError("JWT-SVID sandbox ID is invalid") from exc

        sandbox = await asyncio.to_thread(self._client.get_sandbox_by_id, sandbox_id)
        if sandbox is None or provider_name not in sandbox.providers:
            raise ValueError("credential provider is not attached to this sandbox")
        provider = await asyncio.to_thread(self._client.get_provider, provider_name)
        if provider is None:
            raise ValueError("credential provider does not exist")
        profile = await asyncio.to_thread(self._client.get_provider_profile, str(provider.type))
        if profile is None or not _profile_authorizes_audience(profile, audience):
            raise ValueError("credential provider does not authorize this audience")

        config = dict(provider.config)
        workload = await self._grant_subject(config, sandbox.labels or {})
        credential_name = str(config.get("volundr_credential_name") or "")
        credential_field = str(config.get("volundr_credential_field") or "")
        if is_platform_grant:
            return self._issue_platform_token(
                workload=workload,
                workload_subject=subject,
                sandbox_id=sandbox_id,
            )

        if self._credential_store is None:
            raise ValueError("credential store is unavailable")
        values = await self._credential_store.get_value("user", workload.owner_id, credential_name)
        value = values.get(credential_field) if values else None
        if not value or "\x00" in value or "\r" in value or "\n" in value:
            raise ValueError("credential field is unavailable for this session")
        return OpenShellCredentialGrantToken(access_token=value)

    def _issue_platform_token(
        self,
        *,
        workload: OpenShellWorkloadSubject,
        workload_subject: str,
        sandbox_id: str,
    ) -> OpenShellCredentialGrantToken:
        if self._workload_token_issuer is None:
            raise ValueError("workload token issuer is unavailable")
        issued = self._workload_token_issuer.issue_token(
            principal=Principal(
                user_id=workload.owner_id,
                email="",
                tenant_id=workload.tenant_id,
                roles=list(self._workload_roles),
            ),
            workload_subject=workload_subject,
            workload_name=f"openshell-{workload.kind}-{workload.id}",
            audiences=list(self._workload_audiences),
            token_use=(
                OPENSHELL_SESSION_TOKEN_USE
                if workload.kind == "session"
                else OPENSHELL_RESIDENT_TOKEN_USE
            ),
            claims={
                f"{workload.kind}_id": str(workload.id),
                "sandbox_id": sandbox_id,
            },
        )
        expires_in = max(1, issued.expires_at - int(time.time()))
        return OpenShellCredentialGrantToken(
            access_token=issued.token,
            expires_in=expires_in,
        )

    async def _cleanup_resources(
        self,
        sandbox_name: str,
        grants: Sequence[OpenShellProviderGrant],
        *,
        service_name: str | None = None,
    ) -> bool:
        errors: list[Exception] = []
        try:
            await asyncio.to_thread(
                self._client.delete_service,
                sandbox_name=sandbox_name,
                service=service_name or self._service_name,
            )
        except Exception as exc:
            errors.append(exc)
        try:
            deleted = await asyncio.to_thread(self._client.delete_sandbox, sandbox_name)
        except Exception as exc:
            errors.append(exc)
            deleted = False
        else:
            try:
                await self._wait_for_sandbox_deleted(sandbox_name)
            except Exception as exc:
                errors.append(exc)
            else:
                for grant in reversed(grants):
                    try:
                        await asyncio.to_thread(self._client.delete_provider_grant, grant)
                    except Exception as exc:
                        errors.append(exc)
        if errors:
            raise RuntimeError(
                f"OpenShell resource cleanup failed for {sandbox_name}: "
                + "; ".join(str(error) for error in errors)
            )
        return deleted

    async def _wait_for_sandbox_deleted(self, sandbox_name: str) -> None:
        deadline = time.monotonic() + self._resource_delete_timeout
        while await asyncio.to_thread(self._client.get_sandbox, sandbox_name) is not None:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"OpenShell sandbox {sandbox_name!r} was not deleted within "
                    f"{self._resource_delete_timeout:g}s"
                )
            await asyncio.sleep(READY_POLL_INTERVAL)

    async def _resolve_credential_env(
        self,
        session: Session,
        spec: SessionSpec,
    ) -> OpenShellCredentialContext:
        return await self._resolve_credential_context(
            self._session_subject(session),
            spec.values,
        )

    async def _resolve_credential_context(
        self,
        subject: OpenShellWorkloadSubject,
        values: dict[str, Any],
    ) -> OpenShellCredentialContext:
        mappings = _credential_mappings_from_values(values)
        if not mappings:
            return OpenShellCredentialContext(
                files={}, providers=(), environment={}, process_environment={}
            )
        if not subject.owner_id:
            raise RuntimeError("OpenShell credential mappings require a workload owner")
        if self._credential_store is None:
            raise RuntimeError("OpenShell credential mappings require a credential store")

        files: dict[str, bytes] = {}
        providers: list[str] = []
        environment: dict[str, str] = {}
        process_environment: dict[str, str] = {}
        try:
            for mapping in mappings:
                await self._resolve_credential_mapping(
                    subject,
                    mapping,
                    files=files,
                    providers=providers,
                    process_environment=process_environment,
                )
        except Exception:
            for provider_name in reversed(providers):
                try:
                    await asyncio.to_thread(
                        self._client.delete_provider_grant,
                        OpenShellProviderGrant(provider_name, provider_name),
                    )
                except Exception:
                    logger.exception("Failed to roll back OpenShell provider %s", provider_name)
            raise

        return OpenShellCredentialContext(
            files=files,
            providers=tuple(providers),
            environment=environment,
            process_environment=process_environment,
        )

    async def _resolve_platform_provider(
        self,
        workload: Session | OpenShellWorkloadSubject,
        *,
        api_urls: Sequence[str] = (),
        binaries: Sequence[str] = (),
    ) -> tuple[str, ...]:
        subject = self._session_subject(workload) if isinstance(workload, Session) else workload
        if not self._volundr_api_url:
            return ()
        if not subject.owner_id:
            raise RuntimeError("OpenShell platform reporting requires a workload owner")
        if self._workload_token_issuer is None or not self._workload_token_issuer.enabled:
            raise RuntimeError("OpenShell platform reporting requires a workload token issuer")

        provider_name = _provider_grant_name(
            session_id=str(subject.id),
            credential_name="volundr-platform",
            field_name="access-token",
            env_name=PLATFORM_ACCESS_TOKEN_ENV,
        )
        profile = _platform_provider_profile(
            profile_id=provider_name,
            token_endpoint=self._credential_token_endpoint,
            api_urls=(self._volundr_api_url, *api_urls),
            additional_binaries=binaries,
        )
        await asyncio.to_thread(
            self._client.create_provider_grant,
            profile=profile,
            provider_name=provider_name,
            config=self._grant_binding(subject, volundr_grant_kind="platform"),
        )
        return (provider_name,)

    async def _resolve_credential_mapping(
        self,
        subject: OpenShellWorkloadSubject,
        mapping: dict[str, Any],
        *,
        files: dict[str, bytes],
        providers: list[str],
        process_environment: dict[str, str],
    ) -> None:
        credential_name = str(mapping.get("credentialName") or mapping.get("credential_name") or "")
        if not credential_name:
            return
        env_mappings = _string_dict(mapping.get("envMappings") or mapping.get("env_mappings") or {})
        file_mappings = _string_dict(
            mapping.get("fileMappings") or mapping.get("file_mappings") or {}
        )
        if not env_mappings and not file_mappings:
            return
        stored = await self._credential_store.get(
            "user",
            subject.owner_id,
            credential_name,
        )
        if stored is None:
            raise RuntimeError(
                f"Credential {credential_name!r} not found for OpenShell session launch"
            )
        requested_fields = set(env_mappings.values()) | set(file_mappings.values())
        missing_fields = sorted(requested_fields - set(stored.keys))
        if missing_fields:
            raise RuntimeError(
                f"Credential {credential_name!r} does not contain fields: "
                + ", ".join(missing_fields)
            )
        if mapping.get("materializeEnvironment") or mapping.get("materialize_environment"):
            values = await self._credential_store.get_value(
                "user",
                subject.owner_id,
                credential_name,
            )
            for env_name, field_name in env_mappings.items():
                value = values.get(field_name) if values else None
                if not value:
                    raise RuntimeError(
                        f"Credential {credential_name!r} does not contain field {field_name!r}"
                    )
                process_environment[env_name] = value
        for env_name, field_name in env_mappings.items():
            if not _valid_env_name(env_name):
                raise RuntimeError(
                    f"Credential {credential_name!r} maps to invalid env var {env_name!r}"
                )
            provider_name = _provider_grant_name(
                session_id=str(subject.id),
                credential_name=credential_name,
                field_name=field_name,
                env_name=env_name,
            )
            profile = _provider_profile(
                profile_id=provider_name,
                env_name=env_name,
                token_endpoint=self._credential_token_endpoint,
                target_config=mapping.get("provider"),
            )
            if provider_name in providers:
                continue
            providers.append(provider_name)
            await asyncio.to_thread(
                self._client.create_provider_grant,
                profile=profile,
                provider_name=provider_name,
                config=self._grant_binding(
                    subject,
                    volundr_credential_name=credential_name,
                    volundr_credential_field=field_name,
                ),
            )

        if file_mappings:
            values = await self._credential_store.get_value(
                "user",
                subject.owner_id,
                credential_name,
            )
            if not values:
                raise RuntimeError(
                    f"Credential {credential_name!r} not found for OpenShell session launch"
                )
            for destination, field_name in file_mappings.items():
                value = values.get(field_name)
                if not value:
                    raise RuntimeError(
                        f"Credential {credential_name!r} does not contain field {field_name!r}"
                    )
                if "\x00" in value:
                    raise RuntimeError(
                        f"Credential {credential_name!r} field {field_name!r} contains a null byte"
                    )
                sandbox_path = _sandbox_credential_path(
                    destination,
                    sandbox_home=self._sandbox_home,
                )
                if sandbox_path.endswith(AGENT_AUTH_FILE_SUFFIXES):
                    raise RuntimeError(
                        f"OpenShell agent authentication file {destination!r} must use a "
                        "dynamic provider"
                    )
                files[sandbox_path] = value.encode("utf-8")

    async def _bootstrap_workspace(
        self,
        sandbox_id: str,
        session: Session,
        spec: SessionSpec,
        env: dict[str, str],
    ) -> None:
        script = self._workspace_bootstrap_script(session, spec)
        if not script:
            return
        exit_code, output = await asyncio.to_thread(
            self._client.exec_script,
            sandbox_id=sandbox_id,
            script=script,
            env=env,
        )
        if exit_code != 0:
            redacted_output = _redact_secret_url(output)
            raise RuntimeError(f"OpenShell workspace bootstrap failed: {redacted_output}")

    async def wait_for_ready(self, session: Session, timeout: float) -> SessionStatus:
        try:
            await self._wait_for_sandbox_name(self._sandbox_name(session), timeout)
        except (RuntimeError, TimeoutError):
            return SessionStatus.FAILED
        return await self.status(session)

    async def _wait_for_sandbox_name(self, name: str, timeout: float) -> OpenShellSandbox:
        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            sandbox = await asyncio.to_thread(self._client.get_sandbox, name)
            if sandbox is not None:
                status = _status_from_sandbox(sandbox)
                if status == SessionStatus.RUNNING:
                    return sandbox
                if status == SessionStatus.FAILED:
                    raise RuntimeError(f"OpenShell sandbox {name} entered error phase")
            await asyncio.sleep(READY_POLL_INTERVAL)
        raise TimeoutError(f"OpenShell sandbox {name} was not ready within {timeout}s")

    def _build_env(self, session: Session, spec: SessionSpec) -> dict[str, str]:
        env = LocalProcessPodManager._build_env(spec, Path(self._sandbox_workspace))
        env.update(self._brokered_credential_environment(spec))
        env["SKULD__SESSION__ID"] = str(session.id)
        env["SKULD__SESSION__NAME"] = session.name
        env["SKULD__SESSION__WORKSPACE_DIR"] = self._sandbox_workspace
        env["SKULD__HOST"] = "127.0.0.1"
        env["SKULD__PORT"] = str(self._service_port)
        env["SKULD__PERSISTENCE_MOUNT_PATH"] = self._sandbox_workspace
        if session.owner_id:
            env["SKULD__SESSION__OWNER_ID"] = session.owner_id
        if session.tenant_id:
            env["SKULD__SESSION__TENANT_ID"] = session.tenant_id
        if session.model:
            env["SKULD__SESSION__MODEL"] = session.model
        env["SKULD__VOLUNDR_API_URL"] = str(
            spec.values.get("volundr", {}).get("apiUrl") or self._volundr_api_url
        )

        sandbox_env = {key: value for key, value in env.items() if _safe_env_var(key, value)}

        extra_env = spec.values.get("env", {})
        if isinstance(extra_env, dict):
            for key, value in extra_env.items():
                key_str = str(key)
                value_str = str(value)
                if "\x00" not in key_str and "\x00" not in value_str:
                    sandbox_env[key_str] = value_str

        if spec.pod_spec and spec.pod_spec.env:
            for entry in spec.pod_spec.env:
                env_name = str(entry.get("name") or "")
                if not env_name:
                    continue
                if "value" in entry:
                    env_value = str(entry.get("value") or "")
                    if "\x00" not in env_name and "\x00" not in env_value:
                        sandbox_env[env_name] = env_value
                elif "valueFrom" in entry:
                    logger.warning(
                        "OpenShell gateway adapter cannot translate valueFrom env %s "
                        "for session %s",
                        env_name,
                        session.id,
                    )

        return sandbox_env

    def _workspace_bootstrap_script(self, session: Session, spec: SessionSpec) -> str:
        if session.source.type != "git" or not session.source.repo:
            return f"mkdir -p {shlex.quote(self._sandbox_workspace)}"

        git_cfg = spec.values.get("git", {})
        if not isinstance(git_cfg, dict):
            git_cfg = {}
        repo_url = _strip_url_credentials(
            str(git_cfg.get("repoUrl") or session.source.repo).strip()
        )
        clone_url = _strip_url_credentials(
            str(git_cfg.get("cloneUrl") or _public_clone_url(repo_url)).strip()
        )
        branch = str(git_cfg.get("branch") or session.source.branch or "").strip()
        base_branch = str(git_cfg.get("baseBranch") or session.source.base_branch or "").strip()
        workspace = self._sandbox_workspace

        return f"""\
set -eu
export GIT_TERMINAL_PROMPT=0
WORKSPACE={shlex.quote(workspace)}
CLONE_URL={shlex.quote(clone_url)}
REPO_URL={shlex.quote(repo_url)}
BRANCH={shlex.quote(branch)}
BASE_BRANCH={shlex.quote(base_branch)}
mkdir -p "$WORKSPACE"
git config --global --add safe.directory "$WORKSPACE" >/dev/null 2>&1 || true
git config --system --add safe.directory "$WORKSPACE" >/dev/null 2>&1 || true
HOME=/root git config --global --add safe.directory "$WORKSPACE" >/dev/null 2>&1 || true
if [ -d "$WORKSPACE/.git" ]; then
  if git -C "$WORKSPACE" rev-parse --verify HEAD >/dev/null 2>&1; then
    echo "Workspace already contains a git repository, skipping clone"
    exit 0
  fi
  echo "Workspace contains an incomplete git repository, resuming clone"
else
  git init "$WORKSPACE"
fi
if git -C "$WORKSPACE" remote get-url origin >/dev/null 2>&1; then
  git -C "$WORKSPACE" remote set-url origin "$CLONE_URL"
else
  git -C "$WORKSPACE" remote add origin "$CLONE_URL"
fi
export GIT_AUTH_TOKEN="${{GITHUB_TOKEN:-${{GITHUB_PERSONAL_ACCESS_TOKEN:-}}}}"
if [ -n "$GIT_AUTH_TOKEN" ]; then
  git -C "$WORKSPACE" config credential.helper \\
    '!f() {{ echo "username=x-access-token"; echo "password=$GIT_AUTH_TOKEN"; }}; f'
fi
attempt=1
until git -C "$WORKSPACE" fetch origin; do
  if [ "$attempt" -ge {BOOTSTRAP_GIT_ATTEMPTS} ]; then
    exit 1
  fi
  echo "Waiting for OpenShell outbound git access (attempt $attempt)"
  sleep $((attempt < 10 ? attempt * 2 : 20))
  attempt=$((attempt + 1))
done
if [ -n "$BRANCH" ] && \
  git -C "$WORKSPACE" rev-parse --verify "origin/$BRANCH" >/dev/null 2>&1; then
  git -C "$WORKSPACE" checkout -B "$BRANCH" "origin/$BRANCH"
else
  if [ -n "$BASE_BRANCH" ] && \
    git -C "$WORKSPACE" rev-parse --verify "origin/$BASE_BRANCH" >/dev/null 2>&1; then
    FALLBACK="$BASE_BRANCH"
  else
    FALLBACK=$(git -C "$WORKSPACE" remote show origin | sed -n 's/.*HEAD branch: //p')
    FALLBACK=${{FALLBACK:-main}}
  fi
  git -C "$WORKSPACE" checkout -B "$FALLBACK" "origin/$FALLBACK"
  if [ -n "$BRANCH" ] && [ "$BRANCH" != "$FALLBACK" ]; then
    git -C "$WORKSPACE" checkout -b "$BRANCH"
  fi
fi
git -C "$WORKSPACE" remote set-url origin "$REPO_URL"
echo "Workspace ready at $WORKSPACE"
"""

    def _resources_from_spec(self, spec: SessionSpec) -> dict[str, Any]:
        return _resources_from_values(spec.values, cpu=self._cpu, memory=self._memory)

    @staticmethod
    def _driver_config_from_spec(spec: SessionSpec) -> dict[str, Any]:
        return _driver_config_from_values(spec.values)

    def _chat_endpoint(self, session: Session) -> str:
        base = self._service_urls.get(str(session.id)) or self._gateway_public_url
        if not base:
            raise RuntimeError(f"OpenShell session {session.id} has no exposed service URL")
        return _service_ws_url(base, "/session")

    def _code_endpoint(self, session: Session) -> str:
        base = self._service_urls.get(str(session.id)) or self._gateway_public_url
        if not base:
            raise RuntimeError(f"OpenShell session {session.id} has no exposed service URL")
        return f"{base}/"

    @staticmethod
    def _warn_unsupported_pod_spec(session: Session, spec: SessionSpec) -> None:
        if not spec.pod_spec:
            return
        unsupported = []
        openshell_values = spec.values.get("openshell")
        translates_processes = isinstance(openshell_values, dict) and bool(
            openshell_values.get("processes")
        )
        if spec.pod_spec.volumes and not translates_processes:
            unsupported.append("volumes")
        if spec.pod_spec.volume_mounts and not translates_processes:
            unsupported.append("volume_mounts")
        if spec.pod_spec.init_containers and not translates_processes:
            unsupported.append("init_containers")
        if spec.pod_spec.extra_containers and not translates_processes:
            unsupported.append("extra_containers")
        if spec.pod_spec.service_account:
            unsupported.append("service_account")
        if unsupported:
            logger.warning(
                "OpenShell gateway adapter cannot translate pod_spec fields for session %s: %s",
                session.id,
                ", ".join(unsupported),
            )

    @staticmethod
    def _supported_annotations_from_pod_spec(annotations: dict[str, str]) -> dict[str, str]:
        supported: dict[str, str] = {}
        for key, value in annotations.items():
            key_str = str(key)
            if key_str.startswith(SECRET_INJECTION_ANNOTATION_PREFIXES):
                continue
            supported[key_str] = str(value)
        return supported

    @staticmethod
    def _sandbox_name(session: Session) -> str:
        return f"forge-{session.id.hex[:22]}"

    @staticmethod
    def _runtime_from_spec(spec: SessionSpec) -> str:
        broker = spec.values.get("broker", {})
        if isinstance(broker, dict):
            return str(broker.get("cliType") or broker.get("runtime") or "skuld")
        return "skuld"


def _provider_grant_name(
    *,
    session_id: str,
    credential_name: str,
    field_name: str,
    env_name: str,
) -> str:
    digest = hashlib.sha256(
        f"{session_id}\0{credential_name}\0{field_name}\0{env_name}".encode()
    ).hexdigest()[:12]
    return f"volundr-{UUID(session_id).hex[:12]}-{digest}"


def _runtime_processes_from_spec(spec: SessionSpec) -> tuple[OpenShellRuntimeProcess, ...]:
    return _runtime_processes_from_values(spec.values)


def _runtime_processes_from_values(
    values: dict[str, Any],
) -> tuple[OpenShellRuntimeProcess, ...]:
    return tuple(
        OpenShellRuntimeProcess(
            name=process.name,
            command=process.command,
            env=process.env,
            files=process.files,
            log_path=process.log_path,
        )
        for process in _shared_runtime_processes_from_values(values)
    )


def _resident_profile_values(profile: ResidentDeploymentProfile) -> dict[str, Any]:
    return _shared_resident_profile_values(profile.id, profile.deployment)


def _resident_service(
    values: dict[str, Any],
    default_name: str,
    default_port: int,
) -> tuple[str, int]:
    return _shared_resident_service(values, default_name, default_port)


def _resident_skuld_config(
    runtime: ResidentRuntime,
    values: dict[str, Any],
    service_port: int,
    volundr_api_url: str,
) -> dict[str, Any]:
    persona = runtime.persona_name or "product-steward"
    route_id = runtime.id.hex[:12]
    ravn_peer = runtime.flock_peer_id or f"flock-{persona}"
    skuld_peer = f"skuld-{route_id}"
    broker = values.get("broker") if isinstance(values.get("broker"), dict) else {}
    session_values = values.get("session") if isinstance(values.get("session"), dict) else {}
    config: dict[str, Any] = {
        "session": {
            "id": str(runtime.id),
            "name": runtime.name,
            "model": runtime.model,
            "reasoning_effort": str(
                session_values.get("reasoningEffort")
                or session_values.get("reasoning_effort")
                or "high"
            ),
            "owner_id": runtime.owner_id,
            "tenant_id": runtime.tenant_id,
            "workspace_dir": "/sandbox/workspace",
        },
        "transport": str(broker.get("transport") or "sdk"),
        "transport_adapter": str(
            broker.get("transportAdapter")
            or broker.get("transport_adapter")
            or "skuld.transports.codex_ws.CodexWebSocketTransport"
        ),
        "cli_type": str(broker.get("cliType") or broker.get("cli_type") or "codex-ws"),
        "host": "0.0.0.0",
        "port": service_port,
        "persistence_mount_path": "/sandbox/workspace",
        "volundr_api_url": volundr_api_url,
        "usage_report_path": f"/api/v1/forge/resident-runtimes/{runtime.id}/usage",
        "room": {
            "enabled": True,
            "presence_sweep_interval_s": 0,
            "default_target_peer_id": ravn_peer,
        },
        "mesh": {
            "enabled": True,
            "transport": "nng",
            "peer_id": skuld_peer,
            "nng": {
                "pub_sub_address": "tcp://0.0.0.0:7480",
                "req_rep_address": "tcp://0.0.0.0:7481",
            },
            "adapters": [
                {
                    "adapter": "static",
                    "poll_interval_s": 0,
                    "peers": _resident_mesh_peers(skuld_peer, ravn_peer, persona),
                }
            ],
        },
    }
    if "skipPermissions" in broker or "skip_permissions" in broker:
        config["skip_permissions"] = bool(
            broker.get("skipPermissions", broker.get("skip_permissions"))
        )
    approval_policy = broker.get("approvalPolicy", broker.get("approval_policy"))
    if approval_policy:
        config["approval_policy"] = str(approval_policy)
    if broker.get("sandbox"):
        config["sandbox"] = str(broker["sandbox"])
    openshell = values.get("openshell")
    if isinstance(openshell, dict):
        overlay = openshell.get("skuldConfig") or openshell.get("skuld_config")
        if overlay is not None and not isinstance(overlay, dict):
            raise RuntimeError("OpenShell resident skuldConfig must be an object")
        if isinstance(overlay, dict):
            _deep_merge(config, overlay)
    resident_flock_skuld_config(config, runtime, values)
    return config


def _resident_broker_environment(broker: dict[str, Any]) -> dict[str, str]:
    """Translate the shared broker contract for resident child processes."""
    env: dict[str, str] = {}
    fields = {
        "SKULD__CLI_TYPE": broker.get("cliType", broker.get("cli_type")),
        "SKULD__TRANSPORT": broker.get("transport"),
        "SKULD__TRANSPORT_ADAPTER": broker.get("transportAdapter", broker.get("transport_adapter")),
        "SKULD__APPROVAL_POLICY": broker.get("approvalPolicy", broker.get("approval_policy")),
        "SKULD__SANDBOX": broker.get("sandbox"),
    }
    for name, value in fields.items():
        if value is not None and str(value).strip():
            env[name] = str(value)
    if "skipPermissions" in broker or "skip_permissions" in broker:
        value = broker.get("skipPermissions", broker.get("skip_permissions"))
        env["SKULD__SKIP_PERMISSIONS"] = str(bool(value)).lower()
    return env


def _resident_ravn_config(
    runtime: ResidentRuntime,
    values: dict[str, Any],
    service_port: int,
) -> dict[str, Any]:
    persona = runtime.persona_name or "product-steward"
    route_id = runtime.id.hex[:12]
    ravn_peer = runtime.flock_peer_id or f"flock-{persona}"
    skuld_peer = f"skuld-{route_id}"
    resident = values.get("resident") if isinstance(values.get("resident"), dict) else {}
    platform = resident.get("platform") if isinstance(resident.get("platform"), dict) else {}
    gateway_platform: dict[str, Any] = {
        "enabled": bool(platform.get("enabled", True)),
        "base_url": str(platform.get("baseUrl") or platform.get("base_url") or ""),
        "workflow_aliases": platform.get("workflowAliases")
        or platform.get("workflow_aliases")
        or {},
    }
    config: dict[str, Any] = {
        "persona": persona,
        "mesh": {
            "enabled": True,
            "adapter": "nng",
            "own_peer_id": ravn_peer,
            "nng": {
                "pub_sub_address": "tcp://0.0.0.0:7482",
                "req_rep_address": "tcp://0.0.0.0:7483",
            },
            "peers": [{"peer_id": skuld_peer}],
        },
        "discovery": {
            "enabled": True,
            "adapters": [
                {
                    "adapter": "static",
                    "peers": _resident_mesh_peers(skuld_peer, ravn_peer, persona),
                    "poll_interval_s": 0,
                }
            ],
        },
        "cascade": {"enabled": True},
        "gateway": {
            "enabled": True,
            "channels": {"http": {"enabled": True, "host": "0.0.0.0", "port": 7781}},
            "platform": gateway_platform,
        },
        "initiative": {
            "enabled": True,
            "max_concurrent_tasks": int(
                resident.get("maxConcurrentTasks") or resident.get("max_concurrent_tasks") or 4
            ),
        },
        "permission": {"workspace_root": "/sandbox/workspace"},
        "logging": {"level": "INFO"},
        "skuld": {
            "enabled": True,
            "broker_url": f"ws://127.0.0.1:{service_port}/ws/ravn",
            "display_name": runtime.name,
            "reconnect_delay_seconds": int(
                (resident.get("skuld") or {}).get("reconnectDelaySeconds", 2)
            ),
            "max_reconnect_attempts": int(
                (resident.get("skuld") or {}).get("maxReconnectAttempts", 300)
            ),
            "session_ready_timeout_seconds": int(
                (resident.get("skuld") or {}).get("sessionReadyTimeoutSeconds", 900)
            ),
        },
        "environment": {"resident_name": runtime.name},
    }
    llm = dict(resident.get("llm") or {})
    if runtime.model:
        llm["model"] = runtime.model
    if llm:
        config["llm"] = llm
    if isinstance(resident.get("wakefulness"), dict):
        config["wakefulness"] = resident["wakefulness"]
    resident_flock_runtime_config(config, runtime, values)
    if resident.get("dailyBudgetUsd") or resident.get("daily_budget_usd"):
        config["budget"] = {
            "daily_cap_usd": float(
                resident.get("dailyBudgetUsd") or resident.get("daily_budget_usd")
            )
        }
    mimir = _resident_mimir_config(values)
    if mimir:
        config["mimir"] = mimir
    openshell = values.get("openshell")
    if isinstance(openshell, dict):
        overlay = openshell.get("ravnConfig") or openshell.get("ravn_config")
        if overlay is not None and not isinstance(overlay, dict):
            raise RuntimeError("OpenShell resident ravnConfig must be an object")
        if isinstance(overlay, dict):
            _deep_merge(config, overlay)
    return config


def _resident_mesh_peers(
    skuld_peer: str,
    ravn_peer: str,
    persona: str,
) -> list[dict[str, Any]]:
    return [
        {
            "peer_id": skuld_peer,
            "persona": "Skuld",
            "pub_address": "tcp://127.0.0.1:7480",
            "rep_address": "tcp://127.0.0.1:7481",
            "handshake_port": 7580,
            "consumes_event_types": [],
            "emits_event_types": ["code.changed"],
        },
        {
            "peer_id": ravn_peer,
            "persona": persona,
            "pub_address": "tcp://127.0.0.1:7482",
            "rep_address": "tcp://127.0.0.1:7483",
            "handshake_port": 7581,
            "consumes_event_types": [],
            "emits_event_types": [],
        },
    ]


def _resident_mimir_config(values: dict[str, Any]) -> dict[str, Any]:
    raw = values.get("mimir")
    if not isinstance(raw, dict) or not isinstance(raw.get("instances"), list):
        return {}
    instances = []
    for item in raw["instances"]:
        if not isinstance(item, dict):
            continue
        instance = dict(item)
        auth = instance.get("auth")
        if isinstance(auth, dict) and auth.get("type") == "workload":
            instance["auth"] = {
                "type": "bearer",
                "token_env": PLATFORM_ACCESS_TOKEN_ENV,
            }
        instances.append(instance)
    write_default = [
        str(item.get("name"))
        for item in instances
        if item.get("role", "shared") == "local" and item.get("name")
    ][:1]
    if not write_default and len(instances) == 1 and instances[0].get("name"):
        write_default = [str(instances[0]["name"])]
    resident = values.get("resident") if isinstance(values.get("resident"), dict) else {}
    resident_mimir = resident.get("mimir") if isinstance(resident.get("mimir"), dict) else {}
    source = resident_mimir.get("sourceTrigger") or resident_mimir.get("source_trigger") or {}
    stale = resident_mimir.get("stalenessTrigger") or resident_mimir.get("staleness_trigger") or {}
    return {
        "enabled": True,
        "instances": instances,
        "source_trigger": {
            "enabled": bool(source.get("enabled", False)),
            "poll_interval_seconds": int(
                source.get("pollIntervalSeconds") or source.get("poll_interval_seconds") or 60
            ),
        },
        "staleness_trigger": {
            "enabled": bool(stale.get("enabled", False)),
            "schedule_hours": int(stale.get("scheduleHours") or stale.get("schedule_hours") or 6),
        },
        "write_routing": {"rules": [], "default": write_default},
    }


def _resident_api_urls(values: dict[str, Any]) -> tuple[str, ...]:
    urls: list[str] = []
    resident = values.get("resident") if isinstance(values.get("resident"), dict) else {}
    platform = resident.get("platform") if isinstance(resident.get("platform"), dict) else {}
    platform_url = platform.get("baseUrl") or platform.get("base_url")
    if platform_url:
        urls.append(str(platform_url))
    mimir = values.get("mimir")
    if isinstance(mimir, dict) and isinstance(mimir.get("instances"), list):
        urls.extend(
            str(instance["url"])
            for instance in mimir["instances"]
            if isinstance(instance, dict) and instance.get("url")
        )
    llm = resident.get("llm") if isinstance(resident.get("llm"), dict) else {}
    provider = llm.get("provider") if isinstance(llm.get("provider"), dict) else {}
    kwargs = provider.get("kwargs") if isinstance(provider.get("kwargs"), dict) else {}
    if kwargs.get("base_url") or kwargs.get("baseUrl"):
        urls.append(str(kwargs.get("base_url") or kwargs.get("baseUrl")))
    return tuple(urls)


def _resident_platform_binaries(runtime: ResidentRuntime) -> tuple[str, ...]:
    if runtime.engine is ResidentEngine.OPENCLAW:
        return ("/usr/bin/node",)
    if runtime.engine is ResidentEngine.HERMES:
        return ("/opt/hermes/**", "/usr/bin/python3")
    return ()


def _resident_hermes_config(
    runtime: ResidentRuntime,
    values: dict[str, Any],
) -> dict[str, Any]:
    from volundr.adapters.outbound.hermes_gateway import normalize_hermes_model_id

    resident = values.get("resident") if isinstance(values.get("resident"), dict) else {}
    llm = resident.get("llm") if isinstance(resident.get("llm"), dict) else {}
    provider = llm.get("provider") if isinstance(llm.get("provider"), dict) else {}
    kwargs = provider.get("kwargs") if isinstance(provider.get("kwargs"), dict) else {}
    base_url = str(kwargs.get("base_url") or kwargs.get("baseUrl") or "").strip()
    if not base_url:
        raise RuntimeError("Hermes residents require resident.llm.provider.kwargs.base_url")
    _, service_port = _resident_service(
        values,
        "hermes",
        HERMES_API_SERVER_DEFAULT_PORT,
    )
    model_id = normalize_hermes_model_id(runtime.model)
    return {
        "model": {
            "default": model_id,
            "provider": "custom:niuu",
            "base_url": base_url,
            "api_mode": "chat_completions",
            "default_headers": _shared_resident_attribution_headers(runtime),
        },
        "custom_providers": [
            {
                "name": "niuu",
                "base_url": base_url,
                "key_env": PLATFORM_ACCESS_TOKEN_ENV,
                "api_mode": "chat_completions",
                "model": model_id,
            }
        ],
        "terminal": {"cwd": "/sandbox/workspace"},
        "approvals": {"mode": "off"},
        "gateway": {
            "platforms": {
                "api_server": {
                    "enabled": True,
                    "extra": {
                        "host": "127.0.0.1",
                        "port": service_port,
                    },
                }
            }
        },
    }


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
            continue
        base[key] = value


def _resources_from_values(
    values: dict[str, Any],
    *,
    cpu: str = "",
    memory: str = "",
) -> dict[str, Any]:
    resources = values.get("resources")
    if not isinstance(resources, dict):
        resources = {}
    result: dict[str, Any] = {}
    for key in ("requests", "limits"):
        configured = resources.get(key)
        if isinstance(configured, dict):
            clean = {
                str(resource): str(quantity)
                for resource, quantity in configured.items()
                if resource and quantity
            }
            if clean:
                result[key] = clean
    if cpu or memory:
        limits = dict(result.get("limits") or {})
        limits.update(_compact({"cpu": cpu, "memory": memory}))
        result["limits"] = limits
    return result


def _image_from_values(values: dict[str, Any], *, default: str) -> str:
    return _shared_image_from_values(values, default=default)


def _driver_config_from_values(values: dict[str, Any]) -> dict[str, Any]:
    pod: dict[str, Any] = {}
    if isinstance(values.get("nodeSelector"), dict):
        pod["node_selector"] = _string_dict(values["nodeSelector"])
    if isinstance(values.get("tolerations"), list) and values["tolerations"]:
        pod["tolerations"] = values["tolerations"]
    if values.get("runtimeClassName"):
        pod["runtime_class_name"] = str(values["runtimeClassName"])
    if values.get("priorityClassName"):
        pod["priority_class_name"] = str(values["priorityClassName"])
    return {"pod": pod} if pod else {}


def _resident_state_from_sandbox(
    sandbox: OpenShellSandbox,
    processes_ready: bool,
) -> ResidentObservedState:
    if sandbox.phase == openshell_pb2.SANDBOX_PHASE_ERROR:
        return ResidentObservedState.FAILED
    if sandbox.phase == openshell_pb2.SANDBOX_PHASE_DELETING:
        return ResidentObservedState.DELETING
    if sandbox.ready and processes_ready:
        return ResidentObservedState.ACTIVE
    return ResidentObservedState.DEPLOYING


def _resident_health_script(
    process_names: Sequence[str] = ("skuld", "ravn"),
    *,
    service_port: int | None = None,
) -> str:
    lines = ["set -eu"]
    for name in process_names:
        pid_path = f"/sandbox/.volundr/{name}.pid"
        lines.append(f'test -s {shlex.quote(pid_path)} && kill -0 "$(cat {shlex.quote(pid_path)})"')
    if service_port is not None:
        port_hex = f"{service_port:04X}"
        lines.append(
            "awk '$2 ~ /:" + port_hex + '$/ && $4 == "0A" { found=1 } '
            "END { exit !found }' /proc/net/tcp /proc/net/tcp6"
        )
    return "\n".join(lines)


def _resident_stop_script(
    process_names: Sequence[str] = ("skuld", "ravn"),
) -> str:
    quoted = " ".join(shlex.quote(name) for name in process_names)
    return f"""\
set -eu
for name in {quoted}; do
  pid_file="/sandbox/.volundr/$name.pid"
  [ -s "$pid_file" ] || continue
  pid="$(cat "$pid_file")"
  kill "$pid" 2>/dev/null || true
done
for _ in 1 2 3 4 5; do
  alive=0
  for name in {quoted}; do
    pid_file="/sandbox/.volundr/$name.pid"
    [ -s "$pid_file" ] || continue
    kill -0 "$(cat "$pid_file")" 2>/dev/null && alive=1 || true
  done
  [ "$alive" -eq 1 ] || break
  sleep 1
done
for name in {quoted}; do
  pid_file="/sandbox/.volundr/$name.pid"
  if [ -s "$pid_file" ]; then
    kill -9 "$(cat "$pid_file")" 2>/dev/null || true
  fi
  rm -f "$pid_file"
done
"""


def _resident_process_log_script(lines: int, sources: Sequence[str]) -> str:
    commands = ["set -eu"]
    for source in sources:
        path = f"/sandbox/.volundr/{source}.log"
        commands.extend(
            (
                f"printf '%s\\n' {shlex.quote(f'__VOLUNDR_LOG_SOURCE__={source}')}",
                f"[ ! -f {shlex.quote(path)} ] || tail -n {int(lines)} {shlex.quote(path)}",
            )
        )
    return "\n".join(commands)


def _exec_output_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _resident_process_log_entries(output: str, *, min_level: str) -> list[ResidentLogEntry]:
    source = ""
    entries: list[ResidentLogEntry] = []
    minimum = _resident_log_level_value(min_level)
    for raw_line in output.splitlines():
        if raw_line.startswith("__VOLUNDR_LOG_SOURCE__="):
            source = raw_line.partition("=")[2]
            continue
        line = raw_line.strip()
        if not source or not line:
            continue
        timestamp_ms = 0
        level = "INFO"
        message = line
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            level = str(payload.get("level") or level).upper()
            message = str(payload.get("message") or payload.get("event") or line)
            timestamp_ms = _resident_log_timestamp_ms(
                payload.get("time") or payload.get("timestamp")
            )
        if _resident_log_level_value(level) < minimum:
            continue
        entries.append(
            ResidentLogEntry(
                timestamp_ms=timestamp_ms,
                level=level,
                source=source,
                target="process",
                message=message,
                fields={},
            )
        )
    return entries


def _resident_log_timestamp_ms(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def _resident_log_level_value(level: str) -> int:
    return {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "WARN": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }.get(str(level or "").strip().upper(), logging.DEBUG)


def _provider_profile(
    *,
    profile_id: str,
    env_name: str,
    token_endpoint: str,
    cache_ttl_seconds: int = 300,
    target_config: Any = None,
) -> Any:
    target = _provider_target(env_name, target_config)
    audience = f"{GRANT_AUDIENCE_PREFIX}{profile_id}"
    credential = openshell_pb2.ProviderProfileCredential(
        name="access_token",
        description=f"Runtime {env_name} credential from Niuu OpenBao",
        env_vars=[env_name],
        required=False,
        auth_style=target["auth_style"],
        header_name=target["header_name"],
        token_grant=openshell_pb2.ProviderCredentialTokenGrant(
            token_endpoint=token_endpoint,
            audience=audience,
            jwt_svid_audience=token_endpoint,
            client_assertion_type=OAUTH_CLIENT_ASSERTION_TYPE,
            cache_ttl_seconds=cache_ttl_seconds,
        ),
    )
    endpoints = target.get("endpoints") or [
        sandbox_pb2.NetworkEndpoint(
            host=host,
            port=443,
            protocol="rest",
            tls="terminate",
            enforcement="enforce",
            access="full",
        )
        for host in target["hosts"]
    ]
    binaries = [sandbox_pb2.NetworkBinary(path=path) for path in target["binaries"]]
    return openshell_pb2.ProviderProfile(
        id=profile_id,
        display_name=f"Niuu runtime credential for {env_name}",
        description="OpenBao-backed dynamic credential scoped to one Volundr workload",
        category=target["category"],
        credentials=[credential],
        endpoints=endpoints,
        binaries=binaries,
    )


def _provider_credential_slots(profile: Any) -> dict[str, str]:
    """Name the credential slots a provider carries, empty until the grant fills them.

    The gateway rejects a provider with no credentials at all, so a
    network-only profile still declares one named slot that stays empty —
    values arrive from the profile's token grant, never from this call.
    """
    slots = {str(credential.name): "" for credential in profile.credentials}
    if slots:
        return slots
    return {PROVIDER_NETWORK_ONLY_CREDENTIAL: ""}


def _codex_enrollment_profile(profile_id: str) -> Any:
    """Network-only OpenShell provider used during a Codex device login."""
    target = {
        "hosts": (
            "api.openai.com",
            "auth.openai.com",
            "chatgpt.com",
            "ab.chatgpt.com",
            "files.openai.com",
            "*.oaiusercontent.com",
        ),
        # The login runs whichever image the cluster configured as the login
        # runtime, and codex ships as a node shim in front of a native binary.
        # Cover both layouts we build — the openshell sandbox image installs
        # under /usr/lib/node_modules, the skuld image under /opt/skuld-tools —
        # plus node itself, which is the process that actually opens the socket.
        "binaries": (
            "/usr/bin/codex",
            "/usr/local/bin/codex",
            "/opt/niuu/bin/codex",
            "/usr/lib/node_modules/@openai/**",
            "/opt/skuld-tools/node_modules/@openai/**",
            "/usr/bin/node",
            "/usr/local/bin/node",
        ),
    }
    endpoints = [
        sandbox_pb2.NetworkEndpoint(
            host=host,
            port=443,
            protocol="rest",
            tls="terminate",
            enforcement="enforce",
            access="full",
        )
        for host in target["hosts"]
    ]
    binaries = [sandbox_pb2.NetworkBinary(path=path) for path in target["binaries"]]
    return openshell_pb2.ProviderProfile(
        id=profile_id,
        display_name="Niuu Codex device enrollment",
        description="Workspace-free network access for one user-initiated Codex login",
        category=openshell_pb2.PROVIDER_PROFILE_CATEGORY_AGENT,
        credentials=[],
        endpoints=endpoints,
        binaries=binaries,
    )


def _platform_provider_profile(
    *,
    profile_id: str,
    token_endpoint: str,
    api_urls: Sequence[str],
    additional_binaries: Sequence[str] = (),
) -> Any:
    parsed_urls = []
    seen: set[tuple[str, int]] = set()
    for api_url in api_urls:
        parsed = urlparse(str(api_url))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise RuntimeError("OpenShell platform API URLs must be absolute HTTP URLs")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        key = (parsed.hostname, port)
        if key in seen:
            continue
        seen.add(key)
        parsed_urls.append((parsed, port))
    audience = f"{PLATFORM_GRANT_AUDIENCE_PREFIX}{profile_id}"
    credential = openshell_pb2.ProviderProfileCredential(
        name="access_token",
        description="Workload-bound Völundr token",
        env_vars=[PLATFORM_ACCESS_TOKEN_ENV],
        required=False,
        auth_style="bearer",
        header_name="Authorization",
        token_grant=openshell_pb2.ProviderCredentialTokenGrant(
            token_endpoint=token_endpoint,
            audience=audience,
            jwt_svid_audience=token_endpoint,
            client_assertion_type=OAUTH_CLIENT_ASSERTION_TYPE,
            cache_ttl_seconds=0,
        ),
    )
    endpoints = [
        sandbox_pb2.NetworkEndpoint(
            host=parsed.hostname,
            port=port,
            protocol="rest",
            tls="terminate" if parsed.scheme == "https" else "",
            enforcement="enforce",
            access="full",
        )
        for parsed, port in parsed_urls
    ]
    binary_paths = dict.fromkeys(
        (
            "/opt/niuu/**",
            "/sandbox/.uv/python/**",
            *additional_binaries,
        )
    )
    binaries = [sandbox_pb2.NetworkBinary(path=path) for path in binary_paths]
    return openshell_pb2.ProviderProfile(
        id=profile_id,
        display_name="Niuu Völundr workload reporting",
        description="SPIFFE-authenticated platform access for one Völundr workload",
        category=openshell_pb2.PROVIDER_PROFILE_CATEGORY_AGENT,
        credentials=[credential],
        endpoints=endpoints,
        binaries=binaries,
    )


def _provider_target(env_name: str, config: Any = None) -> dict[str, Any]:
    if isinstance(config, dict):
        raw_endpoints = config.get("endpoints")
        raw_binaries = config.get("binaries")
        if not isinstance(raw_endpoints, list) or not raw_endpoints:
            raise RuntimeError("OpenShell credential provider endpoints must be a non-empty list")
        if not isinstance(raw_binaries, list) or not raw_binaries:
            raise RuntimeError("OpenShell credential provider binaries must be a non-empty list")
        endpoints = []
        for raw in raw_endpoints:
            if not isinstance(raw, dict) or not raw.get("host") or not raw.get("port"):
                raise RuntimeError("OpenShell credential provider endpoint requires host and port")
            allowed_ips = raw.get("allowedIps") or raw.get("allowed_ips") or []
            if not isinstance(allowed_ips, list):
                raise RuntimeError("OpenShell credential provider allowed_ips must be a list")
            endpoints.append(
                sandbox_pb2.NetworkEndpoint(
                    host=str(raw["host"]),
                    port=int(raw["port"]),
                    protocol=str(raw.get("protocol") or ""),
                    tls=str(raw.get("tls") or "skip"),
                    enforcement=str(raw.get("enforcement") or "enforce"),
                    access=str(raw.get("access") or "full"),
                    allowed_ips=[str(item) for item in allowed_ips],
                )
            )
        binaries = [
            str(item.get("path") if isinstance(item, dict) else item) for item in raw_binaries
        ]
        if any(not path for path in binaries):
            raise RuntimeError("OpenShell credential provider binary path is required")
        return {
            "auth_style": str(config.get("authStyle") or config.get("auth_style") or "bearer"),
            "header_name": str(
                config.get("headerName") or config.get("header_name") or "Authorization"
            ),
            "endpoints": endpoints,
            "binaries": binaries,
            "category": openshell_pb2.PROVIDER_PROFILE_CATEGORY_AGENT,
        }
    if env_name == "OPENAI_API_KEY":
        return {
            "auth_style": "bearer",
            "header_name": "Authorization",
            "hosts": ("api.openai.com",),
            "binaries": (
                "/usr/local/bin/codex",
                "/usr/bin/node",
                "/usr/local/bin/node",
                "/opt/venv/bin/python3",
            ),
            "category": openshell_pb2.PROVIDER_PROFILE_CATEGORY_AGENT,
        }
    if env_name in {"ANTHROPIC_API_KEY", "CLAUDE_API_KEY"}:
        return {
            "auth_style": "header",
            "header_name": "x-api-key",
            "hosts": ("api.anthropic.com",),
            "binaries": (
                "/usr/local/bin/claude",
                "/usr/bin/node",
                "/usr/local/bin/node",
                "/opt/venv/bin/python3",
            ),
            "category": openshell_pb2.PROVIDER_PROFILE_CATEGORY_AGENT,
        }
    if env_name in {
        "GIT_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GITHUB_PERSONAL_ACCESS_TOKEN",
    }:
        return {
            "auth_style": "bearer",
            "header_name": "Authorization",
            "hosts": (
                "github.com",
                "api.github.com",
                "codeload.github.com",
                "objects.githubusercontent.com",
                "raw.githubusercontent.com",
            ),
            "binaries": (
                "/usr/bin/git",
                "/usr/lib/git-core/git-remote-http",
                "/usr/lib/git-core/git-remote-https",
                "/usr/bin/gh",
                "/usr/bin/curl",
            ),
            "category": openshell_pb2.PROVIDER_PROFILE_CATEGORY_SOURCE_CONTROL,
        }
    raise RuntimeError(
        f"OpenShell has no dynamic provider route for credential environment {env_name!r}"
    )


def _profile_authorizes_audience(profile: Any, audience: str) -> bool:
    return any(
        credential.HasField("token_grant") and credential.token_grant.audience == audience
        for credential in profile.credentials
    )


def _profiles_equivalent(existing: Any, expected: Any) -> bool:
    existing_copy = openshell_pb2.ProviderProfile()
    existing_copy.CopyFrom(existing)
    existing_copy.resource_version = 0
    expected_copy = openshell_pb2.ProviderProfile()
    expected_copy.CopyFrom(expected)
    expected_copy.resource_version = 0
    return existing_copy.SerializeToString(deterministic=True) == expected_copy.SerializeToString(
        deterministic=True
    )


def _sandbox_credential_path(destination: str, *, sandbox_home: str = "/sandbox") -> str:
    path = destination.strip()
    for source_prefix in ("/home/volundr", "/root"):
        if path == source_prefix or path.startswith(source_prefix + "/"):
            path = sandbox_home + path.removeprefix(source_prefix)
            break
    normalized = PurePosixPath(path)
    if not normalized.is_absolute() or ".." in normalized.parts:
        raise RuntimeError(f"OpenShell credential file path is invalid: {destination!r}")
    normalized_path = str(normalized)
    allowed_roots = (sandbox_home, "/sandbox/workspace", "/run/secrets")
    if not any(
        normalized_path == root or normalized_path.startswith(root + "/") for root in allowed_roots
    ):
        raise RuntimeError(
            f"OpenShell credential file path is outside allowed roots: {destination!r}"
        )
    if normalized_path in allowed_roots:
        raise RuntimeError(f"OpenShell credential file path must name a file: {destination!r}")
    return normalized_path


def _credential_file_archives(files: dict[str, bytes]) -> list[tuple[str, bytes]]:
    grouped: dict[str, dict[str, bytes]] = {}
    for destination, content in files.items():
        path = _sandbox_credential_path(destination)
        extraction_root = "/run/secrets" if path.startswith("/run/secrets/") else "/sandbox"
        grouped.setdefault(extraction_root, {})[path] = content
    return [
        (root, _credential_file_archive(group, extraction_root=root))
        for root, group in sorted(grouped.items())
    ]


def _credential_file_archive(
    files: dict[str, bytes],
    *,
    extraction_root: str = "/",
) -> bytes:
    output = io.BytesIO()
    directories: set[str] = set()
    root = PurePosixPath(extraction_root)
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for destination in sorted(files):
            path = PurePosixPath(_sandbox_credential_path(destination))
            try:
                archive_path = path.relative_to(root)
            except ValueError as exc:
                raise RuntimeError(
                    f"OpenShell credential file path is outside extraction root: {destination!r}"
                ) from exc
            parents = list(archive_path.parents)
            for parent in reversed(parents):
                parent_str = str(parent)
                if parent_str == ".":
                    continue
                if parent_str in directories:
                    continue
                directory = tarfile.TarInfo(parent_str)
                directory.type = tarfile.DIRTYPE
                directory.mode = 0o700
                directory.mtime = 0
                archive.addfile(directory)
                directories.add(parent_str)

            content = files[destination]
            info = tarfile.TarInfo(str(archive_path))
            info.mode = 0o600
            info.size = len(content)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def _sandbox_policy_from_config(config: dict[str, Any]) -> Any:
    if int(config.get("version") or 0) != 1:
        raise ValueError("OpenShell sandbox_policy.version must be 1")
    filesystem = config.get("filesystem") or {}
    process = config.get("process") or {}
    network_policies = {
        str(key): sandbox_pb2.NetworkPolicyRule(
            name=str(value.get("name") or key),
            endpoints=[
                sandbox_pb2.NetworkEndpoint(**endpoint) for endpoint in value.get("endpoints", [])
            ],
            binaries=[sandbox_pb2.NetworkBinary(**binary) for binary in value.get("binaries", [])],
        )
        for key, value in (config.get("network_policies") or {}).items()
    }
    return sandbox_pb2.SandboxPolicy(
        version=1,
        filesystem=sandbox_pb2.FilesystemPolicy(**filesystem),
        landlock=sandbox_pb2.LandlockPolicy(**(config.get("landlock") or {})),
        process=sandbox_pb2.ProcessPolicy(**process),
        network_policies=network_policies,
    )


def _sandbox_from_proto(raw: Any) -> OpenShellSandbox:
    metadata = raw.metadata
    status = raw.status
    return OpenShellSandbox(
        id=str(metadata.id or ""),
        name=str(metadata.name or status.sandbox_name or ""),
        phase=int(status.phase),
        ready=any(
            condition.type == "Ready" and condition.status == "True"
            for condition in status.conditions
        ),
        labels={str(key): str(value) for key, value in metadata.labels.items()},
        providers=tuple(str(name) for name in raw.spec.providers),
    )


def _status_from_sandbox(sandbox: OpenShellSandbox) -> SessionStatus:
    if sandbox.phase == openshell_pb2.SANDBOX_PHASE_READY or sandbox.ready:
        return SessionStatus.RUNNING
    if sandbox.phase == openshell_pb2.SANDBOX_PHASE_ERROR:
        return SessionStatus.FAILED
    if sandbox.phase == openshell_pb2.SANDBOX_PHASE_DELETING:
        return SessionStatus.STOPPING
    return SessionStatus.PROVISIONING


def _protobuf_struct(value: dict[str, Any]) -> struct_pb2.Struct:
    struct = struct_pb2.Struct()
    struct.update(value)
    return struct


def _compact(value: dict[str, str]) -> dict[str, str]:
    return {key: item for key, item in value.items() if item}


def _credential_mappings_from_spec(spec: SessionSpec) -> list[dict[str, Any]]:
    return _credential_mappings_from_values(spec.values)


def _credential_mappings_from_values(values: dict[str, Any]) -> list[dict[str, Any]]:
    openshell_values = values.get("openshell")
    if not isinstance(openshell_values, dict):
        return []
    raw_mappings = openshell_values.get("credentialMappings") or openshell_values.get(
        "credential_mappings"
    )
    if not isinstance(raw_mappings, list):
        return []
    return [mapping for mapping in raw_mappings if isinstance(mapping, dict)]


def _parse_codex_auth_document(raw: object) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("Codex auth document is unavailable")
    try:
        auth = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Codex auth document is invalid JSON") from exc
    if not isinstance(auth, dict) or not isinstance(auth.get("tokens"), dict):
        raise ValueError("Codex auth document does not contain a token set")
    tokens = auth["tokens"]
    required = ("access_token", "refresh_token", "account_id")
    missing = [name for name in required if not str(tokens.get(name) or "")]
    if missing:
        raise ValueError("Codex auth document is missing required OAuth fields")
    return auth


def _parse_codex_device_challenge(output: str) -> tuple[str, str] | None:
    clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
    verification_uri = ""
    for candidate in re.findall(r"https://[^\s<>\"']+", clean):
        candidate = candidate.rstrip(".,);]")
        hostname = (urlparse(candidate).hostname or "").lower()
        if hostname == "chatgpt.com" or hostname.endswith(".openai.com"):
            verification_uri = candidate
            break
    user_code = _parse_codex_device_code(clean)
    if not verification_uri or not user_code:
        return None
    return verification_uri, user_code


def _parse_codex_device_code(clean: str) -> str:
    """Read the one-time code Codex printed, ignoring paths that look like one.

    Codex prints the code alone on its own line, so that wins. Anything else
    has to survive a boundary check: its startup warning names the CODEX_HOME
    path /tmp/niuu-codex-enrollment, which otherwise reads as "NIUU-CODEX".
    """
    lines = [line.strip().upper() for line in clean.splitlines()]
    for line in lines:
        if CODEX_DEVICE_CODE_PATTERN.fullmatch(line):
            return line
    for line in lines:
        for match in CODEX_DEVICE_CODE_PATTERN.finditer(line):
            before = line[match.start() - 1] if match.start() else " "
            after = line[match.end()] if match.end() < len(line) else " "
            if before in "/-_." or after in "/-_.":
                continue
            return match.group(0)
    return ""


def _string_dict(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(item)
        for key, item in value.items()
        if key and item and "\x00" not in str(key) and "\x00" not in str(item)
    }


def _public_clone_url(repo_url: str) -> str:
    if repo_url.startswith(("http://", "https://", "ssh://", "git@")):
        return repo_url
    return f"https://{repo_url.lstrip('/')}"


def _strip_url_credentials(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.hostname or (parsed.username is None and parsed.password is None):
        return value
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def _redact_secret_url(value: str) -> str:
    return re.sub(r"://[^/@\s]+@", "://***@", value)


def _valid_env_name(value: str) -> bool:
    return re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", value) is not None


def _safe_env_var(key: object, value: object) -> bool:
    if not isinstance(key, str):
        return False
    if not isinstance(value, str):
        return False
    if "\x00" in key or "\x00" in value:
        return False
    if key.startswith("SKULD__"):
        return True
    return key in SECRET_ENV_KEYS


def _endpoint_hostport(endpoint: str) -> str:
    if "://" not in endpoint:
        return endpoint
    parsed = urlparse(endpoint)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return f"{host}:{port}"


def _service_ws_url(base: str, path: str) -> str:
    parsed = urlparse(base)
    if parsed.scheme == "https":
        scheme = "wss"
    elif parsed.scheme == "http":
        scheme = "ws"
    else:
        return f"{base.rstrip('/')}{path}"
    return f"{scheme}://{parsed.netloc}{parsed.path.rstrip('/')}{path}"


def _normalize_command(value: list[str] | str | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return shlex.split(value)
    return [str(part) for part in value]
