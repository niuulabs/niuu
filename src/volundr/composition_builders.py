"""Dynamic adapter and contributor builders for Volundr composition."""

from __future__ import annotations

import inspect
import logging
import socket
import typing
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from niuu.adapters.workload_identity.key_file import (
    SigningKeyFileError,
    load_or_create_rsa_key_pem,
)
from niuu.config_models import WorkloadIdentityConfig
from niuu.domain.services.forge_session_token import ForgeSessionTokenService
from niuu.domain.services.mesh_certificate import MeshCertificateIssuer
from niuu.domain.services.workload_identity import WorkloadIdentityService
from niuu.mesh.attestation import KIND_AUTHORITY, MeshSigner
from niuu.mesh.config import MeshAttestationConfig
from niuu.mesh.platform_events import SigningBus
from niuu.mesh.platform_identity import start_platform_signer
from niuu.ports.credentials import CredentialRefreshLockPort
from niuu.ports.http_auth import HttpAuthPort
from niuu.ports.workload_identity import WorkloadTokenIssuer
from niuu.utils import import_class, resolve_secret_kwargs
from volundr.adapters.inbound.host_join_listener import (
    HostJoinListenerError,
    JoinTls,
    load_or_create_join_tls,
)
from volundr.adapters.outbound.contributors.workflow_execution_credentials import (
    WorkflowExecutionCredentialContributor,
)
from volundr.adapters.outbound.mesh_revocation import MeshRevocationPublisher
from volundr.adapters.outbound.mesh_settings import parse_mesh_settings
from volundr.config import ALLOW_ALL_IDENTITY_ADAPTERS, Settings
from volundr.domain.mesh_peer_claims import MeshPeerClaims
from volundr.domain.ports import (
    ArchiveStorePort,
    AuthorizationPort,
    CodexCredentialBrokerPort,
    CredentialEnrollmentRunnerPort,
    CredentialStorePort,
    ExternalSessionProvider,
    GatewayPort,
    IntegrationRepository,
    PodManager,
    ResidentRuntimeController,
    ResidentSessionController,
    ResourceProvider,
    SecretInjectionPort,
    SessionContributor,
)
from volundr.domain.presented_files import PresentedFileStore
from volundr.domain.project_memory import (
    MemoryMountDirectory,
    ProjectMemoryRepository,
    ProjectMemoryResolver,
)
from volundr.domain.services.client_credentials import ClientCredentialsMinter
from volundr.domain.services.integration_registry import IntegrationRegistry
from volundr.domain.services.mesh_identity import ForgeMeshCertificateSource
from volundr.domain.services.oauth_clients import (
    SOURCE_CONFIGURED,
    OAuthClient,
    OAuthClientRegistry,
)
from volundr.domain.services.oauth_token_refresh import (
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    OAuthTokenRefreshService,
)
from volundr.domain.services.workflow_execution_credentials import (
    WorkflowExecutionCredentialService,
)
from volundr.ports.workflow_execution_credentials import ExecutionCredentialProjectionPort

logger = logging.getLogger(__name__)

KEY_SOURCE_WORKLOAD_IDENTITY = "workload_identity"
KEY_SOURCE_KEY_FILE = "key_file"


class ForgeSessionCredentialsUnavailableError(RuntimeError):
    """Forge session credentials are enabled but no signing key is usable."""


class MeshIdentityUnavailableError(RuntimeError):
    """Mesh identities are enabled but the session-credential signer cannot sign them."""


class HostPairingSignerUnavailableError(RuntimeError):
    """Registered hosts are configured but nothing can sign a usable pairing code."""


#: Issuer and kid of pairing codes signed with ``compute.hosts.pairing_signing_key_file``.
HOST_PAIRING_ISSUER = "niuu-forge-host-pairing"


def create_project_memory(
    settings: Settings, pool: Any, token_issuer: WorkloadTokenIssuer | None = None
) -> tuple[ProjectMemoryRepository | None, ProjectMemoryResolver | None]:
    """The store of the Mimir mounts projects own and the resolver launches use.

    ``(None, None)`` when projects are off. An adapter that is not of its port's type, or a
    directory that cannot be built, is fatal here, at startup, rather than at a launch.
    """
    if not settings.projects.enabled:
        return None, None
    config = settings.projects.memory
    repository = import_class(config.repository_adapter)(pool=pool, **config.repository_kwargs)
    if not isinstance(repository, ProjectMemoryRepository):
        raise TypeError("projects.memory.repository_adapter must implement ProjectMemoryRepository")
    directory_class = import_class(config.directory_adapter)
    kwargs = dict(config.directory_kwargs)
    if "token_issuer" in inspect.signature(directory_class).parameters:
        # The directory asks the hosted Mimir with a workload token Forge mints.
        kwargs["token_issuer"] = token_issuer
    directory = directory_class(**kwargs)
    if not isinstance(directory, MemoryMountDirectory):
        raise TypeError("projects.memory.directory_adapter must implement MemoryMountDirectory")
    return repository, ProjectMemoryResolver(repository, directory)


