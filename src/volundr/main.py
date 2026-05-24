"""Application factory for Volundr API."""

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from importlib.metadata import metadata

from fastapi import FastAPI

from niuu.adapters.inbound.rest_credentials_settings import create_credentials_settings_router
from niuu.adapters.inbound.rest_integrations_settings import create_integrations_settings_router
from niuu.cors import apply_cors_middleware
from niuu.ports.http_auth import HttpAuthPort
from niuu.service_integrations import (
    has_seeded_linear_integration as _has_seeded_linear_integration,
)
from niuu.service_integrations import (
    seed_configured_integrations as _seed_configured_integrations,
)
from niuu.service_integrations import seed_linear_integration as _seed_linear_integration
from niuu.service_runtime import (
    configure_logging,
)
from niuu.service_runtime import (
    create_credential_store as _create_credential_store,
)
from niuu.service_runtime import create_identity_adapter as _create_identity_adapter
from niuu.service_runtime import create_pat_validator as _create_pat_validator
from niuu.service_runtime import create_storage_adapter as _create_storage_adapter
from niuu.service_runtime import release_credential_store as _release_credential_store
from niuu.service_settings import Settings
from niuu.utils import import_class, resolve_secret_kwargs
from sleipnir.adapters.audit_postgres import PostgresAuditRepository
from sleipnir.adapters.audit_subscriber import AuditSubscriber
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.inbound.rest_admin_settings import create_admin_settings_router
from volundr.adapters.inbound.rest_audit import (
    create_audit_router,
    create_canonical_audit_router,
)
from volundr.adapters.inbound.rest_credentials import create_canonical_credentials_router
from volundr.adapters.inbound.rest_events import create_events_router
from volundr.adapters.inbound.rest_git import create_git_router
from volundr.adapters.inbound.rest_integrations import create_canonical_integrations_router
from volundr.adapters.inbound.rest_issues import create_canonical_issues_router
from volundr.adapters.inbound.rest_oauth import create_canonical_oauth_router
from volundr.adapters.inbound.rest_presets import create_presets_router
from volundr.adapters.inbound.rest_profiles import create_profiles_router
from volundr.adapters.inbound.rest_prompts import create_prompts_router
from volundr.adapters.inbound.rest_resources import create_resources_router
from volundr.adapters.inbound.rest_secrets import create_canonical_secrets_router
from volundr.adapters.inbound.rest_trace import create_trace_router
from volundr.adapters.inbound.rest_tracker import create_canonical_tracker_router
from volundr.adapters.outbound.bifrost_catalog_http import HttpBifrostCatalogAdapter
from volundr.adapters.outbound.broadcaster import InMemoryEventBroadcaster
from volundr.adapters.outbound.config_mcp_servers import ConfigMCPServerProvider
from volundr.adapters.outbound.config_profiles import ConfigProfileProvider
from volundr.adapters.outbound.config_templates import ConfigTemplateProvider
from volundr.adapters.outbound.git_registry import create_git_registry
from volundr.adapters.outbound.linear import LinearAdapter
from volundr.adapters.outbound.memory_secrets import InMemorySecretManager
from volundr.adapters.outbound.pg_event_sink import PostgresEventSink
from volundr.adapters.outbound.postgres import PostgresSessionRepository
from volundr.adapters.outbound.postgres_chronicles import PostgresChronicleRepository
from volundr.adapters.outbound.postgres_communication_cursors import (
    PostgresCommunicationCursorRepository,
)
from volundr.adapters.outbound.postgres_communication_routes import (
    PostgresCommunicationRouteRepository,
)
from volundr.adapters.outbound.postgres_integrations import PostgresIntegrationRepository
from volundr.adapters.outbound.postgres_mappings import PostgresMappingRepository
from volundr.adapters.outbound.postgres_presets import PostgresPresetRepository
from volundr.adapters.outbound.postgres_prompts import PostgresPromptRepository
from volundr.adapters.outbound.postgres_spans import PostgresSpanRepository
from volundr.adapters.outbound.postgres_stats import PostgresStatsRepository
from volundr.adapters.outbound.postgres_tenants import PostgresTenantRepository
from volundr.adapters.outbound.postgres_timeline import PostgresTimelineRepository
from volundr.adapters.outbound.postgres_tokens import PostgresTokenTracker
from volundr.adapters.outbound.postgres_users import PostgresUserRepository
from volundr.adapters.outbound.pricing import HardcodedPricingProvider
from volundr.adapters.outbound.skuld_room import SkuldRoomAdapter
from volundr.domain.ports import SessionContributor
from volundr.domain.services import (
    ChronicleService,
    GitWorkflowService,
    PresetService,
    PromptService,
    RepoService,
    SessionArchiveService,
    SessionService,
    StatsService,
    TenantService,
    TokenService,
)
from volundr.domain.services.communication_ingress import CommunicationIngressService
from volundr.domain.services.credential import CredentialService
from volundr.domain.services.event_ingestion import EventIngestionService
from volundr.domain.services.mount_strategies import SecretMountStrategyRegistry
from volundr.domain.services.profile import ForgeProfileService
from volundr.domain.services.telegram_ingress import TelegramIngressService
from volundr.domain.services.template import WorkspaceTemplateService
from volundr.domain.services.tracker import TrackerService
from volundr.domain.services.tracker_factory import TrackerFactory
from volundr.domain.services.workspace import WorkspaceService
from volundr.infrastructure.database import database_pool

