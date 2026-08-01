"""Dynamic adapter and contributor builders for Volundr composition."""

from __future__ import annotations

import logging

from niuu.ports.http_auth import HttpAuthPort
from niuu.utils import import_class, resolve_secret_kwargs
from volundr.config import Settings
from volundr.domain.ports import (
    ArchiveStorePort,
    AuthorizationPort,
    CodexCredentialBrokerPort,
    CredentialEnrollmentRunnerPort,
    CredentialRefreshLockPort,
    CredentialStorePort,
    ExternalSessionProvider,
    GatewayPort,
    PodManager,
    ResidentRuntimeController,
    ResidentSessionController,
    ResourceProvider,
    SecretInjectionPort,
    SessionContributor,
)

logger = logging.getLogger(__name__)


def _create_codex_credential_broker(
    settings: Settings,
    *,
    credential_store: CredentialStorePort,
    refresh_lock: CredentialRefreshLockPort,
) -> CodexCredentialBrokerPort:
    """Create the configured central Codex token broker adapter."""
    config = settings.codex_credential_broker
    cls = import_class(config.adapter)
    kwargs = resolve_secret_kwargs(config.kwargs, config.secret_kwargs_env)
    instance = cls(
        credential_store=credential_store,
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


def _runtime_backend(settings: Settings) -> str:
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
    from volundr.adapters.outbound.contributors.session_def import SessionDefinitionContributor
    from volundr.adapters.outbound.contributors.workload_config import WorkloadConfigContributor
    from volundr.adapters.outbound.contributors.workload_identity import (
        WorkloadIdentityContributor,
    )

    contributors: list[SessionContributor] = []

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
        contributors.append(RavnFlockContributor(**ravn_kwargs))
        logger.info("Session contributor: ravn_flock (auto-wired)")

    if not _has_contributor("session_mcp"):
        contributors.append(SessionMCPContributor(**ports))
        logger.info("Session contributor: session_mcp (auto-wired)")

    return contributors
