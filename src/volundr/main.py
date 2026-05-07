"""Application factory for Volundr API."""

import asyncio
import logging
import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.metadata import metadata
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from fastapi import FastAPI

from niuu.cors import apply_cors_middleware
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.inbound.rest_admin_settings import create_admin_settings_router
from volundr.adapters.inbound.rest_audit import create_canonical_audit_router
from volundr.adapters.inbound.rest_credentials import (
    create_canonical_credentials_router,
)
from volundr.adapters.inbound.rest_events import create_events_router
from volundr.adapters.inbound.rest_features import (
    create_feature_catalog_router,
)
from volundr.adapters.inbound.rest_git import create_git_router
from volundr.adapters.inbound.rest_integrations import (
    create_canonical_integrations_router,
)
from volundr.adapters.inbound.rest_issues import (
    create_canonical_issues_router,
)
from volundr.adapters.inbound.rest_oauth import create_canonical_oauth_router
from volundr.adapters.inbound.rest_presets import create_presets_router
from volundr.adapters.inbound.rest_profiles import create_profiles_router
from volundr.adapters.inbound.rest_prompts import create_prompts_router
from volundr.adapters.inbound.rest_resources import create_resources_router
from volundr.adapters.inbound.rest_secrets import (
    create_canonical_secrets_router,
)
from volundr.adapters.inbound.rest_tenants import create_identity_router, create_tenants_router
from volundr.adapters.inbound.rest_tracker import create_canonical_tracker_router
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
from volundr.adapters.outbound.postgres_stats import PostgresStatsRepository
from volundr.adapters.outbound.postgres_tenants import PostgresTenantRepository
from volundr.adapters.outbound.postgres_timeline import PostgresTimelineRepository
from volundr.adapters.outbound.postgres_tokens import PostgresTokenTracker
from volundr.adapters.outbound.postgres_users import PostgresUserRepository
from volundr.adapters.outbound.pricing import HardcodedPricingProvider
from volundr.adapters.outbound.skuld_room import SkuldRoomAdapter
from volundr.config import LoggingConfig, Settings
from volundr.domain.ports import SessionContributor
from volundr.domain.services import (
    ChronicleService,
    GitWorkflowService,
    PresetService,
    PromptService,
    RepoService,
    SessionService,
    StatsService,
    TenantService,
    TokenService,
    TrackerService,
)
from volundr.domain.services.communication_ingress import CommunicationIngressService
from volundr.domain.services.event_ingestion import EventIngestionService
from volundr.domain.services.feature import FeatureService
from volundr.domain.services.profile import ForgeProfileService
from volundr.domain.services.telegram_ingress import TelegramIngressService
from volundr.domain.services.template import WorkspaceTemplateService
from volundr.domain.services.workspace import WorkspaceService
from volundr.infrastructure.database import database_pool
from volundr.utils import import_class

# Interval for periodic stats and heartbeat broadcasts (seconds)
BROADCAST_INTERVAL = 30

logger = logging.getLogger(__name__)


def configure_logging(config: LoggingConfig | None = None) -> None:
    """Configure logging from settings.

    Args:
        config: Logging configuration. If None, uses defaults from
            LoggingConfig which reads from LOG_LEVEL and LOG_FORMAT
            environment variables.
    """
    if config is None:
        config = LoggingConfig()

    level_name = config.level.upper()
    log_format = config.format.lower()

    # Map level name to logging constant
    level = getattr(logging, level_name, logging.INFO)

    # Choose format based on config
    if log_format == "json":
        # Simple JSON-ish format for structured logging
        fmt = (
            '{"time":"%(asctime)s","level":"%(levelname)s",'
            '"logger":"%(name)s","message":"%(message)s"}'
        )
    else:
        fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # Configure root logger — set level directly to avoid uvicorn overriding
    logging.basicConfig(
        level=level,
        format=fmt,
        stream=sys.stderr,
        force=True,
    )
    logging.getLogger().setLevel(level)

    logging.getLogger(__name__).info(
        "Logging configured: level=%s, format=%s",
        level_name,
        log_format,
    )