def _create_forge_session_tokens(
    settings: Settings,
    workload_identity: WorkloadIdentityService,
) -> ForgeSessionTokenService | None:
    """The issuer of scoped session credentials, or ``None`` when they are disabled.

    The workload-identity issuer signs them when it is enabled with a configured
    key: the same key Envoy validates through the workload JWKS. Otherwise (local
    mini mode) Forge uses its own RSA key file, generated once. When credentials are
    enabled but neither key is usable, Forge refuses to start: sessions would run
    with their broker credential and the Forge MCP would silently offer no grants.
    """
    cfg = settings.forge_mcp.session_tokens
    if not cfg.enabled:
        logger.info("Forge session credentials: disabled (forge_mcp.session_tokens.enabled)")
        return None
    if workload_identity.enabled and workload_identity.has_configured_key:
        logger.info("Forge session credentials: signed by the workload identity issuer")
        return ForgeSessionTokenService(
            workload_identity,
            audiences=settings.workload_identity.audiences,
            ttl_seconds=cfg.ttl_seconds,
            key_source=KEY_SOURCE_WORKLOAD_IDENTITY,
        )
    try:
        pem = load_or_create_rsa_key_pem(cfg.signing_key_file, key_size=cfg.signing_key_bits)
    except SigningKeyFileError as exc:
        raise ForgeSessionCredentialsUnavailableError(
            f"Forge session credentials are enabled but have no usable signing key: {exc}. "
            "Fix forge_mcp.session_tokens.signing_key_file, enable workload_identity with "
            "a signing key, or set forge_mcp.session_tokens.enabled: false."
        ) from exc
    issuer = WorkloadIdentityService(
        WorkloadIdentityConfig(
            enabled=True,
            issuer=cfg.issuer,
            audiences=list(cfg.audiences),
            key_id=cfg.key_id,
        ),
        signing_key_pem=pem,
    )
    logger.info("Forge session credentials: signed with the local key file")
    return ForgeSessionTokenService(
        issuer,
        audiences=cfg.audiences,
        ttl_seconds=cfg.ttl_seconds,
        key_source=KEY_SOURCE_KEY_FILE,
    )


def _create_mesh_certificate_issuer(
    settings: Settings,
    session_tokens: ForgeSessionTokenService | None,
) -> MeshCertificateIssuer | None:
    """The issuer of mesh certificates, or ``None`` when Forge runs without a mesh identity.

    It signs with the issuer that signs the session credentials, so the same key serves
    both (the workload-identity key, or Forge's own key file in local mini mode) and the
    certificates verify against a JWKS the peers can fetch from Forge. No session
    credentials means no mesh identity. With them enabled the signer must be able to sign
    mesh certificates; one that cannot is fatal, never "no mesh identity".
    """
    cfg = settings.forge_mcp.mesh_identity
    if not settings.mesh.attestation.required:
        logger.info("Mesh identity: not issued (mesh.attestation.required is false)")
        return None
    if session_tokens is None:
        logger.info(
            "Mesh identity: off because Forge session credentials are disabled "
            "(forge_mcp.session_tokens.enabled)"
        )
        return None
    signing = session_tokens.signing_issuer
    if not callable(getattr(signing, "sign_claims", None)) or not callable(
        getattr(signing, "jwks", None)
    ):
        raise MeshIdentityUnavailableError(
            "mesh.attestation.required is true but the session-credential signer "
            f"({type(signing).__name__}) cannot sign mesh certificates; use a workload "
            "identity issuer, or set mesh.attestation.required: false."
        )
    logger.info("Mesh identity: certificates valid for %ss", cfg.ttl_seconds)
    return MeshCertificateIssuer(signing, ttl_seconds=cfg.ttl_seconds)


def _forge_authority_peer_id(settings: Settings) -> str:
    """The mesh peer id of this Forge: ``forge:<instance id>``, else its host name."""
    return f"forge:{settings.projects.instance_id or socket.gethostname()}"


async def _create_mesh_revocation_publisher(
    settings: Settings,
    issuer: MeshCertificateIssuer,
    claims: MeshPeerClaims,
    bus: typing.Any,
    attestation: MeshAttestationConfig | None = None,
) -> MeshRevocationPublisher:
    """Forge's authority peer: a key generated now, certified by Forge itself.

    Raises when the authority certificate cannot be had (for instance another Forge
    already holds this peer id): a Forge that was asked to publish revocations and cannot
    must not start as if it did.
    """
    attestation = attestation or MeshAttestationConfig()
    peer_id = _forge_authority_peer_id(settings)
    source = ForgeMeshCertificateSource(
        issuer,
        claims,
        holder=f"authority:{peer_id}",
        kind=KIND_AUTHORITY,
        launch_id=uuid.uuid4().hex,
        memberships=(),
    )
    signer = MeshSigner(
        source,
        peer_id,
        renew_fraction=attestation.renew_fraction,
        retry_fraction=attestation.retry_fraction,
    )
    publisher = MeshRevocationPublisher(bus, signer, attestation=attestation)
    await publisher.start()
    logger.info("Mesh revocations: published as %s on the Sleipnir bus", peer_id)
    return publisher