# Interval for periodic stats and heartbeat broadcasts (seconds)
BROADCAST_INTERVAL = 30

logger = logging.getLogger(__name__)


async def _load_bifrost_catalog(
    pricing_provider: HardcodedPricingProvider,
    bifrost_catalog: HttpBifrostCatalogAdapter,
) -> None:
    delay_seconds = 0.1
    while True:
        try:
            models = await bifrost_catalog.list_models()
            pricing_provider.replace_models(models)
            logger.info("Loaded %s model(s) from configured Bifrost catalog", len(models))
            return
        except Exception:
            logger.warning(
                "Bifrost catalog not ready yet at %s; retrying in %.1fs",
                bifrost_catalog._base_url,  # noqa: SLF001
                delay_seconds,
                exc_info=True,
            )
            await asyncio.sleep(delay_seconds)
            delay_seconds = min(delay_seconds * 2, 5.0)


def _create_pod_manager(settings: Settings) -> "PodManager":  # noqa: F821
    """Create the PodManager adapter from dynamic config."""
    pm_cfg = settings.pod_manager
    cls = import_class(pm_cfg.adapter)
    kwargs = resolve_secret_kwargs(pm_cfg.kwargs, pm_cfg.secret_kwargs_env)
    instance = cls(**kwargs)
    logger.info("Pod manager: %s", pm_cfg.adapter.rsplit(".", 1)[-1])
    return instance


def _create_authorization_adapter(settings: Settings) -> "AuthorizationPort":  # noqa: F821
    """Create the AuthorizationPort adapter from dynamic config."""
    az_cfg = settings.authorization
    cls = import_class(az_cfg.adapter)
    kwargs = resolve_secret_kwargs(az_cfg.kwargs, az_cfg.secret_kwargs_env)
    instance = cls(**kwargs)
    logger.info("Authorization adapter: %s", az_cfg.adapter.rsplit(".", 1)[-1])
    return instance


def _create_gateway_adapter(settings: Settings) -> "GatewayPort":  # noqa: F821
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


def _create_secret_injection_adapter(settings: Settings) -> "SecretInjectionPort":  # noqa: F821
    """Create the SecretInjectionPort adapter from dynamic config."""
    si_cfg = settings.secret_injection
    cls = import_class(si_cfg.adapter)
    kwargs = resolve_secret_kwargs(si_cfg.kwargs, si_cfg.secret_kwargs_env)
    instance = cls(**kwargs)
    logger.info("Secret injection: %s", si_cfg.adapter.rsplit(".", 1)[-1])
    return instance