def _resolve_secret_kwargs(
    kwargs: dict[str, Any],
    secret_kwargs_env: dict[str, str],
) -> dict[str, Any]:
    """Merge secret kwargs from environment variables into adapter kwargs.

    secret_kwargs_env maps kwarg names to env var names. Values from env
    vars override any same-named keys already in kwargs.
    """
    if not secret_kwargs_env:
        return kwargs
    resolved = dict(kwargs)
    for kwarg_name, env_var in secret_kwargs_env.items():
        value = os.environ.get(env_var)
        if value is not None:
            resolved[kwarg_name] = value
    return resolved


def _create_pod_manager(settings: Settings) -> "PodManager":  # noqa: F821
    """Create the PodManager adapter from dynamic config."""
    pm_cfg = settings.pod_manager
    cls = import_class(pm_cfg.adapter)
    kwargs = _resolve_secret_kwargs(pm_cfg.kwargs, pm_cfg.secret_kwargs_env)
    instance = cls(**kwargs)
    logger.info("Pod manager: %s", pm_cfg.adapter.rsplit(".", 1)[-1])
    return instance


def _create_identity_adapter(
    settings: Settings,
    user_repository,
    storage=None,
) -> "IdentityPort":  # noqa: F821
    """Create the IdentityPort adapter from dynamic config."""
    id_cfg = settings.identity
    kwargs = _resolve_secret_kwargs(id_cfg.kwargs, id_cfg.secret_kwargs_env)
    kwargs = dict(kwargs)
    kwargs["user_repository"] = user_repository
    kwargs["role_mapping"] = settings.identity.role_mapping
    if storage is not None:
        kwargs["storage"] = storage
    cls = import_class(id_cfg.adapter)
    instance = cls(**kwargs)
    logger.info("Identity adapter: %s", id_cfg.adapter.rsplit(".", 1)[-1])
    return instance


def _create_authorization_adapter(settings: Settings) -> "AuthorizationPort":  # noqa: F821
    """Create the AuthorizationPort adapter from dynamic config."""
    az_cfg = settings.authorization
    cls = import_class(az_cfg.adapter)
    kwargs = _resolve_secret_kwargs(az_cfg.kwargs, az_cfg.secret_kwargs_env)
    instance = cls(**kwargs)
    logger.info("Authorization adapter: %s", az_cfg.adapter.rsplit(".", 1)[-1])
    return instance


def _create_credential_store(settings: Settings) -> "CredentialStorePort":  # noqa: F821
    """Create the CredentialStorePort adapter from dynamic config."""
    cs_cfg = settings.credential_store
    cls = import_class(cs_cfg.adapter)
    kwargs = _resolve_secret_kwargs(cs_cfg.kwargs, cs_cfg.secret_kwargs_env)
    instance = cls(**kwargs)
    logger.info("Credential store: %s", cs_cfg.adapter.rsplit(".", 1)[-1])
    return instance


def _create_gateway_adapter(settings: Settings) -> "GatewayPort":  # noqa: F821
    """Create the GatewayPort adapter from dynamic config."""
    gw_cfg = settings.gateway
    cls = import_class(gw_cfg.adapter)
    kwargs = _resolve_secret_kwargs(gw_cfg.kwargs, gw_cfg.secret_kwargs_env)
    instance = cls(**kwargs)
    logger.info("Gateway adapter: %s", gw_cfg.adapter.rsplit(".", 1)[-1])
    return instance


def _create_secret_injection_adapter(settings: Settings) -> "SecretInjectionPort":  # noqa: F821
    """Create the SecretInjectionPort adapter from dynamic config."""
    si_cfg = settings.secret_injection
    cls = import_class(si_cfg.adapter)
    kwargs = _resolve_secret_kwargs(si_cfg.kwargs, si_cfg.secret_kwargs_env)
    instance = cls(**kwargs)
    logger.info("Secret injection: %s", si_cfg.adapter.rsplit(".", 1)[-1])
    return instance


def _create_resource_provider(settings: Settings) -> "ResourceProvider":  # noqa: F821
    """Create the ResourceProvider adapter from dynamic config."""
    rp_cfg = settings.resource_provider
    cls = import_class(rp_cfg.adapter)
    kwargs = _resolve_secret_kwargs(rp_cfg.kwargs, rp_cfg.secret_kwargs_env)
    instance = cls(**kwargs)
    logger.info("Resource provider: %s", rp_cfg.adapter.rsplit(".", 1)[-1])
    return instance