def _create_host_pairing_signer(
    settings: Settings,
    workload_identity: WorkloadIdentityService,
) -> WorkloadIdentityService:
    """The issuer of host pairing codes.

    The workload-identity issuer signs them when it is enabled with a configured key:
    that is the key Envoy and the identity adapter validate. Otherwise, only where
    the identity adapter is allow-all (local mini mode, where nothing but Forge itself
    checks a bearer), Forge signs them with its own RSA key file, generated once, so
    a single machine can add hosts without any identity infrastructure. Anywhere
    else a code signed by Forge's own key would be refused before it reached Forge,
    so startup fails instead.
    """
    hosts = settings.compute.hosts if settings.compute is not None else None
    if hosts is None:
        raise HostPairingSignerUnavailableError("compute.hosts is not configured")
    if workload_identity.enabled and workload_identity.has_configured_key:
        logger.info("Host pairing codes: signed by the workload identity issuer")
        return workload_identity
    if settings.identity.adapter not in ALLOW_ALL_IDENTITY_ADAPTERS:
        raise HostPairingSignerUnavailableError(
            "compute.hosts needs a pairing-code signer the identity layer accepts: "
            f"identity.adapter is {settings.identity.adapter}, so enable "
            "workload_identity with a configured signing key (signing_key_pem or "
            "signing_key_env). Forge's own key file is only used with the allow-all "
            "identity adapter of local mini mode."
        )
    try:
        pem = load_or_create_rsa_key_pem(
            hosts.pairing_signing_key_file, key_size=hosts.pairing_signing_key_bits
        )
    except SigningKeyFileError as exc:
        raise HostPairingSignerUnavailableError(
            f"compute.hosts has no usable pairing signing key: {exc}. Fix "
            "compute.hosts.pairing_signing_key_file, or enable workload_identity with a "
            "signing key."
        ) from exc
    logger.info("Host pairing codes: signed with the local key file")
    return WorkloadIdentityService(
        WorkloadIdentityConfig(
            enabled=True,
            issuer=HOST_PAIRING_ISSUER,
            audiences=["volundr-api"],
            key_id=HOST_PAIRING_ISSUER,
        ),
        signing_key_pem=pem,
    )


@dataclass(frozen=True)
class HostJoinEndpoint:
    """Where hosts join: the URL, and the pinned listener's TLS when Forge serves it."""

    url: str
    tls: JoinTls | None


def _create_host_join_endpoint(settings: Settings) -> HostJoinEndpoint:
    """The join URL and, for Forge's own join listener, its key, certificate and pin.

    Without ``join_url`` the URL is the listener at ``server_public_host``; an address no
    other machine can reach is a startup error, never a plaintext or loopback join.
    """
    hosts = settings.compute.hosts if settings.compute is not None else None
    if hosts is None:
        raise HostJoinListenerError("compute.hosts is not configured")
    try:
        url = hosts.join_base_url(settings.server_public_host)
    except ValueError as exc:
        raise HostJoinListenerError(str(exc)) from exc
    listener = hosts.join_listener
    if listener is None:
        logger.info("Hosts join through Forge's API at %s", url)
        return HostJoinEndpoint(url=url, tls=None)
    tls = load_or_create_join_tls(
        cert_file=listener.cert_file, key_file=listener.key_file, key_bits=listener.key_bits
    )
    return HostJoinEndpoint(url=url, tls=tls)


def _create_forge_session_projection(
    settings: Settings,
) -> ExecutionCredentialProjectionPort | None:
    """The projection that mounts session credentials into session pods, if configured."""
    cfg = settings.forge_mcp.session_tokens
    if not cfg.enabled or not cfg.projection_adapter.strip():
        return None
    projection = import_class(cfg.projection_adapter)(
        **resolve_secret_kwargs(cfg.projection_kwargs, cfg.projection_secret_kwargs_env)
    )
    if not isinstance(projection, ExecutionCredentialProjectionPort):
        raise TypeError(
            f"Forge session credential projection {cfg.projection_adapter} must implement "
            "ExecutionCredentialProjectionPort"
        )
    logger.info(
        "Forge session credentials: projected by %s", cfg.projection_adapter.rsplit(".", 1)[-1]
    )
    return projection


async def create_presented_file_store(settings: Settings, pool: Any) -> PresentedFileStore | None:
    """Forge's store of durable presented files (F2), or ``None`` when they are off.

    Every store gets Forge's database pool and ``read_chunk_bytes``. A store that
    cannot hold the quota, or cannot be written, is fatal now rather than an HTTP 500
    on the first upload.
    """
    config = settings.presented_files
    if not config.enabled:
        return None
    store_config = dict(config.store)
    store_cls = import_class(store_config.pop("adapter"))
    store = store_cls(pool=pool, read_chunk_bytes=config.read_chunk_bytes, **store_config)
    if not isinstance(store, PresentedFileStore):
        raise TypeError(
            f"presented_files.store.adapter {store_cls.__name__} must implement PresentedFileStore"
        )
    await store.ensure_capacity(config.max_total_bytes)
    return store


def require_presented_file_uploads(
    settings: Settings, *, delivers_session_credential: bool
) -> None:
    """Refuse durable presented files that no broker could upload.

    Brokers upload durable copies with their session credential. Behind an
    authenticating identity nothing else reliably reaches Forge (a browser login
    expires, a headless session has none), so every upload would be refused and no
    copy would ever be kept. Forge's allow-all identity admits the broker's own call.
    """
    if settings.identity.adapter in ALLOW_ALL_IDENTITY_ADAPTERS:
        return
    if delivers_session_credential:
        return
    raise RuntimeError(
        "presented_files.enabled needs session brokers to hold their Forge session "
        "credential: enable forge_mcp.session_tokens and, for pod managers that do not "
        "deliver it themselves (Flux, Kubernetes), set "
        "forge_mcp.session_tokens.projection_adapter"
    )


def require_memory_credentials(settings: Settings, *, delivers_session_credential: bool) -> None:
    """Refuse a memory gateway that no session broker could authenticate to.

    A session's memory server calls the gateway with the session's own Forge credential
    (scope ``forge:memory``), which the gateway checks with Forge. Sessions that hold no
    credential would start a memory server that every call of fails, so the combination is
    refused at startup.
    """
    if not settings.memory.gateway_url:
        return
    if not settings.forge_mcp.session_tokens.enabled:
        raise RuntimeError(
            "memory.gateway_url is set, but forge_mcp.session_tokens.enabled is false: the "
            "memory server authenticates to the gateway with the session's Forge credential. "
            "Enable forge_mcp.session_tokens, or clear memory.gateway_url to run sessions "
            "without a memory server"
        )
    if delivers_session_credential:
        return
    raise RuntimeError(
        "memory.gateway_url is set, but this pod manager does not hand session brokers "
        "their Forge credential. For pod managers that do not deliver it themselves (Flux, "
        "Kubernetes) set forge_mcp.session_tokens.projection_adapter, or clear "
        "memory.gateway_url to run sessions without a memory server"
    )


