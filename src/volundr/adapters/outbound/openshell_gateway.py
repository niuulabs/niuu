"""Native OpenShell gateway adapter for Kubernetes-backed Forge sessions.

It mints a Keycloak client-credentials token and talks to the OpenShell gateway
gRPC API directly. The old OpenShell CLI shell-out adapter was intentionally
removed; service/runtime auth belongs at this gateway boundary.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import io
import itertools
import json
import logging
import re
import shlex
import socket
import tarfile
import tempfile
import threading
import time
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import IO, Any
from urllib.parse import urlparse, urlunparse
from uuid import UUID, uuid4

import grpc
import httpx
import yaml
from google.protobuf import duration_pb2, struct_pb2
from openshell._proto import datamodel_pb2, openshell_pb2, openshell_pb2_grpc, sandbox_pb2

from niuu.adapters.workload_identity.jwt import JwtWorkloadIdentityVerifier
from niuu.domain.models import Principal
from niuu.domain.oauth_credentials import OAUTH_ENGINE
from niuu.domain.services.token_scope import (
    OPENSHELL_RESIDENT_TOKEN_USE,
    OPENSHELL_SESSION_TOKEN_USE,
)
from niuu.ports.realm_repository import RealmRepository
from niuu.ports.session_proxy import SessionProxyTarget
from niuu.ports.workload_identity import WorkloadTokenIssuer
from volundr.adapters.outbound.brokered_credentials import BrokeredCredentialPodManager
from volundr.adapters.outbound.k8s_storage_adapter import _storage_bytes
from volundr.adapters.outbound.local_process import LocalProcessPodManager
from volundr.adapters.outbound.openshell_contract import (
    DEFAULT_SANDBOX_ATTACHABLE_LABELS,
    OPENSHELL_WORKSPACE,
    check_peer_workloads,
    check_pod_metadata_keys,
    check_volume_mounts,
    network_mode,
)
from volundr.adapters.outbound.openshell_resident_workspace import (
    ANNOTATION_MIGRATION_DIGEST,
    ANNOTATION_MIGRATION_HOLDER,
    ANNOTATION_MIGRATION_PHASE,
    ANNOTATION_MIGRATION_SOURCE,
    PHASE_COMPLETE,
    PHASE_COPYING,
    PHASE_VERIFIED,
    RESTARTABLE_PHASES,
    ResidentWorkspaceClaim,
    ResidentWorkspaceClaims,
    WorkspaceClaimInUseError,
    WorkspaceManifest,
    WorkspaceMigrationError,
    WorkspaceMigrationHeldError,
    WorkspaceMigrationLease,
    WorkspaceTooLargeError,
    WorkspaceVerificationError,
    read_workspace_manifest,
    resident_workspace_claim,
    resident_workspace_claim_name,
    workspace_migration_state,
)
from volundr.adapters.outbound.resident_container_spec import (
    RAVN_OPENCLAW_PORT,
    RAVN_OPENCLAW_TOKEN_ENV,
    enable_resident_openclaw_shim,
    realm_charter_page_for,
    realm_mount_name_for,
    realm_routing_prefix_for,
    resident_flock_environment,
    resident_flock_labels,
    resident_flock_profile_configured,
    resident_flock_runtime_config,
    resident_flock_skuld_config,
    resident_mesh_pod_metadata,
    resident_openclaw_shim_enabled,
)
from volundr.adapters.outbound.resident_container_spec import (
    image_from_values as _shared_image_from_values,
)
from volundr.adapters.outbound.resident_container_spec import (
    resident_attribution_headers as _shared_resident_attribution_headers,
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
    ResidentDesiredState,
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
    ResidentOpenClawShimHost,
    ResidentOpenClawTokens,
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
#: How long a resident's deploy or migration waits for a sandbox it removes to go.
#: Deleting a resident sandbox (its pod, volumes and gateway records) took over
#: 30s on Noatun, so a rollback gave up on a sandbox that was still going.
DEFAULT_RESIDENT_SANDBOX_DELETE_TIMEOUT_SECONDS = 180.0
TCP_FORWARD_BUFFER_BYTES = 64 * 1024
# The gateway decodes at most 1 MiB per gRPC message; streamed stdin stays well below.
EXEC_STDIN_CHUNK_BYTES = 256 * 1024
#: The image Völundr created a resident's sandbox with (sandbox annotation).
SANDBOX_IMAGE_ANNOTATION = "volundr.niuu.io/image"
#: The image of the sandbox this one replaced on an upgrade. A Völundr that still
#: renders it (the old replica while Völundr itself rolls out) must not move back.
SANDBOX_REPLACED_IMAGE_ANNOTATION = "volundr.niuu.io/replaced-image"
#: The resident's own workspace claim this sandbox mounts. A resident sandbox
#: without it predates the claim: its workspace is copied onto the claim once.
SANDBOX_WORKSPACE_CLAIM_ANNOTATION = "volundr.niuu.io/workspace-claim"
#: The sandbox a resident's workspace migration creates beside the old one; the
#: next one it is recreated as (an upgrade) takes the default name again.
RESIDENT_MIGRATION_SANDBOX_PREFIX = "m-"
RESIDENT_SANDBOX_PREFIX = "r-"
RESIDENT_SANDBOX_ID_CHARS = 17
#: Ravn keeps much of its state under ~/.ravn (memory.db, the drive-loop queue and
#: cron state, skills), outside RAVN_STATE_DIR. On a persistent workspace that
#: directory lives on the claim and ~/.ravn links to it.
RAVN_HOME_STATE_DIR = ".ravn"
RAVN_PERSISTED_HOME_DIR = ".ravn-home"
#: Where a resident's own claim is mounted; its workspace path links here.
#: Outside /sandbox on purpose: as soon as a driver mount sits under /sandbox, the
#: gateway drops its own /sandbox volume (seeded from the image, and writable
#: through the pod's fsGroup), and /sandbox is the image's directory, owned by the
#: image's ``sandbox`` user rather than the UID the gateway runs the sandbox as
#: (1000). Völundr then cannot write ``/sandbox/.volundr`` or link ``~/.ravn``.
RESIDENT_WORKSPACE_VOLUME_PATH = "/resident-workspace"
RESIDENT_WORKSPACE_VOLUME_NAME = "resident-workspace"
#: What the workspace path of a claim-backed sandbox can be (_workspace_link_state_script).
WORKSPACE_LINK_STATES = frozenset({"linked", "mounted", "foreign", "directory", "missing"})
#: The states in which the workspace path is the resident's volume.
WORKSPACE_ON_VOLUME = frozenset({"linked", "mounted"})
DEFAULT_WORKSPACE_MIGRATION_LEASE_SECONDS = 300.0
DEFAULT_WORKSPACE_MIGRATION_TIMEOUT_SECONDS = 1800.0
# Backoff for calls the gateway rejected because its SQLite store was locked by
# a concurrent write (SQLITE_BUSY); several resident deployments at once hit it.
GATEWAY_BUSY_RETRY_DELAYS = (0.25, 0.5, 1.0, 2.0, 4.0)
BOOTSTRAP_TIMEOUT_SECONDS = 600
#: Backgrounding a process with nohup returns at once; this bounds a wedged exec.
DETACHED_EXEC_TIMEOUT_SECONDS = 10
#: Unpacking one projected-file tar archive into the sandbox.
WRITE_FILES_TIMEOUT_SECONDS = 30.0
BOOTSTRAP_GIT_ATTEMPTS = 20
MAX_SANDBOX_ROUTING_NAME_LENGTH = 19
OAUTH_CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-spiffe"
GRANT_AUDIENCE_PREFIX = "niuu:credential:"
PLATFORM_GRANT_AUDIENCE_PREFIX = "niuu:platform:"
PLATFORM_ACCESS_TOKEN_ENV = "NIUU_VOLUNDR_ACCESS_TOKEN"
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
    "XAI_API_KEY",
    "DEEPSEEK_API_KEY",
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
    #: The image of the sandbox's template and the sandbox's own annotations.
    image: str = ""
    annotations: dict[str, str] | None = None


class WorkspaceRepair(Enum):
    """What checking a running resident's workspace link did."""

    #: Already on its volume, checked before, or a deployment owns its processes.
    SKIPPED = "skipped"
    REPAIRED = "repaired"
    #: Its sandbox exists but the repair failed; tried again on the next pass.
    FAILED = "failed"
    #: Its sandbox is gone or being deleted: redeploy it, do not repair it.
    SANDBOX_GONE = "sandbox-gone"


@dataclass(frozen=True)
class OpenShellPlatformFollow:
    """What following the platform's version means for one resident this pass."""

    #: The sandbox is being recreated on the current image: report this.
    redeploy: ResidentRuntimeObservation | None = None
    #: Reported with the resident's observation: a held or failed upgrade.
    condition: ResidentCondition | None = None
    failed: bool = False
    #: The upgrade removed the resident's service but not its sandbox: expose it again.
    service_removed: bool = False


@dataclass(frozen=True)
class OpenShellTransfer:
    """How a streamed exec ended: its exit (None: none reported), stderr, bytes read."""

    exit_code: int | None
    stderr: str
    size: int
    truncated: bool = False


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


def _gateway_store_busy(exc: grpc.RpcError) -> bool:
    details = getattr(exc, "details", None)
    message = details() if callable(details) else str(exc)
    return "database is locked" in str(message or "")


class _BusyRetryingStub:
    """Retry gateway calls rejected only because the gateway's store was busy.

    The rejected call's database transaction did not commit, so it is safe to
    send again. Exec streams fail on their first event, so that event is read
    inside the retry; the bidirectional TCP forward is passed through untouched.
    """

    _SERVER_STREAMS = frozenset({"ExecSandbox"})
    # Bidirectional streams consume their request iterator: never sent twice.
    _PASSTHROUGH = frozenset({"ForwardTcp", "ExecSandboxInteractive"})

    def __init__(self, stub: Any, delays: Sequence[float] = GATEWAY_BUSY_RETRY_DELAYS) -> None:
        self._stub = stub
        self._delays = tuple(delays)

    def __getattr__(self, name: str) -> Any:
        method = getattr(self._stub, name)
        if name in self._PASSTHROUGH or not callable(method):
            return method
        if name in self._SERVER_STREAMS:
            return lambda *args, **kwargs: self._retry(
                lambda: _started_stream(method(*args, **kwargs))
            )
        return lambda *args, **kwargs: self._retry(lambda: method(*args, **kwargs))

    def _retry(self, call: Any) -> Any:
        for delay in self._delays:
            try:
                return call()
            except grpc.RpcError as exc:
                if not _gateway_store_busy(exc):
                    raise
                logger.info("OpenShell gateway store busy; retrying in %.2fs", delay)
            time.sleep(delay)
        return call()


def _started_stream(stream: Any) -> Any:
    iterator = iter(stream)
    try:
        first = next(iterator)
    except StopIteration:
        return iter(())
    return itertools.chain((first,), iterator)