def _create_storage_adapter(settings: Settings) -> "StoragePort":  # noqa: F821
    """Create the StoragePort adapter from dynamic config."""
    st_cfg = settings.storage
    cls = import_class(st_cfg.adapter)
    kwargs = _resolve_secret_kwargs(st_cfg.kwargs, st_cfg.secret_kwargs_env)
    instance = cls(**kwargs)
    logger.info("Storage adapter: %s", st_cfg.adapter.rsplit(".", 1)[-1])
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
        resolved_kwargs = _resolve_secret_kwargs(cfg.kwargs, cfg.secret_kwargs_env)
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


async def _seed_linear_integration(
    integration_repo: object,
    credential_store: object,
    api_key: str,
) -> None:
    """Seed Linear integration from config into the DB.

    Idempotent — uses a fixed ID so repeated calls update the same row.
    This lets the integration-based /issues/search endpoint find Linear
    without manual UI setup.
    """
    from niuu.domain.models import IntegrationConnection, IntegrationType, SecretType

    owner_id = "dev-user"
    cred_name = "linear-config"

    await credential_store.store(
        owner_type="user",
        owner_id=owner_id,
        name=cred_name,
        secret_type=SecretType.API_KEY,
        data={"api_key": api_key},
    )

    connection = IntegrationConnection(
        id="f976d725-2a19-558a-a2d0-99258577f615",
        owner_id=owner_id,
        integration_type=IntegrationType.ISSUE_TRACKER,
        adapter="volundr.adapters.outbound.linear.LinearAdapter",
        credential_name=cred_name,
        config={},
        enabled=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        slug="linear",
    )
    await integration_repo.save_connection(connection)


def _seeded_integration_connection_id(
    *,
    owner_type: str,
    owner_id: str,
    integration_type: str,
    adapter: str,
    credential_name: str,
    slug: str,
) -> str:
    """Return a stable UUID for a config-seeded integration connection."""
    value = (
        f"volundr:integration-seed:{owner_type}:{owner_id}:{integration_type}:"
        f"{adapter}:{credential_name}:{slug}"
    )
    return str(uuid5(NAMESPACE_URL, value))


async def _seed_configured_integrations(
    integration_repo: object,
    credential_store: object,
    settings: Settings,
) -> None:
    """Seed configured integration connections into storage at startup."""
    from niuu.domain.models import IntegrationConnection

    for seed in settings.integrations.seed_connections:
        if seed.credential is not None:
            await credential_store.store(
                owner_type=seed.owner_type,
                owner_id=seed.owner_id,
                name=seed.credential_name,
                secret_type=seed.credential.secret_type,
                data=seed.credential.data,
                metadata=seed.credential.metadata,
            )

        connection = IntegrationConnection(
            id=seed.id
            or _seeded_integration_connection_id(
                owner_type=seed.owner_type,
                owner_id=seed.owner_id,
                integration_type=str(seed.integration_type),
                adapter=seed.adapter,
                credential_name=seed.credential_name,
                slug=seed.slug,
            ),
            owner_id=seed.owner_id,
            integration_type=seed.integration_type,
            adapter=seed.adapter,
            credential_name=seed.credential_name,
            config=seed.config,
            enabled=seed.enabled,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            slug=seed.slug,
        )
        await integration_repo.save_connection(connection)