def _create_workflow_execution_credential_service(
    settings: Settings,
    *,
    repository,
    token_issuer,
    runtime_backend: str,
) -> WorkflowExecutionCredentialService | None:
    """Compose the configured rotation service without selecting adapters in code."""
    config = settings.workflow_execution_credentials
    if not config.enabled:
        return None
    if config.refresh_interval_seconds >= settings.workload_identity.token_ttl_seconds:
        raise ValueError(
            "workflow_execution_credentials.refresh_interval_seconds must be less than "
            "workload_identity.token_ttl_seconds"
        )
    projection_class = import_class(config.projection_adapter)
    projection_kwargs = resolve_secret_kwargs(
        config.projection_kwargs,
        config.projection_secret_kwargs_env,
    )
    projection = projection_class(**projection_kwargs)
    if not isinstance(projection, ExecutionCredentialProjectionPort):
        raise TypeError(
            f"Developer credential projection {config.projection_adapter} must implement "
            "ExecutionCredentialProjectionPort"
        )
    service = WorkflowExecutionCredentialService(
        repository=repository,
        token_issuer=token_issuer,
        projection=projection,
        runtime_backend=runtime_backend,
        refresh_interval_seconds=config.refresh_interval_seconds,
        trusted_signing_configured=bool(getattr(token_issuer, "trusted_signing_configured", False)),
        admission_roles=config.admission_roles,
    )
    logger.info(
        "Developer execution credential projection: %s",
        config.projection_adapter.rsplit(".", 1)[-1],
    )
    return service


@asynccontextmanager
async def integration_database_pool(settings: Settings, service_pool):
    """Use the same connection records as the integrations-serving API."""
    name = settings.integrations.database_name
    if not name or name == settings.database.name:
        yield service_pool
        return
    from niuu.service_database import database_pool

    async with database_pool(settings.database.model_copy(update={"name": name})) as pool:
        yield pool


def _create_codex_credential_broker(
    settings: Settings,
    *,
    credential_store: CredentialStorePort,
    refresh_lock: CredentialRefreshLockPort | None = None,
) -> CodexCredentialBrokerPort:
    """Create the configured central Codex token broker adapter."""
    config = settings.codex_credential_broker
    cls = import_class(config.adapter)
    kwargs = resolve_secret_kwargs(config.kwargs, config.secret_kwargs_env)
    instance = cls(
        credential_store=credential_store,
        mini_mode=settings.local_mounts.mini_mode,
        refresh_lock=refresh_lock,
        **kwargs,
    )
    if not isinstance(instance, CodexCredentialBrokerPort):
        raise TypeError(
            f"Codex credential broker {config.adapter} must implement CodexCredentialBrokerPort"
        )
    logger.info("Codex credential broker: %s", config.adapter.rsplit(".", 1)[-1])
    return instance


def _create_credential_enrollment_runner(settings: Settings) -> CredentialEnrollmentRunnerPort:
    """Create the configured trusted login runner independently of PodManager."""
    config = settings.credential_enrollment_runner
    cls = import_class(config.adapter)
    kwargs = resolve_secret_kwargs(config.kwargs, config.secret_kwargs_env)
    instance = cls(**kwargs)
    if not isinstance(instance, CredentialEnrollmentRunnerPort):
        raise TypeError(
            f"Credential enrollment runner {config.adapter} must implement "
            "CredentialEnrollmentRunnerPort"
        )
    logger.info("Credential enrollment runner: %s", config.adapter.rsplit(".", 1)[-1])
    return instance


def create_oauth_client_registry(
    settings: Settings,
    *,
    credential_store: CredentialStorePort,
    integration_registry: IntegrationRegistry,
) -> OAuthClientRegistry:
    """The install's OAuth applications: ``oauth.clients`` plus those registered in the wizard."""
    from niuu.ports.credentials import OAuthApplicationStorePort

    application_store = (
        credential_store
        if isinstance(credential_store, OAuthApplicationStorePort)
        and credential_store.manages_oauth_applications
        else None
    )
    return OAuthClientRegistry(
        application_store=application_store,
        credential_store=credential_store,
        integration_registry=integration_registry,
        configured={
            slug: OAuthClient(
                slug=slug,
                client_id=client.client_id,
                client_secret=client.client_secret,
                source=SOURCE_CONFIGURED,
                base_url=client.base_url,
            )
            for slug, client in settings.oauth.clients.items()
        },
    )


def create_oauth_token_refresh_service(
    *,
    integration_repository: IntegrationRepository,
    integration_registry: IntegrationRegistry,
    credential_store: CredentialStorePort,
    oauth_clients: OAuthClientRegistry,
) -> OAuthTokenRefreshService:
    """Refresher for OAuth sign-in tokens (device and authorization-code grants)."""
    return OAuthTokenRefreshService(
        integration_repository=integration_repository,
        integration_registry=integration_registry,
        credential_store=credential_store,
        clients=oauth_clients,
        client_credentials_minter=ClientCredentialsMinter(
            request_timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS
        ),
    )


