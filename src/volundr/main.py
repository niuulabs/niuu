"""Application factory for Volundr API."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI

from niuu.adapters.inbound.rest_credentials_settings import create_credentials_settings_router
from niuu.adapters.inbound.rest_integrations_settings import create_integrations_settings_router
from niuu.adapters.inbound.rest_pats import create_pats_router
from niuu.adapters.inbound.rest_realms import create_realms_router
from niuu.adapters.postgres_credential_refresh_lock import PostgresCredentialRefreshLock
from niuu.adapters.postgres_realms import PostgresRealmRepository
from niuu.cors import apply_cors_middleware
from niuu.domain.services.pat import PATService
from niuu.domain.services.realm import RealmService
from niuu.service_integrations import (
    has_seeded_linear_integration as _has_seeded_linear_integration,
)
from niuu.service_integrations import (
    seed_configured_integrations as _seed_configured_integrations,
)
from niuu.service_integrations import seed_linear_integration as _seed_linear_integration
from niuu.service_runtime import (
    create_credential_store as _create_credential_store,
)
from niuu.service_runtime import create_identity_adapter as _create_identity_adapter
from niuu.service_runtime import create_pat_validator as _create_pat_validator
from niuu.service_runtime import create_storage_adapter as _create_storage_adapter
from niuu.service_runtime import create_workload_identity_service
from niuu.service_runtime import release_credential_store as _release_credential_store
from niuu.utils import import_class, resolve_secret_kwargs
from sleipnir.adapters.audit_postgres import PostgresAuditRepository
from sleipnir.adapters.audit_subscriber import AuditSubscriber
from volundr.adapters.inbound.auth import extract_principal
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
from volundr.adapters.inbound.rest_openshell_credentials import (
    create_openshell_credentials_router,
)
from volundr.adapters.inbound.rest_prompts import create_prompts_router
from volundr.adapters.inbound.rest_resident_runtimes import create_resident_runtimes_router
from volundr.adapters.inbound.rest_resources import create_resources_router
from volundr.adapters.inbound.rest_secrets import create_canonical_secrets_router
from volundr.adapters.inbound.rest_session_log import create_session_log_router
from volundr.adapters.inbound.rest_trace import create_trace_router
from volundr.adapters.inbound.rest_tracker import create_canonical_tracker_router
from volundr.adapters.outbound.bifrost_catalog_http import HttpBifrostCatalogAdapter
from volundr.adapters.outbound.broadcaster import InMemoryEventBroadcaster
from volundr.adapters.outbound.config_mcp_servers import ConfigMCPServerProvider
from volundr.adapters.outbound.config_resident_profiles import (
    ConfigResidentDeploymentProfileProvider,
)
from volundr.adapters.outbound.git_registry import create_git_registry
from volundr.adapters.outbound.linear import LinearAdapter
from volundr.adapters.outbound.memory_secrets import InMemorySecretManager
from volundr.adapters.outbound.pg_event_sink import PostgresEventSink
from volundr.adapters.outbound.pg_session_event_log import PostgresSessionEventLog
from volundr.adapters.outbound.postgres import PostgresSessionRepository
from volundr.adapters.outbound.postgres_chronicles import PostgresChronicleRepository
from volundr.adapters.outbound.postgres_communication_cursors import (
    PostgresCommunicationCursorRepository,
)
from volundr.adapters.outbound.postgres_communication_routes import (
    PostgresCommunicationRouteRepository,
)
from volundr.adapters.outbound.postgres_credential_enrollments import (
    PostgresCredentialEnrollmentRepository,
)
from volundr.adapters.outbound.postgres_device_tokens import PostgresDeviceTokenRepository
from volundr.adapters.outbound.postgres_integrations import PostgresIntegrationRepository
from volundr.adapters.outbound.postgres_launch_specs import PostgresLaunchSpecRepository
from volundr.adapters.outbound.postgres_mappings import PostgresMappingRepository
from volundr.adapters.outbound.postgres_prompts import PostgresPromptRepository
from volundr.adapters.outbound.postgres_resident_runtimes import (
    PostgresResidentRuntimeRepository,
)
from volundr.adapters.outbound.postgres_spans import PostgresSpanRepository
from volundr.adapters.outbound.postgres_stats import PostgresStatsRepository
from volundr.adapters.outbound.postgres_tenants import PostgresTenantRepository
from volundr.adapters.outbound.postgres_timeline import PostgresTimelineRepository
from volundr.adapters.outbound.postgres_tokens import PostgresTokenTracker
from volundr.adapters.outbound.postgres_users import PostgresUserRepository
from volundr.adapters.outbound.pricing import HardcodedPricingProvider
from volundr.adapters.outbound.resident_flock import ResidentFlockAdapter
from volundr.adapters.outbound.skuld_room import SkuldRoomAdapter
from volundr.app_shell import build_app_shell
from volundr.catalog import build_catalog
from volundr.composition_builders import (  # noqa: F401
    _create_archive_store,
    _create_authorization_adapter,
    _create_contributors,
    _create_external_session_providers,
    _create_gateway_adapter,
    _create_http_auth_adapter,
    _create_pod_manager,
    _create_resident_controllers,
    _create_resident_session_controllers,
    _create_resource_provider,
    _create_secret_injection_adapter,
    _runtime_backend,
)
from volundr.config import Settings
from volundr.domain.models import SessionStatus
from volundr.domain.ports import CredentialEnrollmentRunnerPort, OpenShellCredentialGrantPort
from volundr.domain.services import (
    ChronicleService,
    ExternalSessionService,
    GitWorkflowService,
    PromptService,
    RepoService,
    SessionArchiveService,
    SessionService,
    StatsService,
    TenantService,
    TokenService,
)
from volundr.domain.services.attention_notifier import PushAttentionNotifier
from volundr.domain.services.communication_ingress import CommunicationIngressService
from volundr.domain.services.credential import CredentialService
from volundr.domain.services.credential_enrollment import CredentialEnrollmentService
from volundr.domain.services.event_ingestion import EventIngestionService
from volundr.domain.services.mount_strategies import SecretMountStrategyRegistry
from volundr.domain.services.resident_runtime import (
    ResidentRuntimeNotFoundError,
    ResidentRuntimeService,
)
from volundr.domain.services.telegram_ingress import TelegramIngressService
from volundr.domain.services.tracker import TrackerService
from volundr.domain.services.tracker_factory import TrackerFactory
from volundr.domain.services.workspace import WorkspaceService
from volundr.infrastructure.database import database_pool

# Interval for periodic stats and heartbeat broadcasts (seconds)
BROADCAST_INTERVAL = 30
CREDENTIAL_ENROLLMENT_RECONCILE_INTERVAL_SECONDS = 30

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


async def _refresh_bifrost_catalog(
    pricing_provider: HardcodedPricingProvider,
    bifrost_catalog: HttpBifrostCatalogAdapter,
    *,
    interval_seconds: float,
) -> None:
    while True:
        await _load_bifrost_catalog(pricing_provider, bifrost_catalog)
        await asyncio.sleep(interval_seconds)


async def _bootstrap_startup_schema(settings: Settings) -> None:
    """Apply embedded Volundr migrations for standalone startup paths."""
    import asyncpg

    from cli.resources import migration_dir, ordered_migration_files

    try:
        mig_dir = migration_dir("volundr")
    except FileNotFoundError:
        logger.debug("No Volundr migrations available for startup bootstrap")
        return

    sql_files = ordered_migration_files(mig_dir)
    if not sql_files:
        return

    conn = await asyncpg.connect(
        host=settings.database.host,
        port=settings.database.port,
        user=settings.database.user,
        password=settings.database.password,
        database=settings.database.name,
    )
    try:
        for sql_file in sql_files:
            try:
                await conn.execute(sql_file.read_text())
            except Exception:
                logger.debug("Migration %s skipped", sql_file.name, exc_info=True)
    finally:
        await conn.close()


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


async def _reconcile_liveness_loop(
    session_service: SessionService,
    *,
    interval_seconds: int,
    stale_after_seconds: int,
    exempt_workload_types: list[str] | None = None,
) -> None:
    """Periodically mark running sessions whose broker has gone silent as stopped."""
    logger.info(
        "Liveness reconciliation started, interval=%ds stale_after=%ds",
        interval_seconds,
        stale_after_seconds,
    )
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            count = await session_service.reconcile_liveness(
                stale_after_seconds,
                exempt_workload_types=exempt_workload_types,
            )
            if count:
                logger.info("Liveness: reconciled %d stale running session(s)", count)
        except asyncio.CancelledError:
            logger.info("Liveness reconciliation task cancelled")
            break
        except Exception:
            logger.exception("Liveness reconciliation iteration failed")


async def _reconcile_active_loop(
    session_service: SessionService,
    *,
    interval_seconds: int,
) -> None:
    """Periodically reconcile session rows against pod_manager.status().

    Pod-status authoritative (INV-9): active rows follow runtime state, while
    Kubernetes terminal rows release orphaned runtime resources. This is the
    always-on truth mechanism the heartbeat reaper could not safely provide.
    """
    logger.info(
        "Active-session reconcile loop started, interval=%ds",
        interval_seconds,
    )
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            count = await session_service.reconcile_active_sessions()
            if count:
                logger.info("Reconcile: corrected %d divergent session(s)", count)
        except asyncio.CancelledError:
            logger.info("Active-session reconcile loop cancelled")
            break
        except Exception:
            logger.exception("Active-session reconcile iteration failed")


async def _reconcile_resident_runtimes_loop(
    service: ResidentRuntimeService,
    *,
    interval_seconds: float,
    flock_adapter: ResidentFlockAdapter | None = None,
) -> None:
    """Periodically converge durable resident records with backend state."""
    logger.info(
        "Resident runtime reconcile loop started, interval=%.1fs",
        interval_seconds,
    )
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await service.reconcile_all()
            if flock_adapter is not None:
                await flock_adapter.sync()
        except asyncio.CancelledError:
            logger.info("Resident runtime reconcile loop cancelled")
            break
        except Exception:
            logger.exception("Resident runtime reconcile iteration failed")


async def _reconcile_credential_enrollments_loop(
    service: CredentialEnrollmentService,
    *,
    interval_seconds: float = CREDENTIAL_ENROLLMENT_RECONCILE_INTERVAL_SECONDS,
) -> None:
    """Destroy expired interactive-login sandboxes independently of the UI."""
    logger.info(
        "Credential enrollment reconciliation started, interval=%.1fs",
        interval_seconds,
    )
    while True:
        try:
            count = await service.expire_stale()
            if count:
                logger.info("Expired %d stale credential enrollment(s)", count)
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info("Credential enrollment reconciliation task cancelled")
            break
        except Exception:
            logger.exception("Credential enrollment reconciliation iteration failed")
            await asyncio.sleep(interval_seconds)


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


def create_app(
    settings: Settings | None = None,
    *,
    public_origin: str = "http://localhost:8080",
    skuld_registry: object | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        settings: Application settings. If None, uses Settings() which
                  automatically loads from YAML + env vars.
    """
    if settings is None:
        settings = Settings()

    app = build_app_shell(settings)

    # Bifrost is its own service/plugin. Volundr no longer co-hosts it; it consumes
    # the model catalog over HTTP from settings.bifrost.url for cost/pricing only.

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """Manage application lifecycle."""
        settings = app.state.settings
        audit_subscriber: AuditSubscriber | None = None

        await _bootstrap_startup_schema(settings)

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
            resident_runtime_repository = PostgresResidentRuntimeRepository(pool)
            device_repository = PostgresDeviceTokenRepository(pool)
            communication_route_repository = PostgresCommunicationRouteRepository(pool)
            communication_cursor_repository = PostgresCommunicationCursorRepository(pool)
            stats_repository = PostgresStatsRepository(pool)
            token_tracker = PostgresTokenTracker(pool)
            span_repository = PostgresSpanRepository(pool)
            pg_event_sink = PostgresEventSink(
                pool, buffer_size=settings.event_pipeline.postgres_buffer_size
            )
            from ravn.adapters.personas.postgres_registry import PostgresPersonaRegistry

            persona_registry = PostgresPersonaRegistry(pool)
            app.state.persona_registry = persona_registry
            from volundr.adapters.outbound.session_personas import (
                RegistrySessionPersonaProvider,
            )

            session_persona_provider = RegistrySessionPersonaProvider(persona_registry)
            workload_identity_service = create_workload_identity_service(settings.workload_identity)
            pod_manager = _create_pod_manager(settings)
            resident_controllers = _create_resident_controllers(settings, pod_manager)
            credential_refresh_lock = PostgresCredentialRefreshLock(pool)
            if hasattr(pod_manager, "set_credential_refresh_lock"):
                pod_manager.set_credential_refresh_lock(credential_refresh_lock)
            if hasattr(pod_manager, "set_session_repository"):
                pod_manager.set_session_repository(repository)
            if hasattr(pod_manager, "set_workload_token_issuer"):
                pod_manager.set_workload_token_issuer(workload_identity_service)
            for controller in resident_controllers:
                if controller is not pod_manager and hasattr(
                    controller, "set_credential_refresh_lock"
                ):
                    controller.set_credential_refresh_lock(credential_refresh_lock)
                if hasattr(controller, "set_resident_runtime_repository"):
                    controller.set_resident_runtime_repository(resident_runtime_repository)
                if controller is not pod_manager and hasattr(
                    controller, "set_workload_token_issuer"
                ):
                    controller.set_workload_token_issuer(workload_identity_service)

            # Inject Skuld port registry for mini mode proxy routing
            skuld_reg = skuld_registry
            if skuld_reg is not None and hasattr(pod_manager, "set_skuld_registry"):
                pod_manager.set_skuld_registry(skuld_reg)
            if skuld_reg is not None:
                for controller in resident_controllers:
                    if hasattr(controller, "set_skuld_registry"):
                        controller.set_skuld_registry(skuld_reg)

            if skuld_reg is None:
                # Standalone deployment (K8s / bare uvicorn): no CLI root app
                # exists to terminate /s/{session_id} browser traffic, so this
                # app must serve the session proxy itself. Local broker ports
                # never register here; sessions resolve through the target
                # resolver (e.g. the OpenShell gateway) wired below. The
                # registry lives on app.state so a lifespan re-entry rewires
                # hooks on the same object the mounted routes captured.
                from niuu.session_proxy import SkuldPortRegistry, register_session_proxy_routes

                skuld_reg = getattr(app.state, "session_proxy_registry", None)
                if skuld_reg is None:
                    skuld_reg = SkuldPortRegistry()
                    app.state.session_proxy_registry = skuld_reg
                    register_session_proxy_routes(app, skuld_reg)
                if hasattr(pod_manager, "set_skuld_registry"):
                    pod_manager.set_skuld_registry(skuld_reg)

            gateway_adapter = _create_gateway_adapter(settings)
            bifrost_auth = _create_http_auth_adapter(settings.bifrost.auth)
            bifrost_catalog = HttpBifrostCatalogAdapter(
                base_url=settings.bifrost.url,
                auth=bifrost_auth,
                timeout_seconds=settings.bifrost.timeout_seconds,
            )
            pricing_provider = HardcodedPricingProvider()
            bifrost_catalog_task = asyncio.create_task(
                _refresh_bifrost_catalog(
                    pricing_provider,
                    bifrost_catalog,
                    interval_seconds=settings.bifrost.catalog_refresh_interval_seconds,
                )
            )
            resident_profile_provider = ConfigResidentDeploymentProfileProvider(
                settings.resident_runtimes.profiles,
                pricing_provider,
            )
            git_registry = create_git_registry(settings.git)

            # Sleipnir integration (optional — enabled via sleipnir.enabled config)
            sleipnir_bus = None
            if settings.sleipnir.enabled:
                try:
                    sl_cls = import_class(settings.sleipnir.adapter)
                    sleipnir_kwargs = resolve_secret_kwargs(
                        settings.sleipnir.kwargs,
                        settings.sleipnir.secret_kwargs_env,
                    )
                    sleipnir_bus = sl_cls(**sleipnir_kwargs)
                    if hasattr(sleipnir_bus, "start"):
                        await sleipnir_bus.start()
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

            # Push / attention notifier (optional — enabled via push.enabled).
            # Fans a "session needs you" push out to the owner's devices when a
            # session enters awaiting_input.
            attention_notifier = None
            if settings.push.enabled:
                try:
                    channel_cls = import_class(settings.push.adapter)
                    channel_kwargs = resolve_secret_kwargs(
                        settings.push.kwargs, settings.push.secret_kwargs_env
                    )
                    notification_channel = channel_cls(**channel_kwargs)
                    attention_notifier = PushAttentionNotifier(
                        device_repository,
                        notification_channel,
                        min_urgency=settings.push.min_urgency,
                    )
                    logger.info(
                        "Push notifications enabled: adapter=%s",
                        settings.push.adapter.rsplit(".", 1)[-1],
                    )
                except Exception:
                    logger.exception("Failed to initialise push notifications")
                    attention_notifier = None

            # Create services with broadcaster for real-time updates
            # Forge catalog (launch specs + session definitions), built via the
            # shared `build_catalog` builder. The repository enables user-scope CRUD.
            catalog = build_catalog(
                settings,
                launch_spec_repository=PostgresLaunchSpecRepository(pool),
            )
            launch_spec_provider = catalog.launch_spec_provider

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
            for controller in resident_controllers:
                if controller is not pod_manager and hasattr(controller, "set_credential_store"):
                    controller.set_credential_store(credential_store)
            resident_session_controllers = _create_resident_session_controllers(
                settings,
                resident_controllers,
                credential_store,
            )
            resident_runtime_service = ResidentRuntimeService(
                resident_runtime_repository,
                resident_profile_provider,
                resident_controllers,
                resident_session_controllers,
                span_repository=span_repository,
                event_repository=pg_event_sink,
            )
            resident_flock_adapter = (
                ResidentFlockAdapter(
                    resident_runtime_repository,
                    resident_session_controllers,
                    sleipnir_bus,
                    persona_provider=session_persona_provider,
                )
                if sleipnir_bus is not None
                else None
            )
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
            credential_enrollment_service = (
                CredentialEnrollmentService(
                    repository=PostgresCredentialEnrollmentRepository(pool),
                    runner=pod_manager,
                    integration_repository=integration_repo,
                    integration_registry=integration_registry,
                    credential_store=credential_store,
                )
                if isinstance(pod_manager, CredentialEnrollmentRunnerPort)
                else None
            )
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
                launch_spec_provider=launch_spec_provider,
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
                persona_provider=session_persona_provider,
            )

            session_service = SessionService(
                repository,
                pod_manager,
                git_registry=git_registry,
                validate_repos=settings.git.validate_on_create,
                broadcaster=broadcaster,
                launch_spec_provider=launch_spec_provider,
                authorization=authorization_adapter,
                contributors=contributors if contributors else None,
                provisioning_timeout=settings.provisioning.timeout_seconds,
                provisioning_initial_delay=settings.provisioning.initial_delay_seconds,
                integration_repo=integration_repo,
                storage=storage_adapter,
                communication_route_repository=communication_route_repository,
                public_origin=public_origin,
                session_communication_port=session_room_port,
                attention_notifier=attention_notifier,
                runtime_backend=_runtime_backend(settings),
                span_repository=span_repository,
            )
            # Local-process brokers notify the session service when they exit so
            # the DB row is reconciled promptly (pod-status authoritative) rather
            # than waiting for the periodic sweep.
            if hasattr(pod_manager, "set_death_callback"):

                async def _on_broker_death(session_id: str) -> None:
                    try:
                        await session_service.mark_session_dead(UUID(session_id))
                    except ValueError:
                        logger.warning("Broker death for non-UUID session id %s", repr(session_id))

                pod_manager.set_death_callback(_on_broker_death)

            # The live WS proxy reconciles the row when it can't reach a pod, so a
            # dead-session connect self-heals the stale RUNNING status (INV-9).
            if skuld_reg is not None and hasattr(skuld_reg, "set_reconcile_hook"):

                async def _on_proxy_dead(session_id: str) -> bool:
                    # Pod-authoritative: report whether the reconcile CONFIRMS the
                    # session is dead so the registry only drops the port for a
                    # genuinely-gone pod, never on a transient broker-leg blip while
                    # the pod is still RUNNING (M-8). A still-active row => retain.
                    try:
                        reconciled = await session_service.mark_session_dead(UUID(session_id))
                    except ValueError:
                        logger.warning(
                            "WS proxy reconcile for non-UUID session id %s", repr(session_id)
                        )
                        return False
                    if reconciled is None:
                        return True
                    return reconciled.status in (SessionStatus.STOPPED, SessionStatus.FAILED)

                skuld_reg.set_reconcile_hook(_on_proxy_dead)

            if (
                skuld_reg is not None
                and hasattr(skuld_reg, "set_target_resolver")
                and hasattr(pod_manager, "session_proxy_target")
            ):

                async def _resolve_session_proxy_target(session_id: str):
                    try:
                        resource_id = UUID(session_id)
                    except ValueError:
                        return None
                    session = await repository.get(resource_id)
                    if session is not None:
                        return pod_manager.session_proxy_target(session)
                    return await resident_runtime_service.proxy_target(resource_id)

                skuld_reg.set_target_resolver(_resolve_session_proxy_target)

            # Enforce session ownership at the WS proxy (the browser's
            # termination point). The broker's ws_auth is defense-in-depth for
            # direct/flock connections; the proxy dials it from loopback, so
            # this is the check that actually covers proxied browser traffic.
            if skuld_reg is not None and hasattr(skuld_reg, "set_ownership_guard"):
                from niuu.domain.models import Principal
                from volundr.domain.ports import Resource

                async def _may_attach(
                    session_id: str,
                    user_id: str | None,
                    tenant_id: str | None,
                    roles: tuple[str, ...],
                ) -> bool:
                    try:
                        resource_id = UUID(session_id)
                    except ValueError:
                        return False
                    session = await repository.get(resource_id)
                    if session is None:
                        principal = Principal(
                            user_id=user_id or "",
                            email="",
                            tenant_id=tenant_id or "default",
                            roles=list(roles),
                        )
                        try:
                            await resident_runtime_service.get(principal, resource_id)
                        except ResidentRuntimeNotFoundError:
                            return False
                        return True
                    if not session.owner_id:
                        # Unknown or unowned (legacy/dev) session: not the
                        # proxy's job to invent a policy — stay permissive.
                        return True
                    # Delegate to the ONE authorization policy (the same adapter
                    # the REST API uses) so the WS attach check can never drift
                    # from it. "start" is the mutating action-class the ladder
                    # gates on owner match.
                    principal = Principal(
                        user_id=user_id or "",
                        email="",
                        tenant_id=tenant_id or "default",
                        roles=list(roles),
                    )
                    resource = Resource(
                        kind="session",
                        id=session_id,
                        attr={
                            "owner_id": session.owner_id,
                            "tenant_id": session.tenant_id,
                        },
                    )
                    return await authorization_adapter.is_allowed(principal, "start", resource)

                skuld_reg.set_ownership_guard(_may_attach)

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
            session_event_log = PostgresSessionEventLog(pool)
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
                event_log_repository=session_event_log,
            )
            app.state.archive_service = archive_service
            app.state.session_event_log = session_event_log

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

            # External session discovery (Claude Code / Codex on the host)
            external_session_providers = _create_external_session_providers(settings)
            external_session_service = None
            if external_session_providers:
                external_session_service = ExternalSessionService(
                    external_session_providers,
                    repository,
                    session_service,
                    allowed_workspace_prefixes=settings.local_mounts.allowed_prefixes,
                    allow_root_workspace=settings.local_mounts.allow_root_mount,
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
                external_session_service=external_session_service,
                device_repository=device_repository,
                prefix="/api/v1/forge",
                server_public_host=settings.server_public_host,
                openshell_internal_gateway_url=settings.openshell_internal_gateway_url,
            )
            app.include_router(forge_router)
            app.include_router(create_resident_runtimes_router(resident_runtime_service))
            app.state.resident_runtime_service = resident_runtime_service
            credential_grant_brokers = {
                id(adapter): adapter
                for adapter in [pod_manager, *resident_controllers]
                if isinstance(adapter, OpenShellCredentialGrantPort)
            }
            if len(credential_grant_brokers) > 1:
                raise RuntimeError(
                    "Only one OpenShell credential grant broker may be configured per target"
                )
            if credential_grant_brokers:
                app.include_router(
                    create_openshell_credentials_router(
                        next(iter(credential_grant_brokers.values()))
                    )
                )

            app.include_router(catalog.router)

            # Resource discovery endpoint
            resources_router = create_resources_router(
                resource_provider,
                prefix="/api/v1/volundr",
            )
            app.include_router(resources_router)
            app.state.resource_provider = resource_provider

            # Saved prompts
            prompt_repository = PostgresPromptRepository(pool)
            prompt_service = PromptService(prompt_repository)
            prompts_router = create_prompts_router(
                prompt_service,
                prefix="/api/v1/volundr",
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
                    credential_enrollment_service=credential_enrollment_service,
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

            from volundr.adapters.outbound.postgres_pats import PostgresPATRepository

            pat_repository = PostgresPATRepository(pool)
            pat_validator = _create_pat_validator(settings, pat_repository)
            token_issuer_cls = import_class(settings.pat.token_issuer_adapter)
            token_issuer = token_issuer_cls(**settings.pat.token_issuer_kwargs)
            pat_service = PATService(
                repo=pat_repository,
                token_issuer=token_issuer,
                ttl_days=settings.pat.ttl_days,
                validator=pat_validator,
            )
            app.state.pat_validator = pat_validator
            app.state.pat_service = pat_service
            app.state.workload_identity_service = workload_identity_service
            app.include_router(create_pats_router(extract_principal, prefix="/api/v1/tokens"))

            # Realm governance — a Valkyrie's build capability, trust, and config
            # readable by ravn over HTTP (shared niuu postgres, no ravn-local db).
            realm_repository = PostgresRealmRepository(pool)
            app.state.realm_service = RealmService(realm_repository)
            app.include_router(create_realms_router(extract_principal, prefix="/api/v1/realms"))

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
                resident_runtime_service=resident_runtime_service,
                prefix="/api/v1/forge",
            )
            app.include_router(events_router)

            # Durable full-fidelity transcript log: ingest (skuld) + cursor replay
            session_log_router = create_session_log_router(
                session_event_log,
                session_service=session_service,
                prefix="/api/v1/forge",
                default_show_internal=settings.replay.default_show_internal,
            )
            app.include_router(session_log_router)

            # Replay-as-live: paced re-emit of recorded frames over a WebSocket,
            # speaking the live-session frame protocol so existing clients
            # (web SessionSocket, ?qa=stream, iOS) render a finished session live.
            if settings.replay.enabled:
                from volundr.adapters.inbound.ws_session_replay import (
                    create_session_replay_router,
                )

                session_replay_router = create_session_replay_router(
                    session_event_log,
                    session_service=session_service,
                    prefix="/api/v1/forge",
                    config=settings.replay,
                )
                app.include_router(session_replay_router)

            trace_router = create_trace_router(
                span_repository,
                session_service=session_service,
                resident_runtime_service=resident_runtime_service,
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
            app.state.launch_spec_service = catalog.launch_spec_service
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

            # Start liveness reconciliation: expire running sessions whose broker
            # has gone silent so clients stop dialing dead chat endpoints.
            liveness_task: asyncio.Task | None = None
            if settings.session_liveness.enabled:
                liveness_task = asyncio.create_task(
                    _reconcile_liveness_loop(
                        session_service,
                        interval_seconds=settings.session_liveness.check_interval_seconds,
                        stale_after_seconds=settings.session_liveness.stale_after_seconds,
                        exempt_workload_types=settings.session_liveness.exempt_workload_types,
                    )
                )

            # Pod-status-authoritative periodic reconcile (INV-9). Always-on by
            # default and safe: it only corrects a row when pod_manager.status()
            # says the session is actually gone, so it never false-reaps an
            # idle-but-alive session the way the heartbeat reaper would.
            reconcile_task: asyncio.Task | None = None
            if settings.session_liveness.reconcile_enabled:
                reconcile_task = asyncio.create_task(
                    _reconcile_active_loop(
                        session_service,
                        interval_seconds=settings.session_liveness.reconcile_interval_seconds,
                    )
                )
            resident_reconcile_task = asyncio.create_task(
                _reconcile_resident_runtimes_loop(
                    resident_runtime_service,
                    interval_seconds=settings.resident_runtimes.reconciliation_interval_seconds,
                    flock_adapter=resident_flock_adapter,
                )
            )
            credential_enrollment_reconcile_task = (
                asyncio.create_task(
                    _reconcile_credential_enrollments_loop(credential_enrollment_service)
                )
                if credential_enrollment_service is not None
                else None
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
            await resident_runtime_service.reconcile_all()
            if resident_flock_adapter is not None:
                await resident_flock_adapter.sync()

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
                if liveness_task is not None:
                    liveness_task.cancel()
                    try:
                        await liveness_task
                    except asyncio.CancelledError:
                        pass  # Expected: task cancellation during shutdown
                if reconcile_task is not None:
                    reconcile_task.cancel()
                    try:
                        await reconcile_task
                    except asyncio.CancelledError:
                        pass  # Expected: task cancellation during shutdown
                resident_reconcile_task.cancel()
                try:
                    await resident_reconcile_task
                except asyncio.CancelledError:
                    pass
                if credential_enrollment_reconcile_task is not None:
                    credential_enrollment_reconcile_task.cancel()
                    try:
                        await credential_enrollment_reconcile_task
                    except asyncio.CancelledError:
                        pass
                if resident_flock_adapter is not None:
                    await resident_flock_adapter.stop()
                await resident_runtime_service.close()
                await event_ingestion.close_all()
                if hasattr(pod_manager, "close"):
                    await pod_manager.close()
                for controller in resident_controllers:
                    if controller is not pod_manager and hasattr(controller, "close"):
                        await controller.close()
                if hasattr(gateway_adapter, "close"):
                    await gateway_adapter.close()
                await git_registry.close()
                if audit_subscriber is not None:
                    await audit_subscriber.stop()
                if sleipnir_bus is not None and hasattr(sleipnir_bus, "stop"):
                    await sleipnir_bus.stop()
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