def _create_resource_provider(settings: Settings) -> "ResourceProvider":  # noqa: F821
    """Create the ResourceProvider adapter from dynamic config."""
    rp_cfg = settings.resource_provider
    cls = import_class(rp_cfg.adapter)
    kwargs = resolve_secret_kwargs(rp_cfg.kwargs, rp_cfg.secret_kwargs_env)
    instance = cls(**kwargs)
    logger.info("Resource provider: %s", rp_cfg.adapter.rsplit(".", 1)[-1])
    return instance

def _create_archive_store(settings: Settings) -> "ArchiveStorePort":  # noqa: F821
    """Create the ArchiveStorePort adapter from dynamic config."""
    as_cfg = settings.archive_store
    cls = import_class(as_cfg.adapter)
    kwargs = resolve_secret_kwargs(as_cfg.kwargs, as_cfg.secret_kwargs_env)
    instance = cls(**kwargs)
    logger.info("Archive store: %s", as_cfg.adapter.rsplit(".", 1)[-1])
    return instance


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

    contributors.append(PromptContributor())

    # Auto-wire RavnFlockContributor so ravn_flock workloads spawn
    # multi-sidecar sessions (locally via ravn flock init/start).
    from volundr.adapters.outbound.contributors.ravn_flock import RavnFlockContributor
    from volundr.adapters.outbound.contributors.session_mcp import SessionMCPContributor

    if not _has_contributor("ravn_flock"):
        contributors.append(RavnFlockContributor(**ports))
        logger.info("Session contributor: ravn_flock (auto-wired)")

    if not _has_contributor("session_mcp"):
        contributors.append(SessionMCPContributor(**ports))
        logger.info("Session contributor: session_mcp (auto-wired)")

    return contributors


async def _broadcast_periodic_updates(
    broadcaster: InMemoryEventBroadcaster,
    stats_service: StatsService,
) -> None:
    """Background task to broadcast periodic stats and heartbeat updates.

    Args:
        broadcaster: The event broadcaster to publish events to.
        stats_service: The stats service to fetch current statistics.
    """
    logger.info("SSE periodic broadcast task started, interval=%ds", BROADCAST_INTERVAL)
    while True:
        try:
            await asyncio.sleep(BROADCAST_INTERVAL)

            # Only broadcast if there are subscribers
            sub_count = broadcaster.subscriber_count
            if sub_count == 0:
                logger.debug("SSE periodic: no subscribers, skipping broadcast")
                continue

            # Broadcast current stats
            logger.info("SSE periodic: broadcasting stats to %d subscriber(s)", sub_count)
            stats = await stats_service.get_stats()
            logger.info(
                "SSE periodic: stats fetched - tokens_today=%d, cloud=%d, local=%d, cost=%.4f",
                stats.tokens_today,
                stats.cloud_tokens,
                stats.local_tokens,
                float(stats.cost_today),
            )
            await broadcaster.publish_stats(stats)

            # Broadcast heartbeat
            await broadcaster.publish_heartbeat()
            logger.debug("SSE periodic: heartbeat sent")

        except asyncio.CancelledError:
            logger.info("SSE periodic broadcast task cancelled")
            break
        except Exception:
            logger.exception("SSE periodic broadcast failed")