def with_oauth_device_runner(
    runner: CredentialEnrollmentRunnerPort,
    oauth_clients: OAuthClientRegistry,
    registry: IntegrationRegistry,
) -> CredentialEnrollmentRunnerPort:
    """Add the in-process OAuth device grant (GitHub, GitLab) next to the CLI runner."""
    from volundr.adapters.outbound.oauth_device_runner import (
        CompositeCredentialEnrollmentRunner,
        OAuthDeviceFlowRunner,
    )

    device = OAuthDeviceFlowRunner(registry=registry, clients=oauth_clients)
    return CompositeCredentialEnrollmentRunner([runner, device])


def _create_pod_manager(settings: Settings) -> PodManager:
    """Create the PodManager adapter from dynamic config."""
    pm_cfg = settings.pod_manager
    instance = _build_pod_manager(
        settings,
        pm_cfg.adapter,
        resolve_secret_kwargs(pm_cfg.kwargs, pm_cfg.secret_kwargs_env),
    )
    logger.info("Pod manager: %s", pm_cfg.adapter.rsplit(".", 1)[-1])
    return instance


def _nested_pod_manager_parameters(cls: type, kwargs: dict[str, Any]) -> set[str]:
    """The kwargs whose adapter configs are pod managers to build here.

    Only parameters the constructor types as ``PodManager`` (the placement pod
    manager's ``local`` and ``pool``). Any other adapter-shaped mapping is data the
    adapter passes on, such as Skuld's ``preview_router_auth``.
    """
    if not any(isinstance(value, dict) and "adapter" in value for value in kwargs.values()):
        return set()
    try:
        hints = typing.get_type_hints(cls.__init__)
    except NameError as exc:
        raise TypeError(
            f"Pod manager {cls.__qualname__} has constructor annotations that do not "
            f"resolve ({exc}); nested pod manager configs need resolvable types"
        ) from exc
    return {
        name
        for name, value in kwargs.items()
        if isinstance(value, dict) and "adapter" in value and hints.get(name) is PodManager
    }


def _mesh_mapping(raw: Any, where: str) -> dict[str, Any]:
    return parse_mesh_settings(raw, where)


def _apply_mesh_decision(
    cls: type, kwargs: dict[str, Any], settings: Settings, *, covered: bool
) -> None:
    """Carry ``mesh.attestation.required`` into the sessions this pod manager launches.

    It is one decision for everything Forge runs, so every pod manager that starts Skuld
    takes Skuld's ``mesh`` settings (the ``mesh`` kwarg; ``session_defaults.mesh`` for Flux)
    and hands them to its sessions. Forge writes the decision there, and a different value
    set there is refused at startup rather than letting a session disagree with Forge.
    Signing is Skuld's default, so only the operator's ``false`` has to be written. A pod
    manager that cannot carry it is refused while signing is off, never left to disagree.
    ``covered`` is a pod manager that only wraps others (placement), each built on its own.
    """
    required = settings.mesh.attestation.required
    if getattr(cls, "carries_mesh_setting", False):
        mesh = _mesh_mapping(kwargs.get("mesh"), "pod_manager.kwargs.mesh")
        holder, key, where = mesh, "mesh", "pod_manager.kwargs.mesh"
        target = mesh
    elif "session_defaults" in inspect.signature(cls.__init__).parameters:
        defaults = _mesh_mapping(
            kwargs.get("session_defaults"), "pod_manager.kwargs.session_defaults"
        )
        mesh = _mesh_mapping(defaults.get("mesh"), "pod_manager.kwargs.session_defaults.mesh")
        defaults["mesh"] = mesh
        holder, key, where = (
            defaults,
            "session_defaults",
            "pod_manager.kwargs.session_defaults.mesh",
        )
        target = mesh
    else:
        if required or covered:
            return
        raise ValueError(
            f"mesh.attestation.required is false, but {cls.__name__} has no setting for the "
            "Skuld mesh configuration of its sessions, so they would keep signing on and "
            "disagree. Give the pod manager a `mesh` setting (carries_mesh_setting), or "
            "set mesh.attestation.required: true"
        )
    attestation = _mesh_mapping(target.get("attestation"), f"{where}.attestation")
    explicit = attestation.get("required")
    if explicit is not None and bool(explicit) != required:
        raise ValueError(
            f"{where}.attestation.required is {explicit} but mesh.attestation.required is "
            f"{required}: mesh signing is one decision, set only mesh.attestation.required"
        )
    if required:
        return
    attestation["required"] = False
    target["attestation"] = attestation
    kwargs[key] = holder


def _build_pod_manager(settings: Settings, adapter: str, kwargs: dict[str, Any]) -> PodManager:
    cls = import_class(adapter)
    kwargs = dict(kwargs)
    # Found before the nested configs are replaced by the managers built from them.
    nested_names = _nested_pod_manager_parameters(cls, kwargs)
    for name in nested_names:
        nested = kwargs[name]
        unknown = set(nested) - {"adapter", "kwargs", "secret_kwargs_env"}
        if unknown:
            raise ValueError(
                f"pod_manager.kwargs.{name} takes adapter, kwargs and secret_kwargs_env; "
                f"move {sorted(unknown)} under kwargs"
            )
        kwargs[name] = _build_pod_manager(
            settings,
            str(nested["adapter"]),
            resolve_secret_kwargs(
                dict(nested.get("kwargs") or {}), dict(nested.get("secret_kwargs_env") or {})
            ),
        )
    _apply_mesh_decision(cls, kwargs, settings, covered=bool(nested_names))
    kwargs.setdefault("server_public_host", settings.server_public_host)
    kwargs.setdefault("server_host", settings.server_host)
    kwargs.setdefault("server_port", settings.server_port)
    kwargs.setdefault("gateway_endpoint", settings.openshell_gateway_endpoint)
    kwargs.setdefault("gateway_public_url", settings.openshell_gateway_public_url)
    kwargs.setdefault("token_url", settings.openshell_oidc_token_url)
    kwargs.setdefault("client_id", settings.openshell_oidc_client_id)
    if settings.openshell_oidc_client_secret:
        kwargs.setdefault("client_secret", settings.openshell_oidc_client_secret)
    return cls(**kwargs)