class OpenShellGatewayClient:
    """Native gRPC client for the OpenShell gateway calls Volundr needs."""

    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_GATEWAY_ENDPOINT,
        token_provider: ClientCredentialsTokenProvider,
        compute_driver: str = "kubernetes",
        plaintext: bool = True,
        timeout: float = 30.0,
    ) -> None:
        self._compute_driver = compute_driver
        self._endpoint = _endpoint_hostport(endpoint)
        self._token_provider = token_provider
        self._timeout = float(timeout)
        channel = (
            grpc.insecure_channel(self._endpoint)
            if plaintext
            else grpc.secure_channel(self._endpoint, grpc.ssl_channel_credentials())
        )
        self._channel = channel
        self._stub = _BusyRetryingStub(openshell_pb2_grpc.OpenShellStub(channel))
        self._workspace_scope = datamodel_pb2.WorkspaceSelector(workspace=OPENSHELL_WORKSPACE)

    def close(self) -> None:
        self._channel.close()
        self._token_provider.close()

    def wait_for_ready(self, timeout: float) -> None:
        """Wait for the native channel after establishing a new guest tunnel."""
        grpc.channel_ready_future(self._channel).result(timeout=timeout)

    def create_sandbox(
        self,
        *,
        name: str,
        image: str,
        env: dict[str, str],
        labels: dict[str, str],
        annotations: dict[str, str] | None = None,
        pod_labels: dict[str, str] | None = None,
        pod_annotations: dict[str, str] | None = None,
        resources: dict[str, Any] | None = None,
        driver_config: dict[str, Any] | None = None,
        providers: Sequence[str] = (),
        policy: Any | None = None,
    ) -> OpenShellSandbox:
        """Create a sandbox.

        ``labels``/``annotations`` are the sandbox's own gateway metadata, which
        Völundr reads back. ``pod_labels``/``pod_annotations`` go onto the
        sandbox Pod and must be admitted by the gateway's ``allowed_pod_labels``
        / ``allowed_pod_annotations``; only what something on the Pod reads
        belongs there.
        """
        check_pod_metadata_keys("labels", (pod_labels or {}).keys())
        check_pod_metadata_keys("annotations", (pod_annotations or {}).keys())
        template = openshell_pb2.SandboxTemplate(image=image)
        if pod_labels:
            template.labels.update(pod_labels)
        if pod_annotations:
            template.annotations.update(pod_annotations)
        template.environment.update(env)
        if resources:
            template.resources.CopyFrom(_protobuf_struct(resources))
        if driver_config:
            template.driver_config.CopyFrom(_protobuf_struct({self._compute_driver: driver_config}))
        spec = openshell_pb2.SandboxSpec(
            environment=env,
            template=template,
            policy=policy,
            providers=list(providers),
        )
        request = openshell_pb2.CreateSandboxRequest(
            workspace_scope=self._workspace_scope,
            spec=spec,
            name=name,
            labels=labels,
            annotations=annotations or {},
        )
        response = self._stub.CreateSandbox(
            request,
            timeout=self._timeout,
            metadata=self._metadata(),
        )
        return _sandbox_from_proto(response.sandbox)

    def get_sandbox(self, name: str) -> OpenShellSandbox | None:
        try:
            response = self._stub.GetSandbox(
                openshell_pb2.GetSandboxRequest(workspace_scope=self._workspace_scope, name=name),
                timeout=self._timeout,
                metadata=self._metadata(),
            )
        except grpc.RpcError as exc:
            if exc.code() == grpc.StatusCode.NOT_FOUND:
                return None
            raise
        return _sandbox_from_proto(response.sandbox)

    def get_sandbox_by_id(self, sandbox_id: str) -> OpenShellSandbox | None:
        page_token = ""
        while True:
            response = self._stub.ListSandboxes(
                openshell_pb2.ListSandboxesRequest(
                    workspace_scope=self._workspace_scope, page_size=100, page_token=page_token
                ),
                timeout=self._timeout,
                metadata=self._metadata(),
            )
            for sandbox in response.sandboxes:
                if str(sandbox.metadata.id) == sandbox_id:
                    return _sandbox_from_proto(sandbox)
            page_token = str(response.next_page_token)
            if not page_token:
                return None

    def stop_sandbox(self, name: str) -> OpenShellSandbox:
        response = self._stub.StopSandbox(
            openshell_pb2.StopSandboxRequest(workspace_scope=self._workspace_scope, name=name),
            timeout=self._timeout,
            metadata=self._metadata(),
        )
        return _sandbox_from_proto(response.sandbox)

    def start_sandbox(self, name: str) -> OpenShellSandbox:
        response = self._stub.StartSandbox(
            openshell_pb2.StartSandboxRequest(workspace_scope=self._workspace_scope, name=name),
            timeout=self._timeout,
            metadata=self._metadata(),
        )
        return _sandbox_from_proto(response.sandbox)

    def delete_sandbox(self, name: str) -> bool:
        """Delete a sandbox by name; False when it was already gone.

        ACCEPTED (the gateway record still exists while the pod goes) and
        COMPLETED both mean this call deleted it; callers that need it gone
        poll ``get_sandbox`` until it disappears.
        """
        response = self._stub.DeleteSandbox(
            openshell_pb2.DeleteSandboxRequest(
                workspace_scope=self._workspace_scope, name=name, allow_missing=True
            ),
            timeout=self._timeout,
            metadata=self._metadata(),
        )
        return _deletion_outcome(response.outcome, f"sandbox {name!r}")

    def delete_service(self, *, sandbox_name: str, service: str) -> bool:
        if len(sandbox_name) > MAX_SANDBOX_ROUTING_NAME_LENGTH:
            return False

        response = self._stub.DeleteService(
            openshell_pb2.DeleteServiceRequest(
                workspace_scope=self._workspace_scope,
                sandbox=sandbox_name,
                name=service,
                allow_missing=True,
            ),
            timeout=self._timeout,
            metadata=self._metadata(),
        )
        return _deletion_outcome(response.outcome, f"service {service!r} of {sandbox_name!r}")

    def get_provider(self, name: str) -> Any | None:
        try:
            response = self._stub.GetProvider(
                openshell_pb2.GetProviderRequest(workspace_scope=self._workspace_scope, name=name),
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
                    workspace_scope=self._workspace_scope,
                    provider=datamodel_pb2.Provider(
                        metadata=datamodel_pb2.ObjectMeta(name=provider_name),
                        type=str(profile.id),
                        config=config,
                        credentials=_provider_credential_slots(profile),
                    ),
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
                openshell_pb2.DeleteProviderRequest(
                    workspace_scope=self._workspace_scope, name=grant.provider_name
                ),
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
                workspace_scope=self._workspace_scope,
                sandbox=sandbox_name,
                name=service,
                target_port=int(target_port),
                domain=True,
            ),
            timeout=self._timeout,
            metadata=self._metadata(),
        )
        return str(response.url or "")

    def start_tcp_forward(self, *, sandbox: str, target_port: int) -> OpenShellTcpForwarder:
        """Open a local listener backed by OpenShell's authenticated ForwardTcp RPC."""
        return OpenShellTcpForwarder(self, sandbox=sandbox, target_port=target_port)

    def _forward_tcp_connection(
        self,
        connection: socket.socket,
        *,
        sandbox: str,
        target_port: int,
    ) -> None:
        # A TCP target is authorized by this client's gateway principal; only
        # SSH targets need a CreateSshSession token, so none is minted here.
        def frames():
            yield openshell_pb2.TcpForwardFrame(
                init=openshell_pb2.TcpForwardInit(
                    sandbox=sandbox,
                    workspace=self._workspace_scope.workspace,
                    service_id=f"volundr-resident:{sandbox}:{target_port}",
                    tcp=openshell_pb2.TcpRelayTarget(host="127.0.0.1", port=target_port),
                )
            )
            while True:
                data = connection.recv(TCP_FORWARD_BUFFER_BYTES)
                if not data:
                    return
                yield openshell_pb2.TcpForwardFrame(data=data)

        responses = self._stub.ForwardTcp(frames(), metadata=self._metadata())
        for response in responses:
            if response.HasField("data"):
                connection.sendall(response.data)

    def exec_detached(
        self,
        *,
        sandbox: str,
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
                workspace_scope=self._workspace_scope,
                sandbox=sandbox,
                command=["sh", "-s"],
                environment=env,
                stdin=script.encode(),
                execution_timeout=_duration(DETACHED_EXEC_TIMEOUT_SECONDS),
            ),
            timeout=self._timeout,
            metadata=self._metadata(),
        )
        exit_code = 0
        for event in stream:
            if event.HasField("exit"):
                exit_code = int(event.exit.exit_code)
        return exit_code

    def get_sandbox_logs(
        self,
        sandbox: str,
        *,
        lines: int,
        sources: Sequence[str],
        min_level: str,
    ) -> ResidentLogPage:
        response = self._stub.GetSandboxLogs(
            openshell_pb2.GetSandboxLogsRequest(
                workspace_scope=self._workspace_scope,
                sandbox=sandbox,
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
                    timestamp_ms=(
                        entry.event_time.ToMilliseconds() if entry.HasField("event_time") else 0
                    ),
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
        sandbox: str,
        script: str,
        env: dict[str, str],
    ) -> tuple[int, str]:
        stdin = script.encode()
        stream = self._stub.ExecSandbox(
            openshell_pb2.ExecSandboxRequest(
                workspace_scope=self._workspace_scope,
                sandbox=sandbox,
                command=["sh", "-s"],
                environment=env,
                stdin=stdin,
                execution_timeout=_duration(BOOTSTRAP_TIMEOUT_SECONDS),
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

    def exec_to_file(
        self,
        *,
        sandbox: str,
        script: str,
        destination: IO[bytes],
        max_bytes: int,
        timeout_seconds: float,
    ) -> OpenShellTransfer:
        """Run a script and write its stdout to ``destination``, up to ``max_bytes``.

        Runs without a login shell, so profile output can't mix into the bytes.
        Past ``max_bytes`` the stream is abandoned and the transfer is truncated.
        """
        stream = self._stub.ExecSandbox(
            openshell_pb2.ExecSandboxRequest(
                workspace_scope=self._workspace_scope,
                sandbox=sandbox,
                command=["sh", "-s"],
                stdin=script.encode(),
                execution_timeout=_duration(timeout_seconds),
                no_login_shell=True,
            ),
            timeout=max(self._timeout, float(timeout_seconds)),
            metadata=self._metadata(),
        )
        written = 0
        errors: list[str] = []
        exit_code: int | None = None
        for event in stream:
            if event.HasField("stdout"):
                data = bytes(event.stdout.data)
                if written + len(data) > max_bytes:
                    return OpenShellTransfer(
                        exit_code=None, stderr="".join(errors), size=written, truncated=True
                    )
                destination.write(data)
                written += len(data)
            elif event.HasField("stderr"):
                errors.append(_exec_output_text(event.stderr.data))
            elif event.HasField("exit"):
                exit_code = int(event.exit.exit_code)
        return OpenShellTransfer(exit_code=exit_code, stderr="".join(errors), size=written)

    def exec_from_file(
        self,
        *,
        sandbox: str,
        command: Sequence[str],
        source: IO[bytes],
        timeout_seconds: float,
    ) -> OpenShellTransfer:
        """Run a command with the rest of ``source`` on its stdin, in gateway-sized chunks.

        ExecSandbox takes stdin in one message, which the gateway caps, so this
        streams it over the interactive exec. That stream has no stdin EOF of its
        own: when the request stream ends, the gateway sends the sandbox EOF and
        closes the SSH channel at once, so the supervisor can no longer report
        the command's exit and the call fails with "exec relay closed before the
        command reported an exit status". So the command reads exactly the
        source's size (``head -c``) and finishes without EOF, and the request
        stream stays open until the gateway has reported the exit.

        ``source`` must be seekable; it is read from its current position.
        """
        position = source.tell()
        size = source.seek(0, io.SEEK_END) - position
        source.seek(position)
        remote = f"head -c {size} | {shlex.join(str(part) for part in command)}"
        start = openshell_pb2.ExecSandboxRequest(
            workspace_scope=self._workspace_scope,
            sandbox=sandbox,
            command=["sh", "-c", remote],
            execution_timeout=_duration(timeout_seconds),
            no_login_shell=True,
        )
        deadline = max(self._timeout, float(timeout_seconds))
        finished = threading.Event()
        sent = 0

        def frames():
            nonlocal sent
            yield openshell_pb2.ExecSandboxInput(start=start)
            while not finished.is_set() and (chunk := source.read(EXEC_STDIN_CHUNK_BYTES)):
                sent += len(chunk)
                yield openshell_pb2.ExecSandboxInput(stdin=chunk)
            # Ending the request stream now would close the channel before the exit.
            finished.wait(deadline)

        output: list[str] = []
        exit_code: int | None = None
        try:
            stream = self._stub.ExecSandboxInteractive(
                frames(),
                timeout=deadline,
                metadata=self._metadata(),
            )
            for event in stream:
                if event.HasField("stdout"):
                    output.append(_exec_output_text(event.stdout.data))
                elif event.HasField("stderr"):
                    output.append(_exec_output_text(event.stderr.data))
                elif event.HasField("exit"):
                    exit_code = int(event.exit.exit_code)
        finally:
            finished.set()
        return OpenShellTransfer(exit_code=exit_code, stderr="".join(output), size=sent)

    def write_files(self, *, sandbox: str, files: dict[str, bytes]) -> None:
        for extraction_root, archive in _credential_file_archives(files):
            stream = self._stub.ExecSandbox(
                openshell_pb2.ExecSandboxRequest(
                    workspace_scope=self._workspace_scope,
                    sandbox=sandbox,
                    command=["tar", "-xf", "-", "-C", extraction_root],
                    stdin=archive,
                    execution_timeout=_duration(WRITE_FILES_TIMEOUT_SECONDS),
                ),
                timeout=max(self._timeout, WRITE_FILES_TIMEOUT_SECONDS),
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
        sandbox: str,
        target_port: int,
    ) -> None:
        self._client = client
        self._sandbox = sandbox
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
                sandbox=self._sandbox,
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
    ResidentOpenClawShimHost,
):
    """Kubernetes OpenShell PodManager using OIDC and native gRPC."""

    @property
    def runtime_backend(self) -> str:
        return "openshell"

    def __init__(
        self,
        *,
        gateway_endpoint: str = DEFAULT_GATEWAY_ENDPOINT,
        gateway_public_url: str = "",
        compute_driver: str = "kubernetes",
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
        resident_sandbox_delete_timeout_seconds: float = (
            DEFAULT_RESIDENT_SANDBOX_DELETE_TIMEOUT_SECONDS
        ),
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
        driver_config: dict[str, Any] | None = None,
        resident_workspace_namespace: str = "",
        resident_workspace_size: str = "2Gi",
        resident_storage_class: str = "",
        resident_access_mode: str = "ReadWriteOnce",
        resident_workspace_migration_max_size: str = "1Gi",
        resident_workspace_migration_lease_seconds: float = (
            DEFAULT_WORKSPACE_MIGRATION_LEASE_SECONDS
        ),
        resident_workspace_migration_timeout_seconds: float = (
            DEFAULT_WORKSPACE_MIGRATION_TIMEOUT_SECONDS
        ),
        resident_workspace_migration_holder: str = "",
        sandbox_attachable_labels: dict[str, str] | None = None,
        client: OpenShellGatewayClient | None = None,
        **_extra: object,
    ) -> None:
        if sandbox_policy is None and client is None:
            raise ValueError("OpenShell sandbox_policy configuration is required")
        if resident_workspace_migration_lease_seconds <= 0:
            raise ValueError("resident_workspace_migration_lease_seconds must be positive")
        if resident_workspace_migration_timeout_seconds <= 0:
            raise ValueError("resident_workspace_migration_timeout_seconds must be positive")
        if resident_sandbox_delete_timeout_seconds <= 0:
            raise ValueError("resident_sandbox_delete_timeout_seconds must be positive")
        # Each resident's own workspace claim, in the namespace where the gateway runs
        # sandboxes. Unset, residents keep their workspace in the sandbox only.
        # The gateway mounts a claim only when it carries the labels its resource
        # admission requires (``required_labels``; OpenShell's defaults unless
        # the operator changed them on the gateway).
        self._workspace_claims = (
            ResidentWorkspaceClaims(
                self._get_core_api,
                resident_workspace_namespace,
                attach_labels=(
                    DEFAULT_SANDBOX_ATTACHABLE_LABELS
                    if sandbox_attachable_labels is None
                    else sandbox_attachable_labels
                ),
            )
            if resident_workspace_namespace
            else None
        )
        self._resident_workspace_defaults = {
            "size": resident_workspace_size,
            "storage_class": resident_storage_class,
            "access_mode": resident_access_mode,
        }
        self._workspace_migration_max_bytes = int(
            _storage_bytes(resident_workspace_migration_max_size)
        )
        self._workspace_migration_max_size = resident_workspace_migration_max_size
        self._workspace_migration_lease_seconds = float(resident_workspace_migration_lease_seconds)
        self._workspace_migration_timeout = float(resident_workspace_migration_timeout_seconds)
        # Who holds a migration: the pod name, so a restarted container resumes its own.
        self._workspace_migration_holder = resident_workspace_migration_holder or (
            socket.gethostname()
        )
        self._core_api_client: Any | None = None
        # Residents whose workspace migration failed in this Völundr: id -> reason.
        # Their old sandbox keeps running; retried on Restart or when Völundr restarts.
        self._failed_workspace_migrations: dict[str, WorkspaceMigrationError] = {}
        # The sandbox a resident's failed migration created and could not remove,
        # by resident id: reconcile removes it (_remove_abandoned_migration_sandbox).
        self._abandoned_migration_sandboxes: dict[str, str] = {}
        # Claim-backed sandboxes whose workspace path this Völundr saw on the volume.
        self._linked_workspaces: set[str] = set()
        self._gateway_public_url = gateway_public_url.rstrip("/")
        gateway_hostport = _endpoint_hostport(gateway_endpoint)
        gateway_host, separator, gateway_port = gateway_hostport.rpartition(":")
        if not separator or not gateway_host or not gateway_port.isdigit():
            raise ValueError("OpenShell gateway endpoint must include a host and port")
        self._gateway_connect_host = gateway_host.strip("[]")
        self._gateway_connect_port = int(gateway_port)
        self._gateway_connect_secure = not plaintext
        self._driver_config = deepcopy(driver_config or {})
        self._sandbox_image = sandbox_image
        self._sandbox_command = _normalize_command(sandbox_command) or DEFAULT_SANDBOX_COMMAND
        self._sandbox_workspace = sandbox_workspace
        self._sandbox_home = sandbox_home.rstrip("/")
        self._sandbox_policy = _sandbox_policy_from_config(sandbox_policy or {"version": 1})
        # A resident on its own claim also writes the volume mounted outside /sandbox.
        self._resident_claim_policy = _sandbox_policy_from_config(
            _with_read_write_path(sandbox_policy or {"version": 1}, RESIDENT_WORKSPACE_VOLUME_PATH)
        )
        self._service_port = int(service_port)
        self._service_name = service_name
        self._command_log_path = command_log_path
        self._cpu = cpu
        self._memory = memory
        self._ready_timeout = float(ready_timeout)
        self._resource_delete_timeout = float(resource_delete_timeout)
        self._resident_delete_timeout = float(resident_sandbox_delete_timeout_seconds)
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
            compute_driver=compute_driver,
            token_provider=ClientCredentialsTokenProvider(
                token_url=token_url,
                client_id=client_id,
                client_secret=client_secret,
            ),
            plaintext=plaintext,
            timeout=command_timeout,
        )
        self._service_urls: dict[str, str] = {}
        # Held while a deploy, restart or reconcile owns a resident's processes.
        self._resident_process_locks: dict[str, asyncio.Lock] = {}
        self._resident_forwarders: dict[str, OpenShellTcpForwarder] = {}
        self._provider_grants: dict[str, tuple[OpenShellProviderGrant, ...]] = {}
        self._credential_store: CredentialStorePort | None = None
        self._session_repository: SessionRepository | None = None
        self._resident_runtime_repository: ResidentRuntimeRepository | None = None
        self._workload_token_issuer: WorkloadTokenIssuer | None = None
        self._realm_repository: RealmRepository | None = None
        # Where each Ravn resident's own OpenClaw shim token is kept, handed over by
        # the Ravn session controller (set_resident_openclaw_tokens). None: no Ravn
        # sessions on this backend.
        self._resident_openclaw_tokens: ResidentOpenClawTokens | None = None
        # Ravn residents whose shim this Volundr has seen up, or restarted once to start.
        self._resident_openclaw_checked: set[str] = set()
        # Residents whose sandbox is being recreated on the current image: resident
        # id -> the image it replaces, until the new sandbox is deployed.
        self._resident_upgrades: dict[str, str] = {}
        # Residents whose upgrade failed in this Völundr: id -> (target image, reason).
        # Not retried until the target changes, an explicit restart, or Völundr restarts.
        self._failed_resident_upgrades: dict[str, tuple[str, str]] = {}
        # (resident id, target image) upgrades held, logged once; "" when the image is unknown.
        self._held_resident_upgrades: set[tuple[str, str]] = set()
        # Residents whose old sandbox an upgrade could not remove: id -> the reason,
        # logged once per reason. Retried on every pass.
        self._retrying_resident_upgrades: dict[str, str] = {}
        # Residents whose sandbox was seen gone or being deleted while they should run:
        # id -> that sandbox, logged once until the resident is deployed again.
        self._going_resident_sandboxes: dict[str, str] = {}
        # Why a running resident's workspace link could not be repaired: sandbox id ->
        # the error, logged once per error until the repair succeeds.
        self._failed_workspace_repairs: dict[str, str] = {}

    def set_resident_openclaw_tokens(self, tokens: ResidentOpenClawTokens) -> None:
        """Where each Ravn resident's own shim token is kept (RavnResidentSessionController)."""
        self._resident_openclaw_tokens = tokens

    async def _get_core_api(self) -> Any:
        """CoreV1Api for residents' workspace claims (the gateway can't create volumes)."""
        from kubernetes_asyncio import client

        from niuu.adapters.flux import create_flux_api_client

        if self._core_api_client is None:
            self._core_api_client = await create_flux_api_client()
        return client.CoreV1Api(self._core_api_client)

    def _resident_workspace_claim(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentWorkspaceClaim | None:
        """The claim Völundr manages for this resident; None when it manages none."""
        if self._workspace_claims is None:
            return None
        return resident_workspace_claim(
            runtime,
            profile,
            size=self._resident_workspace_defaults["size"],
            storage_class=self._resident_workspace_defaults["storage_class"],
            access_mode=self._resident_workspace_defaults["access_mode"],
            mount_path=self._sandbox_workspace,
        )

    def _resident_values(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> tuple[dict[str, Any], ResidentWorkspaceClaim | None]:
        """The values a resident's sandbox renders, with its own claim at the workspace."""
        values = self._with_brokered_credential_values(_resident_profile_values(profile))
        claim = self._resident_workspace_claim(runtime, profile)
        if claim is None:
            return values, None
        resident_values = {key: value for key, value in values.items() if key != "persistence"}
        resident_values["residentWorkspace"] = {
            "existingClaim": claim.name,
            "mountPath": RESIDENT_WORKSPACE_VOLUME_PATH,
        }
        return resident_values, claim

    async def resident_openclaw_token(self, runtime: ResidentRuntime) -> str:
        """The kept token; else the one the resident's Ravn process runs with; else new."""
        tokens = self._resident_openclaw_tokens
        if tokens is None:
            raise RuntimeError("Ravn resident sessions are not wired on openshell")
        token = await tokens.stored(runtime)
        if token:
            return token
        return await tokens.ensure(runtime, adopt=await self._read_resident_openclaw_token(runtime))

    async def _read_resident_openclaw_token(self, runtime: ResidentRuntime) -> str | None:
        """Read RAVN_OPENCLAW_TOKEN back from the running Ravn process, if it has one."""
        sandbox_name = str(runtime.backend_ref.get("name") or "")
        if not sandbox_name:
            return None
        try:
            exit_code, output = await asyncio.to_thread(
                self._client.exec_script,
                sandbox=sandbox_name,
                script=_resident_process_env_script("ravn", RAVN_OPENCLAW_TOKEN_ENV),
                env={},
            )
        except Exception:
            logger.debug("Could not read resident %s's shim token", runtime.id, exc_info=True)
            return None
        token = output.strip()
        if exit_code != 0 or not token or any(c.isspace() for c in token):
            return None
        return token

    def _resident_openclaw_enabled(
        self,
        runtime: ResidentRuntime,
        values: dict[str, Any],
    ) -> bool:
        return resident_openclaw_shim_enabled(
            runtime,
            values,
            tokens_provisioned=self._resident_openclaw_tokens is not None,
        )

    async def _resident_openclaw_environment(
        self,
        runtime: ResidentRuntime,
        values: dict[str, Any],
    ) -> dict[str, str]:
        """The shim's token for the Ravn process; never in the sandbox's own env."""
        if not self._resident_openclaw_enabled(runtime, values):
            return {}
        return {RAVN_OPENCLAW_TOKEN_ENV: await self.resident_openclaw_token(runtime)}

    async def _forget_resident_openclaw_token(self, runtime: ResidentRuntime) -> None:
        if runtime.engine is ResidentEngine.RAVN and self._resident_openclaw_tokens is not None:
            await self._resident_openclaw_tokens.forget(runtime)

    async def _start_resident_openclaw_shim(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
        values: dict[str, Any],
        sandbox: OpenShellSandbox,
    ) -> None:
        """Restart a running Ravn resident once whose processes predate its shim.

        A Ravn resident deployed before Ravn sessions were on by default runs without
        the shim; restarting its processes (the restart path) starts it with the new
        ravn.yaml and the resident's own token. Checked until seen up, and restarted at
        most once per resident per Volundr, so a shim that cannot start never restarts
        the resident in a loop.
        """
        runtime_id = str(runtime.id)
        if runtime_id in self._resident_openclaw_checked:
            return
        if self._resident_process_lock(runtime).locked():
            return
        port = _resident_openclaw_port(values)
        listening = await self._resident_processes_ready(sandbox.name, (), service_port=port)
        self._resident_openclaw_checked.add(runtime_id)
        if listening:
            return
        logger.info("Restarting resident %s to start its Ravn sessions shim", runtime.id)
        try:
            # Its processes only, in place: moving it to another image is
            # _follow_platform's decision, not this one's.
            async with self._resident_process_lock(runtime):
                await self._restart(runtime, profile, follow_platform=False)
        except Exception:
            logger.warning(
                "Could not restart resident %s to start its Ravn sessions shim",
                runtime.id,
                exc_info=True,
            )

    def resident_openclaw_target(
        self,
        runtime: ResidentRuntime,
        port: int,
    ) -> SessionProxyTarget | None:
        """The shim's sandbox port over the same native TCP tunnel Hermes residents use."""
        sandbox_id = str(runtime.backend_ref.get("id") or "")
        sandbox_name = str(runtime.backend_ref.get("name") or "")
        if not sandbox_id or not sandbox_name:
            return None
        key = _openclaw_forwarder_key(runtime, sandbox_id)
        forwarder = self._resident_forwarders.get(key)
        if forwarder is None:
            # A redeployed resident has a new sandbox: retire the old one's tunnel.
            for stale in [k for k in self._resident_forwarders if _is_openclaw_key(k, runtime)]:
                self._resident_forwarders.pop(stale).close()
            forwarder = self._client.start_tcp_forward(sandbox=sandbox_name, target_port=port)
            self._resident_forwarders[key] = forwarder
        return SessionProxyTarget(
            service_url=f"http://ravn-openclaw.internal:{port}/",
            connect_host=forwarder.host,
            connect_port=forwarder.port,
        )

    def set_credential_store(self, store: CredentialStorePort) -> None:
        """Inject credential store for resolving OpenShell launch credentials."""
        self._credential_store = store

    def set_realm_repository(self, repository: RealmRepository) -> None:
        """Enable resolving a resident's realm slug for its container config.

        Optional: without it, deploying a resident with ``realm_id`` set
        raises in ``_resolve_realm_slug`` rather than silently deploying with
        no realm/charter binding.
        """
        self._realm_repository = repository

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
                sandbox=ready.name,
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
                sandbox=ready.name,
                command=("codex", "login", "--device-auth"),
                env=environment,
                log_path=CODEX_ENROLLMENT_LOG_PATH,
                pid_path=CODEX_ENROLLMENT_PID_PATH,
            )
            if launch_exit != 0:
                raise RuntimeError("Codex device login failed to start")
            verification_uri, user_code = await self._wait_for_codex_device_challenge(
                ready.name,
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
        enrollment_sandbox = enrollment.runner_ref.get("sandbox_name", "")
        if not enrollment_sandbox:
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
            sandbox=enrollment_sandbox,
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
        sandbox_name: str,
        environment: dict[str, str],
    ) -> tuple[str, str]:
        deadline = time.monotonic() + self._credential_enrollment_challenge_timeout_seconds
        while time.monotonic() < deadline:
            exit_code, output = await asyncio.to_thread(
                self._client.exec_script,
                sandbox=sandbox_name,
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
        if len(self._sandbox_name(session)) > MAX_SANDBOX_ROUTING_NAME_LENGTH:
            sandbox = self._client.get_sandbox(self._sandbox_name(session))
            if sandbox is None or not sandbox.ready:
                return None
            return self._tcp_proxy_target(str(session.id), sandbox.name, self._service_port)
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
        if len(self._resident_sandbox_name(runtime)) > MAX_SANDBOX_ROUTING_NAME_LENGTH:
            sandbox_name = str(runtime.backend_ref.get("name") or "")
            if not sandbox_name:
                return None
            return self._tcp_proxy_target(
                str(runtime.id),
                sandbox_name,
                int(runtime.backend_ref.get("service_port") or self._service_port),
            )
        base = self._service_urls.get(str(runtime.id)) or str(
            runtime.backend_ref.get("service_url") or ""
        )
        return self._proxy_target(base)

    def _hermes_proxy_target(self, runtime: ResidentRuntime) -> SessionProxyTarget | None:
        runtime_id = str(runtime.id)
        forwarder = self._resident_forwarders.get(runtime_id)
        if forwarder is None:
            sandbox_name = str(runtime.backend_ref.get("name") or "")
            service_port = int(
                runtime.backend_ref.get("service_port") or HERMES_API_SERVER_DEFAULT_PORT
            )
            if not sandbox_name:
                return None
            forwarder = self._client.start_tcp_forward(
                sandbox=sandbox_name,
                target_port=service_port,
            )
            self._resident_forwarders[runtime_id] = forwarder
        return SessionProxyTarget(
            service_url=HERMES_INTERNAL_SERVICE_URL,
            connect_host=forwarder.host,
            connect_port=forwarder.port,
        )

    def _tcp_proxy_target(
        self, workload_id: str, sandbox_name: str, port: int
    ) -> SessionProxyTarget:
        # Existing long sandbox names predate the workspace-aware DNS limit.
        # Reuse the authenticated native tunnel rather than renaming live pods.
        forwarder = self._resident_forwarders.get(workload_id)
        if forwarder is None:
            forwarder = self._client.start_tcp_forward(sandbox=sandbox_name, target_port=port)
            self._resident_forwarders[workload_id] = forwarder
        return SessionProxyTarget(
            service_url="http://skuld.internal",
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
        legacy = None
        if session.pod_name and not spec.values.get("persistence", {}).get("existingClaim"):
            legacy = await asyncio.to_thread(self._client.get_sandbox, sandbox_name)
        env = self._build_env(session, spec)
        if legacy is not None:
            env.update(
                {
                    "HOME": self._sandbox_home,
                    "CODEX_HOME": f"{self._sandbox_home}/.codex",
                    "CLAUDE_CONFIG_DIR": f"{self._sandbox_home}/.claude",
                }
            )
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
        if spec.values.get("persistence", {}).get("existingClaim"):
            labels["volundr.niuu.io/storage"] = "forge"
        pod_labels: dict[str, str] = {}
        pod_annotations: dict[str, str] = {}
        if spec.pod_spec:
            pod_labels.update({str(key): str(value) for key, value in spec.pod_spec.labels.items()})
            pod_annotations.update(
                self._supported_annotations_from_pod_spec(spec.pod_spec.annotations)
            )
        self._warn_unsupported_pod_spec(session, spec)
        try:
            platform_providers = await self._resolve_platform_provider(
                session, api_urls=_resident_api_urls(spec.values)
            )
            grants = tuple(
                OpenShellProviderGrant(provider_name=name, profile_id=name)
                for name in platform_providers
            )
            credential_context = await self._resolve_credential_env(session, spec)
            env.update(credential_context.environment)
            process_env = {**env, **credential_context.process_environment}
            runtime_processes = _runtime_processes_from_spec(spec)
            workloads = (spec.values.get("openshell") or {}).get("workloads", [])
            workload_start_file = ""
            if workloads:
                if runtime_processes:
                    raise ValueError(
                        "OpenShell flock workloads cannot also declare detached processes"
                    )
                if credential_context.process_environment:
                    raise ValueError(
                        "OpenShell workload containers require dynamic provider credentials; "
                        "disable materializeEnvironment"
                    )
                workload_start_file = f"{self._sandbox_workspace}/.volundr/start-{uuid4().hex}"
            driver_config = _driver_config_from_values(
                spec.values,
                workload_start_file=workload_start_file,
                workload_environment=env,
            )
            if self._driver_config.keys() & driver_config.keys():
                raise ValueError("OpenShell operator and session driver configuration overlap")
            driver_config = {**self._driver_config, **driver_config}
            provider_names = (*platform_providers, *credential_context.providers)
            grants = tuple(
                OpenShellProviderGrant(provider_name=name, profile_id=name)
                for name in provider_names
            )
            if legacy is not None:
                sandbox = await self._resume_legacy_sandbox(sandbox_name)
            else:
                sandbox = await asyncio.to_thread(
                    self._client.create_sandbox,
                    name=sandbox_name,
                    image=self._sandbox_image,
                    env=env,
                    labels=labels,
                    pod_labels=pod_labels,
                    pod_annotations=pod_annotations,
                    resources=self._resources_from_spec(spec),
                    driver_config=driver_config,
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
                    sandbox=ready.name,
                    files=projected_files,
                )
            await self._bootstrap_workspace(
                ready.name,
                session,
                spec,
                env,
            )
            if workload_start_file:
                # Configuration travels over the file API, outside the bounded
                # pod driver config. Write the startup marker last.
                workload_files = {
                    path: content.encode("utf-8")
                    for path, content in (spec.values["openshell"].get("files") or {}).items()
                }
                await asyncio.to_thread(
                    self._client.write_files,
                    sandbox=ready.name,
                    files={**workload_files, workload_start_file: b"ready"},
                )
            for process in runtime_processes:
                process_exit = await asyncio.to_thread(
                    self._client.exec_detached,
                    sandbox=ready.name,
                    command=process.command,
                    env={**process_env, **process.env},
                    log_path=process.log_path,
                )
                if process_exit != 0:
                    raise RuntimeError(
                        f"OpenShell runtime process {process.name!r} failed with exit "
                        f"{process_exit}"
                    )
            # ExecSandbox env values reject newlines; command arguments are
            # shell-quoted by exec_detached and preserve the original prose.
            session_values = spec.values.get("session", {})
            prompts = [
                f"SKULD__SESSION__{name}={session_values[key]}"
                for name, key in (
                    ("INITIAL_PROMPT", "initialPrompt"),
                    ("SYSTEM_PROMPT", "systemPrompt"),
                )
                if session_values.get(key)
            ]
            command = (
                ["env", *prompts, *self._sandbox_command] if prompts else self._sandbox_command
            )
            exit_code = await asyncio.to_thread(
                self._client.exec_detached,
                sandbox=ready.name,
                command=command,
                env=process_env,
                log_path=self._command_log_path,
            )
            if exit_code != 0:
                raise RuntimeError(
                    f"OpenShell session command bootstrap failed with exit {exit_code}"
                )
            if len(sandbox_name) > MAX_SANDBOX_ROUTING_NAME_LENGTH:
                service_url = "http://skuld.internal"
            else:
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
                if legacy is not None:
                    await asyncio.to_thread(self._client.stop_sandbox, sandbox_name)
                else:
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

    async def _resume_legacy_sandbox(self, sandbox_name: str) -> OpenShellSandbox:
        """Start a stopped sandbox that owns its own storage.

        OpenShell v0.1.6 re-admits a sandbox's attachments on start and refuses
        state recorded by earlier gateways without admission provenance, so a
        session stopped on an older gateway cannot be resumed in place.
        """
        try:
            return await asyncio.to_thread(self._client.start_sandbox, sandbox_name)
        except grpc.RpcError as exc:
            raise RuntimeError(
                f"OpenShell could not resume sandbox {sandbox_name}: {_rpc_error_text(exc)}. "
                "A session stopped on an OpenShell gateway older than v0.1.6 cannot be "
                "resumed; archive what it needs and start a new session (its storage "
                "belongs to the old sandbox)"
            ) from exc

    async def stop(self, session: Session) -> bool:
        forwarder = self._resident_forwarders.pop(str(session.id), None)
        if forwarder is not None:
            await asyncio.to_thread(forwarder.close)

        self._service_urls.pop(str(session.id), None)
        sandbox_name = self._sandbox_name(session)
        grants = self._provider_grants.pop(str(session.id), ())
        sandbox = await asyncio.to_thread(self._client.get_sandbox, sandbox_name)
        if not grants and sandbox is not None:
            grants = tuple(
                OpenShellProviderGrant(provider_name=name, profile_id=name)
                for name in sandbox.providers
                if name.startswith("volundr-")
            )
        if sandbox is not None and (sandbox.labels or {}).get("volundr.niuu.io/storage") != "forge":
            # Legacy volumes are owned by the Sandbox CR: deleting it also
            # deletes history. Native stop removes compute but retains storage.
            if sandbox.phase == openshell_pb2.SANDBOX_PHASE_STOPPED:
                return True
            if sandbox.ready or sandbox.phase == openshell_pb2.SANDBOX_PHASE_READY:
                # Older bootstrap scripts copied CLI homes into /tmp. Save
                # those files onto the retained volume before removing compute.
                # Stage the merge to replace root-owned image defaults and
                # read-only Git caches, retaining the previous home as a backup.
                exit_code, _ = await asyncio.to_thread(
                    self._client.exec_script,
                    sandbox=sandbox.name,
                    script=(
                        "set -eu\n"
                        f"HOME_ROOT={shlex.quote(self._sandbox_home)}\n"
                        "for cli in codex claude; do\n"
                        '  source="/tmp/$cli-home"\n'
                        '  if [ -d "$source" ]; then\n'
                        '    target="$HOME_ROOT/.$cli"\n'
                        '    saved=$(mktemp -d "$HOME_ROOT/.$cli-save.XXXXXX")\n'
                        '    if [ -d "$target" ]; then cp -a "$target/." "$saved/"; fi\n'
                        '    cp -af "$source/." "$saved/"\n'
                        '    if [ -e "$target" ]; then mv "$target" "$saved.previous"; fi\n'
                        '    if ! mv "$saved" "$target"; then\n'
                        '      if [ -e "$saved.previous" ]; then\n'
                        '        mv "$saved.previous" "$target"\n'
                        "      fi\n"
                        "      exit 1\n"
                        "    fi\n"
                        "  fi\n"
                        "done\n"
                    ),
                    env={},
                )
                if exit_code != 0:
                    raise RuntimeError(
                        "Could not preserve legacy OpenShell CLI state; stop aborted"
                    )
            await asyncio.to_thread(
                self._client.delete_service,
                sandbox_name=sandbox_name,
                service=self._service_name,
            )
            await asyncio.to_thread(self._client.stop_sandbox, sandbox_name)
            deadline = time.monotonic() + self._ready_timeout
            while time.monotonic() < deadline:
                current = await asyncio.to_thread(self._client.get_sandbox, sandbox_name)
                if current is None or current.phase == openshell_pb2.SANDBOX_PHASE_STOPPED:
                    return True
                await asyncio.sleep(READY_POLL_INTERVAL)
            raise TimeoutError(f"OpenShell sandbox {sandbox_name} did not stop")
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

    def _resident_process_lock(self, runtime: ResidentRuntime) -> asyncio.Lock:
        return self._resident_process_locks.setdefault(str(runtime.id), asyncio.Lock())

    async def deploy(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        async with self._resident_process_lock(runtime):
            return await self._deploy(runtime, profile)

    async def _deploy(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        if not self.supports(profile):
            raise RuntimeError(f"OpenShell does not support resident profile {profile.id!r}")
        values, claim = self._resident_values(runtime, profile)
        sandbox_name = self._resident_sandbox_name(runtime)
        sandbox = await self._owned_resident_sandbox(runtime, sandbox_name)
        if claim is None:
            return await self._deploy_sandbox(
                runtime, values, sandbox_name=sandbox_name, sandbox=sandbox, claim=None
            )
        sandboxes = {sandbox_name: sandbox} if sandbox is not None else {}
        for name in self._resident_sandbox_names(runtime):
            if name in sandboxes or name == sandbox_name:
                continue
            found = await self._owned_resident_sandbox(runtime, name)
            if found is not None:
                sandboxes[name] = found
        mounted = [item for item in sandboxes.values() if _sandbox_mounts_claim(item, claim)]
        unmounted = [item for item in sandboxes.values() if not _sandbox_mounts_claim(item, claim)]
        if len(unmounted) > 1:
            raise RuntimeError(
                f"Resident {runtime.name} has several sandboxes without its workspace volume "
                f"({', '.join(sorted(item.name for item in unmounted))}); delete all but the "
                "one that holds its state"
            )
        if unmounted:
            return await self._deploy_unmigrated(
                runtime, profile, values, claim, unmounted[0], mounted
            )
        if len(mounted) > 1:
            raise RuntimeError(
                f"Resident {runtime.name} has several sandboxes on its workspace volume "
                f"({', '.join(sorted(item.name for item in mounted))}); delete all but one"
            )
        await self._ensure_resident_workspace_claim(runtime, claim)
        current = mounted[0] if mounted else None
        return await self._deploy_sandbox(
            runtime,
            values,
            sandbox_name=current.name if current is not None else sandbox_name,
            sandbox=current,
            claim=claim,
        )

    async def _owned_resident_sandbox(
        self,
        runtime: ResidentRuntime,
        sandbox_name: str,
    ) -> OpenShellSandbox | None:
        """The resident's sandbox of this name; None when there is none (or it is going)."""
        sandbox = await asyncio.to_thread(self._client.get_sandbox, sandbox_name)
        if sandbox is None:
            return None
        resident_label = dict(sandbox.labels or {}).get("volundr.niuu.io/resident")
        if resident_label != str(runtime.id):
            raise RuntimeError(
                f"OpenShell sandbox {sandbox_name!r} is not owned by resident {runtime.id}"
            )
        if sandbox.phase == openshell_pb2.SANDBOX_PHASE_DELETING:
            # A rolled-back attempt is still being deleted: let it go, then
            # provision a fresh sandbox under the same name.
            await self._wait_for_sandbox_deleted(
                sandbox_name, timeout=self._resident_delete_timeout
            )
            return None
        return sandbox

    async def _deploy_sandbox(
        self,
        runtime: ResidentRuntime,
        values: dict[str, Any],
        *,
        sandbox_name: str,
        sandbox: OpenShellSandbox | None,
        claim: ResidentWorkspaceClaim | None,
        replaced_image: str = "",
        before_start: Any = None,
        migration: _WorkspaceMigrationProgress | None = None,
        keep_sandbox_on_failure: bool = False,
    ) -> ResidentRuntimeObservation:
        """Deploy the resident into ``sandbox`` (adopted), or a new one named ``sandbox_name``.

        ``before_start`` runs once the sandbox is ready, before any file is
        written or process started (a workspace migration restores there).
        ``keep_sandbox_on_failure`` leaves an adopted sandbox in place when the
        deployment fails, for a sandbox that still holds the resident's state.
        A migration's rollback removes only the new sandbox, never its source.
        """
        if migration is not None and sandbox_name == migration.source:
            raise RuntimeError(
                f"A workspace migration cannot deploy into its source sandbox {sandbox_name}"
            )
        subject = self._resident_subject(runtime)
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
        grants: tuple[OpenShellProviderGrant, ...] = ()
        resumed_deployment = sandbox is not None
        image = _image_from_values(values, default=self._sandbox_image)
        # A resident whose sandbox an upgrade recreates keeps its identity: its
        # credentials survive a failed attempt, as they did the old sandbox.
        upgraded_from = self._resident_upgrades.get(str(runtime.id))
        replaced_image = replaced_image or upgraded_from or ""
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
            env.update(credential_context.environment)
            mesh_labels, mesh_annotations = resident_mesh_pod_metadata(runtime)
            if not resumed_deployment:
                sandbox = await asyncio.to_thread(
                    self._client.create_sandbox,
                    name=sandbox_name,
                    image=image,
                    env=env,
                    labels={
                        "app.kubernetes.io/managed-by": "volundr",
                        "volundr.niuu.io/resident": str(runtime.id),
                        "volundr.niuu.io/runtime": runtime.engine.value,
                        **resident_flock_labels(runtime, prefix="volundr.niuu.io"),
                    },
                    # Ravn's Kubernetes discovery reads these from the Pod.
                    pod_labels=mesh_labels,
                    pod_annotations=mesh_annotations,
                    annotations={
                        SANDBOX_IMAGE_ANNOTATION: image,
                        **(
                            {SANDBOX_REPLACED_IMAGE_ANNOTATION: replaced_image}
                            if replaced_image and replaced_image != image
                            else {}
                        ),
                        **(
                            {SANDBOX_WORKSPACE_CLAIM_ANNOTATION: claim.name}
                            if claim is not None
                            else {}
                        ),
                    },
                    resources=_resources_from_values(values, cpu=self._cpu, memory=self._memory),
                    driver_config=_driver_config_from_values(values),
                    providers=provider_names,
                    policy=self._resident_sandbox_policy(claim),
                )
            ready = await self._wait_for_sandbox_name(sandbox.name, self._ready_timeout)
            processes = self._resident_processes(runtime, values)
            relaunch = False
            if claim is not None:
                if resumed_deployment and (
                    await self._resident_workspace_link_state(ready) not in WORKSPACE_ON_VOLUME
                ):
                    # Its processes run off the volume: stop them, link, start them all.
                    await self._stop_resident_processes_in(ready, processes)
                    relaunch = True
                # Every engine, before anything is written into the workspace.
                await self._ensure_resident_workspace_link(ready)
            if before_start is not None:
                await before_start(ready)
            files = {
                **credential_context.files,
                **await self._resident_config_files(runtime, values),
            }
            for process in processes:
                files.update(_shared_resident_process_files(runtime, process.files))
            await asyncio.to_thread(
                self._client.write_files,
                sandbox=ready.name,
                files=files,
            )
            process_env = {**env, **credential_context.process_environment}
            if runtime.engine is ResidentEngine.OPENCLAW and machine_credential is not None:
                process_env["OPENCLAW_GATEWAY_TOKEN"] = machine_credential["gateway_token"]
            if runtime.engine is ResidentEngine.HERMES and machine_credential is not None:
                process_env[HERMES_API_SERVER_KEY_ENV] = machine_credential["api_key"]
            process_env.update(await self._resident_openclaw_environment(runtime, values))
            if claim is not None:
                await self._verify_resident_workspace_link(ready)
                await self._ensure_resident_home_links(runtime, ready)
            if resumed_deployment and not relaunch:
                await self._launch_missing_resident_processes(ready.name, process_env, processes)
            else:
                await self._launch_resident_processes(ready.name, process_env, processes)
            await self._wait_for_resident_processes(
                ready.name,
                processes,
                service_port=service_port,
            )
            if runtime.engine is ResidentEngine.HERMES:
                service_url = HERMES_INTERNAL_SERVICE_URL
            elif len(sandbox_name) > MAX_SANDBOX_ROUTING_NAME_LENGTH:
                service_url = "http://skuld.internal"
            else:
                service_url = await asyncio.to_thread(
                    self._client.expose_service,
                    sandbox_name=sandbox_name,
                    target_port=service_port,
                    service=service_name,
                )
                if not service_url and not self._gateway_public_url:
                    raise RuntimeError("OpenShell did not return a resident service URL")
        except Exception as exc:
            # A migration's old sandbox keeps running on the resident's provider
            # grants until the new one is healthy and the old one is gone.
            keep_grants = migration is not None
            if not keep_sandbox_on_failure:
                try:
                    await self._cleanup_resident_resources(
                        sandbox_name,
                        () if keep_grants else grants,
                        service_name=service_name,
                    )
                except Exception:
                    logger.exception("OpenShell resident rollback failed for %s", runtime.id)
                    if migration is not None:
                        # Reconcile removes it once it is safe to.
                        migration.abandoned = sandbox_name
            if upgraded_from is not None:
                reason = (
                    f"Upgrading resident {runtime.name} from {upgraded_from} to {image} "
                    f"failed: {exc}. Restart the resident to retry."
                )
                logger.warning(reason)
                self._failed_resident_upgrades[str(runtime.id)] = (image, reason)
                raise
            if migration is not None or keep_sandbox_on_failure or claim is not None:
                # The resident keeps its identity: its state survives on its old
                # sandbox or on its workspace volume.
                raise
            if runtime.engine is ResidentEngine.OPENCLAW and self._credential_store is not None:
                await self._credential_store.delete("resident", str(runtime.id), "openclaw-gateway")
            if runtime.engine is ResidentEngine.HERMES and self._credential_store is not None:
                from volundr.adapters.outbound.hermes_gateway import HERMES_CREDENTIAL_NAME

                await self._credential_store.delete(
                    "resident", str(runtime.id), HERMES_CREDENTIAL_NAME
                )
            await self._forget_resident_openclaw_token(runtime)
            raise

        resolved_service_url = (service_url or self._gateway_public_url).rstrip("/")
        self._service_urls[str(runtime.id)] = resolved_service_url
        self._provider_grants[str(runtime.id)] = grants
        self._resident_upgrades.pop(str(runtime.id), None)
        self._failed_resident_upgrades.pop(str(runtime.id), None)
        self._going_resident_sandboxes.pop(str(runtime.id), None)
        if claim is not None:
            self._failed_workspace_migrations.pop(str(runtime.id), None)
        return self._resident_observation(
            runtime,
            ready,
            service_url=resolved_service_url,
            processes_ready=True,
            service_name=service_name,
            service_port=service_port,
            process_names=tuple(process.name for process in processes),
        )

    def _resident_sandbox_names(self, runtime: ResidentRuntime) -> tuple[str, str]:
        """The two names a resident's sandbox takes: the default, and a migration's."""
        suffix = runtime.id.hex[:RESIDENT_SANDBOX_ID_CHARS]
        return (
            f"{RESIDENT_SANDBOX_PREFIX}{suffix}",
            f"{RESIDENT_MIGRATION_SANDBOX_PREFIX}{suffix}",
        )

    def _migration_sandbox_name(self, runtime: ResidentRuntime, old_name: str) -> str:
        """The name of the sandbox a migration creates beside ``old_name``."""
        default, migration = self._resident_sandbox_names(runtime)
        return default if old_name == migration else migration

    async def _ensure_resident_workspace_claim(
        self,
        runtime: ResidentRuntime,
        claim: ResidentWorkspaceClaim,
    ) -> None:
        """Create the resident's claim; finish a migration whose old sandbox is gone.

        A claim left ``verified`` lost its old sandbox after the copy was checked:
        it is complete. One left ``copying`` lost the sandbox it was copying from,
        so it holds a partial copy, and the resident is not started on it.
        """
        claims = self._require_workspace_claims()
        pvc, created = await claims.ensure(runtime, claim)
        if created:
            logger.info(
                "Created resident %s's workspace volume %s/%s (%s)",
                runtime.name,
                claims.namespace,
                claim.name,
                claim.size,
            )
            return
        state = workspace_migration_state(pvc)
        if state is None or state.phase == PHASE_COMPLETE:
            return
        if state.phase == PHASE_VERIFIED:
            lease = WorkspaceMigrationLease(
                claims,
                pvc,
                self._workspace_migration_holder,
                self._workspace_migration_lease_seconds,
            )
            await lease.acquire(source=state.source, phase=PHASE_VERIFIED)
            await lease.set_phase(PHASE_COMPLETE)
            await lease.release()
            logger.info(
                "Migrated resident %s's workspace to %s/%s (old sandbox %s already gone)",
                runtime.name,
                claims.namespace,
                claim.name,
                state.source,
            )
            return
        raise WorkspaceMigrationError(
            f"Resident {runtime.name}'s workspace migration from sandbox {state.source} did "
            f"not finish, and that sandbox is gone: volume {claims.namespace}/{claim.name} "
            "holds a partial copy. Völundr will not start the resident on it. Inspect the "
            "volume, then remove its volundr.niuu.io/workspace-migration annotation to use it "
            "as it is, or delete the volume to start the resident empty."
        )

    async def _deploy_unmigrated(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
        values: dict[str, Any],
        claim: ResidentWorkspaceClaim,
        old: OpenShellSandbox,
        stale: Sequence[OpenShellSandbox],
    ) -> ResidentRuntimeObservation:
        """Deploy a resident whose sandbox predates its claim: copy its state over once.

        A migration that fails before the old sandbox is removed leaves it running
        (its processes restarted) and reports a WorkspaceMigration condition; it is
        retried on Restart or when Völundr restarts.
        """
        runtime_id = str(runtime.id)
        failed = self._failed_workspace_migrations.get(runtime_id)
        if failed is not None:
            return await self._keep_unmigrated_sandbox(runtime, profile, old, failed)
        try:
            return await self._migrate_workspace(runtime, values, claim, old, stale)
        except WorkspaceMigrationHeldError:
            raise
        except WorkspaceMigrationError as exc:
            self._failed_workspace_migrations[runtime_id] = exc
            logger.warning(
                "Resident %s's workspace migration failed (%s): %s. Its sandbox %s keeps "
                "running; Restart the resident to retry.",
                runtime.name,
                exc.reason,
                exc,
                old.name,
            )
            return await self._keep_unmigrated_sandbox(runtime, profile, old, exc)

    async def _keep_unmigrated_sandbox(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
        sandbox: OpenShellSandbox,
        error: WorkspaceMigrationError,
    ) -> ResidentRuntimeObservation:
        """Run the resident on the sandbox that still holds its state, and say why.

        The sandbox is adopted as it is (no claim mounted): missing processes are
        started. A failure here never removes it.
        """
        values = self._with_brokered_credential_values(_resident_profile_values(profile))
        observation = await self._deploy_sandbox(
            runtime,
            values,
            sandbox_name=sandbox.name,
            sandbox=sandbox,
            claim=None,
            keep_sandbox_on_failure=True,
        )
        conditions = [*observation.conditions, _workspace_migration_failed_condition(error)]
        running, target = _sandbox_image(sandbox), self._target_image(values)
        if running and running != target:
            conditions.append(self._hold_upgrade(runtime, running, target))
        return replace(observation, conditions=conditions)

    async def _remove_abandoned_migration_sandbox(
        self,
        runtime: ResidentRuntime,
        values: dict[str, Any],
        claim: ResidentWorkspaceClaim,
        old: OpenShellSandbox,
    ) -> None:
        """Remove the sandbox this resident's failed migration created and left behind.

        Only the sandbox this Völundr recorded when that migration's rollback could
        not remove it, and only while it is still the resident's migration sandbox
        beside ``old`` (labelled for the resident, mounting its claim), never
        ``old`` itself, which runs the resident. It is removed under the
        migration's lease, and only while the claim is still ``copying`` or holds a
        copy of ``old`` (which then still runs the resident), so a migration that
        another Völundr runs keeps its sandbox.
        The removal is requested, not awaited; a later pass sees the sandbox gone.
        """
        runtime_id = str(runtime.id)
        name = self._abandoned_migration_sandboxes.get(runtime_id)
        if not name:
            return
        if name == old.name or name != self._migration_sandbox_name(runtime, old.name):
            self._abandoned_migration_sandboxes.pop(runtime_id, None)
            return
        lock = self._resident_process_lock(runtime)
        if lock.locked():
            return
        async with lock:
            leftover = await asyncio.to_thread(self._client.get_sandbox, name)
            if leftover is None:
                self._abandoned_migration_sandboxes.pop(runtime_id, None)
                logger.info(
                    "Sandbox %s of resident %s's failed migration is gone", name, runtime.name
                )
                return
            owner = dict(leftover.labels or {}).get("volundr.niuu.io/resident")
            if owner != runtime_id or not _sandbox_mounts_claim(leftover, claim):
                self._abandoned_migration_sandboxes.pop(runtime_id, None)
                logger.warning(
                    "Not removing sandbox %s: it is not resident %s's migration sandbox",
                    name,
                    runtime.name,
                )
                return
            if leftover.phase == openshell_pb2.SANDBOX_PHASE_DELETING:
                return
            claims = self._require_workspace_claims()
            pvc = await claims.read(claim.name)
            state = workspace_migration_state(pvc) if pvc is not None else None
            handover = _copied_from(state, old.name)
            if state is None or (state.phase != PHASE_COPYING and not handover):
                return
            lease = WorkspaceMigrationLease(
                claims,
                pvc,
                self._workspace_migration_holder,
                self._workspace_migration_lease_seconds,
            )
            try:
                if handover:
                    await lease.acquire(source=old.name, phase=PHASE_COMPLETE, handover=True)
                else:
                    await lease.acquire(source=state.source or old.name)
            except (WorkspaceMigrationHeldError, WorkspaceClaimInUseError):
                return
            service_name, _ = _resident_service(values, self._service_name, self._service_port)
            try:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(
                        self._client.delete_service, sandbox_name=name, service=service_name
                    )
                await asyncio.to_thread(self._client.delete_sandbox, name)
                logger.info(
                    "Removing sandbox %s, left by resident %s's failed workspace migration",
                    name,
                    runtime.name,
                )
            except Exception:
                logger.warning(
                    "Could not remove sandbox %s of resident %s's failed workspace migration; "
                    "retrying on the next pass",
                    name,
                    runtime.name,
                    exc_info=True,
                )
            finally:
                try:
                    await lease.release()
                except Exception:
                    logger.exception("Could not release %s's migration lease", runtime.name)

    async def _migrate_workspace(
        self,
        runtime: ResidentRuntime,
        values: dict[str, Any],
        claim: ResidentWorkspaceClaim,
        old: OpenShellSandbox,
        stale: Sequence[OpenShellSandbox],
    ) -> ResidentRuntimeObservation:
        """Copy the resident's state from ``old`` onto its claim, then run it there.

        1. Measure the state; refuse more than the migration limit or the claim.
        2. Create the claim and take the migration's lease on it.
        3. Stop the resident's processes in ``old`` and archive its state out.
        4. Create the new sandbox, on the current image, with the claim mounted.
        5. Restore the archive into the claim, verify it file by file, and mark
           the claim ``complete``.
        6. Start the resident in the new sandbox and wait until it is healthy.
        7. Only then remove ``old``.

        Any failure before step 7 removes the new sandbox and raises a
        WorkspaceMigrationError: ``old`` is untouched, and the caller restarts its
        processes. A claim already ``complete`` from ``old`` (an earlier attempt
        failed after its copy) is handed over without a copy when ``old`` still
        holds exactly what the claim recorded; when ``old`` ran on since, its
        newer state is copied again first.
        """
        claims = self._require_workspace_claims()
        runtime_id = str(runtime.id)
        sources = self._workspace_migration_sources(runtime)
        processes = self._resident_processes(runtime, values)
        service_name, _ = _resident_service(values, self._service_name, self._service_port)
        new_name = self._migration_sandbox_name(runtime, old.name)
        progress = _WorkspaceMigrationProgress(source=old.name)
        # This attempt removes any leftover of an earlier one itself, under its lease.
        self._abandoned_migration_sandboxes.pop(runtime_id, None)
        lease: WorkspaceMigrationLease | None = None
        renewer: asyncio.Task[None] | None = None
        with contextlib.ExitStack() as spools:
            try:
                sizes = await self._workspace_source_sizes(old, sources)
                self._check_workspace_fits(runtime, claim, sum(sizes.values()))
                pvc, created = await claims.ensure(
                    runtime,
                    claim,
                    annotations=_initial_migration_annotations(
                        old.name, self._workspace_migration_holder
                    ),
                )
                handover = False
                if not created:
                    state = workspace_migration_state(pvc)
                    handover = _copied_from(state, old.name)
                    if not handover and (state is None or state.phase not in RESTARTABLE_PHASES):
                        raise WorkspaceClaimInUseError(
                            f"Resident {runtime.name} runs in sandbox {old.name} without its "
                            f"workspace volume, but {claims.namespace}/{claim.name} already "
                            "holds a workspace; Völundr will not overwrite it. Delete the "
                            f"volume to copy {old.name}'s state onto a new one, or delete "
                            f"sandbox {old.name} to run the resident on the volume."
                        )
                candidate = WorkspaceMigrationLease(
                    claims,
                    pvc,
                    self._workspace_migration_holder,
                    self._workspace_migration_lease_seconds,
                )
                if handover:
                    await candidate.acquire(source=old.name, phase=PHASE_COMPLETE, handover=True)
                else:
                    await candidate.acquire(source=old.name)
                lease = candidate
                logger.info(
                    "Migrating resident %s's workspace to a persistent volume "
                    "(%s/%s, from sandbox %s)",
                    runtime.name,
                    claims.namespace,
                    claim.name,
                    old.name,
                )
                renewer = asyncio.create_task(self._renew_workspace_migration_lease(lease))
                for leftover in stale:
                    # A sandbox an earlier attempt created: it never ran the resident
                    # healthy, or ``old`` would be gone. The claim keeps its state.
                    await self._cleanup_resident_resources(
                        leftover.name,
                        (),
                        service_name=service_name,
                    )
                await self._stop_resident_processes(old, processes)
                await self._close_resident_forwarders(runtime)
                archives: list[tuple[str, IO[bytes]]] = []
                expected = WorkspaceManifest({})
                for source, destination in sources:
                    if source not in sizes:
                        continue
                    archive = spools.enter_context(tempfile.TemporaryFile())
                    manifest = await self._archive_workspace_source(old, source, archive)
                    archives.append((destination, archive))
                    expected = expected.merged(manifest.under(destination))

                restore: Any = None
                recorded = _migration_digest(lease.pvc)
                if handover and recorded == expected.summary():
                    logger.info(
                        "Resident %s's workspace on %s/%s matches sandbox %s (%s); "
                        "starting it there without copying again",
                        runtime.name,
                        claims.namespace,
                        claim.name,
                        old.name,
                        recorded,
                    )
                else:
                    if handover:
                        # ``old`` ran on after the earlier copy: its state is newer.
                        logger.info(
                            "Resident %s's sandbox %s changed since its copy (%s, now %s); "
                            "copying it onto %s/%s again",
                            runtime.name,
                            old.name,
                            recorded or "no digest",
                            expected.summary(),
                            claims.namespace,
                            claim.name,
                        )
                        await lease.set_phase(PHASE_COPYING)

                    async def restore(ready: OpenShellSandbox) -> None:
                        await self._restore_workspace(ready, archives)
                        restored = await self._restored_workspace_manifest(ready, claim, spools)
                        _verify_workspace_copy(expected, restored)
                        # The copy is the resident's state from here on, whatever
                        # happens to this attempt: a retry starts from it.
                        await lease.set_phase(
                            PHASE_COMPLETE, **{ANNOTATION_MIGRATION_DIGEST: expected.summary()}
                        )
                        logger.info(
                            "Copied resident %s's workspace to %s/%s: %d files, %d bytes, "
                            "verified; sandbox %s stays until the resident runs on it",
                            runtime.name,
                            claims.namespace,
                            claim.name,
                            expected.files,
                            expected.size,
                            old.name,
                        )

                observation = await self._deploy_sandbox(
                    runtime,
                    values,
                    sandbox_name=new_name,
                    sandbox=None,
                    claim=claim,
                    replaced_image=_sandbox_image(old),
                    before_start=restore,
                    migration=progress,
                )
            except Exception as exc:
                if progress.abandoned:
                    self._abandoned_migration_sandboxes[runtime_id] = progress.abandoned
                if lease is not None and not lease.lost:
                    try:
                        await lease.release()
                    except Exception:
                        logger.exception("Could not release %s's migration lease", runtime.name)
                if isinstance(exc, WorkspaceMigrationError):
                    raise
                raise WorkspaceMigrationError(
                    f"Moving resident {runtime.name} from {old.name} onto its workspace "
                    f"volume failed: {exc}"
                ) from exc
            finally:
                if renewer is not None:
                    renewer.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await renewer
        # The resident runs healthy on its claim: only now does the old sandbox go.
        await self._retire_migrated_sandbox(runtime, claim, old, service_name, lease)
        return observation

    async def _retire_migrated_sandbox(
        self,
        runtime: ResidentRuntime,
        claim: ResidentWorkspaceClaim,
        old: OpenShellSandbox,
        service_name: str,
        lease: WorkspaceMigrationLease,
    ) -> None:
        """Remove the sandbox a resident ran in before its claim, once it runs on the claim.

        A failure here never touches the new sandbox, which now runs the resident:
        ``old`` (its processes stopped) is removed on the next deployment.
        """
        claims = self._require_workspace_claims()
        try:
            await self._cleanup_resident_resources(
                old.name,
                (),
                service_name=service_name,
            )
            logger.info(
                "Migrated resident %s's workspace to %s/%s; it runs there, and its old "
                "sandbox %s was removed",
                runtime.name,
                claims.namespace,
                claim.name,
                old.name,
            )
        except Exception:
            logger.warning(
                "Resident %s runs on its workspace volume %s/%s, but its old sandbox %s "
                "(its processes stopped, its state copied) could not be removed; the "
                "resident's next redeployment removes it, or delete it by hand",
                runtime.name,
                claims.namespace,
                claim.name,
                old.name,
                exc_info=True,
            )
        finally:
            if not lease.lost:
                try:
                    await lease.release()
                except Exception:
                    logger.exception("Could not release %s's migration lease", runtime.name)

    async def _renew_workspace_migration_lease(self, lease: WorkspaceMigrationLease) -> None:
        """Keep the lease while the migration runs; a lost lease stops its next step."""
        interval = self._workspace_migration_lease_seconds / 3
        while True:
            await asyncio.sleep(interval)
            try:
                await lease.renew()
            except WorkspaceMigrationError:
                return
            except Exception as exc:
                lease.mark_lost(f"Could not renew the workspace migration lease: {exc}")
                return

    def _workspace_migration_sources(self, runtime: ResidentRuntime) -> tuple[tuple[str, str], ...]:
        """(directory in the old sandbox, directory on the claim) pairs to copy."""
        sources = [(self._sandbox_workspace, "")]
        if runtime.engine is ResidentEngine.RAVN:
            sources.append((f"{self._sandbox_home}/{RAVN_HOME_STATE_DIR}", RAVN_PERSISTED_HOME_DIR))
        return tuple(sources)

    def _check_workspace_fits(
        self,
        runtime: ResidentRuntime,
        claim: ResidentWorkspaceClaim,
        size: int,
    ) -> None:
        if size > self._workspace_migration_max_bytes:
            raise WorkspaceTooLargeError(
                f"Resident {runtime.name}'s workspace is {_human_bytes(size)}, over the "
                f"{self._workspace_migration_max_size} migration limit; nothing was changed. "
                "Free space in the sandbox, or raise "
                "podManager.kwargs.resident_workspace_migration_max_size, then Restart it."
            )
        if size > claim.size_bytes:
            raise WorkspaceTooLargeError(
                f"Resident {runtime.name}'s workspace is {_human_bytes(size)}, more than its "
                f"{claim.size} volume; nothing was changed. Raise the volume size "
                "(deployment.volumes.workspace.size, or resident_workspace_size), then Restart it."
            )

    async def _workspace_source_sizes(
        self,
        sandbox: OpenShellSandbox,
        sources: Sequence[tuple[str, str]],
    ) -> dict[str, int]:
        """Bytes used by each source directory that exists in the sandbox."""
        exit_code, output = await asyncio.to_thread(
            self._client.exec_script,
            sandbox=sandbox.name,
            script=_workspace_size_script(tuple(source for source, _ in sources)),
            env={},
        )
        if exit_code != 0:
            raise WorkspaceMigrationError(
                f"Could not measure the workspace in sandbox {sandbox.name}: {output.strip()}"
            )
        sizes: dict[str, int] = {}
        for line in output.splitlines():
            kib, _, path = line.partition("\t")
            if path and kib.strip().isdigit():
                sizes[path] = int(kib) * 1024
        return sizes

    async def _stop_resident_processes(
        self,
        sandbox: OpenShellSandbox,
        processes: Sequence[OpenShellRuntimeProcess],
    ) -> None:
        exit_code, output = await asyncio.to_thread(
            self._client.exec_script,
            sandbox=sandbox.name,
            script=_resident_stop_script(tuple(process.name for process in processes)),
            env={},
        )
        if exit_code != 0:
            raise WorkspaceMigrationError(
                f"Could not stop the resident's processes in {sandbox.name}: {output.strip()}"
            )

    async def _archive_workspace_source(
        self,
        sandbox: OpenShellSandbox,
        source: str,
        archive: IO[bytes],
    ) -> WorkspaceManifest:
        transfer = await asyncio.to_thread(
            self._client.exec_to_file,
            sandbox=sandbox.name,
            script=f"set -eu\ncd {shlex.quote(source)}\nexec tar -cf - .\n",
            destination=archive,
            max_bytes=self._workspace_migration_max_bytes,
            timeout_seconds=self._workspace_migration_timeout,
        )
        if transfer.truncated:
            raise WorkspaceTooLargeError(
                f"The archive of {source} in {sandbox.name} passed the "
                f"{self._workspace_migration_max_size} migration limit; nothing was changed"
            )
        if transfer.exit_code != 0:
            raise WorkspaceMigrationError(
                f"Archiving {source} in {sandbox.name} failed with exit {transfer.exit_code}: "
                f"{transfer.stderr.strip()}"
            )
        archive.seek(0)
        manifest = read_workspace_manifest(archive)
        archive.seek(0)
        return manifest

    async def _restore_workspace(
        self,
        sandbox: OpenShellSandbox,
        archives: Sequence[tuple[str, IO[bytes]]],
    ) -> None:
        """Empty the claim (a resumed attempt's partial copy), then extract each archive."""
        exit_code, output = await asyncio.to_thread(
            self._client.exec_script,
            sandbox=sandbox.name,
            script=_workspace_clear_script(self._sandbox_workspace),
            env={},
        )
        if exit_code != 0:
            raise WorkspaceMigrationError(
                f"Could not empty the workspace volume in {sandbox.name}: {output.strip()}"
            )
        for destination, archive in archives:
            archive.seek(0)
            target = str(PurePosixPath(self._sandbox_workspace, destination))
            try:
                transfer = await asyncio.to_thread(
                    self._client.exec_from_file,
                    sandbox=sandbox.name,
                    command=["sh", "-c", _workspace_extract_script(target)],
                    source=archive,
                    timeout_seconds=self._workspace_migration_timeout,
                )
            except grpc.RpcError as exc:
                raise WorkspaceMigrationError(
                    f"Restoring into {target} in {sandbox.name} failed: {_rpc_error_text(exc)}"
                ) from exc
            if transfer.exit_code != 0:
                raise WorkspaceMigrationError(
                    f"Restoring into {target} in {sandbox.name} failed with exit "
                    f"{transfer.exit_code}: {transfer.stderr.strip()}"
                )

    async def _restored_workspace_manifest(
        self,
        sandbox: OpenShellSandbox,
        claim: ResidentWorkspaceClaim,
        spools: contextlib.ExitStack,
    ) -> WorkspaceManifest:
        """Read what the claim now holds back out of the new sandbox (at most its size)."""
        readback = spools.enter_context(tempfile.TemporaryFile())
        transfer = await asyncio.to_thread(
            self._client.exec_to_file,
            sandbox=sandbox.name,
            script=(
                f"set -eu\ncd {shlex.quote(self._sandbox_workspace)}\n"
                "exec tar --exclude=./lost+found -cf - .\n"
            ),
            destination=readback,
            max_bytes=claim.size_bytes,
            timeout_seconds=self._workspace_migration_timeout,
        )
        if transfer.truncated or transfer.exit_code != 0:
            raise WorkspaceVerificationError(
                f"Could not read the restored workspace back from {sandbox.name} "
                f"(exit {transfer.exit_code}): {transfer.stderr.strip()}"
            )
        readback.seek(0)
        return read_workspace_manifest(readback)

    async def _ensure_resident_home_links(
        self,
        runtime: ResidentRuntime,
        sandbox: OpenShellSandbox,
    ) -> None:
        """Point ~/.ravn at its directory on the claim before a Ravn resident starts."""
        if runtime.engine is not ResidentEngine.RAVN:
            return
        link = f"{self._sandbox_home}/{RAVN_HOME_STATE_DIR}"
        target = str(PurePosixPath(self._sandbox_workspace, RAVN_PERSISTED_HOME_DIR))
        exit_code, output = await asyncio.to_thread(
            self._client.exec_script,
            sandbox=sandbox.name,
            script=_home_link_script(link, target),
            env={},
        )
        if exit_code != 0:
            raise RuntimeError(
                f"Could not keep {link} on the workspace volume in {sandbox.name}: {output.strip()}"
            )

    def _resident_sandbox_policy(self, claim: ResidentWorkspaceClaim | None) -> Any:
        """The sandbox policy, with the resident's workspace volume writable."""
        return self._sandbox_policy if claim is None else self._resident_claim_policy

    async def _ensure_resident_workspace_link(self, sandbox: OpenShellSandbox) -> None:
        """Point the workspace path at the resident's volume before anything uses it."""
        exit_code, output = await asyncio.to_thread(
            self._client.exec_script,
            sandbox=sandbox.name,
            script=_workspace_link_script(self._sandbox_workspace, RESIDENT_WORKSPACE_VOLUME_PATH),
            env={},
        )
        if exit_code != 0:
            raise RuntimeError(
                f"Could not link {self._sandbox_workspace} to the workspace volume in "
                f"{sandbox.name}: {output.strip()}"
            )
        for line in output.splitlines():
            if line.startswith("moved-aside="):
                logger.warning(
                    "Moved %s in sandbox %s aside to %s (it was not the workspace volume) "
                    "and linked it to %s",
                    self._sandbox_workspace,
                    sandbox.name,
                    line.removeprefix("moved-aside="),
                    RESIDENT_WORKSPACE_VOLUME_PATH,
                )
        self._linked_workspaces.add(sandbox.id)

    async def _stop_resident_processes_in(
        self,
        sandbox: OpenShellSandbox,
        processes: Sequence[OpenShellRuntimeProcess],
    ) -> None:
        exit_code, output = await asyncio.to_thread(
            self._client.exec_script,
            sandbox=sandbox.name,
            script=_resident_stop_script(tuple(process.name for process in processes)),
            env={},
        )
        if exit_code != 0:
            raise RuntimeError(f"OpenShell resident process stop failed: {output.strip()}")

    async def _resident_workspace_link_state(self, sandbox: OpenShellSandbox) -> str:
        exit_code, output = await asyncio.to_thread(
            self._client.exec_script,
            sandbox=sandbox.name,
            script=_workspace_link_state_script(
                self._sandbox_workspace, RESIDENT_WORKSPACE_VOLUME_PATH
            ),
            env={},
        )
        state = output.strip().splitlines()[-1] if output.strip() else ""
        if exit_code != 0 or state not in WORKSPACE_LINK_STATES:
            raise RuntimeError(
                f"Could not read {self._sandbox_workspace} in {sandbox.name}: {output.strip()}"
            )
        return state

    async def _verify_resident_workspace_link(self, sandbox: OpenShellSandbox) -> None:
        """Fail the deployment if the workspace path is no longer the volume."""
        state = await self._resident_workspace_link_state(sandbox)
        if state not in WORKSPACE_ON_VOLUME:
            self._linked_workspaces.discard(sandbox.id)
            raise RuntimeError(
                f"{self._sandbox_workspace} in {sandbox.name} is a {state}, not the resident's "
                "workspace volume; not starting the resident on it"
            )

    async def _repair_resident_workspace_link(
        self,
        runtime: ResidentRuntime,
        values: dict[str, Any],
        sandbox: OpenShellSandbox,
        processes: Sequence[OpenShellRuntimeProcess],
        service_port: int,
    ) -> WorkspaceRepair:
        """Put a running resident back on its volume if its workspace path is not the link.

        Checked once per sandbox in this Völundr, and only on a sandbox that still
        exists and is not being deleted (else SANDBOX_GONE: its redeployment
        recreates it on the claim). The resident's processes stop, what is at the
        workspace path is moved aside (never deleted), the path is linked, and the
        processes start again with their files projected anew. The volume itself
        is not written by the repair. A failure is logged once per error and
        retried on the next pass.
        """
        if sandbox.id in self._linked_workspaces:
            return WorkspaceRepair.SKIPPED
        lock = self._resident_process_lock(runtime)
        if lock.locked():
            return WorkspaceRepair.SKIPPED
        async with lock:
            # Re-read under the lock: an upgrade or rollback may have removed it.
            current = await asyncio.to_thread(self._client.get_sandbox, sandbox.name)
            if (
                current is None
                or current.id != sandbox.id
                or current.phase == openshell_pb2.SANDBOX_PHASE_DELETING
            ):
                self._failed_workspace_repairs.pop(sandbox.id, None)
                return WorkspaceRepair.SANDBOX_GONE
            try:
                state = await self._resident_workspace_link_state(current)
                if state in WORKSPACE_ON_VOLUME:
                    self._linked_workspaces.add(current.id)
                    self._failed_workspace_repairs.pop(current.id, None)
                    return WorkspaceRepair.SKIPPED
                logger.warning(
                    "Resident %s's sandbox %s runs with %s %s, not on its workspace volume; "
                    "stopping its processes, linking %s to %s and starting them again",
                    runtime.name,
                    current.name,
                    self._sandbox_workspace,
                    "missing" if state == "missing" else f"a {state}",
                    self._sandbox_workspace,
                    RESIDENT_WORKSPACE_VOLUME_PATH,
                )
                files, env = await self._resident_launch_inputs(runtime, values, processes)
                await self._stop_resident_processes_in(current, processes)
                await self._ensure_resident_workspace_link(current)
                await asyncio.to_thread(self._client.write_files, sandbox=current.name, files=files)
                await self._verify_resident_workspace_link(current)
                await self._ensure_resident_home_links(runtime, current)
                await self._launch_resident_processes(current.name, env, processes)
                await self._wait_for_resident_processes(
                    current.name, processes, service_port=service_port
                )
            except Exception as exc:
                error = str(exc) or type(exc).__name__
                if self._failed_workspace_repairs.get(current.id) != error:
                    self._failed_workspace_repairs[current.id] = error
                    logger.warning(
                        "Could not put resident %s's sandbox %s back on its workspace volume; "
                        "retrying on every pass: %s",
                        runtime.name,
                        current.name,
                        error,
                        exc_info=True,
                    )
                return WorkspaceRepair.FAILED
        self._failed_workspace_repairs.pop(current.id, None)
        logger.info(
            "Resident %s runs on its workspace volume again in sandbox %s",
            runtime.name,
            current.name,
        )
        return WorkspaceRepair.REPAIRED

    def _require_workspace_claims(self) -> ResidentWorkspaceClaims:
        if self._workspace_claims is None:
            raise RuntimeError("Resident workspace claims need resident_workspace_namespace")
        return self._workspace_claims

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
            failed_upgrade = self._failed_resident_upgrade(runtime, profile)
            if failed_upgrade is not None and runtime.desired_state is ResidentDesiredState.RUNNING:
                # Its upgrade's redeploy failed and rolled back: report that rather
                # than redeploying it on every pass.
                return ResidentRuntimeObservation(
                    observed_state=ResidentObservedState.FAILED,
                    conditions=[_upgrade_failed_condition(failed_upgrade)],
                )
            if (
                runtime.desired_state is ResidentDesiredState.RUNNING
                and str(runtime.id) in self._resident_upgrades
            ):
                # Its upgrade removed the old sandbox: the redeployment is the upgrade.
                return _upgrading_observation(self._target_image(_resident_profile_values(profile)))
            if runtime.desired_state is ResidentDesiredState.RUNNING:
                # The gateway no longer knows this sandbox (for example after a
                # gateway upgrade orphaned it). Report the resident as owning no
                # backend so the service redeploys it in the background.
                return ResidentRuntimeObservation(
                    observed_state=ResidentObservedState.PENDING,
                    conditions=[
                        ResidentCondition(
                            type="SandboxReady",
                            status=ResidentConditionStatus.FALSE,
                            reason="NotFound",
                            message="OpenShell sandbox does not exist; redeploying",
                        )
                    ],
                )
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
        if (
            sandbox.phase == openshell_pb2.SANDBOX_PHASE_DELETING
            and runtime.desired_state is ResidentDesiredState.RUNNING
        ):
            return self._going_sandbox_observation(runtime, profile, sandbox.name)
        if (
            runtime.observed_state is ResidentObservedState.FAILED
            and not runtime.backend_ref
            and runtime.desired_state is ResidentDesiredState.RUNNING
        ):
            # A failed deployment never recorded owning this sandbox: its rollback
            # did not finish. Deploying again adopts it and starts whatever
            # resident processes are missing.
            return ResidentRuntimeObservation(
                observed_state=ResidentObservedState.PENDING,
                conditions=[
                    ResidentCondition(
                        type="SandboxReady",
                        status=ResidentConditionStatus.FALSE,
                        reason="Unadopted",
                        message="OpenShell sandbox is left from an unfinished deployment; adopting",
                    )
                ],
            )
        processes_ready = False
        values, claim = self._resident_values(runtime, profile)
        follow = OpenShellPlatformFollow()
        migration_failed: WorkspaceMigrationError | None = None
        running_resident = bool(
            sandbox.ready
            and runtime.backend_ref
            and runtime.desired_state is ResidentDesiredState.RUNNING
        )
        if running_resident and claim is not None and not _sandbox_mounts_claim(sandbox, claim):
            migration_failed = self._failed_workspace_migrations.get(str(runtime.id))
            if migration_failed is None:
                # Its state is only in this sandbox: the deployment copies it onto
                # the resident's own claim, then recreates the sandbox on it.
                return ResidentRuntimeObservation(
                    observed_state=ResidentObservedState.PENDING,
                    conditions=[
                        ResidentCondition(
                            type="WorkspaceMigration",
                            status=ResidentConditionStatus.FALSE,
                            reason="Migrating",
                            message=(
                                f"Copying the workspace from sandbox {sandbox.name} onto "
                                f"persistent volume {claim.name}"
                            ),
                        )
                    ],
                )
            await self._remove_abandoned_migration_sandbox(runtime, values, claim, sandbox)
        if running_resident:
            follow = await self._follow_platform(
                runtime,
                values,
                sandbox,
                persisted=_sandbox_persists_workspace(
                    values, self._sandbox_workspace, sandbox, claim
                ),
            )
            if follow.redeploy is not None:
                return follow.redeploy
        processes = self._resident_processes(runtime, values)
        service_name, service_port = _resident_service(
            values, self._service_name, self._service_port
        )
        if running_resident and claim is not None and _sandbox_mounts_claim(sandbox, claim):
            repaired = await self._repair_resident_workspace_link(
                runtime, values, sandbox, processes, service_port
            )
            if repaired is WorkspaceRepair.SANDBOX_GONE:
                return self._going_sandbox_observation(runtime, profile, sandbox.name)
        if sandbox.ready:
            processes_ready = await self._resident_processes_ready(
                sandbox.name,
                processes,
                service_port=service_port,
            )
            if (
                not processes_ready
                and runtime.backend_ref
                and runtime.desired_state is ResidentDesiredState.RUNNING
            ):
                await self._start_missing_resident_processes(
                    runtime,
                    values,
                    sandbox,
                    processes,
                    on_claim=claim is not None and _sandbox_mounts_claim(sandbox, claim),
                )
            elif (
                processes_ready
                and runtime.backend_ref
                and runtime.desired_state is ResidentDesiredState.RUNNING
                and self._resident_openclaw_enabled(runtime, values)
            ):
                await self._start_resident_openclaw_shim(runtime, profile, values, sandbox)
        service_url = (
            "" if follow.service_removed else str(runtime.backend_ref.get("service_url") or "")
        )
        if not service_url and runtime.engine is ResidentEngine.HERMES:
            service_url = HERMES_INTERNAL_SERVICE_URL
        if (
            not service_url
            and len(self._resident_sandbox_name(runtime)) > MAX_SANDBOX_ROUTING_NAME_LENGTH
        ):
            service_url = "http://skuld.internal"
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
        observation = self._resident_observation(
            runtime,
            sandbox,
            service_url=service_url,
            processes_ready=processes_ready,
            service_name=service_name,
            service_port=service_port,
            process_names=tuple(process.name for process in processes),
        )
        if migration_failed is not None:
            observation = replace(
                observation,
                conditions=[
                    *observation.conditions,
                    _workspace_migration_failed_condition(migration_failed),
                ],
            )
        if follow.condition is None:
            return observation
        return replace(
            observation,
            observed_state=(
                ResidentObservedState.FAILED if follow.failed else observation.observed_state
            ),
            conditions=[*observation.conditions, follow.condition],
        )

    def _going_sandbox_observation(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
        sandbox_name: str,
    ) -> ResidentRuntimeObservation:
        """A resident that should run whose sandbox is gone or being deleted: redeploy it.

        Its deployment waits for the sandbox to go, then creates it anew on the
        resident's workspace claim (no copy: the claim already holds its state), as
        a Restart does. Logged once per sandbox.
        """
        runtime_id = str(runtime.id)
        if self._going_resident_sandboxes.get(runtime_id) != sandbox_name:
            self._going_resident_sandboxes[runtime_id] = sandbox_name
            logger.info(
                "Resident %s's sandbox %s is gone or being deleted; redeploying it",
                runtime.name,
                sandbox_name,
            )
        if runtime_id in self._resident_upgrades:
            return _upgrading_observation(
                self._target_image(_resident_profile_values(profile)),
                waiting_for=sandbox_name,
            )
        return ResidentRuntimeObservation(
            observed_state=ResidentObservedState.PENDING,
            conditions=[
                ResidentCondition(
                    type="SandboxReady",
                    status=ResidentConditionStatus.FALSE,
                    reason="SandboxGone",
                    message=(
                        f"OpenShell sandbox {sandbox_name} is gone or being deleted; "
                        "redeploying the resident once it is gone"
                    ),
                )
            ],
        )

    def _target_image(self, values: dict[str, Any]) -> str:
        """The image this Völundr deploys a resident's sandbox with."""
        return _image_from_values(values, default=self._sandbox_image)

    def _failed_resident_upgrade(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> str | None:
        """The reason the resident's upgrade to the current image failed, if it did."""
        failed = self._failed_resident_upgrades.get(str(runtime.id))
        if failed is None:
            return None
        values = _resident_profile_values(profile)
        return failed[1] if failed[0] == self._target_image(values) else None

    async def _follow_platform(
        self,
        runtime: ResidentRuntime,
        values: dict[str, Any],
        sandbox: OpenShellSandbox,
        *,
        persisted: bool,
    ) -> OpenShellPlatformFollow:
        """Move a running resident onto the image this Völundr deploys, once.

        A sandbox's image is fixed when it is created, so moving a resident onto a
        new image (a new Ravn or Skuld) recreates its sandbox: the existing
        sandbox and service are removed and the resident is reported as owning no
        backend, so the service redeploys it in the background like a resident
        whose sandbox is gone. Its credentials, shim token and record are kept.

        Only a resident whose sandbox keeps its workspace on a claim (``persisted``:
        its own Völundr-managed claim mounted, or the profile's) is moved; any
        other would lose its state under ``/sandbox``, so its upgrade is held and
        reported (UpgradeHeld), and neither reconcile nor restart recreates it. An image
        the sandbox replaced is never deployed again by reconcile (an older
        Völundr replica during Völundr's own rollout), and a failed redeployment is
        reported, not retried, until the target changes or the resident restarts.
        An old sandbox that is slow to go, or that the gateway would not remove, is
        no failure: the redeployment waits for it, or the next pass tries again.
        """
        runtime_id = str(runtime.id)
        target = self._target_image(values)
        running = _sandbox_image(sandbox)
        if not running:
            if (runtime_id, "") not in self._held_resident_upgrades:
                self._held_resident_upgrades.add((runtime_id, ""))
                logger.info(
                    "Resident %s's sandbox reports no image; not following the platform",
                    runtime.name,
                )
            return OpenShellPlatformFollow()
        if running == target:
            self._failed_resident_upgrades.pop(runtime_id, None)
            self._retrying_resident_upgrades.pop(runtime_id, None)
            return OpenShellPlatformFollow()
        if target == (sandbox.annotations or {}).get(SANDBOX_REPLACED_IMAGE_ANNOTATION):
            return OpenShellPlatformFollow()
        failed = self._failed_resident_upgrades.get(runtime_id)
        if failed is not None and failed[0] == target:
            return OpenShellPlatformFollow(
                condition=_upgrade_failed_condition(failed[1]), failed=True
            )
        if not persisted:
            return OpenShellPlatformFollow(condition=self._hold_upgrade(runtime, running, target))
        lock = self._resident_process_lock(runtime)
        if lock.locked():
            return OpenShellPlatformFollow()
        async with lock:
            logger.info("Upgrading resident %s from %s to %s", runtime.name, running, target)
            try:
                await self._retire_resident_sandbox(runtime, running)
            except Exception as exc:
                return await self._unfinished_upgrade(runtime, sandbox.name, running, target, exc)
        self._retrying_resident_upgrades.pop(runtime_id, None)
        return OpenShellPlatformFollow(redeploy=_upgrading_observation(target))

    async def _unfinished_upgrade(
        self,
        runtime: ResidentRuntime,
        sandbox_name: str,
        running: str,
        target: str,
        error: Exception,
    ) -> OpenShellPlatformFollow:
        """What an upgrade reports when its old sandbox did not go; never a dead end.

        A deletion the gateway accepted but had not finished in time: the resident
        is handed to its redeployment, which waits for the sandbox to go and
        creates it anew on its workspace claim, as Restart does. A deletion the
        gateway refused leaves the old sandbox running the resident: its service is
        exposed again and the upgrade is retried on the next pass.
        """
        runtime_id = str(runtime.id)
        if await self._sandbox_going(sandbox_name):
            self._retrying_resident_upgrades.pop(runtime_id, None)
            logger.warning(
                "Resident %s's old sandbox %s was not gone within %gs of its upgrade to %s; "
                "its redeployment waits for OpenShell to finish deleting it, then recreates "
                "it on its workspace volume",
                runtime.name,
                sandbox_name,
                self._resident_delete_timeout,
                target,
            )
            return OpenShellPlatformFollow(
                redeploy=_upgrading_observation(target, waiting_for=sandbox_name)
            )
        # The sandbox was not replaced: it runs the resident on its old image.
        self._resident_upgrades.pop(runtime_id, None)
        reason = (
            f"Upgrading resident {runtime.name} from {running} to {target} is retried on the "
            f"next pass: its sandbox {sandbox_name} could not be removed ({error})"
        )
        if self._retrying_resident_upgrades.get(runtime_id) != reason:
            self._retrying_resident_upgrades[runtime_id] = reason
            logger.warning(reason)
        return OpenShellPlatformFollow(
            condition=ResidentCondition(
                type="PlatformUpgrade",
                status=ResidentConditionStatus.FALSE,
                reason="UpgradeRetrying",
                message=reason,
            ),
            service_removed=True,
        )

    def _hold_upgrade(
        self, runtime: ResidentRuntime, running: str, target: str
    ) -> ResidentCondition:
        """Why a resident without a persistent workspace stays on its image.

        Its state lives only in its sandbox's ``/sandbox``, and a sandbox's image
        cannot change without creating a new sandbox, so no path recreates it:
        neither reconcile nor an explicit restart.
        """
        runtime_id = str(runtime.id)
        reason = (
            "No persistent workspace; the sandbox image can't change without losing "
            f"/sandbox. The resident runs {running}; the platform deploys {target}. "
            "Give it a persistent workspace (Völundr's resident_workspace_namespace, or the "
            "profile's persistence.existingClaim) to upgrade it."
        )
        if (runtime_id, target) not in self._held_resident_upgrades:
            self._held_resident_upgrades.add((runtime_id, target))
            logger.info("Not upgrading resident %s: %s", runtime.name, reason)
        return ResidentCondition(
            type="PlatformUpgrade",
            status=ResidentConditionStatus.FALSE,
            reason="UpgradeHeld",
            message=reason,
        )

    async def _retire_resident_sandbox(self, runtime: ResidentRuntime, image: str) -> None:
        """Remove a resident's sandbox and service so it is deployed anew; keep the rest.

        Its provider grants are named for the resident and re-applied by the
        deployment; its credentials and shim token stay where they are kept.
        """
        runtime_id = str(runtime.id)
        self._resident_upgrades[runtime_id] = image
        await self._close_resident_forwarders(runtime)
        self._service_urls.pop(runtime_id, None)
        await self._cleanup_resident_resources(
            self._resident_sandbox_name(runtime),
            (),
            service_name=str(runtime.backend_ref.get("service_name") or self._service_name),
        )

    async def _sandbox_going(self, sandbox_name: str) -> bool:
        """Whether the sandbox is gone or the gateway is deleting it."""
        sandbox = await asyncio.to_thread(self._client.get_sandbox, sandbox_name)
        return sandbox is None or sandbox.phase == openshell_pb2.SANDBOX_PHASE_DELETING

    async def _close_resident_forwarders(self, runtime: ResidentRuntime) -> None:
        runtime_id = str(runtime.id)
        for key in [
            key
            for key in self._resident_forwarders
            if key == runtime_id or _is_openclaw_key(key, runtime)
        ]:
            await asyncio.to_thread(self._resident_forwarders.pop(key).close)

    async def _start_missing_resident_processes(
        self,
        runtime: ResidentRuntime,
        values: dict[str, Any],
        sandbox: OpenShellSandbox,
        processes: Sequence[OpenShellRuntimeProcess],
        *,
        on_claim: bool = False,
    ) -> None:
        """Start the processes of a deployed resident that are no longer running.

        A process can stop after its deployment finished, for example when the
        Volundr that launched it with an older configuration was replaced. A
        deploy or restart in progress owns the processes, so this leaves them to
        it. A failure is logged and retried on the next reconcile pass.
        """
        lock = self._resident_process_lock(runtime)
        if lock.locked():
            return
        async with lock:
            try:
                files, env = await self._resident_launch_inputs(runtime, values, processes)
                await asyncio.to_thread(self._client.write_files, sandbox=sandbox.name, files=files)
                if on_claim:
                    await self._ensure_resident_home_links(runtime, sandbox)
                await self._launch_missing_resident_processes(sandbox.name, env, processes)
            except Exception:
                logger.warning(
                    "Could not start missing OpenShell resident processes for %s",
                    runtime.id,
                    exc_info=True,
                )
                return
        logger.info("Started missing OpenShell resident processes for %s", runtime.id)

    async def _resident_launch_inputs(
        self,
        runtime: ResidentRuntime,
        values: dict[str, Any],
        processes: Sequence[OpenShellRuntimeProcess],
    ) -> tuple[dict[str, bytes], dict[str, str]]:
        """Return the files and environment that launch a deployed resident's processes."""
        credential_context = await self._resolve_credential_context(
            self._resident_subject(runtime), values
        )
        files = {**credential_context.files, **await self._resident_config_files(runtime, values)}
        for process in processes:
            files.update(_shared_resident_process_files(runtime, process.files))
        env = self._resident_environment(runtime, values)
        env.update(resident_flock_environment(runtime))
        env.update(credential_context.environment)
        env.update(credential_context.process_environment)
        env.update(await self._resident_openclaw_environment(runtime, values))
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
        return files, env

    async def restart(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        async with self._resident_process_lock(runtime):
            return await self._restart(runtime, profile)

    async def _restart(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
        *,
        follow_platform: bool = True,
    ) -> ResidentRuntimeObservation:
        if not self.supports(profile):
            raise RuntimeError(f"OpenShell does not support resident profile {profile.id!r}")
        values, claim = self._resident_values(runtime, profile)
        sandbox = await asyncio.to_thread(
            self._client.get_sandbox,
            self._resident_sandbox_name(runtime),
        )
        runtime_id = str(runtime.id)
        if claim is not None and sandbox is not None and not _sandbox_mounts_claim(sandbox, claim):
            # Restart retries a failed workspace migration (or runs it now): the
            # resident comes back on its own claim, or on its old sandbox as before.
            self._failed_workspace_migrations.pop(runtime_id, None)
            return await self._deploy(runtime, profile)
        if follow_platform and sandbox is None and runtime_id in self._failed_resident_upgrades:
            # Its upgrade's redeploy failed and rolled back: try it again.
            self._failed_resident_upgrades.pop(runtime_id, None)
            return await self._deploy(runtime, profile)
        if sandbox is None or not sandbox.ready:
            raise RuntimeError("OpenShell resident sandbox is not ready")
        running = _sandbox_image(sandbox)
        target = self._target_image(values)
        held: ResidentCondition | None = None
        if follow_platform and running and running != target:
            if _sandbox_persists_workspace(values, self._sandbox_workspace, sandbox, claim):
                # A restart runs the resident on the platform's current version: a
                # new image needs a new sandbox, and its workspace survives that.
                logger.info("Upgrading resident %s from %s to %s", runtime.name, running, target)
                self._failed_resident_upgrades.pop(runtime_id, None)
                try:
                    await self._retire_resident_sandbox(runtime, running)
                except Exception:
                    # Accepted but slow: the deployment waits for it to go.
                    if not await self._sandbox_going(sandbox.name):
                        raise
                return await self._deploy(runtime, profile)
            # Its state is only in this sandbox: restart its processes in place,
            # as always, and keep reporting why it stays on its image.
            held = self._hold_upgrade(runtime, running, target)
        processes = self._resident_processes(runtime, values)
        files, env = await self._resident_launch_inputs(runtime, values, processes)
        exit_code, output = await asyncio.to_thread(
            self._client.exec_script,
            sandbox=sandbox.name,
            script=_resident_stop_script(tuple(process.name for process in processes)),
            env={},
        )
        if exit_code != 0:
            raise RuntimeError(f"OpenShell resident process stop failed: {output.strip()}")
        on_claim = claim is not None and _sandbox_mounts_claim(sandbox, claim)
        if on_claim:
            # A Restart also puts a resident that runs off its volume back on it.
            await self._ensure_resident_workspace_link(sandbox)
        await asyncio.to_thread(
            self._client.write_files,
            sandbox=sandbox.name,
            files=files,
        )
        if on_claim:
            await self._verify_resident_workspace_link(sandbox)
        if claim is not None:
            await self._ensure_resident_home_links(runtime, sandbox)
        await self._launch_resident_processes(sandbox.name, env, processes)
        service_name, service_port = _resident_service(
            values, self._service_name, self._service_port
        )
        await self._wait_for_resident_processes(
            sandbox.name,
            processes,
            service_port=service_port,
        )
        observation = self._resident_observation(
            runtime,
            sandbox,
            service_url=str(runtime.backend_ref.get("service_url") or ""),
            processes_ready=True,
            service_name=service_name,
            service_port=service_port,
            process_names=tuple(process.name for process in processes),
        )
        if held is None:
            return observation
        return replace(observation, conditions=[*observation.conditions, held])

    async def suspend(self, runtime: ResidentRuntime) -> ResidentRuntimeObservation:
        raise RuntimeError("OpenShell resident suspension is unsupported")

    async def resume(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile | None = None,
    ) -> ResidentRuntimeObservation:
        raise RuntimeError("OpenShell resident suspension is unsupported")

    async def delete(self, runtime: ResidentRuntime) -> bool:
        runtime_id = str(runtime.id)
        self._service_urls.pop(runtime_id, None)
        self._resident_process_locks.pop(runtime_id, None)
        self._resident_openclaw_checked.discard(runtime_id)
        self._resident_upgrades.pop(runtime_id, None)
        self._failed_resident_upgrades.pop(runtime_id, None)
        self._retrying_resident_upgrades.pop(runtime_id, None)
        self._going_resident_sandboxes.pop(runtime_id, None)
        await self._close_resident_forwarders(runtime)
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
        service_name = str(runtime.backend_ref.get("service_name") or self._service_name)
        self._failed_workspace_migrations.pop(runtime_id, None)
        self._abandoned_migration_sandboxes.pop(runtime_id, None)
        # A workspace migration leaves a second sandbox while it runs: remove both
        # (only the resident's own), then the resident's workspace claim.
        for other_name in self._resident_sandbox_names(runtime):
            if self._workspace_claims is None or other_name == sandbox_name:
                continue
            other = await asyncio.to_thread(self._client.get_sandbox, other_name)
            if other is None:
                continue
            if dict(other.labels or {}).get("volundr.niuu.io/resident") != runtime_id:
                continue
            await self._cleanup_resident_resources(other_name, (), service_name=service_name)
        deleted = await self._cleanup_resident_resources(
            sandbox_name,
            grants,
            service_name=service_name,
        )
        if self._workspace_claims is not None and await self._workspace_claims.delete(runtime):
            logger.info(
                "Deleted resident %s's workspace volume %s/%s",
                runtime.name,
                self._workspace_claims.namespace,
                resident_workspace_claim_name(runtime),
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
        await self._forget_resident_openclaw_token(runtime)
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
            sandbox.name,
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
            sandbox=sandbox.name,
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
        if self._core_api_client is not None:
            await self._core_api_client.close()
            self._core_api_client = None

    async def _launch_resident_processes(
        self,
        sandbox_name: str,
        env: dict[str, str],
        processes: Sequence[OpenShellRuntimeProcess],
    ) -> None:
        for process in processes:
            exit_code = await asyncio.to_thread(
                self._client.exec_detached,
                sandbox=sandbox_name,
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
        sandbox_name: str,
        env: dict[str, str],
        processes: Sequence[OpenShellRuntimeProcess],
    ) -> None:
        for process in processes:
            if await self._resident_processes_ready(sandbox_name, (process,)):
                continue
            await self._launch_resident_processes(sandbox_name, env, (process,))

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
                    'export RAVN_GATEWAY__PLATFORM__ACCESS_TOKEN="$NIUU_VOLUNDR_ACCESS_TOKEN"; '
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
        sandbox_name: str,
        processes: Sequence[OpenShellRuntimeProcess],
        *,
        service_port: int | None = None,
    ) -> bool:
        exit_code, _ = await asyncio.to_thread(
            self._client.exec_script,
            sandbox=sandbox_name,
            script=_resident_health_script(
                tuple(process.name for process in processes),
                service_port=service_port,
            ),
            env={},
        )
        return exit_code == 0

    async def _wait_for_resident_processes(
        self,
        sandbox_name: str,
        processes: Sequence[OpenShellRuntimeProcess],
        *,
        service_port: int | None = None,
    ) -> None:
        deadline = time.monotonic() + self._ready_timeout
        while time.monotonic() < deadline:
            if await self._resident_processes_ready(
                sandbox_name,
                processes,
                service_port=service_port,
            ):
                return
            await asyncio.sleep(READY_POLL_INTERVAL)
        raise TimeoutError(
            f"OpenShell resident processes were not ready within {self._ready_timeout}s"
        )

    async def _resident_config_files(
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
        realm_slug = await self._resolve_realm_slug(runtime)
        ravn_config = _resident_ravn_config(
            runtime, values, self._service_port, realm_slug, self._volundr_api_url
        )
        if self._resident_openclaw_enabled(runtime, values):
            enable_resident_openclaw_shim(ravn_config, values)
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
            "/sandbox/.volundr/ravn.yaml": yaml.safe_dump(ravn_config, sort_keys=False).encode(),
        }

    async def _resolve_realm_slug(self, runtime: ResidentRuntime) -> str:
        """Return the slug of the realm this resident is bound to, or "".

        Mirrors LocalContainerResidentRuntimeController._resolve_realm_slug:
        a configured realm_id that cannot be resolved is a real
        misconfiguration and fails loudly rather than deploying with a
        silently dropped charter/realm binding.
        """
        if runtime.realm_id is None:
            return ""
        if self._realm_repository is None:
            raise RuntimeError(
                f"resident {runtime.name!r} has realm_id={runtime.realm_id} but this "
                "controller has no realm repository configured; call set_realm_repository "
                "at composition time."
            )
        realm = await self._realm_repository.get_realm(runtime.realm_id)
        if realm is None:
            raise RuntimeError(
                f"resident {runtime.name!r} names realm_id={runtime.realm_id}, but no "
                "such realm exists; clear the resident's realm binding or restore the realm."
            )
        return realm.slug

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
                "image": _sandbox_image(sandbox),
                **(
                    {"workspace_claim": workspace_claim}
                    if (
                        workspace_claim := (sandbox.annotations or {}).get(
                            SANDBOX_WORKSPACE_CLAIM_ANNOTATION, ""
                        )
                    )
                    else {}
                ),
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
            sandbox=sandbox.name,
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
        if runtime.backend_ref.get("name"):
            return str(runtime.backend_ref["name"])
        return f"r-{runtime.id.hex[:17]}"

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
        stored = await self._credential_store.get("user", workload.owner_id, credential_name)
        if stored and stored.metadata.get("renewal_owner") == OAUTH_ENGINE:
            if stored.metadata.get("tenant_id") != workload.tenant_id:
                raise ValueError("OAuth credential does not belong to this workload tenant")
            if credential_field != stored.metadata.get("oauth_token_field"):
                raise ValueError("OAuth credential grants may expose only the access token")
        values = await self._credential_store.get_value("user", workload.owner_id, credential_name)
        value = values.get(credential_field) if values else None
        if not value or "\x00" in value or "\r" in value or "\n" in value:
            raise ValueError("credential field is unavailable for this session")
        basic_username = str(config.get("volundr_basic_auth_username") or "")
        if basic_username:
            value = "Basic " + base64.b64encode(f"{basic_username}:{value}".encode()).decode()
        token = OpenShellCredentialGrantToken(access_token=value)
        if values and values.get("expires_at"):
            expiry = datetime.fromisoformat(values["expires_at"])
            if expiry.tzinfo is None:
                raise ValueError("OAuth credential expiry must include a timezone")
            remaining = int((expiry - datetime.now(UTC)).total_seconds())
            if remaining <= 0:
                raise ValueError("OAuth credential expired; reconnect the integration")
            token = replace(token, expires_in=min(token.expires_in, remaining))
        return token

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
        delete_timeout: float | None = None,
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
                await self._wait_for_sandbox_deleted(sandbox_name, timeout=delete_timeout)
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

    async def _cleanup_resident_resources(
        self,
        sandbox_name: str,
        grants: Sequence[OpenShellProviderGrant],
        *,
        service_name: str | None = None,
    ) -> bool:
        """Remove a resident's sandbox, service and grants, waiting as long as a resident needs.

        Every removal of a resident sandbox goes through here: a resident's pod,
        volumes and gateway records take longer to go than a session's.
        """
        return await self._cleanup_resources(
            sandbox_name,
            grants,
            service_name=service_name,
            delete_timeout=self._resident_delete_timeout,
        )

    async def _wait_for_sandbox_deleted(
        self, sandbox_name: str, *, timeout: float | None = None
    ) -> None:
        limit = self._resource_delete_timeout if timeout is None else timeout
        deadline = time.monotonic() + limit
        while await asyncio.to_thread(self._client.get_sandbox, sandbox_name) is not None:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"OpenShell sandbox {sandbox_name!r} was not deleted within {limit:g}s"
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
        if stored.metadata.get("renewal_owner") == OAUTH_ENGINE:
            if stored.metadata.get("tenant_id") != subject.tenant_id:
                raise RuntimeError("OAuth credential does not belong to this workload tenant")
            if (
                file_mappings
                or mapping.get("materializeEnvironment")
                or mapping.get("materialize_environment")
            ):
                raise RuntimeError("Managed OAuth credentials require dynamic provider injection")
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
            binding = self._grant_binding(
                subject,
                volundr_credential_name=credential_name,
                volundr_credential_field=field_name,
            )
            target = _provider_target(env_name, mapping.get("provider"))
            if target.get("basic_username"):
                binding["volundr_basic_auth_username"] = target["basic_username"]
            await asyncio.to_thread(
                self._client.create_provider_grant,
                profile=profile,
                provider_name=provider_name,
                config=binding,
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
        sandbox_name: str,
        session: Session,
        spec: SessionSpec,
        env: dict[str, str],
    ) -> None:
        script = self._workspace_bootstrap_script(session, spec)
        if not script:
            return
        exit_code, output = await asyncio.to_thread(
            self._client.exec_script,
            sandbox=sandbox_name,
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

        home = spec.values.get("homeVolume", {})
        if isinstance(home, dict) and home.get("enabled") and home.get("existingClaim"):
            home_path = str(home["mountPath"])
            sandbox_env["HOME"] = home_path
            sandbox_env["CODEX_HOME"] = f"{self._sandbox_workspace}/.codex"
            sandbox_env["CLAUDE_CONFIG_DIR"] = f"{home_path}/.claude"
            sandbox_env["SKULD__PERSISTENT_HOME_PATH"] = home_path

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
if ! git -C "$WORKSPACE" rev-parse --git-dir >/dev/null 2>&1; then
  git init "$WORKSPACE"
fi
if git -C "$WORKSPACE" remote get-url origin >/dev/null 2>&1; then
  git -C "$WORKSPACE" remote set-url origin "$CLONE_URL"
else
  git -C "$WORKSPACE" remote add origin "$CLONE_URL"
fi
# OpenShell's attached dynamic provider authenticates every Git request.
# Clear bootstrap helpers left by older sessions that relied on a temporary env var.
git -C "$WORKSPACE" config credential.helper ''
git -C "$WORKSPACE" config user.name >/dev/null 2>&1 || \
  git -C "$WORKSPACE" config user.name {shlex.quote(str(git_cfg.get("userName") or ""))}
git -C "$WORKSPACE" config user.email >/dev/null 2>&1 || \
  git -C "$WORKSPACE" config user.email {shlex.quote(str(git_cfg.get("userEmail") or ""))}
if git -C "$WORKSPACE" rev-parse --verify HEAD >/dev/null 2>&1; then
  echo "Workspace already contains a git repository, skipping clone"
  exit 0
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
        return session.pod_name or f"forge-{session.id.hex[:13]}"

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
        # A resident is a resident runtime, not a Forge session: Volundr has no
        # Forge session event log or activity for its id (as in FluxPodManager).
        "event_log_enabled": False,
        "activity_reporting_enabled": False,
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
    realm_slug: str = "",
    volundr_api_url: str = "",
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
    if realm_slug:
        config["environment"]["charter_mimir_page"] = realm_charter_page_for(realm_slug)
        config["resident_evolution"] = {
            "realm_slug": realm_slug,
            "realm_api_base_url": volundr_api_url,
        }
    resident_flock_runtime_config(config, runtime, values)
    if resident.get("dailyBudgetUsd") or resident.get("daily_budget_usd"):
        config["budget"] = {
            "daily_cap_usd": float(
                resident.get("dailyBudgetUsd") or resident.get("daily_budget_usd")
            )
        }
    mimir = _resident_mimir_config(values, realm_slug)
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


def _resident_mimir_config(values: dict[str, Any], realm_slug: str = "") -> dict[str, Any]:
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
    mount_name = realm_mount_name_for(realm_slug) if realm_slug else ""
    write_rules: list[list[Any]] = []
    if mount_name and any(item.get("name") == mount_name for item in instances):
        write_rules = [[realm_routing_prefix_for(realm_slug), [mount_name]]]
        if not write_default:
            write_default = [mount_name]
    if not write_default and not write_rules:
        raise RuntimeError(
            "Resident Mímir instances are configured but no write target resolves: no "
            f"instance has role 'local', there is more than one instance, and no mount "
            f"named {mount_name!r} is present. Configure a 'local' role on one instance "
            "or tag one mount for this realm, so resident writes have somewhere to go."
        )
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
        "write_routing": {"rules": write_rules, "default": write_default},
    }


def _resident_api_urls(values: dict[str, Any]) -> tuple[str, ...]:
    urls: list[str] = []
    resident = values.get("resident") if isinstance(values.get("resident"), dict) else {}
    platform = resident.get("platform") if isinstance(resident.get("platform"), dict) else {}
    platform_url = platform.get("baseUrl") or platform.get("base_url")
    if platform_url:
        urls.append(str(platform_url))
    mimir = values.get("mimir")
    if isinstance(mimir, dict):
        hosted_url = mimir.get("hostedUrl") or mimir.get("hosted_url")
        if hosted_url:
            urls.append(str(hosted_url))
        for instance in [
            *(mimir.get("instances") or []),
            *(mimir.get("registryRefs") or mimir.get("registry_refs") or []),
        ]:
            if not isinstance(instance, dict):
                continue
            kwargs = instance.get("kwargs") or {}
            url = kwargs.get("base_url") or instance.get("url")
            if url:
                urls.append(str(url))
    llm = resident.get("llm") if isinstance(resident.get("llm"), dict) else {}
    provider = llm.get("provider") if isinstance(llm.get("provider"), dict) else {}
    kwargs = provider.get("kwargs") if isinstance(provider.get("kwargs"), dict) else {}
    if kwargs.get("base_url") or kwargs.get("baseUrl"):
        urls.append(str(kwargs.get("base_url") or kwargs.get("baseUrl")))
    broker = values.get("broker") or {}
    codex_auth = broker.get("codexAuth") or {}
    token_path = (codex_auth.get("kwargs") or {}).get("token_path", "")
    if urlparse(token_path).scheme:
        urls.append(token_path)
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


# A regular container may be Running while its entry command waits here. Do not
# use a Kubernetes readiness probe for this gate: the primary's exec channel is
# needed to finish workspace/credential bootstrap and release the workloads.
_WORKLOAD_START_COMMAND = """import os, pathlib, sys, time
marker = pathlib.Path(sys.argv[1])
deadline = time.monotonic() + float(sys.argv[2])
while not marker.exists():
    if time.monotonic() >= deadline:
        raise TimeoutError('Völundr did not finish workload bootstrap')
    time.sleep(0.1)
os.execvp(sys.argv[3], sys.argv[3:])
"""


def _driver_config_from_values(
    values: dict[str, Any],
    *,
    workload_start_file: str = "",
    workload_environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    pod: dict[str, Any] = {}
    if isinstance(values.get("nodeSelector"), dict):
        pod["node_selector"] = _string_dict(values["nodeSelector"])
    if isinstance(values.get("tolerations"), list) and values["tolerations"]:
        pod["tolerations"] = values["tolerations"]
    if values.get("runtimeClassName"):
        pod["runtime_class_name"] = str(values["runtimeClassName"])
    if values.get("priorityClassName"):
        pod["priority_class_name"] = str(values["priorityClassName"])
    config: dict[str, Any] = {"pod": pod} if pod else {}
    volumes = []
    mounts = []
    for key, name in (("homeVolume", "forge-home"), ("persistence", "forge-workspace")):
        storage = values.get(key, {})
        if not isinstance(storage, dict) or not storage.get("existingClaim"):
            continue
        if key == "homeVolume" and not storage.get("enabled"):
            continue
        mount_path = str(storage.get("mountPath") or "")
        if not mount_path.startswith("/sandbox/"):
            raise ValueError("OpenShell storage mount paths must be under /sandbox/")
        volumes.append(
            {
                "name": name,
                "persistent_volume_claim": {
                    "claim_name": str(storage["existingClaim"]),
                    "read_only": False,
                },
            }
        )
        mounts.append({"name": name, "mount_path": mount_path, "read_only": False})
    resident_workspace = values.get("residentWorkspace")
    if isinstance(resident_workspace, dict) and resident_workspace.get("existingClaim"):
        # A resident's own claim, mounted outside /sandbox so the gateway keeps
        # its writable /sandbox (RESIDENT_WORKSPACE_VOLUME_PATH).
        mount_path = str(resident_workspace.get("mountPath") or "")
        if not mount_path.startswith("/") or PurePosixPath(mount_path).is_relative_to("/sandbox"):
            raise ValueError("A resident's workspace volume must be mounted outside /sandbox")
        volumes.append(
            {
                "name": RESIDENT_WORKSPACE_VOLUME_NAME,
                "persistent_volume_claim": {
                    "claim_name": str(resident_workspace["existingClaim"]),
                    "read_only": False,
                },
            }
        )
        mounts.append(
            {"name": RESIDENT_WORKSPACE_VOLUME_NAME, "mount_path": mount_path, "read_only": False}
        )
    openshell = values.get("openshell") or {}
    workloads = deepcopy(openshell.get("workloads") or [])
    if workloads:
        if not workload_start_file:
            raise ValueError("OpenShell workload containers require session workspace bootstrap")
        volumes.extend(deepcopy(openshell.get("volumes") or []))
        shared_mounts = deepcopy(openshell.get("volumeMounts") or [])
        for workload in workloads:
            command = workload.get("command")
            if not isinstance(command, list) or not command:
                raise ValueError("OpenShell workload containers require an explicit command")
            environment = {
                key: value
                for key, value in (workload_environment or {}).items()
                if key not in SECRET_ENV_KEYS
                and not key.startswith(("OPENSHELL_", "SKULD__WORKFLOW__"))
            }
            environment.update(workload.get("environment") or {})
            workload["environment"] = environment
            workload["volume_mounts"] = [*deepcopy(mounts), *workload.get("volume_mounts", [])]
            workload["command"] = [
                "python",
                "-c",
                _WORKLOAD_START_COMMAND,
                workload_start_file,
                "300",
                *command,
            ]
        mounts.extend(shared_mounts)
        # Peers need the gateway's allow_driver_config = true; the driver rejects
        # anything but the fields openshell_contract lists.
        check_peer_workloads(workloads, volumes)
        config["containers"] = {"workloads": workloads}
    if volumes:
        check_volume_mounts("agent", mounts, volumes)
        config["volumes"] = volumes
        config.setdefault("containers", {})["agent"] = {"volume_mounts": mounts}
    return config


def _sandbox_image(sandbox: OpenShellSandbox) -> str:
    """The image a resident's sandbox runs: as Völundr recorded it, else the gateway's."""
    return (sandbox.annotations or {}).get(SANDBOX_IMAGE_ANNOTATION) or sandbox.image


def _workspace_persisted(values: dict[str, Any], workspace: str) -> bool:
    """Whether the profile mounts a persistent claim at or above the sandbox workspace."""
    storage = values.get("persistence")
    if not isinstance(storage, dict) or not storage.get("existingClaim"):
        return False
    mount_path = str(storage.get("mountPath") or "")
    if not mount_path.startswith("/sandbox/"):
        return False
    return PurePosixPath(workspace).is_relative_to(PurePosixPath(mount_path))


@dataclass
class _WorkspaceMigrationProgress:
    """A migration in flight: the sandbox it copies from, and what its rollback left."""

    #: The old sandbox: it runs the resident until the new one is healthy, and a
    #: rollback never removes it.
    source: str = ""
    #: The new sandbox, when the rollback could not remove it.
    abandoned: str = ""


def _copied_from(state: Any, source: str) -> bool:
    """Whether the claim holds a verified copy of ``source``, which still exists."""
    return state is not None and state.phase == PHASE_COMPLETE and state.source == source


def _migration_digest(pvc: Any) -> str:
    return str((pvc.metadata.annotations or {}).get(ANNOTATION_MIGRATION_DIGEST) or "")


def _sandbox_mounts_claim(sandbox: OpenShellSandbox, claim: ResidentWorkspaceClaim) -> bool:
    return (sandbox.annotations or {}).get(SANDBOX_WORKSPACE_CLAIM_ANNOTATION) == claim.name


def _sandbox_persists_workspace(
    values: dict[str, Any],
    workspace: str,
    sandbox: OpenShellSandbox,
    claim: ResidentWorkspaceClaim | None,
) -> bool:
    """Whether recreating this sandbox keeps the resident's workspace.

    With a Völundr-managed claim, only a sandbox created with it mounted; a
    sandbox that predates the claim keeps its state in ``/sandbox`` until migrated.
    """
    if claim is not None:
        return _sandbox_mounts_claim(sandbox, claim)
    return _workspace_persisted(values, workspace)


def _initial_migration_annotations(source: str, holder: str) -> dict[str, str]:
    """A claim created for a migration is ``copying`` from birth, never mistaken for state."""
    return {
        ANNOTATION_MIGRATION_PHASE: PHASE_COPYING,
        ANNOTATION_MIGRATION_SOURCE: source,
        ANNOTATION_MIGRATION_HOLDER: holder,
    }


def _verify_workspace_copy(expected: WorkspaceManifest, restored: WorkspaceManifest) -> None:
    """Every path, kind, size and SHA-256 must match before anything is deleted."""
    if restored.entries == expected.entries:
        return
    missing = sorted(set(expected.entries) - set(restored.entries))
    extra = sorted(set(restored.entries) - set(expected.entries))
    changed = sorted(
        path
        for path in set(expected.entries) & set(restored.entries)
        if expected.entries[path] != restored.entries[path]
    )
    raise WorkspaceVerificationError(
        "The restored workspace does not match the archive "
        f"(expected {expected.summary()}, restored {restored.summary()}; "
        f"missing {missing[:5]}, unexpected {extra[:5]}, differing {changed[:5]})"
    )


def _workspace_migration_failed_condition(error: WorkspaceMigrationError) -> ResidentCondition:
    return ResidentCondition(
        type="WorkspaceMigration",
        status=ResidentConditionStatus.FALSE,
        reason=error.reason,
        message=(
            f"{error} The resident keeps running on its old sandbox with its state intact; "
            "Restart it to retry the migration."
        ),
    )


def _human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


def _workspace_size_script(sources: Sequence[str]) -> str:
    """Print "<KiB>\\t<dir>" for each source directory that exists."""
    quoted = " ".join(shlex.quote(source) for source in sources)
    return (
        "set -eu\n"
        f"for src in {quoted}; do\n"
        '  [ -d "$src" ] || continue\n'
        '  printf \'%s\\t%s\\n\' "$(du -sk "$src" | cut -f1)" "$src"\n'
        "done\n"
    )


def _workspace_clear_script(workspace: str) -> str:
    """Empty the claim's root, keeping the filesystem's own lost+found."""
    return (
        "set -eu\n"
        f"cd {shlex.quote(workspace)}\n"
        "find . -mindepth 1 -maxdepth 1 ! -name lost+found -exec rm -rf {} +\n"
    )


def _workspace_extract_script(target: str) -> str:
    """Extract stdin's archive into ``target`` without touching ``target`` itself.

    The claim's root belongs to root (the fsGroup grants the sandbox user write
    access), so restoring the archive's "./" onto it fails ("Cannot change mode");
    ``--no-overwrite-dir`` avoids that only with some tar versions. The archive is
    extracted into a fresh directory on the claim and its entries moved up.
    """
    return (
        "set -eu\n"
        f"target={shlex.quote(target)}\n"
        'mkdir -p "$target"\n'
        'stage=$(mktemp -d "$target/.volundr-restore.XXXXXX")\n'
        'tar -C "$stage" -xf -\n'
        'find "$stage" -mindepth 1 -maxdepth 1 -exec mv -- {} "$target"/ \\;\n'
        'rmdir "$stage"\n'
    )


def _workspace_link_script(link: str, target: str) -> str:
    """Make ``link`` (the workspace path) a symlink to the mounted volume ``target``.

    A sandbox that mounts the volume at ``link`` itself (created before the volume
    moved out of /sandbox) is left as it is. Anything else at ``link`` (a directory
    a file projection or the runtime created) is moved aside, never deleted, and
    its new path printed.
    """
    return (
        "set -eu\n"
        f"link={shlex.quote(link)}\n"
        f"target={shlex.quote(target)}\n"
        'if [ -L "$link" ]; then\n'
        '  [ "$(readlink "$link")" = "$target" ] && exit 0\n'
        '  echo "$link links to $(readlink "$link"), not $target" >&2\n'
        "  exit 1\n"
        "fi\n"
        'if [ -d "$link" ] && [ "$(stat -c %d "$link")" != "$(stat -c %d "${link%/*}")" ]; then\n'
        '  [ ! -e "$target" ] && exit 0\n'
        '  echo "$link is a mount of its own, and the workspace volume is at $target" >&2\n'
        "  exit 1\n"
        "fi\n"
        'if [ ! -d "$target" ]; then\n'
        '  echo "the workspace volume is not mounted at $target" >&2\n'
        "  exit 1\n"
        "fi\n"
        'if [ -d "$link" ] && rmdir "$link" 2>/dev/null; then\n'
        "  :\n"
        'elif [ -e "$link" ]; then\n'
        '  aside="$link.pre-link-$(date -u +%Y%m%dT%H%M%SZ)"\n'
        '  [ -e "$aside" ] && aside="$aside-$$"\n'
        '  mv "$link" "$aside"\n'
        '  echo "moved-aside=$aside"\n'
        "fi\n"
        'ln -s "$target" "$link"\n'
    )


def _workspace_link_state_script(link: str, target: str) -> str:
    """Print what the workspace path is: linked, mounted, foreign, directory or missing."""
    return (
        "set -eu\n"
        f"link={shlex.quote(link)}\n"
        f"target={shlex.quote(target)}\n"
        'if [ -L "$link" ]; then\n'
        '  if [ "$(readlink "$link")" = "$target" ]; then echo linked; else echo foreign; fi\n'
        'elif [ -d "$link" ] && [ "$(stat -c %d "$link")" != "$(stat -c %d "${link%/*}")" ]; then\n'
        "  echo mounted\n"
        'elif [ -e "$link" ]; then\n'
        "  echo directory\n"
        "else\n"
        "  echo missing\n"
        "fi\n"
    )


def _home_link_script(link: str, target: str) -> str:
    """Make ``link`` a symlink to ``target`` on the claim; never replace real state."""
    return (
        "set -eu\n"
        f"link={shlex.quote(link)}\n"
        f"target={shlex.quote(target)}\n"
        'mkdir -p "$target"\n'
        'if [ -L "$link" ]; then\n'
        '  [ "$(readlink "$link")" = "$target" ] && exit 0\n'
        '  echo "$link links to $(readlink "$link"), not $target" >&2\n'
        "  exit 1\n"
        "fi\n"
        'if [ -e "$link" ]; then\n'
        '  echo "$link holds state outside the workspace volume; refusing to replace it" >&2\n'
        "  exit 1\n"
        "fi\n"
        'ln -s "$target" "$link"\n'
    )


def _upgrading_observation(target: str, *, waiting_for: str = "") -> ResidentRuntimeObservation:
    """A resident whose sandbox is being recreated on ``target``: no backend, redeploy it."""
    message = f"Recreating the OpenShell sandbox on {target}"
    if waiting_for:
        message += f" once OpenShell has finished deleting sandbox {waiting_for}"
    return ResidentRuntimeObservation(
        observed_state=ResidentObservedState.PENDING,
        conditions=[
            ResidentCondition(
                type="SandboxReady",
                status=ResidentConditionStatus.FALSE,
                reason="Upgrading",
                message=message,
            ),
            ResidentCondition(
                type="PlatformUpgrade",
                status=ResidentConditionStatus.FALSE,
                reason="Upgrading",
                message=message,
            ),
        ],
    )


def _upgrade_failed_condition(message: str) -> ResidentCondition:
    return ResidentCondition(
        type="PlatformUpgrade",
        status=ResidentConditionStatus.FALSE,
        reason="UpgradeFailed",
        message=message,
    )


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


def _resident_openclaw_port(values: dict[str, Any]) -> int:
    resident = values.get("resident") if isinstance(values.get("resident"), dict) else {}
    shim = resident.get("openclaw") if isinstance(resident.get("openclaw"), dict) else {}
    return int(shim.get("port") or RAVN_OPENCLAW_PORT)


def _resident_process_env_script(process_name: str, variable: str) -> str:
    """Print one variable of a running resident process's environment, if set."""
    pid_path = shlex.quote(f"/sandbox/.volundr/{process_name}.pid")
    pattern = shlex.quote(f"s/^{variable}=//p")
    return (
        "set -eu\n"
        f'pid="$(cat {pid_path})"\n'
        f"tr '\\000' '\\n' < \"/proc/$pid/environ\" | sed -n {pattern}\n"
    )


def _openclaw_forwarder_key(runtime: ResidentRuntime, sandbox_id: str) -> str:
    """A Ravn resident's shim tunnel to one sandbox, beside its skuld tunnel (its id)."""
    return f"{runtime.id}:openclaw:{sandbox_id}"


def _is_openclaw_key(key: str, runtime: ResidentRuntime) -> bool:
    return key.startswith(f"{runtime.id}:openclaw:")


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


def _rpc_error_text(exc: grpc.RpcError) -> str:
    """``CODE: details`` of a failed gateway call, without the gRPC debug dump."""
    code = exc.code() if callable(getattr(exc, "code", None)) else None
    details = exc.details() if callable(getattr(exc, "details", None)) else None
    name = getattr(code, "name", "")
    text = str(details or exc)
    return f"{name}: {text}" if name else text


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
        # Named for the env var it fills, not the generic "access_token".
        # The provisioner treats this name as an env key across every provider
        # attached to a sandbox, so two providers both calling it
        # "access_token" collided and the session failed to provision:
        # "credential env key 'access_token' is provided by both provider ...;
        # use provider-specific env names". env_name is already unique per
        # provider and is the key the message is actually about.
        name=env_name,
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
            cache_ttl=_duration(cache_ttl_seconds),
        ),
    )
    endpoints = target.get("endpoints") or [
        _network_endpoint(
            {
                "host": host,
                "port": 443,
                "protocol": "rest",
                "enforcement": "enforce",
                "access": "full",
            }
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
        _network_endpoint(
            {
                "host": host,
                "port": 443,
                "protocol": "rest",
                "enforcement": "enforce",
                "access": "full",
            }
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
        # Same reason as the per-connection profile: the slot name doubles as
        # an env key at the provisioner, so it has to be provider-specific.
        name=PLATFORM_ACCESS_TOKEN_ENV,
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
            # No cache_ttl override: the gateway caches for the token's own expires_in.
        ),
    )
    # TLS is left unspecified: OpenShell detects it per connection and
    # terminates HTTPS for inspection, while plain HTTP is inspected as is.
    endpoints = [
        _network_endpoint(
            {
                "host": parsed.hostname,
                "port": port,
                "protocol": "rest",
                "enforcement": "enforce",
                "access": "full",
            }
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
                _network_endpoint(
                    {
                        "host": str(raw["host"]),
                        "port": int(raw["port"]),
                        "path": str(raw.get("path") or ""),
                        "protocol": str(raw.get("protocol") or ""),
                        # Unspecified TLS is OpenShell's automatic detection and
                        # termination: the credential is injected into inspected
                        # traffic. "skip" must be asked for explicitly.
                        "tls": str(raw.get("tls") or ""),
                        "enforcement": str(raw.get("enforcement") or "enforce"),
                        "access": str(raw.get("access") or "full"),
                        "allowed_ips": [str(item) for item in allowed_ips],
                        "allow_uninspected_credentials": (
                            raw.get("allow_uninspected_credentials") is True
                        ),
                    }
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
    if env_name in {"ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}:
        subscription = env_name == "CLAUDE_CODE_OAUTH_TOKEN"
        return {
            "auth_style": "bearer" if subscription else "header",
            "header_name": "Authorization" if subscription else "x-api-key",
            "hosts": ("api.anthropic.com",),
            "binaries": (
                "/usr/local/bin/claude",
                "/usr/bin/node",
                "/usr/local/bin/node",
                "/opt/venv/bin/python3",
            ),
            "category": openshell_pb2.PROVIDER_PROFILE_CATEGORY_AGENT,
        }
    if env_name == "XAI_API_KEY":
        return {
            "auth_style": "bearer",
            "header_name": "Authorization",
            "hosts": ("api.x.ai",),
            "binaries": (
                "/usr/local/bin/grok",
                "/opt/skuld-tools/node_modules/@xai-official/grok/bin/grok-native",
            ),
            "category": openshell_pb2.PROVIDER_PROFILE_CATEGORY_AGENT,
        }
    if env_name == "DEEPSEEK_API_KEY":
        return {
            "auth_style": "bearer",
            "header_name": "Authorization",
            "hosts": ("api.deepseek.com",),
            "binaries": (
                "/opt/niuu/lib/python*/site-packages/deepseek_harness_runtime/runtime/*",
                "/opt/venv/lib/python*/site-packages/deepseek_harness_runtime/runtime/*",
            ),
            "category": openshell_pb2.PROVIDER_PROFILE_CATEGORY_AGENT,
        }
    if env_name == "LINEAR_API_KEY":
        return {
            "auth_style": "header",
            "header_name": "Authorization",
            "hosts": ("api.linear.app",),
            "binaries": (
                "/usr/bin/curl",
                "/usr/bin/node",
                "/usr/local/bin/node",
                "/opt/venv/bin/python3",
                "/opt/niuu/bin/python",
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
            # GitHub Git endpoints reject Bearer PATs. The token exchange returns
            # the complete Basic header for OpenShell's raw-header injection.
            "auth_style": "header",
            "basic_username": "x-access-token",
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
    if env_name == "GITLAB_TOKEN":
        return {
            # GitLab accepts personal, project, group, and OAuth access tokens
            # through the OAuth-compatible Bearer header.  Mediation therefore
            # works for both PAT and device-flow credentials without exposing the
            # token to glab, git, curl, or the sandbox environment.
            "auth_style": "bearer",
            "header_name": "Authorization",
            "hosts": ("gitlab.com",),
            "binaries": (
                "/usr/bin/glab",
                "/usr/bin/git",
                "/usr/lib/git-core/git-remote-http",
                "/usr/lib/git-core/git-remote-https",
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
    expected_copy = openshell_pb2.ProviderProfile()
    expected_copy.CopyFrom(expected)
    for profile in (existing_copy, expected_copy):
        # These fields are gateway-owned and ignored on profile import.
        profile.resource_version = 0
        profile.source = ""
        profile.scope = ""
        for endpoint in profile.endpoints:
            # Gateway-derived on import; user-authored values are ignored.
            endpoint.token_grant_owner = ""
            endpoint.provider_credentialed = False
        for credential in profile.credentials:
            # Output-only endpoint authorities the gateway resolves for a grant.
            del credential.token_grant_owners[:]
            if credential.HasField("token_grant") and not credential.token_grant.grant_type:
                # OpenShell canonicalizes an omitted grant type on import.
                credential.token_grant.grant_type = (
                    openshell_pb2.PROVIDER_CREDENTIAL_TOKEN_GRANT_TYPE_CLIENT_CREDENTIALS
                )
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


#: Directories Völundr may make symlinks in a resident's sandbox (the workspace,
#: onto its volume, and ~/.ravn, onto the volume's .ravn-home). A file projection
#: never carries a directory entry for them: GNU tar replaces a symlink with a
#: new, empty directory when it extracts a directory entry of that name.
_LINKED_SANDBOX_DIRECTORIES = frozenset({"/sandbox/workspace", "/sandbox/.ravn"})


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
                if str(root / parent) in _LINKED_SANDBOX_DIRECTORIES:
                    # Extracted through whatever is there: a link, or a directory
                    # tar creates itself.
                    directories.add(parent_str)
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


def _with_read_write_path(config: dict[str, Any], path: str) -> dict[str, Any]:
    """The policy config with ``path`` writable (once a filesystem policy is set)."""
    filesystem = config.get("filesystem")
    if not isinstance(filesystem, dict):
        return config
    read_write = list(filesystem.get("read_write") or [])
    if path not in read_write:
        read_write.append(path)
    return {**config, "filesystem": {**filesystem, "read_write": read_write}}


#: NetworkEndpoint mode enums by their policy-YAML spelling (openshell_contract).
_NETWORK_MODE_ENUMS = {
    "tls": {
        "": sandbox_pb2.NETWORK_TLS_MODE_UNSPECIFIED,
        "skip": sandbox_pb2.NETWORK_TLS_MODE_SKIP,
    },
    "enforcement": {
        "": sandbox_pb2.NETWORK_ENFORCEMENT_MODE_UNSPECIFIED,
        "enforce": sandbox_pb2.NETWORK_ENFORCEMENT_MODE_ENFORCE,
        "audit": sandbox_pb2.NETWORK_ENFORCEMENT_MODE_AUDIT,
    },
    "access": {
        "": sandbox_pb2.NETWORK_ACCESS_PRESET_UNSPECIFIED,
        "read-only": sandbox_pb2.NETWORK_ACCESS_PRESET_READ_ONLY,
        "read-write": sandbox_pb2.NETWORK_ACCESS_PRESET_READ_WRITE,
        "full": sandbox_pb2.NETWORK_ACCESS_PRESET_FULL,
    },
}


def _network_endpoint(fields: dict[str, Any]) -> Any:
    """A NetworkEndpoint from its policy-YAML form, mode strings mapped to enums."""
    if not isinstance(fields, dict):
        raise ValueError("OpenShell network endpoint must be a mapping")
    endpoint = dict(fields)
    for field, enums in _NETWORK_MODE_ENUMS.items():
        endpoint[field] = enums[network_mode(field, endpoint.get(field))]
    return sandbox_pb2.NetworkEndpoint(**endpoint)


def _sandbox_policy_from_config(config: dict[str, Any]) -> Any:
    if int(config.get("version") or 0) != 1:
        raise ValueError("OpenShell sandbox_policy.version must be 1")
    filesystem = config.get("filesystem") or {}
    process = config.get("process") or {}
    network_policies = {
        str(key): sandbox_pb2.NetworkPolicyRule(
            name=str(value.get("name") or key),
            endpoints=[_network_endpoint(endpoint) for endpoint in value.get("endpoints", [])],
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
    template = getattr(raw.spec, "template", None)
    return OpenShellSandbox(
        id=str(metadata.id or ""),
        name=str(metadata.name or ""),
        phase=int(status.phase),
        ready=any(
            condition.type == "Ready" and condition.status == "True"
            for condition in status.conditions
        ),
        labels={str(key): str(value) for key, value in metadata.labels.items()},
        providers=tuple(str(name) for name in raw.spec.providers),
        image=str(getattr(template, "image", "") or ""),
        annotations={str(key): str(value) for key, value in metadata.annotations.items()},
    )


def _status_from_sandbox(sandbox: OpenShellSandbox) -> SessionStatus:
    if sandbox.phase == openshell_pb2.SANDBOX_PHASE_STOPPED:
        return SessionStatus.STOPPED
    if sandbox.phase == openshell_pb2.SANDBOX_PHASE_STOPPING:
        return SessionStatus.STOPPING
    if sandbox.phase == openshell_pb2.SANDBOX_PHASE_READY or sandbox.ready:
        return SessionStatus.RUNNING
    if sandbox.phase == openshell_pb2.SANDBOX_PHASE_ERROR:
        return SessionStatus.FAILED
    if sandbox.phase == openshell_pb2.SANDBOX_PHASE_DELETING:
        return SessionStatus.STOPPING
    return SessionStatus.PROVISIONING


def _duration(seconds: float) -> duration_pb2.Duration:
    duration = duration_pb2.Duration()
    duration.FromNanoseconds(int(float(seconds) * 1_000_000_000))
    return duration


def _deletion_outcome(outcome: int, target: str) -> bool:
    """Whether a delete removed its target (True) or found it already gone (False).

    ACCEPTED means the gateway still holds the record while the platform tears
    it down: the delete was issued, and callers that need it gone keep polling.
    """
    if outcome in (
        openshell_pb2.DELETION_OUTCOME_COMPLETED,
        openshell_pb2.DELETION_OUTCOME_ACCEPTED,
    ):
        return True
    if outcome == openshell_pb2.DELETION_OUTCOME_ALREADY_ABSENT:
        return False
    raise RuntimeError(
        f"OpenShell gateway returned no deletion outcome for {target}; "
        "the gateway predates DeletionOutcome — upgrade it to the pinned OpenShell release"
    )


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