def _has_seeded_linear_integration(settings: Settings) -> bool:
    """Return whether config already seeds a Linear issue-tracker connection."""
    from niuu.domain.models import IntegrationType

    for seed in settings.integrations.seed_connections:
        if seed.integration_type != IntegrationType.ISSUE_TRACKER:
            continue
        if seed.slug == "linear":
            return True
        if seed.adapter == "volundr.adapters.outbound.linear.LinearAdapter":
            return True
    return False


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

        async with database_pool(settings.database) as pool:
            # Identity & authorization adapters (dynamic adapter pattern)
            tenant_repository = PostgresTenantRepository(pool)
            user_repository = PostgresUserRepository(pool)

            resource_provider = _create_resource_provider(settings)
            storage_adapter = _create_storage_adapter(settings)
            identity_adapter = _create_identity_adapter(
                settings,
                user_repository,
                storage=storage_adapter,
            )
            authorization_adapter = _create_authorization_adapter(settings)

            # Store identity/authz on app.state for auth dependencies
            app.state.identity = identity_adapter
            app.state.authorization = authorization_adapter

            # Tenant service + ensure default tenant exists
            tenant_service = TenantService(tenant_repository, user_repository)
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
            pricing_provider = HardcodedPricingProvider(settings.models or None)
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
            from volundr.domain.services.credential import CredentialService
            from volundr.domain.services.mount_strategies import SecretMountStrategyRegistry

            credential_store = _create_credential_store(settings)

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

            # Create profile and template services (providers already created above)
            profile_service = ForgeProfileService(profile_provider, session_repository=repository)
            template_service = WorkspaceTemplateService(template_provider)

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

            # MCP servers and secrets
            mcp_provider = ConfigMCPServerProvider(settings.mcp_servers)
            secret_manager = InMemorySecretManager()
            canonical_secrets_router = create_canonical_secrets_router(mcp_provider, secret_manager)
            app.include_router(canonical_secrets_router)

            # Saved prompts
            prompt_repository = PostgresPromptRepository(pool)
            prompt_service = PromptService(prompt_repository)
            prompts_router = create_prompts_router(
                prompt_service,
                prefix="/api/v1/forge",
            )
            app.include_router(prompts_router)

            # Presets (DB-stored runtime config)
            preset_repository = PostgresPresetRepository(pool)
            preset_service = PresetService(preset_repository)
            presets_router = create_presets_router(
                preset_service,
                prefix="/api/v1/forge",
            )
            app.include_router(presets_router)

            # Personal access tokens
            from volundr.adapters.outbound.postgres_pats import PostgresPATRepository
            from volundr.domain.services.pat import PATService

            pat_repository = PostgresPATRepository(pool)

            from niuu.domain.services.pat_validator import PATValidator

            pat_validator = PATValidator(
                repo=pat_repository,
                cache_ttl=settings.pat.revocation_cache_ttl,
                revoked_cache_ttl=settings.pat.revoked_cache_ttl,
            )
            app.state.pat_validator = pat_validator

            # Resolve the token issuer (IDP adapter) via dynamic import
            token_issuer_cls = import_class(settings.pat.token_issuer_adapter)
            token_issuer = token_issuer_cls(**settings.pat.token_issuer_kwargs)

            pat_service = PATService(
                repo=pat_repository,
                token_issuer=token_issuer,
                ttl_days=settings.pat.ttl_days,
                validator=pat_validator,
            )
            app.state.pat_service = pat_service

            from volundr.adapters.inbound.auth import extract_principal as _extract_principal
            from volundr.adapters.inbound.rest_pats import create_pats_router

            tokens_router = create_pats_router(_extract_principal, prefix="/api/v1/tokens")
            app.include_router(tokens_router)

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

            # Tenant and identity management
            identity_router = create_identity_router(tenant_service)
            app.include_router(identity_router)
            tenants_router = create_tenants_router(tenant_service)
            app.include_router(tenants_router)

            # Admin settings (config-driven, runtime-toggleable)
            admin_settings_router = create_admin_settings_router()
            app.include_router(admin_settings_router)

            # Feature module system (config-driven, DB-persisted toggles)
            # In mini mode, disable modules that require container infrastructure
            feature_configs = list(settings.features)
            if settings.local_mounts.mini_mode:
                _mini_disabled = {"terminal", "code"}
                for fc in feature_configs:
                    if fc.key in _mini_disabled:
                        fc.default_enabled = False
            feature_service = FeatureService(pool, feature_configs)
            feature_catalog_router = create_feature_catalog_router(feature_service)
            app.include_router(feature_catalog_router)
            app.state.feature_service = feature_service

            # Credential management (reuses credential_store created above)
            credential_service = CredentialService(
                store=credential_store,
                strategies=SecretMountStrategyRegistry(),
            )
            canonical_credentials_router = create_canonical_credentials_router(
                credential_service,
            )
            app.include_router(canonical_credentials_router)

            # Workspace management — PVCs are the source of truth
            workspace_service = WorkspaceService(storage_adapter)
            app.state.workspace_service = workspace_service

            # Integration tracker factory (reuses credential_store created above)
            from volundr.domain.services.tracker_factory import TrackerFactory

            tracker_factory = TrackerFactory(credential_store)
            mapping_repository = PostgresMappingRepository(pool)
            default_tracker = None
            if settings.linear.enabled and settings.linear.api_key:
                default_tracker = LinearAdapter(api_key=settings.linear.api_key)
            tracker_service = TrackerService(
                default_tracker,
                mapping_repository,
                integration_repo=integration_repo,
                tracker_factory=tracker_factory,
            )

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

            # Integration management endpoints
            canonical_integrations_router = create_canonical_integrations_router(
                integration_repo,
                tracker_factory,
                registry=integration_registry,
                credential_store=credential_store,
            )
            app.include_router(canonical_integrations_router)

            # OAuth integration endpoints
            canonical_oauth_router = create_canonical_oauth_router(
                oauth_config=settings.oauth,
                integration_registry=integration_registry,
                credential_store=credential_store,
                integration_repo=integration_repo,
            )
            app.include_router(canonical_oauth_router)

            # Generic issue endpoints
            canonical_issues_router = create_canonical_issues_router(
                integration_repo,
                tracker_factory,
            )
            app.include_router(canonical_issues_router)

            canonical_tracker_router = create_canonical_tracker_router(
                tracker_service=tracker_service,
            )
            app.include_router(canonical_tracker_router)

            app.state.user_integration_service = user_integration_service
            app.state.communication_route_repository = communication_route_repository
            app.state.communication_cursor_repository = communication_cursor_repository
            app.state.session_room_port = session_room_port
            app.state.communication_ingress = communication_ingress
            app.state.telegram_ingress = telegram_ingress

            # Event pipeline: sinks + ingestion service + REST endpoints
            pg_event_sink = PostgresEventSink(
                pool, buffer_size=settings.event_pipeline.postgres_buffer_size
            )
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
            audit_subscriber = None
            if sleipnir_bus is not None:
                from volundr.adapters.outbound.sleipnir_event_sink import (  # noqa: PLC0415
                    SleipnirEventSink,
                )

                event_sinks.append(SleipnirEventSink(sleipnir_bus))
                logger.info("Sleipnir event sink registered in pipeline")

                # Audit log subscriber — persistent record of every event
                from sleipnir.adapters.audit_postgres import PostgresAuditRepository
                from sleipnir.adapters.audit_subscriber import AuditSubscriber

                audit_repository = PostgresAuditRepository(pool)
                audit_subscriber = AuditSubscriber(sleipnir_bus, audit_repository)
                await audit_subscriber.start()

                canonical_audit_router = create_canonical_audit_router(audit_repository)
                app.include_router(canonical_audit_router)
                logger.info("Audit log subscriber started")

            event_ingestion = EventIngestionService(sinks=event_sinks)
            events_router = create_events_router(
                event_ingestion,
                pg_event_sink,
                session_service=session_service,
                prefix="/api/v1/forge",
            )
            app.include_router(events_router)

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
            app.state.tracker_service = None  # Deprecated: use integration-based /issues/
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
                    "Volundr Telegram ingress disabled via config "
                    "(telegram_ingress.enabled=false)"
                )

            # Reconcile sessions stuck in PROVISIONING after a restart
            await session_service.reconcile_provisioning_sessions()

            try:
                yield
            finally:
                await telegram_ingress.stop()
                background_task.cancel()
                try:
                    await background_task
                except asyncio.CancelledError:
                    pass  # Expected: task cancellation during shutdown
                if audit_subscriber is not None:
                    await audit_subscriber.stop()
                await event_ingestion.close_all()
                if hasattr(pod_manager, "close"):
                    await pod_manager.close()
                if hasattr(gateway_adapter, "close"):
                    await gateway_adapter.close()
                await git_registry.close()

    app.router.lifespan_context = lifespan

    apply_cors_middleware(app, settings.cors)

    # PAT revocation enforcement
    from niuu.adapters.pat_revocation_middleware import PATRevocationMiddleware

    app.add_middleware(PATRevocationMiddleware)

    @app.get("/health", tags=["Health"])
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