def _create_otel_providers(otel_cfg):  # pragma: no cover
    """Build OTel TracerProvider + MeterProvider from config.

    Only called when otel is enabled and the SDK is installed.
    """
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": otel_cfg.service_name})

    # Traces
    span_exporter = OTLPSpanExporter(
        endpoint=otel_cfg.endpoint,
        insecure=otel_cfg.insecure,
    )
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))

    # Metrics
    metric_exporter = OTLPMetricExporter(
        endpoint=otel_cfg.endpoint,
        insecure=otel_cfg.insecure,
    )
    metric_reader = PeriodicExportingMetricReader(metric_exporter)
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[metric_reader],
    )

    return tracer_provider, meter_provider


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        settings: Application settings. If None, uses Settings() which
                  automatically loads from YAML + env vars.
    """
    if settings is None:
        settings = Settings()

    # Configure logging from settings
    configure_logging(settings.logging)

    _meta = metadata("volundr")

    app = FastAPI(
        title="Volundr",
        description=_meta["Summary"],
        version=_meta["Version"],
        openapi_tags=[
            {
                "name": "Sessions",
                "description": "Session lifecycle management — create, start, stop, "
                "delete sessions and report token usage.",
            },
            {
                "name": "Chronicles",
                "description": "Session history records — snapshots of completed or "
                "in-progress sessions, reforge chains, and broker reports.",
            },
            {
                "name": "Timeline",
                "description": "Granular event timelines within a chronicle — "
                "messages, file edits, git commits, and terminal activity.",
            },
            {
                "name": "Models & Stats",
                "description": "Available LLM models and aggregate usage statistics.",
            },
            {
                "name": "Repositories",
                "description": "Git providers and repository discovery.",
            },
            {
                "name": "Profiles",
                "description": "Forge profiles — resource and workload configuration "
                "presets (read-only, config-driven).",
            },
            {
                "name": "Templates",
                "description": "Workspace templates — multi-repo workspace layouts "
                "with setup scripts (read-only, config-driven).",
            },
            {
                "name": "Git Workflow",
                "description": "Git workflow operations — create PRs from sessions, "
                "merge, check CI status, and calculate merge confidence.",
            },
            {
                "name": "MCP Servers",
                "description": "Available MCP server configurations for session setup.",
            },
            {
                "name": "Secrets",
                "description": "Kubernetes secret management — list and create "
                "mountable secrets for sessions.",
            },
            {
                "name": "Presets",
                "description": "Runtime configuration presets — portable, DB-stored "
                "bundles of model, MCP servers, resources, and environment config.",
            },
            {
                "name": "Issue Tracker",
                "description": "External issue tracker integration — search issues, "
                "update status, and manage repo-to-project mappings.",
            },
        ],
    )

    # Store settings for lifespan access
    app.state.settings = settings
    app.state.admin_settings = {
        "storage": {"home_enabled": True},
    }

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """Manage application lifecycle."""
        settings = app.state.settings
        audit_subscriber: AuditSubscriber | None = None

        async with database_pool(settings.database) as pool:
            # Identity & authorization adapters (dynamic adapter pattern)
            tenant_repository = PostgresTenantRepository(pool)
            user_repository = PostgresUserRepository(pool)
            tenant_service = TenantService(tenant_repository, user_repository)

            resource_provider = _create_resource_provider(settings)
            storage_adapter = _create_storage_adapter(settings)
            identity_adapter = _create_identity_adapter(
                settings,
                user_repository,
                storage=storage_adapter,
                tenant_service=tenant_service,
            )
            authorization_adapter = _create_authorization_adapter(settings)

            # Store identity/authz on app.state for auth dependencies
            app.state.identity = identity_adapter
            app.state.authorization = authorization_adapter

            # Tenant service + ensure default tenant exists
            await tenant_service.ensure_default_tenant()

            # Create adapters
            repository = PostgresSessionRepository(pool)
            communication_route_repository = PostgresCommunicationRouteRepository(pool)
            communication_cursor_repository = PostgresCommunicationCursorRepository(pool)
            stats_repository = PostgresStatsRepository(pool)
            token_tracker = PostgresTokenTracker(pool)
            from ravn.adapters.personas.postgres_registry import PostgresPersonaRegistry

            persona_registry = PostgresPersonaRegistry(pool)
            app.state.persona_registry = persona_registry
            pod_manager = _create_pod_manager(settings)

            # Inject Skuld port registry for mini mode proxy routing
            try:
                from cli.server import get_skuld_registry

                skuld_reg = get_skuld_registry()
                if skuld_reg is not None and hasattr(pod_manager, "set_skuld_registry"):
                    pod_manager.set_skuld_registry(skuld_reg)
            except ImportError:
                pass  # Not running via CLI

            gateway_adapter = _create_gateway_adapter(settings)
            bifrost_auth = _create_http_auth_adapter(settings.bifrost.auth)
            bifrost_catalog = HttpBifrostCatalogAdapter(
                base_url=settings.bifrost.url,
                auth=bifrost_auth,
                timeout_seconds=settings.bifrost.timeout_seconds,
            )
            pricing_provider = HardcodedPricingProvider()
            bifrost_catalog_task = asyncio.create_task(
                _load_bifrost_catalog(
                    pricing_provider,
                    bifrost_catalog,
                )
            )
            git_registry = create_git_registry(settings.git)

            # Sleipnir integration (optional — enabled via sleipnir.enabled config)
            sleipnir_bus = None
            if settings.sleipnir.enabled:
                try:
                    sl_cls = import_class(settings.sleipnir.adapter)
                    sleipnir_bus = sl_cls(**settings.sleipnir.kwargs)
                    logger.info(
                        "Sleipnir integration enabled: adapter=%s",
                        settings.sleipnir.adapter.rsplit(".", 1)[-1],
                    )
                except Exception:
                    logger.exception("Failed to initialise Sleipnir integration")
                    sleipnir_bus = None

            broadcaster = InMemoryEventBroadcaster(
                sleipnir_publisher=sleipnir_bus,
            )

            # Create services with broadcaster for real-time updates
            # Create profile and template adapters (config-driven)
            profile_provider = ConfigProfileProvider(settings.profiles)
            template_provider = ConfigTemplateProvider(settings.templates)

            # Create shared adapters used by both contributors and credential routes
            secret_injection = _create_secret_injection_adapter(settings)

            # Credential store (pluggable: memory, Vault, Infisical)
            credential_store = _create_credential_store(settings)
            credential_service = CredentialService(
                store=credential_store,
                strategies=SecretMountStrategyRegistry(),
            )
            mcp_provider = ConfigMCPServerProvider(settings.mcp_servers)
            secret_manager = InMemorySecretManager()

            # Inject credential store into pod manager for envSecrets resolution
            if hasattr(pod_manager, "set_credential_store"):
                pod_manager.set_credential_store(credential_store)
            if hasattr(pod_manager, "set_persona_registry"):
                pod_manager.set_persona_registry(persona_registry)

            # Integration registry + repository
            from volundr.domain.services.integration_registry import (
                IntegrationRegistry,
                definitions_from_config,
            )

            integration_definitions = definitions_from_config(
                [d.model_dump() for d in settings.integrations.definitions],
            )
            integration_registry = IntegrationRegistry(integration_definitions)
            integration_repo = PostgresIntegrationRepository(pool)
            mapping_repository = PostgresMappingRepository(pool)
            tracker_factory = TrackerFactory(credential_store)
            default_tracker = (
                LinearAdapter(api_key=settings.linear.api_key)
                if settings.linear.enabled and settings.linear.api_key
                else None
            )

            # User integration service — ephemeral per-user provider factory.
            from volundr.domain.services.user_integration import UserIntegrationService

            user_integration_service = UserIntegrationService(
                shared_git_providers=git_registry.providers,
                integration_repo=integration_repo,
                integration_registry=integration_registry,
                credential_store=credential_store,
            )
            session_room_port = SkuldRoomAdapter(repository)
            communication_ingress = CommunicationIngressService(
                route_repository=communication_route_repository,
                room_port=session_room_port,
            )
            telegram_ingress = TelegramIngressService(
                integration_repo=integration_repo,
                credential_store=credential_store,
                communication_ingress=communication_ingress,
                cursor_repository=communication_cursor_repository,
            )

            # Create session contributors (dynamic adapter pattern)
            mount_strategies = SecretMountStrategyRegistry()
            contributors = _create_contributors(
                settings,
                template_provider=template_provider,
                profile_provider=profile_provider,
                git_registry=git_registry,
                storage=storage_adapter,
                admin_settings=app.state.admin_settings,
                gateway=gateway_adapter,
                secret_injection=secret_injection,
                credential_store=credential_store,
                mount_strategies=mount_strategies,
                integration_repo=integration_repo,
                integration_registry=integration_registry,
                user_integration=user_integration_service,
                resource_provider=resource_provider,
            )

            session_service = SessionService(
                repository,
                pod_manager,
                git_registry=git_registry,
                validate_repos=settings.git.validate_on_create,
                broadcaster=broadcaster,
                template_provider=template_provider,
                authorization=authorization_adapter,
                contributors=contributors if contributors else None,
                provisioning_timeout=settings.provisioning.timeout_seconds,
                provisioning_initial_delay=settings.provisioning.initial_delay_seconds,
                integration_repo=integration_repo,
                storage=storage_adapter,
                communication_route_repository=communication_route_repository,
                session_communication_port=session_room_port,
            )
            stats_service = StatsService(stats_repository)
            token_service = TokenService(
                token_tracker, repository, pricing_provider, broadcaster=broadcaster
            )
            repo_service = RepoService(
                git_registry,
                user_integration=user_integration_service,
            )

            chronicle_repository = PostgresChronicleRepository(pool)
            timeline_repository = PostgresTimelineRepository(pool)
            chronicle_service = ChronicleService(
                chronicle_repository,
                session_service,
                broadcaster=broadcaster,
                timeline_repository=timeline_repository,
            )
            archive_store = _create_archive_store(settings)
            archive_service = SessionArchiveService(
                session_service,
                storage_adapter,
                archive_store,
                chronicle_service=chronicle_service,
            )
            app.state.archive_service = archive_service

            # Create profile and template services (providers already created above)
            profile_service = ForgeProfileService(profile_provider, session_repository=repository)
            template_service = WorkspaceTemplateService(template_provider)
            tracker_service = TrackerService(
                default_tracker,
                mapping_repository,
                integration_repo=integration_repo,
                tracker_factory=tracker_factory,
            )

            # Create git workflow service (PRs sourced from GitHub/GitLab)
            git_workflow_service = GitWorkflowService(
                git_registry=git_registry,
                chronicle_repository=chronicle_repository,
                session_repository=repository,
                broadcaster=broadcaster,
                workflow_config=settings.git.workflow,
            )

            # Create and include routers
            forge_router = create_router(
                session_service,
                stats_service,
                token_service,
                pricing_provider,
                broadcaster=broadcaster,
                repo_service=repo_service,
                chronicle_service=chronicle_service,
                archive_service=archive_service,
                prefix="/api/v1/forge",
            )
            app.include_router(forge_router)

            profiles_router = create_profiles_router(
                profile_service,
                template_service,
                settings.session_definitions,
                prefix="/api/v1/forge",
            )
            app.include_router(profiles_router)

            # Resource discovery endpoint
            resources_router = create_resources_router(
                resource_provider,
                prefix="/api/v1/forge",
            )
            app.include_router(resources_router)
            app.state.resource_provider = resource_provider

            # Saved prompts
            prompt_repository = PostgresPromptRepository(pool)
            prompt_service = PromptService(prompt_repository)
            prompts_router = create_prompts_router(
                prompt_service,
                prefix="/api/v1/forge",
            )
            app.include_router(prompts_router)

            app.include_router(create_credentials_settings_router())
            app.include_router(create_canonical_credentials_router(credential_service))
            app.include_router(create_canonical_secrets_router(mcp_provider, secret_manager))

            app.include_router(create_integrations_settings_router())
            app.include_router(
                create_canonical_integrations_router(
                    integration_repo,
                    tracker_factory,
                    registry=integration_registry,
                    credential_store=credential_store,
                )
            )
            app.include_router(
                create_canonical_oauth_router(
                    oauth_config=settings.oauth,
                    integration_registry=integration_registry,
                    credential_store=credential_store,
                    integration_repo=integration_repo,
                )
            )
            app.include_router(create_canonical_tracker_router(tracker_service=tracker_service))
            app.include_router(create_canonical_issues_router(integration_repo, tracker_factory))

            # Presets (DB-stored runtime config)
            preset_repository = PostgresPresetRepository(pool)
            preset_service = PresetService(preset_repository)
            presets_router = create_presets_router(
                preset_service,
                prefix="/api/v1/forge",
            )
            app.include_router(presets_router)

            from volundr.adapters.outbound.postgres_pats import PostgresPATRepository

            pat_repository = PostgresPATRepository(pool)
            pat_validator = _create_pat_validator(settings, pat_repository)
            app.state.pat_validator = pat_validator

            git_router = create_git_router(
                git_workflow_service,
                prefix="/api/v1/forge",
            )
            app.include_router(git_router)

            # Local git workspace endpoints (mini/local mode)
            from volundr.adapters.inbound.rest_local_git import create_local_git_router
            from volundr.adapters.outbound.local_git import LocalGitService

            local_git_service = LocalGitService(
                subprocess_timeout=settings.local_git.subprocess_timeout,
            )
            local_git_router = create_local_git_router(
                local_git_service,
                session_repository=repository,
                prefix="/api/v1/forge",
            )
            app.include_router(local_git_router)
            app.state.local_git_service = local_git_service

            # Admin settings (config-driven, runtime-toggleable)
            admin_settings_router = create_admin_settings_router()
            app.include_router(admin_settings_router)

            # Workspace management — PVCs are the source of truth
            workspace_service = WorkspaceService(storage_adapter)
            app.state.workspace_service = workspace_service

            if settings.integrations.seed_connections:
                await _seed_configured_integrations(
                    integration_repo=integration_repo,
                    credential_store=credential_store,
                    settings=settings,
                )
                logger.info(
                    "Seeded %d integration connection(s) from config",
                    len(settings.integrations.seed_connections),
                )

            # Seed Linear integration from config so the integration-based
            # endpoints (/issues/search) find it in the DB.
            if (
                settings.linear.enabled
                and settings.linear.api_key
                and not _has_seeded_linear_integration(settings)
            ):
                await _seed_linear_integration(
                    integration_repo,
                    credential_store,
                    api_key=settings.linear.api_key,
                )
                logger.info("Linear integration seeded from config")
            app.state.user_integration_service = user_integration_service
            app.state.communication_route_repository = communication_route_repository
            app.state.communication_cursor_repository = communication_cursor_repository
            app.state.session_room_port = session_room_port
            app.state.communication_ingress = communication_ingress
            app.state.telegram_ingress = telegram_ingress

            audit_repository = PostgresAuditRepository(pool)
            if settings.sleipnir.enabled and sleipnir_bus is not None:
                try:
                    audit_subscriber = AuditSubscriber(sleipnir_bus, audit_repository)
                    await audit_subscriber.start()
                except Exception:
                    logger.exception("Failed to start audit subscriber")
            app.include_router(create_canonical_audit_router(audit_repository))
            app.include_router(create_audit_router(audit_repository))

            # Event pipeline: sinks + ingestion service + REST endpoints
            pg_event_sink = PostgresEventSink(
                pool, buffer_size=settings.event_pipeline.postgres_buffer_size
            )
            span_repository = PostgresSpanRepository(pool)
            event_sinks: list = [pg_event_sink]

            # Optional: RabbitMQ sink
            rabbitmq_sink = None
            if settings.event_pipeline.rabbitmq.enabled:
                try:
                    from volundr.adapters.outbound.rabbitmq_event_sink import (
                        RabbitMQEventSink,
                    )

                    rmq_cfg = settings.event_pipeline.rabbitmq
                    rabbitmq_sink = RabbitMQEventSink(
                        url=rmq_cfg.url,
                        exchange_name=rmq_cfg.exchange_name,
                        exchange_type=rmq_cfg.exchange_type,
                    )
                    await rabbitmq_sink.connect()
                    event_sinks.append(rabbitmq_sink)
                    logger.info("RabbitMQ event sink enabled")
                except ImportError:
                    logger.warning(
                        "RabbitMQ sink enabled but aio-pika not installed. "
                        "Install with: pip install volundr[rabbitmq]"
                    )
                except Exception:
                    logger.exception("Failed to connect RabbitMQ event sink")

            # Optional: OTel sink (GenAI semantic conventions)
            otel_sink = None
            if settings.event_pipeline.otel.enabled:
                try:
                    from volundr.adapters.outbound.otel_event_sink import (
                        OtelEventSink,
                    )

                    otel_cfg = settings.event_pipeline.otel
                    tp, mp = _create_otel_providers(otel_cfg)
                    otel_sink = OtelEventSink(
                        tracer_provider=tp,
                        meter_provider=mp,
                        service_name=otel_cfg.service_name,
                        provider_name=otel_cfg.provider_name,
                    )
                    event_sinks.append(otel_sink)
                    logger.info(
                        "OTel event sink enabled (endpoint=%s)",
                        otel_cfg.endpoint,
                    )
                except ImportError:
                    logger.warning(
                        "OTel sink enabled but opentelemetry not installed. "
                        "Install with: pip install volundr[otel]"
                    )
                except Exception:
                    logger.exception("Failed to initialize OTel event sink")

            # Register Sleipnir event sink when integration is active
            if sleipnir_bus is not None:
                from volundr.adapters.outbound.sleipnir_event_sink import (  # noqa: PLC0415
                    SleipnirEventSink,
                )

                event_sinks.append(SleipnirEventSink(sleipnir_bus))
                logger.info("Sleipnir event sink registered in pipeline")

            event_ingestion = EventIngestionService(sinks=event_sinks)
            events_router = create_events_router(
                event_ingestion,
                pg_event_sink,
                session_service=session_service,
                prefix="/api/v1/forge",
            )
            app.include_router(events_router)
            trace_router = create_trace_router(
                span_repository,
                session_service=session_service,
                prefix="/api/v1/forge",
            )
            app.include_router(trace_router)

            # GitHub webhook ingestion
            from volundr.adapters.inbound.rest_webhooks import create_webhooks_router

            webhooks_router = create_webhooks_router(
                publisher=sleipnir_bus,
                config=settings.webhooks.github,
            )
            app.include_router(webhooks_router)

            # Store for access in routes if needed
            app.state.session_service = session_service
            app.state.stats_service = stats_service
            app.state.token_service = token_service
            app.state.pod_manager = pod_manager
            app.state.pricing_provider = pricing_provider
            app.state.git_registry = git_registry
            app.state.broadcaster = broadcaster
            app.state.chronicle_service = chronicle_service
            app.state.profile_service = profile_service
            app.state.template_service = template_service
            app.state.git_workflow_service = git_workflow_service
            app.state.event_ingestion = event_ingestion
            app.state.tenant_service = tenant_service
            app.state.gateway = gateway_adapter
            app.state.user_repository = user_repository
            app.state.tenant_repository = tenant_repository
            app.state.secret_injection = secret_injection
            app.state.storage = storage_adapter

            # Start background task for periodic stats and heartbeat broadcasts
            background_task = asyncio.create_task(
                _broadcast_periodic_updates(broadcaster, stats_service)
            )
            if settings.telegram_ingress.enabled:
                await telegram_ingress.start()
            else:
                logger.info(
                    "Volundr Telegram ingress disabled via config (telegram_ingress.enabled=false)"
                )

            # Reconcile sessions stuck in PROVISIONING after a restart
            await session_service.reconcile_provisioning_sessions()
            await session_service.reconcile_active_sessions()

            try:
                yield
            finally:
                if bifrost_catalog_task is not None:
                    bifrost_catalog_task.cancel()
                    await asyncio.gather(bifrost_catalog_task, return_exceptions=True)
                await telegram_ingress.stop()
                background_task.cancel()
                try:
                    await background_task
                except asyncio.CancelledError:
                    pass  # Expected: task cancellation during shutdown
                await event_ingestion.close_all()
                if hasattr(pod_manager, "close"):
                    await pod_manager.close()
                if hasattr(gateway_adapter, "close"):
                    await gateway_adapter.close()
                await git_registry.close()
                if audit_subscriber is not None:
                    await audit_subscriber.stop()
                _release_credential_store(settings)

    app.router.lifespan_context = lifespan

    apply_cors_middleware(app, settings.cors)

    # PAT revocation enforcement
    from niuu.adapters.pat_revocation_middleware import PATRevocationMiddleware

    app.add_middleware(PATRevocationMiddleware)

    @app.get("/health", tags=["Health"])
    @app.get("/api/v1/forge/health", include_in_schema=False)
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "healthy"}

    return app


# Default app instance for uvicorn
app = create_app()


def main() -> None:
    """Run the Volundr API server."""
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    workers = int(os.environ.get("WORKERS", "4"))

    uvicorn.run(
        "volundr.main:app",
        host=host,
        port=port,
        workers=workers,
        access_log=False,
    )


if __name__ == "__main__":
    main()
