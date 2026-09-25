"""Dynamic adapter and contributor builders for Volundr composition."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from niuu.ports.credentials import CredentialRefreshLockPort
from niuu.ports.http_auth import HttpAuthPort
from niuu.utils import import_class, resolve_secret_kwargs
from volundr.adapters.outbound.contributors.workflow_execution_credentials import (
    WorkflowExecutionCredentialContributor,
)
from volundr.config import Settings
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
from volundr.domain.services.integration_registry import IntegrationRegistry
from volundr.domain.services.oauth_clients import (
    SOURCE_CONFIGURED,
    OAuthClient,
    OAuthClientRegistry,
)
from volundr.domain.services.oauth_token_refresh import OAuthTokenRefreshService
from volundr.domain.services.workflow_execution_credentials import (
    WorkflowExecutionCredentialService,
)
from volundr.ports.workflow_execution_credentials import ExecutionCredentialProjectionPort

logger = logging.getLogger(__name__)


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
    cls = import_class(pm_cfg.adapter)
    kwargs = resolve_secret_kwargs(pm_cfg.kwargs, pm_cfg.secret_kwargs_env)
    kwargs.setdefault("server_public_host", settings.server_public_host)
    kwargs.setdefault("server_host", settings.server_host)
    kwargs.setdefault("server_port", settings.server_port)
    kwargs.setdefault("gateway_endpoint", settings.openshell_gateway_endpoint)
    kwargs.setdefault("gateway_public_url", settings.openshell_gateway_public_url)
    kwargs.setdefault("token_url", settings.openshell_oidc_token_url)
    kwargs.setdefault("client_id", settings.openshell_oidc_client_id)
    if settings.openshell_oidc_client_secret:
        kwargs.setdefault("client_secret", settings.openshell_oidc_client_secret)
    instance = cls(**kwargs)
    logger.info("Pod manager: %s", pm_cfg.adapter.rsplit(".", 1)[-1])
    return instance


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
            RoomRoleSourceContributor(room_role_source=settings.pod_manager.room_role_source)
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
            ModelGatewayContributor(gateway_url=settings.bifrost.session_gateway_url, **ports)
        )
        logger.info("Session contributor: model_gateway (auto-wired)")

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
    from volundr.adapters.outbound.contributors.notification_channels import (
        NotificationChannelContributor,
    )
    from volundr.adapters.outbound.contributors.prompt import PromptContributor

    if not _has_contributor("notification_channels"):
        contributors.append(NotificationChannelContributor(**ports))
        logger.info("Session contributor: notification_channels (auto-wired)")

    persona_provider = ports.get("persona_provider")
    if persona_provider is not None and not _has_contributor("persona"):
        from volundr.adapters.outbound.contributors.persona import PersonaContributor

        contributors.append(PersonaContributor(persona_provider=persona_provider))
        logger.info("Session contributor: persona (auto-wired)")

    contributors.append(PromptContributor())

    # Auto-wire RavnFlockContributor so ravn_flock workloads spawn
    # multi-sidecar sessions (locally via ravn flock init/start).
    from volundr.adapters.outbound.contributors.ravn_flock import RavnFlockContributor
    from volundr.adapters.outbound.contributors.session_mcp import SessionMCPContributor

    if not _has_contributor("ravn_flock"):
        ravn_kwargs = dict(ports)
        if settings.ravn_flock_image:
            ravn_kwargs["ravn_image"] = settings.ravn_flock_image
        if settings.ravn_flock_init_writer_image:
            ravn_kwargs["init_writer_image"] = settings.ravn_flock_init_writer_image
        if settings.ravn_flock_llm_config:
            ravn_kwargs["default_llm_config"] = settings.ravn_flock_llm_config
        contributors.append(RavnFlockContributor(**ravn_kwargs))
        logger.info("Session contributor: ravn_flock (auto-wired)")

    if not _has_contributor("session_mcp"):
        contributors.append(SessionMCPContributor(**ports))
        logger.info("Session contributor: session_mcp (auto-wired)")

    return contributors