_OPENBAO_SECRET_INJECTION_ADAPTER = (
    "volundr.adapters.outbound.openbao_secret_injection.OpenBaoAgentInjectionAdapter"
)


def _validate_remote_room_role_config(settings: Settings, runtime_backend: str) -> None:
    """Fail fast at startup rather than shipping a 'remote' config that can never work.

    ``pod_manager.room_role_source`` is a whole-deployment decision (see its
    own field description in ``volundr/config.py``) — every future session
    pod's trust boundary changes with it, so refusing an impossible
    combination once at process startup is far better than letting it
    render broken session pods one at a time, discovered only when the
    first invited participant can never attach.
    """
    if settings.pod_manager.room_role_source != "remote":
        return
    if runtime_backend != "kubernetes":
        return
    session_defaults = settings.pod_manager.kwargs.get("session_defaults") or {}
    ws_auth_defaults = session_defaults.get("wsAuth") or {}
    if ws_auth_defaults.get("enforce_ownership"):
        raise ValueError(
            "pod_manager.room_role_source is 'remote' but pod_manager.kwargs."
            "session_defaults.wsAuth.enforce_ownership is true — the pod-local "
            "ext_authz sidecar (identity.adapters.envoy_authz) would gate every "
            "connection to Cedar's owner/admin-only 'start' action before a "
            "remote room-role lookup ever ran, so a participant could never "
            "attach. Set pod_manager.kwargs.session_defaults.wsAuth"
            ".enforce_ownership: false, or set pod_manager.room_role_source "
            "back to 'deployment'."
        )
    if settings.secret_injection.adapter != _OPENBAO_SECRET_INJECTION_ADAPTER:
        raise ValueError(
            "pod_manager.room_role_source is 'remote' but secret_injection.adapter "
            f"is {settings.secret_injection.adapter!r}, not "
            f"{_OPENBAO_SECRET_INJECTION_ADAPTER!r} — "
            "skuld.room_role_remote.RemoteAuthorizationAdapter's session-binding "
            "check (see rest_session_participants._require_session_scoped_"
            "workload_credential) trusts the openbao-session-{id} service "
            "account OpenBaoAgentInjectionAdapter creates per session; without "
            "it, session pods have no per-session service account for Forge's "
            "role endpoint to bind a workload credential to. Configure "
            f"secret_injection.adapter: {_OPENBAO_SECRET_INJECTION_ADAPTER!r}, "
            "or set pod_manager.room_role_source back to 'deployment'."
        )


def _create_resident_controllers(
    settings: Settings,
    pod_manager: PodManager,
) -> list[ResidentRuntimeController]:
    """Create configured resident backend adapters through the shared port."""
    controllers: list[ResidentRuntimeController] = []
    if isinstance(pod_manager, ResidentRuntimeController):
        controllers.append(pod_manager)

    for config in settings.resident_runtimes.controllers:
        cls = import_class(config.adapter)
        kwargs = resolve_secret_kwargs(config.kwargs, config.secret_kwargs_env)
        instance = cls(**kwargs)
        if not isinstance(instance, ResidentRuntimeController):
            raise TypeError(
                f"Resident controller {config.adapter} must implement ResidentRuntimeController"
            )
        controllers.append(instance)
        logger.info("Resident controller: %s", config.adapter.rsplit(".", 1)[-1])
    return controllers


def _create_resident_session_controllers(
    settings: Settings,
    runtime_controllers: list[ResidentRuntimeController],
    credential_store: CredentialStorePort,
) -> list[ResidentSessionController]:
    """Create configured engine adapters against their owning runtime backend."""
    controllers_by_backend = {controller.backend: controller for controller in runtime_controllers}
    session_controllers: list[ResidentSessionController] = []
    for config in settings.resident_runtimes.session_controllers:
        runtime_controller = controllers_by_backend.get(config.runtime_backend)
        if runtime_controller is None:
            if config.optional:
                continue
            raise RuntimeError(
                "Resident session controller "
                f"{config.adapter} requires unavailable backend {config.runtime_backend.value}"
            )
        cls = import_class(config.adapter)
        kwargs = resolve_secret_kwargs(config.kwargs, config.secret_kwargs_env)
        instance = cls(
            runtime_controller=runtime_controller,
            credential_store=credential_store,
            **kwargs,
        )
        if not isinstance(instance, ResidentSessionController):
            raise TypeError(
                f"Resident session controller {config.adapter} must implement "
                "ResidentSessionController"
            )
        session_controllers.append(instance)
        logger.info("Resident session controller: %s", config.adapter.rsplit(".", 1)[-1])
    return session_controllers


def _runtime_backend(settings: Settings, pod_manager: PodManager) -> str:
    if settings.pod_manager.runtime_backend:
        return settings.pod_manager.runtime_backend
    if isinstance(pod_manager.runtime_backend, str) and pod_manager.runtime_backend:
        return pod_manager.runtime_backend
    adapter = settings.pod_manager.adapter.rsplit(".", 1)[-1].lower()
    if "openshell" in adapter:
        return "openshell"
    if mode := getattr(settings, "mode", None):
        return mode
    return "kubernetes"


