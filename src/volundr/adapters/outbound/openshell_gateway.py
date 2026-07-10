"""Native OpenShell gateway adapter for Kubernetes-backed Forge sessions.

It mints a Keycloak client-credentials token and talks to the OpenShell gateway
gRPC API directly. The old OpenShell CLI shell-out adapter was intentionally
removed; service/runtime auth belongs at this gateway boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import re
import shlex
import tarfile
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse, urlunparse
from uuid import UUID

import grpc
import httpx
import jwt
from google.protobuf import struct_pb2
from openshell._proto import datamodel_pb2, openshell_pb2, openshell_pb2_grpc, sandbox_pb2

from niuu.adapters.workload_identity.jwt import JwtWorkloadIdentityVerifier
from niuu.ports.session_proxy import SessionProxyTarget
from volundr.adapters.outbound.local_process import LocalProcessPodManager
from volundr.domain.models import Session, SessionSpec, SessionStatus
from volundr.domain.ports import (
    CredentialStorePort,
    OpenShellCredentialGrantPort,
    OpenShellCredentialGrantToken,
    PodManager,
    PodStartResult,
    SessionRepository,
)

logger = logging.getLogger(__name__)

DEFAULT_GATEWAY_ENDPOINT = "openshell.openshell.svc.cluster.local:8080"
DEFAULT_TOKEN_URL = "https://keycloak.niuu.world/realms/volundr/protocol/openid-connect/token"
DEFAULT_CLIENT_ID = "openshell-volundr-agent"
DEFAULT_SANDBOX_IMAGE = "ghcr.io/niuulabs/skuld:openshell-codex-openbao-20260709-5"
DEFAULT_SANDBOX_COMMAND = ["/usr/local/bin/openshell-run-installed-skuld"]
DEFAULT_SERVICE_PORT = 9200
READY_POLL_INTERVAL = 1.0
BOOTSTRAP_TIMEOUT_SECONDS = 600
BOOTSTRAP_GIT_ATTEMPTS = 20
OAUTH_CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-spiffe"
GRANT_AUDIENCE_PREFIX = "niuu:credential:"
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
DEFAULT_CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
DEFAULT_CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
DEFAULT_CODEX_REFRESH_SKEW_SECONDS = 300
DEFAULT_CODEX_OAUTH_TIMEOUT_SECONDS = 15.0
CODEX_AUTH_FORMAT = "codex_auth_json"
CODEX_ACCESS_TOKEN_ENV = "CODEX_AUTH_ACCESS_TOKEN"
CODEX_ACCOUNT_ID_ENV = "CODEX_AUTH_ACCOUNT_ID"
CODEX_ACCESS_TOKEN_REFERENCE = f"openshell:resolve:env:{CODEX_ACCESS_TOKEN_ENV}"
SECRET_ENV_KEYS = {
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GIT_TOKEN",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GITHUB_PERSONAL_ACCESS_TOKEN",
    CODEX_ACCESS_TOKEN_ENV,
    "CODEX_AUTH_REFRESH_TOKEN",
    CODEX_ACCOUNT_ID_ENV,
    "CODEX_AUTH_ID_TOKEN",
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
        token_url = os.environ.get("OPENSHELL_OIDC_TOKEN_URL", token_url)
        client_id = os.environ.get("OPENSHELL_OIDC_CLIENT_ID", client_id)
        if not client_secret:
            client_secret = os.environ.get("OPENSHELL_OIDC_CLIENT_SECRET", "")
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
            policy=_default_policy(),
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

    def exec_detached(
        self,
        *,
        sandbox_id: str,
        command: Sequence[str],
        env: dict[str, str],
        log_path: str,
    ) -> int:
        command_line = shlex.join([str(part) for part in command])
        log_dir = shlex.quote(str(Path(log_path).parent))
        quoted_log_path = shlex.quote(log_path)
        script = f"mkdir -p {log_dir}\nnohup {command_line} >{quoted_log_path} 2>&1 &\n"
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
                output.append(str(event.stdout.data or ""))
            elif event.HasField("stderr"):
                output.append(str(event.stderr.data or ""))
            elif event.HasField("exit"):
                exit_code = int(event.exit.exit_code)
        return exit_code, "".join(output)

    def write_files(self, *, sandbox_id: str, files: dict[str, bytes]) -> None:
        archive = _credential_file_archive(files)
        stream = self._stub.ExecSandbox(
            openshell_pb2.ExecSandboxRequest(
                sandbox_id=sandbox_id,
                command=["tar", "-xf", "-", "-C", "/"],
                stdin=archive,
                timeout_seconds=30,
            ),
            timeout=max(self._timeout, 30.0),
            metadata=self._metadata(),
        )
        for event in stream:
            if event.HasField("exit") and int(event.exit.exit_code) != 0:
                raise RuntimeError(
                    f"OpenShell credential file projection failed with exit {event.exit.exit_code}"
                )

    def _metadata(self) -> tuple[tuple[str, str], ...]:
        return (("authorization", f"Bearer {self._token_provider.token()}"),)


class OpenShellGatewayPodManager(PodManager, OpenShellCredentialGrantPort):
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
        credential_token_endpoint: str = DEFAULT_CREDENTIAL_TOKEN_ENDPOINT,
        spiffe_jwks_uri: str = DEFAULT_SPIFFE_JWKS_URI,
        spiffe_issuer: str = DEFAULT_SPIFFE_ISSUER,
        spiffe_audience: str = DEFAULT_SPIFFE_AUDIENCE,
        spiffe_subject_prefix: str = DEFAULT_SPIFFE_SUBJECT_PREFIX,
        spiffe_ca_cert_path: str = "",
        codex_oauth_token_url: str = DEFAULT_CODEX_OAUTH_TOKEN_URL,
        codex_oauth_client_id: str = DEFAULT_CODEX_OAUTH_CLIENT_ID,
        codex_refresh_skew_seconds: int = DEFAULT_CODEX_REFRESH_SKEW_SECONDS,
        codex_oauth_timeout_seconds: float = DEFAULT_CODEX_OAUTH_TIMEOUT_SECONDS,
        codex_oauth_client: httpx.AsyncClient | None = None,
        client: OpenShellGatewayClient | None = None,
        **_extra: object,
    ) -> None:
        gateway_endpoint = os.environ.get("OPENSHELL_GATEWAY_ENDPOINT", gateway_endpoint)
        gateway_public_url = os.environ.get("OPENSHELL_GATEWAY_PUBLIC_URL", gateway_public_url)
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
        self._service_port = int(service_port)
        self._service_name = service_name
        self._command_log_path = command_log_path
        self._cpu = cpu
        self._memory = memory
        self._ready_timeout = float(ready_timeout)
        self._credential_token_endpoint = credential_token_endpoint
        self._codex_oauth_token_url = codex_oauth_token_url
        self._codex_oauth_client_id = codex_oauth_client_id
        self._codex_refresh_skew_seconds = int(codex_refresh_skew_seconds)
        self._codex_oauth_timeout_seconds = float(codex_oauth_timeout_seconds)
        self._codex_oauth_client = codex_oauth_client
        self._codex_refresh_locks: dict[tuple[str, str], asyncio.Lock] = {}
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
        self._provider_grants: dict[str, tuple[OpenShellProviderGrant, ...]] = {}
        self._credential_store: CredentialStorePort | None = None
        self._session_repository: SessionRepository | None = None

    def set_credential_store(self, store: CredentialStorePort) -> None:
        """Inject credential store for resolving OpenShell launch credentials."""
        self._credential_store = store

    def set_session_repository(self, repository: SessionRepository) -> None:
        """Inject session persistence for sandbox-to-owner grant authorization."""
        self._session_repository = repository

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
        if not base:
            return None
        return SessionProxyTarget(
            service_url=base.rstrip("/"),
            connect_host=self._gateway_connect_host,
            connect_port=self._gateway_connect_port,
            connect_secure=self._gateway_connect_secure,
        )

    async def start(self, session: Session, spec: SessionSpec) -> PodStartResult:
        sandbox_name = self._sandbox_name(session)
        session_id = str(session.id)
        env = self._build_env(session, spec)
        credential_context = OpenShellCredentialContext(files={}, providers=(), environment={})
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
            credential_context = await self._resolve_credential_env(session, spec)
            env.update(credential_context.environment)
            runtime_processes = _runtime_processes_from_spec(spec)
            grants = tuple(
                OpenShellProviderGrant(provider_name=name, profile_id=name)
                for name in credential_context.providers
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
                providers=credential_context.providers,
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
                    env={**env, **process.env},
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
                env=env,
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
        if not audience.startswith(GRANT_AUDIENCE_PREFIX):
            raise ValueError("unsupported credential audience")
        provider_name = audience.removeprefix(GRANT_AUDIENCE_PREFIX)
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
        session_id = str(config.get("volundr_session_id") or "")
        credential_name = str(config.get("volundr_credential_name") or "")
        credential_field = str(config.get("volundr_credential_field") or "")
        labels = sandbox.labels or {}
        if labels.get("volundr.niuu.io/session") != session_id:
            raise ValueError("credential provider session binding does not match sandbox")
        if self._session_repository is None or self._credential_store is None:
            raise ValueError("credential grant dependencies are unavailable")
        try:
            session_uuid = UUID(session_id)
        except ValueError as exc:
            raise ValueError("credential provider session binding is invalid") from exc
        session = await self._session_repository.get(session_uuid)
        if session is None or not session.owner_id:
            raise ValueError("credential grant session does not exist")
        credential_format = str(config.get("volundr_credential_format") or "")
        if credential_format == CODEX_AUTH_FORMAT:
            return await self._exchange_codex_credential(
                session=session,
                credential_name=credential_name,
                credential_field=credential_field,
            )

        values = await self._credential_store.get_value("user", session.owner_id, credential_name)
        value = values.get(credential_field) if values else None
        if not value or "\x00" in value or "\r" in value or "\n" in value:
            raise ValueError("credential field is unavailable for this session")
        return OpenShellCredentialGrantToken(access_token=value)

    async def _exchange_codex_credential(
        self,
        *,
        session: Session,
        credential_name: str,
        credential_field: str,
    ) -> OpenShellCredentialGrantToken:
        if self._credential_store is None or not session.owner_id:
            raise ValueError("credential grant dependencies are unavailable")

        lock_key = (session.owner_id, credential_name)
        lock = self._codex_refresh_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            stored = await self._credential_store.get("user", session.owner_id, credential_name)
            values = await self._credential_store.get_value(
                "user", session.owner_id, credential_name
            )
            raw_auth = values.get(credential_field) if values else None
            auth = _parse_codex_auth_document(raw_auth)
            access_token = str(auth["tokens"]["access_token"])
            remaining = _jwt_remaining_seconds(access_token)

            if remaining <= self._codex_refresh_skew_seconds:
                auth = await self._refresh_codex_auth(auth)
                access_token = str(auth["tokens"]["access_token"])
                remaining = _jwt_remaining_seconds(access_token)
                if stored is None or values is None:
                    raise ValueError("Codex credential metadata is unavailable")
                updated_values = dict(values)
                updated_values[credential_field] = json.dumps(auth, separators=(",", ":"))
                await self._credential_store.store(
                    "user",
                    session.owner_id,
                    credential_name,
                    stored.secret_type,
                    updated_values,
                    stored.metadata,
                )

        return OpenShellCredentialGrantToken(
            access_token=access_token,
            expires_in=max(remaining, 1),
        )

    async def _refresh_codex_auth(self, auth: dict[str, Any]) -> dict[str, Any]:
        tokens = dict(auth["tokens"])
        refresh_token = str(tokens.get("refresh_token") or "")
        if not refresh_token:
            raise ValueError("Codex OAuth refresh token is unavailable")

        request_data = {
            "grant_type": "refresh_token",
            "client_id": self._codex_oauth_client_id,
            "refresh_token": refresh_token,
        }
        try:
            if self._codex_oauth_client is not None:
                response = await self._codex_oauth_client.post(
                    self._codex_oauth_token_url,
                    data=request_data,
                )
            else:
                async with httpx.AsyncClient(timeout=self._codex_oauth_timeout_seconds) as client:
                    response = await client.post(self._codex_oauth_token_url, data=request_data)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ValueError("Codex OAuth token refresh failed") from exc

        access_token = str(payload.get("access_token") or "")
        if not access_token:
            raise ValueError("Codex OAuth token refresh returned no access token")
        tokens["access_token"] = access_token
        if payload.get("refresh_token"):
            tokens["refresh_token"] = str(payload["refresh_token"])
        if payload.get("id_token"):
            tokens["id_token"] = str(payload["id_token"])

        refreshed = dict(auth)
        refreshed["tokens"] = tokens
        refreshed["last_refresh"] = datetime.now(UTC).isoformat()
        return refreshed

    async def _cleanup_resources(
        self,
        sandbox_name: str,
        grants: Sequence[OpenShellProviderGrant],
    ) -> bool:
        errors: list[Exception] = []
        try:
            await asyncio.to_thread(
                self._client.delete_service,
                sandbox_name=sandbox_name,
                service=self._service_name,
            )
        except Exception as exc:
            errors.append(exc)
        try:
            deleted = await asyncio.to_thread(self._client.delete_sandbox, sandbox_name)
        except Exception as exc:
            errors.append(exc)
            deleted = False
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

    async def _resolve_credential_env(
        self,
        session: Session,
        spec: SessionSpec,
    ) -> OpenShellCredentialContext:
        mappings = _credential_mappings_from_spec(spec)
        codex_auth = _codex_auth_from_spec(spec)
        if not mappings and not codex_auth:
            return OpenShellCredentialContext(files={}, providers=(), environment={})
        if not session.owner_id:
            raise RuntimeError("OpenShell credential mappings require a session owner")
        if self._credential_store is None:
            raise RuntimeError("OpenShell credential mappings require a credential store")

        files: dict[str, bytes] = {}
        providers: list[str] = []
        environment: dict[str, str] = {}
        try:
            if codex_auth:
                await self._resolve_codex_auth(
                    session,
                    codex_auth,
                    providers=providers,
                    environment=environment,
                )
            for mapping in mappings:
                mapping_credential_name = str(
                    mapping.get("credentialName") or mapping.get("credential_name") or ""
                )
                if codex_auth and mapping_credential_name == codex_auth["credential_name"]:
                    # codexAuth owns this credential through its scoped provider grant.
                    mapping = dict(mapping)
                    mapping.pop("env_mappings", None)
                    mapping["envMappings"] = {}
                await self._resolve_credential_mapping(
                    session,
                    mapping,
                    files=files,
                    providers=providers,
                    excluded_env_names={"OPENAI_API_KEY"} if codex_auth else set(),
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
        )

    async def _resolve_codex_auth(
        self,
        session: Session,
        config: dict[str, str],
        *,
        providers: list[str],
        environment: dict[str, str],
    ) -> None:
        if self._credential_store is None or not session.owner_id:
            raise RuntimeError("OpenShell Codex auth requires an owned session")
        credential_name = config["credential_name"]
        credential_field = config["auth_field"]
        values = await self._credential_store.get_value("user", session.owner_id, credential_name)
        raw_auth = values.get(credential_field) if values else None
        auth = _parse_codex_auth_document(raw_auth)
        account_id = str(auth["tokens"]["account_id"])

        provider_name = _provider_grant_name(
            session_id=str(session.id),
            credential_name=credential_name,
            field_name=credential_field,
            env_name=CODEX_ACCESS_TOKEN_ENV,
        )
        profile = _provider_profile(
            profile_id=provider_name,
            env_name=CODEX_ACCESS_TOKEN_ENV,
            token_endpoint=self._credential_token_endpoint,
            cache_ttl_seconds=0,
        )
        providers.append(provider_name)
        await asyncio.to_thread(
            self._client.create_provider_grant,
            profile=profile,
            provider_name=provider_name,
            config={
                "volundr_session_id": str(session.id),
                "volundr_credential_name": credential_name,
                "volundr_credential_field": credential_field,
                "volundr_credential_format": CODEX_AUTH_FORMAT,
            },
        )
        environment[CODEX_ACCESS_TOKEN_ENV] = CODEX_ACCESS_TOKEN_REFERENCE
        environment[CODEX_ACCOUNT_ID_ENV] = account_id

    async def _resolve_credential_mapping(
        self,
        session: Session,
        mapping: dict[str, Any],
        *,
        files: dict[str, bytes],
        providers: list[str],
        excluded_env_names: set[str] | None = None,
    ) -> None:
        credential_name = str(mapping.get("credentialName") or mapping.get("credential_name") or "")
        if not credential_name:
            return
        env_mappings = _string_dict(mapping.get("envMappings") or mapping.get("env_mappings") or {})
        if excluded_env_names:
            env_mappings = {
                env_name: field_name
                for env_name, field_name in env_mappings.items()
                if env_name not in excluded_env_names
            }
        file_mappings = _string_dict(
            mapping.get("fileMappings") or mapping.get("file_mappings") or {}
        )
        if not env_mappings and not file_mappings:
            return
        stored = await self._credential_store.get(
            "user",
            session.owner_id or "",
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
        for env_name, field_name in env_mappings.items():
            if not _valid_env_name(env_name):
                raise RuntimeError(
                    f"Credential {credential_name!r} maps to invalid env var {env_name!r}"
                )
            provider_name = _provider_grant_name(
                session_id=str(session.id),
                credential_name=credential_name,
                field_name=field_name,
                env_name=env_name,
            )
            profile = _provider_profile(
                profile_id=provider_name,
                env_name=env_name,
                token_endpoint=self._credential_token_endpoint,
            )
            if provider_name in providers:
                continue
            providers.append(provider_name)
            await asyncio.to_thread(
                self._client.create_provider_grant,
                profile=profile,
                provider_name=provider_name,
                config={
                    "volundr_session_id": str(session.id),
                    "volundr_credential_name": credential_name,
                    "volundr_credential_field": field_name,
                },
            )

        if file_mappings:
            values = await self._credential_store.get_value(
                "user",
                session.owner_id or "",
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
        env["SKULD__VOLUNDR_API_URL"] = str(spec.values.get("volundr", {}).get("apiUrl") or "")
        if not env["SKULD__VOLUNDR_API_URL"]:
            server_host = os.environ.get("NIUU_SERVER_HOST", "127.0.0.1")
            server_port = os.environ.get("NIUU_SERVER_PORT", "8080")
            env["SKULD__VOLUNDR_API_URL"] = f"http://{server_host}:{server_port}"

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
        resources = spec.values.get("resources")
        if not isinstance(resources, dict):
            resources = {}
        result: dict[str, Any] = {}
        for key in ("requests", "limits"):
            values = resources.get(key)
            if isinstance(values, dict):
                clean = {
                    str(resource): str(quantity)
                    for resource, quantity in values.items()
                    if resource and quantity
                }
                if clean:
                    result[key] = clean
        if self._cpu or self._memory:
            limits = dict(result.get("limits") or {})
            limits.update(_compact({"cpu": self._cpu, "memory": self._memory}))
            if limits:
                result["limits"] = limits
        return result

    @staticmethod
    def _driver_config_from_spec(spec: SessionSpec) -> dict[str, Any]:
        pod: dict[str, Any] = {}
        if isinstance(spec.values.get("nodeSelector"), dict):
            pod["node_selector"] = {
                str(key): str(value)
                for key, value in spec.values["nodeSelector"].items()
                if key and value
            }
        tolerations = spec.values.get("tolerations")
        if isinstance(tolerations, list) and tolerations:
            pod["tolerations"] = tolerations
        runtime_class_name = spec.values.get("runtimeClassName")
        if runtime_class_name:
            pod["runtime_class_name"] = str(runtime_class_name)
        priority_class_name = spec.values.get("priorityClassName")
        if priority_class_name:
            pod["priority_class_name"] = str(priority_class_name)

        config: dict[str, Any] = {}
        if pod:
            config["pod"] = pod
        return config

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
    openshell_values = spec.values.get("openshell")
    if not isinstance(openshell_values, dict):
        return ()
    raw_processes = openshell_values.get("processes")
    if not isinstance(raw_processes, list):
        return ()

    processes: list[OpenShellRuntimeProcess] = []
    names: set[str] = set()
    for raw in raw_processes:
        if not isinstance(raw, dict):
            raise RuntimeError("OpenShell runtime process entries must be objects")
        name = str(raw.get("name") or "").strip()
        if not name or name in names:
            raise RuntimeError(f"OpenShell runtime process has invalid name {name!r}")
        command = raw.get("command")
        if not isinstance(command, list) or not command:
            raise RuntimeError(f"OpenShell runtime process {name!r} has no command")
        command_parts = tuple(str(part) for part in command)
        if any(not part or "\x00" in part for part in command_parts):
            raise RuntimeError(f"OpenShell runtime process {name!r} has an invalid command")
        env = _string_dict(raw.get("env") or {})
        raw_files = raw.get("files") or {}
        if not isinstance(raw_files, dict):
            raise RuntimeError(f"OpenShell runtime process {name!r} files must be an object")
        files: dict[str, bytes] = {}
        for destination, content in raw_files.items():
            path = _sandbox_credential_path(str(destination))
            value = str(content)
            if "\x00" in value:
                raise RuntimeError(f"OpenShell runtime process {name!r} file contains a null byte")
            files[path] = value.encode("utf-8")
        log_path = _sandbox_credential_path(str(raw.get("logPath") or raw.get("log_path") or ""))
        names.add(name)
        processes.append(
            OpenShellRuntimeProcess(
                name=name,
                command=command_parts,
                env=env,
                files=files,
                log_path=log_path,
            )
        )
    return tuple(processes)


def _provider_profile(
    *,
    profile_id: str,
    env_name: str,
    token_endpoint: str,
    cache_ttl_seconds: int = 300,
) -> Any:
    target = _provider_target(env_name)
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
        display_name=f"Niuu runtime credential for {env_name}",
        description="OpenBao-backed dynamic credential scoped to one Volundr session",
        category=target["category"],
        credentials=[credential],
        endpoints=endpoints,
        binaries=binaries,
    )


def _provider_target(env_name: str) -> dict[str, Any]:
    if env_name == CODEX_ACCESS_TOKEN_ENV:
        return {
            "auth_style": "bearer",
            "header_name": "Authorization",
            "hosts": (
                "api.openai.com",
                "auth.openai.com",
                "chatgpt.com",
                "ab.chatgpt.com",
                "files.openai.com",
            ),
            "binaries": (
                "/usr/bin/codex",
                "/usr/local/bin/codex",
                "/opt/niuu/bin/codex",
                "/usr/lib/node_modules/@openai/**",
            ),
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


def _credential_file_archive(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    directories: set[str] = set()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for destination in sorted(files):
            path = PurePosixPath(_sandbox_credential_path(destination))
            parents = list(path.parents)
            for parent in reversed(parents):
                parent_str = str(parent)
                if parent_str in {"/", "/sandbox", "/run"}:
                    continue
                if parent_str in directories:
                    continue
                directory = tarfile.TarInfo(parent_str.lstrip("/"))
                directory.type = tarfile.DIRTYPE
                directory.mode = 0o700
                directory.mtime = 0
                archive.addfile(directory)
                directories.add(parent_str)

            content = files[destination]
            info = tarfile.TarInfo(str(path).lstrip("/"))
            info.mode = 0o600
            info.size = len(content)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def _default_policy() -> sandbox_pb2.SandboxPolicy:
    return sandbox_pb2.SandboxPolicy(
        version=1,
        filesystem=sandbox_pb2.FilesystemPolicy(
            include_workdir=True,
            read_only=[
                "/bin",
                "/etc",
                "/lib",
                "/lib64",
                "/opt",
                "/proc",
                "/sbin",
                "/usr",
                "/var/log",
                "/dev/urandom",
            ],
            read_write=["/sandbox", "/tmp", "/dev/null"],
        ),
        landlock=sandbox_pb2.LandlockPolicy(),
        process=sandbox_pb2.ProcessPolicy(run_as_user="sandbox", run_as_group="sandbox"),
        network_policies={
            "github_https": _network_policy_rule(
                name="github-https",
                hosts=(
                    "github.com",
                    "api.github.com",
                    "codeload.github.com",
                    "objects.githubusercontent.com",
                    "raw.githubusercontent.com",
                ),
                binaries=(
                    "/usr/bin/git",
                    "/usr/lib/git-core/git-remote-http",
                    "/usr/lib/git-core/git-remote-https",
                    "/usr/bin/curl",
                ),
            ),
            "openai_https": _network_policy_rule(
                name="openai-https",
                hosts=("api.openai.com",),
                binaries=(
                    "/usr/local/bin/codex",
                    "/usr/bin/node",
                    "/usr/local/bin/node",
                    "/opt/venv/bin/python3",
                ),
            ),
        },
    )


def _network_policy_rule(
    *,
    name: str,
    hosts: Sequence[str],
    binaries: Sequence[str],
) -> sandbox_pb2.NetworkPolicyRule:
    return sandbox_pb2.NetworkPolicyRule(
        name=name,
        endpoints=[
            sandbox_pb2.NetworkEndpoint(
                host=host,
                port=443,
                protocol="rest",
                tls="terminate",
                enforcement="enforce",
                access="full",
            )
            for host in hosts
        ],
        binaries=[sandbox_pb2.NetworkBinary(path=binary) for binary in binaries],
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
    openshell_values = spec.values.get("openshell")
    if not isinstance(openshell_values, dict):
        return []
    raw_mappings = openshell_values.get("credentialMappings") or openshell_values.get(
        "credential_mappings"
    )
    if not isinstance(raw_mappings, list):
        return []
    return [mapping for mapping in raw_mappings if isinstance(mapping, dict)]


def _codex_auth_from_spec(spec: SessionSpec) -> dict[str, str]:
    openshell_values = spec.values.get("openshell")
    if not isinstance(openshell_values, dict):
        return {}
    raw = openshell_values.get("codexAuth") or openshell_values.get("codex_auth")
    if not isinstance(raw, dict):
        return {}
    credential_name = str(raw.get("credentialName") or raw.get("credential_name") or "").strip()
    auth_field = str(raw.get("authField") or raw.get("auth_field") or "").strip()
    if not credential_name or not auth_field:
        raise RuntimeError("OpenShell codexAuth requires credentialName and authField")
    return {"credential_name": credential_name, "auth_field": auth_field}


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


def _jwt_remaining_seconds(token: str) -> int:
    try:
        claims = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_aud": False,
                "verify_exp": False,
            },
        )
        expires_at = int(claims["exp"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("Codex OAuth access token is not a valid expiring JWT") from exc
    return expires_at - int(time.time())


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