def _create_authorization_adapter(settings: Settings) -> AuthorizationPort:
    """Create the AuthorizationPort adapter from dynamic config."""
    az_cfg = settings.authorization
    cls = import_class(az_cfg.adapter)
    kwargs = resolve_secret_kwargs(az_cfg.kwargs, az_cfg.secret_kwargs_env)
    instance = cls(**kwargs)
    logger.info("Authorization adapter: %s", az_cfg.adapter.rsplit(".", 1)[-1])
    return instance


def _create_gateway_adapter(settings: Settings) -> GatewayPort:
    """Create the GatewayPort adapter from dynamic config."""
    gw_cfg = settings.gateway
    cls = import_class(gw_cfg.adapter)
    kwargs = resolve_secret_kwargs(gw_cfg.kwargs, gw_cfg.secret_kwargs_env)
    instance = cls(**kwargs)
    logger.info("Gateway adapter: %s", gw_cfg.adapter.rsplit(".", 1)[-1])
    return instance


def _create_http_auth_adapter(config) -> HttpAuthPort:
    """Create a dynamic outbound HTTP auth adapter."""
    cls = import_class(config.adapter)
    kwargs = resolve_secret_kwargs(config.kwargs, config.secret_kwargs_env)
    return cls(**kwargs)


def _create_secret_injection_adapter(settings: Settings) -> SecretInjectionPort:
    """Create the SecretInjectionPort adapter from dynamic config."""
    si_cfg = settings.secret_injection
    cls = import_class(si_cfg.adapter)
    kwargs = resolve_secret_kwargs(si_cfg.kwargs, si_cfg.secret_kwargs_env)
    instance = cls(**kwargs)
    logger.info("Secret injection: %s", si_cfg.adapter.rsplit(".", 1)[-1])
    return instance


def _create_resource_provider(settings: Settings) -> ResourceProvider:
    """Create the ResourceProvider adapter from dynamic config."""
    rp_cfg = settings.resource_provider
    cls = import_class(rp_cfg.adapter)
    kwargs = resolve_secret_kwargs(rp_cfg.kwargs, rp_cfg.secret_kwargs_env)
    instance = cls(**kwargs)
    logger.info("Resource provider: %s", rp_cfg.adapter.rsplit(".", 1)[-1])
    return instance


def _create_archive_store(settings: Settings) -> ArchiveStorePort:
    """Create the ArchiveStorePort adapter from dynamic config."""
    as_cfg = settings.archive_store
    cls = import_class(as_cfg.adapter)
    kwargs = resolve_secret_kwargs(as_cfg.kwargs, as_cfg.secret_kwargs_env)
    instance = cls(**kwargs)
    logger.info("Archive store: %s", as_cfg.adapter.rsplit(".", 1)[-1])
    return instance


def _create_external_session_providers(
    settings: Settings,
) -> list[ExternalSessionProvider]:
    """Create external session provider adapters from dynamic config.

    Disabled unless ``external_sessions.enabled`` is true, or unset while
    running in mini/local mode — host session stores are only reachable
    when Volundr runs on the host.
    """
    es_cfg = settings.external_sessions
    enabled = es_cfg.enabled if es_cfg.enabled is not None else settings.local_mounts.mini_mode
    if not enabled:
        return []

    providers = []
    for provider_cfg in es_cfg.providers:
        cls = import_class(provider_cfg.adapter)
        instance = cls(**provider_cfg.kwargs)
        providers.append(instance)
        logger.info("External session provider: %s", provider_cfg.adapter.rsplit(".", 1)[-1])
    return providers


def _create_contributors(
    settings: Settings,
    **ports: object,
) -> list[SessionContributor]:
    """Create session contributors from dynamic config.

    Each contributor config specifies a fully-qualified class path.
    Config kwargs are merged with injected port instances so contributors
    can accept the ports they need and ignore others via **_extra.
    """
    from volundr.adapters.outbound.contributors.local_mount import LocalMountContributor
    from volundr.adapters.outbound.contributors.room_role import RoomRoleSourceContributor
    from volundr.adapters.outbound.contributors.session_def import SessionDefinitionContributor
    from volundr.adapters.outbound.contributors.workload_config import WorkloadConfigContributor
    from volundr.adapters.outbound.contributors.workload_identity import (
        WorkloadIdentityContributor,
    )

    contributors: list[SessionContributor] = []
    execution_credential_service = ports.get("execution_credential_service")
    if execution_credential_service is not None:
        contributors.append(
            WorkflowExecutionCredentialContributor(
                execution_credential_service=execution_credential_service
            )
        )
        logger.info("Session contributor: workflow_execution_credentials (auto-wired)")

    def _has_contributor(name: str) -> bool:
        return any(contributor.name == name for contributor in contributors)

    # Auto-wire SessionDefinitionContributor first so definition defaults
    # (broker.cliType, transportAdapter, etc.) are the base layer that
    # later contributors (templates, profiles, resources) can override.
    if settings.session_definitions:
        contributors.append(
            SessionDefinitionContributor(
                definitions=settings.session_definitions,
                default_definition=settings.default_definition,
            )
        )
        logger.info(
            "Session contributor: session_definition (auto-wired, %d definitions, default=%s)",
            len(settings.session_definitions),
            settings.default_definition or "(none)",
        )

    for cfg in settings.session_contributors:
        cls = import_class(cfg.adapter)
        resolved_kwargs = resolve_secret_kwargs(cfg.kwargs, cfg.secret_kwargs_env)
        kwargs = {**resolved_kwargs, **ports}
        instance = cls(**kwargs)
        contributors.append(instance)
        logger.info(
            "Session contributor: %s (%s)",
            instance.name,
            cfg.adapter.rsplit(".", 1)[-1],
        )

    if not _has_contributor("workload_config"):
        contributors.append(WorkloadConfigContributor())
        logger.info("Session contributor: workload_config (auto-wired)")

    if not _has_contributor("workload_identity"):
        contributors.append(WorkloadIdentityContributor())
        logger.info("Session contributor: workload_identity (auto-wired)")

    if not _has_contributor("room_role_source"):
        contributors.append(
            RoomRoleSourceContributor(
                room_role_source=settings.pod_manager.room_role_source,
                cache_ttl_seconds=settings.pod_manager.room_role_cache_ttl_seconds,
            )
        )
        logger.info(
            "Session contributor: room_role_source (auto-wired, %s)",
            settings.pod_manager.room_role_source,
        )

    # The integrations a launch attaches carry more than credentials: the
    # Claude auth mode, MCP servers, and the model gateway URL of a self-hosted
    # model server. Wire the contributor whenever a catalog is present, so a
    # docker or host install behaves like the Helm chart, which lists it.
    if not _has_contributor("integrations") and ports.get("integration_registry") is not None:
        from volundr.adapters.outbound.contributors.integrations import IntegrationContributor

        contributors.append(IntegrationContributor(**ports))
        logger.info("Session contributor: integrations (auto-wired)")

    if (
        not _has_contributor("model_gateway")
        and settings.bifrost.session_gateway_url
        and ports.get("pricing_provider") is not None
    ):
        from volundr.adapters.outbound.contributors.model_gateway import (
            ModelGatewayContributor,
        )

        contributors.append(
            ModelGatewayContributor(
                gateway_url=settings.bifrost.session_gateway_url,
                auth_mode=settings.auth_mode,
                **ports,
            )
        )
        logger.info("Session contributor: model_gateway (auto-wired)")

    # Every session runs Skuld's memory server when Forge is given the memory gateway.
    if not _has_contributor("memory_server") and settings.memory.gateway_url:
        from volundr.adapters.outbound.contributors.memory_server import (
            MemoryServerContributor,
        )

        contributors.append(MemoryServerContributor(gateway_url=settings.memory.gateway_url))
        logger.info("Session contributor: memory_server (auto-wired)")

    # Auto-wire LocalMountContributor from local_mounts config
    lm = settings.local_mounts
    local_mount_contributor = LocalMountContributor(
        enabled=lm.enabled,
        allow_root_mount=lm.allow_root_mount,
        allowed_prefixes=lm.allowed_prefixes,
    )
    contributors.append(local_mount_contributor)
    if lm.enabled:
        logger.info("Session contributor: local_mount (enabled)")

    # Always wire the prompt contributor so system_prompt/initial_prompt
    # from the launch request (or dispatch) are injected into the spec.
    from volundr.adapters.outbound.contributors.prompt import PromptContributor

    # Always wired: a launch may carry its pinned persona document, which needs no catalog.
    # A persona requested by name on a host without a catalog is refused by the contributor.
    if not _has_contributor("persona"):
        from volundr.adapters.outbound.contributors.persona import PersonaContributor

        contributors.append(PersonaContributor(persona_provider=ports.get("persona_provider")))
        logger.info("Session contributor: persona (auto-wired)")

    if not _has_contributor("workflow_step_host"):
        from volundr.adapters.outbound.contributors.workflow_step_host import (
            WorkflowStepHostContributor,
        )

        contributors.append(WorkflowStepHostContributor())
        logger.info("Session contributor: workflow_step_host (auto-wired)")

    contributors.append(PromptContributor())

    from volundr.adapters.outbound.contributors.session_mcp import SessionMCPContributor

    if not _has_contributor("session_mcp"):
        contributors.append(SessionMCPContributor(**ports))
        logger.info("Session contributor: session_mcp (auto-wired)")

    return contributors


async def _start_sleipnir_bus(settings: Settings) -> Any | None:
    """Start the configured Sleipnir bus, or ``None`` when ``sleipnir.enabled`` is false.

    Enabled but unable to start is fatal: Forge running on without its bus would silently
    stop events, audit and mesh revocations.
    """
    if not settings.sleipnir.enabled:
        return None
    try:
        bus_class = import_class(settings.sleipnir.adapter)
        bus = bus_class(
            **resolve_secret_kwargs(settings.sleipnir.kwargs, settings.sleipnir.secret_kwargs_env)
        )
        if hasattr(bus, "start"):
            await bus.start()
    except Exception as exc:
        raise RuntimeError(
            f"sleipnir.enabled is true but the bus {settings.sleipnir.adapter!r} could not "
            f"start: {exc!r}. Fix sleipnir.adapter / sleipnir.kwargs, or set "
            "sleipnir.enabled: false to run without a bus"
        ) from exc
    logger.info(
        "Sleipnir integration enabled: adapter=%s", settings.sleipnir.adapter.rsplit(".", 1)[-1]
    )
    return bus


async def _sign_forge_platform_events(
    settings: Settings, bus: Any | None, revocations: Any | None
) -> Any | None:
    """Forge's bus with its platform events signed, or the bus untouched while signing is off.

    Forge signs with its ``authority`` certificate (the revocation publisher's); without one,
    as a platform service of its own. Mesh events keep the signatures their peers give them.
    """
    if bus is None or not settings.mesh.attestation.required:
        return bus
    signer = (
        revocations.signer
        if revocations is not None
        else await start_platform_signer(
            "forge", settings.mesh.attestation, workload_identity=settings.workload_identity
        )
    )
    if signer is None:
        return bus
    return SigningBus(bus, signer)
