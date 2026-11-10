"""Application factory for Volundr API."""

import asyncio
import inspect
import logging
import os
import urllib.parse
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from functools import partial
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from niuu.adapters.inbound.rest_credentials_settings import create_credentials_settings_router
from niuu.adapters.inbound.rest_integrations_settings import create_integrations_settings_router
from niuu.adapters.inbound.rest_pats import create_pats_router
from niuu.adapters.inbound.rest_realms import create_realms_router
from niuu.adapters.outbound.git_registry import create_provenance_verifier
from niuu.adapters.postgres_credential_refresh_lock import PostgresCredentialRefreshLock
from niuu.adapters.postgres_realms import PostgresRealmRepository
from niuu.build_identity import build_identity
from niuu.config_models import SessionPreviewConfig
from niuu.cors import apply_cors_middleware
from niuu.domain.guild_membership import GuildInstance
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
from niuu.service_runtime import create_workload_identity_service, seed_development_identity
from niuu.service_runtime import release_credential_store as _release_credential_store
from niuu.utils import import_class, resolve_secret_kwargs
from sleipnir.adapters.audit_postgres import PostgresAuditRepository
from sleipnir.adapters.audit_subscriber import AuditSubscriber
from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.inbound.forge_session_auth import ForgeSessionAuthMiddleware
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.inbound.rest_admin_settings import create_admin_settings_router
from volundr.adapters.inbound.rest_artifacts import (
    InstanceIdentity,
    create_artifacts_router,
    create_instance_router,
)
from volundr.adapters.inbound.rest_audit import (
    create_audit_router,
    create_canonical_audit_router,
)
from volundr.adapters.inbound.rest_codex_credentials import create_codex_credentials_router
from volundr.adapters.inbound.rest_compute_metadata import create_compute_metadata_router
from volundr.adapters.inbound.rest_credentials import create_canonical_credentials_router
from volundr.adapters.inbound.rest_events import create_events_router
from volundr.adapters.inbound.rest_forge_mcp import create_forge_mcp_router
from volundr.adapters.inbound.rest_git import create_git_router
from volundr.adapters.inbound.rest_guild import create_guild_router
from volundr.adapters.inbound.rest_integrations import create_canonical_integrations_router
from volundr.adapters.inbound.rest_issues import create_canonical_issues_router
from volundr.adapters.inbound.rest_mesh_identity import create_mesh_identity_router
from volundr.adapters.inbound.rest_message_delivery import create_message_delivery_router
from volundr.adapters.inbound.rest_notifications import create_notifications_router
from volundr.adapters.inbound.rest_oauth import create_canonical_oauth_router
from volundr.adapters.inbound.rest_openshell_credentials import (
    create_openshell_credentials_router,
)
from volundr.adapters.inbound.rest_person_questions import create_person_questions_router
from volundr.adapters.inbound.rest_presented_files import create_presented_files_router
from volundr.adapters.inbound.rest_prompts import create_prompts_router
from volundr.adapters.inbound.rest_push import create_push_router
from volundr.adapters.inbound.rest_resident_a2a import create_resident_a2a_router
from volundr.adapters.inbound.rest_resident_runtimes import create_resident_runtimes_router
from volundr.adapters.inbound.rest_resources import (
    create_forge_cluster_resources_router,
    create_resources_router,
)
from volundr.adapters.inbound.rest_secret_requests import create_secret_requests_router
from volundr.adapters.inbound.rest_secrets import create_canonical_secrets_router
from volundr.adapters.inbound.rest_session_log import create_session_log_router
from volundr.adapters.inbound.rest_session_participants import (
    create_session_participants_router,
)
from volundr.adapters.inbound.rest_session_people import create_session_people_router
from volundr.adapters.inbound.rest_trace import create_trace_router
from volundr.adapters.inbound.rest_tracker import create_canonical_tracker_router
from volundr.adapters.inbound.rest_user_storage import create_user_storage_router
from volundr.adapters.outbound.bifrost_catalog_http import HttpBifrostCatalogAdapter
from volundr.adapters.outbound.broadcaster import InMemoryEventBroadcaster
from volundr.adapters.outbound.config_mcp_servers import ConfigMCPServerProvider
from volundr.adapters.outbound.config_resident_profiles import (
    ConfigResidentDeploymentProfileProvider,
)
from volundr.adapters.outbound.git_registry import create_git_registry
from volundr.adapters.outbound.linear import LinearAdapter
from volundr.adapters.outbound.memory_secrets import InMemorySecretManager
from volundr.adapters.outbound.notification_image_fetcher import (
    BrokerTargetLookup,
    SessionLookup,
)
from volundr.adapters.outbound.persona_playability import SkuldPersonaPlayability
from volundr.adapters.outbound.pg_event_sink import PostgresEventSink
from volundr.adapters.outbound.pg_message_delivery import PostgresMessageDelivery
from volundr.adapters.outbound.pg_session_event_log import PostgresSessionEventLog
from volundr.adapters.outbound.postgres import PostgresSessionRepository
from volundr.adapters.outbound.postgres_admin_settings import PostgresAdminSettingsRepository
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
from volundr.adapters.outbound.postgres_integrations import PostgresIntegrationRepository
from volundr.adapters.outbound.postgres_launch_specs import PostgresLaunchSpecRepository
from volundr.adapters.outbound.postgres_mappings import PostgresMappingRepository
from volundr.adapters.outbound.postgres_mesh_peer_claims import PostgresMeshPeerClaims
from volundr.adapters.outbound.postgres_notifications import (
    PostgresNotificationDeliveryRepository,
    PostgresNotificationImageRepository,
    PostgresNotificationRepository,
    PostgresNotificationRuleRepository,
)
from volundr.adapters.outbound.postgres_prompts import PostgresPromptRepository
from volundr.adapters.outbound.postgres_resident_runtimes import (
    PostgresResidentRuntimeRepository,
)
from volundr.adapters.outbound.postgres_secret_requests import PostgresSecretRequestRepository
from volundr.adapters.outbound.postgres_session_participants import (
    PostgresSessionParticipantRepository,
)
from volundr.adapters.outbound.postgres_spans import PostgresSpanRepository
from volundr.adapters.outbound.postgres_stats import PostgresStatsRepository
from volundr.adapters.outbound.postgres_tenants import PostgresTenantRepository
from volundr.adapters.outbound.postgres_timeline import PostgresTimelineRepository
from volundr.adapters.outbound.postgres_tokens import PostgresTokenTracker
from volundr.adapters.outbound.postgres_users import PostgresUserRepository
from volundr.adapters.outbound.pricing import HardcodedPricingProvider
from volundr.adapters.outbound.resident_flock import (
    ResidentFlockAdapter,
    ResidentMeshCertificates,
)
from volundr.adapters.outbound.skuld_room import SkuldRoomAdapter
from volundr.app_shell import build_app_shell
from volundr.artifact_runtime import (
    ArtifactHold,
    configured_host,
    create_artifact_service,
    resolve_host,
    resolve_tailnet_name,
    startup_backfill,
)
from volundr.catalog import build_catalog
from volundr.composition_builders import (  # noqa: F401
    _create_archive_store,
    _create_authorization_adapter,
    _create_codex_credential_broker,
    _create_contributors,
    _create_credential_enrollment_runner,
    _create_external_session_providers,
    _create_forge_session_projection,
    _create_forge_session_tokens,
    _create_gateway_adapter,
    _create_host_join_endpoint,
    _create_host_pairing_signer,
    _create_http_auth_adapter,
    _create_mesh_certificate_issuer,
    _create_mesh_revocation_publisher,
    _create_pod_manager,
    _create_resident_controllers,
    _create_resident_session_controllers,
    _create_resource_provider,
    _create_secret_injection_adapter,
    _create_workflow_execution_credential_service,
    _runtime_backend,
    _sign_forge_platform_events,
    _start_sleipnir_bus,
    _validate_remote_room_role_config,
    create_oauth_client_registry,
    create_presented_file_store,
    integration_database_pool,
    require_presented_file_uploads,
    with_oauth_device_runner,
)
from volundr.config import Settings
from volundr.domain.models import SessionStatus
from volundr.domain.notifications import NotificationSinkInfo
from volundr.domain.ports import OpenShellCredentialGrantPort
from volundr.domain.presented_files import PresentedFileQuota
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
from volundr.domain.services.communication_ingress import CommunicationIngressService
from volundr.domain.services.credential import CredentialService
from volundr.domain.services.credential_enrollment import CredentialEnrollmentService
from volundr.domain.services.event_ingestion import EventIngestionService
from volundr.domain.services.forge import catalog_session_definition
from volundr.domain.services.mesh_identity import MeshIdentityService
from volundr.domain.services.mount_strategies import SecretMountStrategyRegistry
from volundr.domain.services.notification_images import NotificationImageWorker
from volundr.domain.services.notifications import NotificationService, engine_resolver_for
from volundr.domain.services.resident_runtime import (
    ResidentRuntimeNotFoundError,
    ResidentRuntimeService,
)
from volundr.domain.services.room_grants import RoomGrantSource
from volundr.domain.services.secret_requests import SecretRequestService
from volundr.domain.services.session_events import SessionEventStream
from volundr.domain.services.session_participants import SessionParticipantService
from volundr.domain.services.telegram_ingress import TelegramIngressService
from volundr.domain.services.tracker import TrackerService
from volundr.domain.services.tracker_factory import TrackerFactory
from volundr.domain.services.workspace import WorkspaceService
from volundr.external_modules import load_external_module_manifests
from volundr.infrastructure.database import database_pool
from volundr.integration_definitions import load_integration_definition_configs
from volundr.notification_composition import (
    NotificationDelivery,
    create_notification_delivery,
    create_notification_image_worker,
    create_push_channel,
)
from volundr.push_composition import create_push_gateway

# Interval for periodic stats and heartbeat broadcasts (seconds)
BROADCAST_INTERVAL = 30

logger = logging.getLogger(__name__)

#: Workload name on the credentials Forge's live-preview router presents to Skuld.
PREVIEW_ROUTER_WORKLOAD = "forge-preview-router"


# "Could not reach Bifrost" is the expected startup state in mini mode: the catalog is
# served by this same process and is not listening yet when the lifespan starts.
_BIFROST_NOT_READY_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout)


async def _load_bifrost_catalog(
    pricing_provider: HardcodedPricingProvider,
    bifrost_catalog: HttpBifrostCatalogAdapter,
    *,
    retry_initial_seconds: float,
    retry_max_seconds: float,
) -> None:
    """Load the catalog, retrying every failure with capped backoff until it succeeds.

    A failed attempt never replaces the models already loaded, so a refresh that
    fails keeps serving the last good catalog. Every failure is logged: "not ready"
    at warning, anything else (HTTP error, bad payload, bad auth) at error with the
    knobs to fix, because Volundr serves no resident profiles until a load succeeds.
    """
    delay_seconds = retry_initial_seconds
    while True:
        try:
            models = await bifrost_catalog.list_models()
        except _BIFROST_NOT_READY_ERRORS as exc:
            logger.warning(
                "Bifrost catalog not ready yet at %s (%s); retrying in %.1fs",
                bifrost_catalog._base_url,  # noqa: SLF001
                exc,
                delay_seconds,
            )
        except Exception as exc:
            logger.error(
                "Bifrost catalog load from %s failed: %s; retrying in %.1fs. Fix the catalog "
                "at bifrost.url (and bifrost.auth); until a load succeeds Volundr keeps the "
                "last loaded catalog (none at startup, so no resident profiles resolve)",
                bifrost_catalog._base_url,  # noqa: SLF001
                exc,
                delay_seconds,
                exc_info=exc,
            )
        else:
            pricing_provider.replace_models(models)
            logger.info("Loaded %s model(s) from configured Bifrost catalog", len(models))
            return
        await asyncio.sleep(delay_seconds)
        delay_seconds = min(delay_seconds * 2, retry_max_seconds)


def _log_bifrost_catalog_task_failure(task: asyncio.Task[None]) -> None:
    """Make a dead catalog refresh task loud: without this it would die silently."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    logger.error(
        "Bifrost catalog refresh stopped unexpectedly: %s. Restart Volundr; the model "
        "catalog will not refresh until then",
        exc,
        exc_info=exc,
    )


async def _refresh_bifrost_catalog(
    pricing_provider: HardcodedPricingProvider,
    bifrost_catalog: HttpBifrostCatalogAdapter,
    *,
    interval_seconds: float,
    retry_initial_seconds: float,
    retry_max_seconds: float,
) -> None:
    while True:
        await _load_bifrost_catalog(
            pricing_provider,
            bifrost_catalog,
            retry_initial_seconds=retry_initial_seconds,
            retry_max_seconds=retry_max_seconds,
        )
        await asyncio.sleep(interval_seconds)


async def _retire_unserved_push_rules(
    settings: Settings, notification_service: NotificationService, *, gateway_on: bool
) -> int:
    """Drop the needs-input rules of a sink this node no longer pushes through.

    The gateway alerts instead, or push is off: those rules would fail every
    delivery. A no-op while the sink is still configured. Returns how many went.
    """
    sink = settings.push.attention_sink
    retired = await notification_service.retire_attention_push_rules(sink)
    if retired:
        logger.info(
            "Retired %d needs-input push rule(s) for sink %r: %s",
            retired,
            sink,
            "the push gateway alerts instead" if gateway_on else "push.enabled is false",
        )
    return retired


def _create_notification_service(
    settings: Settings,
    pool: Any,
    *,
    broadcaster: InMemoryEventBroadcaster,
    integration_repository: PostgresIntegrationRepository,
    session_lookup: SessionLookup,
    broker_target: BrokerTargetLookup,
    room_grants: RoomGrantSource,
    guild_instance: GuildInstance | None = None,
) -> tuple[NotificationService, NotificationImageWorker] | None:
    """Compose the notification feed, rules, outbox and image worker
    (``notifications.enabled``)."""
    config = settings.notifications
    if not config.enabled:
        logger.info("Forge notifications disabled (notifications.enabled=false)")
        return None
    cli_types = {
        name: (definition.defaults.get("broker") or {}).get("cliType")
        for name, definition in settings.session_definitions.items()
    }
    repository = PostgresNotificationRepository(pool)
    images = PostgresNotificationImageRepository(
        pool, max_error_chars=config.images.max_error_chars
    )
    image_worker = create_notification_image_worker(
        settings,
        notifications=repository,
        images=images,
        session_lookup=session_lookup,
        broker_target=broker_target,
        broadcaster=broadcaster,
        guild_instance=guild_instance,
    )
    service = NotificationService(
        repository,
        PostgresNotificationRuleRepository(pool),
        PostgresNotificationDeliveryRepository(
            pool, max_error_chars=config.dispatcher.max_error_chars
        ),
        image_repository=images,
        image_scheduler=image_worker,
        broadcaster=broadcaster,
        reply_ready_enabled=config.reply_ready.enabled,
        reply_title_chars=config.reply_ready.title_chars,
        reply_body_chars=config.reply_ready.body_chars,
        stale_after_seconds=config.stale_after_seconds,
        sinks=[
            NotificationSinkInfo(
                name=sink["name"],
                label=str(sink.get("label") or sink["name"]),
                requires_integration=False,
            )
            for sink in config.sinks
        ],
        engine_resolver=engine_resolver_for(cli_types, settings.default_definition),
        integration_repository=integration_repository,
        guild_instance=guild_instance,
        room_grants=room_grants,
    )
    return service, image_worker


def _create_notification_delivery(
    settings: Settings,
    pool: Any,
    *,
    public_origin: str,
    integration_repository: Any,
    credential_store: Any,
    device_repository: Any,
) -> NotificationDelivery | None:
    """Compose the outbox dispatcher (``notifications.dispatcher.enabled``).

    Push sinks deliver through the channel built from ``push.adapter``.
    """
    config = settings.notifications
    push_channel = None
    if config.enabled and config.dispatcher.enabled:
        # A broken push.adapter is a configuration error: raise rather than run
        # delivery with every push rule quietly failing.
        push_channel = create_push_channel(settings)
    return create_notification_delivery(
        settings,
        pool,
        public_origin=public_origin,
        integration_repository=integration_repository,
        credential_store=credential_store,
        device_repository=device_repository,
        push_channel=push_channel,
    )


async def _bootstrap_startup_schema(settings: Settings) -> None:
    """Apply embedded Volundr migrations for standalone startup paths."""
    import asyncpg

    from cli.resources import migration_dir, ordered_migration_files
    from volundr.adapters.outbound.startup_schema import apply_startup_migrations

    try:
        mig_dir = migration_dir("volundr")
    except FileNotFoundError:
        logger.error("Volundr startup migrations are missing; refusing an unverified schema")
        raise

    sql_files = ordered_migration_files(mig_dir)
    if not sql_files:
        raise RuntimeError("Volundr startup migration directory is empty; schema is unverified")

    conn = await asyncpg.connect(
        host=settings.database.host,
        port=settings.database.port,
        user=settings.database.user,
        password=settings.database.password,
        database=settings.database.name,
    )
    try:
        await apply_startup_migrations(conn, sql_files)
    finally:
        await conn.close()


def _ensure_preview_cache_dir_writable(preview_cache_dir: Path) -> None:
    """Fail startup, not the first thumbnail request, when the preview cache
    directory cannot be created and written (e.g. a path on a read-only pod
    root filesystem)."""
    probe = preview_cache_dir / f".write-probe-{os.getpid()}-{uuid4().hex}"
    try:
        preview_cache_dir.mkdir(parents=True, exist_ok=True)
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise RuntimeError(
            f"preview_cache_dir {preview_cache_dir} is not writable: {exc}. Mount a "
            "writable volume there (chart: previewCache.mountPath) or point "
            "preview_cache_dir at a writable path."
        ) from exc


async def _broadcast_periodic_updates(broadcaster: InMemoryEventBroadcaster) -> None:
    """Background task to broadcast periodic stats ticks and heartbeats.

    The stats tick carries no figures: the session event stream computes them
    for each subscriber over the sessions it may list.

    Args:
        broadcaster: The event broadcaster to publish events to.
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

            logger.info("SSE periodic: stats tick to %d subscriber(s)", sub_count)
            await broadcaster.publish_stats_tick()

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
    periodic: bool = True,
) -> None:
    """Reconcile session rows against pod_manager.status(), starting at once.

    Pod-status authoritative (INV-9): active rows follow runtime state, while
    Kubernetes terminal rows release orphaned runtime resources. This is the
    always-on truth mechanism the heartbeat reaper could not safely provide.

    The first pass runs immediately and replaces the startup pass that used to
    run before the server bound its port: it makes a runtime call per stored
    row, which must not hold the listener past its liveness window. With
    ``periodic`` off (``session_liveness.reconcile_enabled: false``) only that
    first pass runs.
    """
    logger.info(
        "Active-session reconcile loop started, interval=%ds periodic=%s",
        interval_seconds,
        periodic,
    )
    while True:
        try:
            count = await session_service.reconcile_active_sessions()
            if count:
                logger.info("Reconcile: corrected %d divergent session(s)", count)
        except asyncio.CancelledError:
            logger.info("Active-session reconcile loop cancelled")
            break
        except Exception:
            logger.exception("Active-session reconcile iteration failed")
        if not periodic:
            return
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info("Active-session reconcile loop cancelled")
            break


async def _reconcile_resident_runtimes_loop(
    service: ResidentRuntimeService,
    *,
    interval_seconds: float,
    flock_adapter: ResidentFlockAdapter | None = None,
) -> None:
    """Converge durable resident records with backend state, starting at once.

    The first pass runs immediately in the background. It used to run before
    the server bound its port, but a pass can upgrade every resident — on
    OpenShell that deletes each sandbox and waits for the deletion — which held
    startup past the liveness window and crash-looped Völundr on every
    version bump.
    """
    logger.info(
        "Resident runtime reconcile loop started, interval=%.1fs",
        interval_seconds,
    )
    while True:
        try:
            await service.reconcile_all()
            if flock_adapter is not None:
                await flock_adapter.sync()
        except asyncio.CancelledError:
            logger.info("Resident runtime reconcile loop cancelled")
            break
        except Exception:
            logger.exception("Resident runtime reconcile iteration failed")
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info("Resident runtime reconcile loop cancelled")
            break


def _create_delivery_service_factory(config, integrations):
    """Compose receipt/workspace adapters once and credential scope per caller."""
    from niuu.domain.services.delivery import EvidenceVerifier
    from niuu.ports.delivery import (
        DeliveryForgeProvider,
        EvidenceAuthenticator,
        WorkstreamRepository,
    )
    from volundr.domain.services.delivery import DeliveryService

    def adapter_kwargs(adapter_config):
        return resolve_secret_kwargs(adapter_config.kwargs, adapter_config.secret_kwargs_env)

    authenticator = import_class(config.authenticator.adapter)(
        **adapter_kwargs(config.authenticator)
    )
    if not isinstance(authenticator, EvidenceAuthenticator):
        raise TypeError("Delivery authenticator must implement EvidenceAuthenticator")
    workstreams = import_class(config.workstreams.adapter)(
        **{
            **adapter_kwargs(config.workstreams),
            "authenticator": authenticator,
            "producer_id": config.workstream_producer_id,
        }
    )
    if not isinstance(workstreams, WorkstreamRepository):
        raise TypeError("Delivery workstreams must implement WorkstreamRepository")
    verifier = EvidenceVerifier(authenticator, trusted_producers=config.trusted_producers)
    forge_class = import_class(config.forge.adapter)
    forge_kwargs = adapter_kwargs(config.forge)

    def for_principal(principal):
        forge = forge_class(
            **{**forge_kwargs, "integrations": integrations, "principal": principal}
        )
        if not isinstance(forge, DeliveryForgeProvider):
            raise TypeError("Delivery forge must implement DeliveryForgeProvider")
        return DeliveryService(
            workstreams=workstreams,
            forge=forge,
            verifier=verifier,
            authenticator=authenticator,
            policies=config.policies,
            producer_id=config.producer_id,
        )

    return for_principal


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


def _build_otel_event_sink(otel_cfg):
    """Build the OTel GenAI event sink from validated config, or raise.

    ``event_pipeline.otel.enabled: true`` with the SDK missing is a
    configured-but-impossible state — raise with the remedy, per
    .claude/rules/no-fallbacks.md, rather than logging a warning and running
    the pipeline without it.
    """
    from volundr.adapters.outbound.otel_event_sink import OtelEventSink

    try:
        tracer_provider, meter_provider = _create_otel_providers(otel_cfg)
    except ImportError as exc:
        raise RuntimeError(
            "event_pipeline.otel.enabled is true but opentelemetry is "
            "not installed — install the 'otel' extra "
            "(pip install 'volundr[otel]') or set "
            "event_pipeline.otel.enabled: false"
        ) from exc
    return OtelEventSink(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        service_name=otel_cfg.service_name,
        provider_name=otel_cfg.provider_name,
    )


def _preview_origin_template(
    standalone: SessionPreviewConfig, *, public_origin: str, embedded: bool
) -> str:
    """The origin of one live preview, with ``{port}`` and ``{session_id}`` to fill.

    Standalone, previews are served on the configured preview domain over HTTPS.
    Embedded in the local host (mini mode), the host serves them on
    ``*.localhost`` at its own port (niuu ``host.session_preview``).
    """
    if not embedded:
        if not standalone.enabled:
            return ""
        return f"https://{{port}}--{{session_id}}.{standalone.domain}"
    from niuu.config import NiuuSettings

    local = NiuuSettings().host.session_preview
    if not local.enabled:
        return ""
    origin = urllib.parse.urlsplit(public_origin)
    port = f":{origin.port}" if origin.port else ""
    return f"{origin.scheme or 'http'}://{{port}}--{{session_id}}.{local.domain}{port}"


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

    # Configured and instrumented here, in create_app, not in lifespan:
    # Starlette builds and caches its middleware stack on the app's first
    # ASGI __call__ (which is also how the lifespan startup event arrives),
    # so instrumenting from inside a lifespan handler has no effect — the
    # stack was already frozen by the time that code would run.
    from niuu.observability import (
        configure_observability,
        install_uvicorn_log_redaction,
        instrument_fastapi_app,
        instrument_httpx_client,
    )

    telemetry = configure_observability(
        settings.observability,
        resource_attributes={"service.namespace": "volundr"},
        component="volundr",
        default_service_name="volundr",
    )
    instrument_fastapi_app(app, telemetry, component="volundr")
    install_uvicorn_log_redaction()
    instrument_httpx_client(telemetry)

    # Keep schema mismatch diagnostics without copying credentials or prompts into logs.
    @app.exception_handler(RequestValidationError)
    async def _log_request_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        logger.warning(
            "422 request validation: %s %s error_types=%s",
            request.method,
            request.url.path,
            [error["type"] for error in exc.errors()],
        )
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})

    # Bifrost is its own service/plugin. Volundr no longer co-hosts it; it consumes
    # the model catalog over HTTP from settings.bifrost.url for cost/pricing only.

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """Manage application lifecycle."""
        settings = app.state.settings
        audit_subscriber: AuditSubscriber | None = None

        external_modules = load_external_module_manifests(
            settings.integrations.module_manifest_files
        )
        await _bootstrap_startup_schema(settings)

        preview_cache_root = Path(settings.preview_cache_dir).expanduser()
        _ensure_preview_cache_dir_writable(preview_cache_root)

        async with (
            database_pool(settings.database) as pool,
            integration_database_pool(settings, pool) as integration_pool,
            AsyncExitStack() as compute_resources,
        ):
            # Identity & authorization adapters (dynamic adapter pattern)
            tenant_repository = PostgresTenantRepository(pool)
            user_repository = PostgresUserRepository(pool)
            tenant_service = TenantService(tenant_repository, user_repository)

            resource_provider = _create_resource_provider(settings)
            storage_adapter = _create_storage_adapter(settings)

            # Admin settings: the in-process dict the contributors and feature
            # flags read, filled from the database so a restart keeps them.
            # Update in place: contributors hold a reference to this dict.
            admin_settings_repository = PostgresAdminSettingsRepository(pool)
            for section, values in (await admin_settings_repository.load()).items():
                app.state.admin_settings.setdefault(section, {}).update(values)
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
            await seed_development_identity(identity_adapter, user_repository)

            # Create adapters
            repository = PostgresSessionRepository(pool)
            resident_runtime_repository = PostgresResidentRuntimeRepository(pool)
            communication_route_repository = PostgresCommunicationRouteRepository(pool)
            communication_cursor_repository = PostgresCommunicationCursorRepository(pool)
            stats_repository = PostgresStatsRepository(pool)
            token_tracker = PostgresTokenTracker(pool)
            span_repository = PostgresSpanRepository(pool)
            pg_event_sink = PostgresEventSink(
                pool, buffer_size=settings.event_pipeline.postgres_buffer_size
            )
            from personas.adapters.postgres_registry import PostgresPersonaRegistry

            persona_registry = PostgresPersonaRegistry(pool)
            app.state.persona_registry = persona_registry
            from volundr.adapters.outbound.session_personas import (
                RegistrySessionPersonaProvider,
            )

            session_persona_provider = RegistrySessionPersonaProvider(persona_registry)
            workload_identity_service = create_workload_identity_service(settings.workload_identity)
            forge_session_tokens = _create_forge_session_tokens(settings, workload_identity_service)
            app.state.forge_session_tokens = forge_session_tokens
            pod_manager = _create_pod_manager(settings)
            runtime_backend = _runtime_backend(settings, pod_manager)
            _validate_remote_room_role_config(settings, runtime_backend)
            execution_credential_service = _create_workflow_execution_credential_service(
                settings,
                repository=repository,
                token_issuer=workload_identity_service,
                runtime_backend=runtime_backend,
            )
            resident_controllers = _create_resident_controllers(settings, pod_manager)
            if hasattr(pod_manager, "set_session_repository"):
                pod_manager.set_session_repository(repository)
            if hasattr(pod_manager, "set_workload_token_issuer"):
                pod_manager.set_workload_token_issuer(workload_identity_service)
            for controller in resident_controllers:
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
                    retry_initial_seconds=settings.bifrost.catalog_retry_initial_seconds,
                    retry_max_seconds=settings.bifrost.catalog_retry_max_seconds,
                )
            )
            bifrost_catalog_task.add_done_callback(_log_bifrost_catalog_task_failure)
            resident_profile_provider = ConfigResidentDeploymentProfileProvider(
                settings.resident_runtimes.profiles,
                pricing_provider,
            )
            git_registry = create_git_registry(settings.git)

            # Sleipnir integration (optional — enabled via sleipnir.enabled config)
            sleipnir_bus = await _start_sleipnir_bus(settings)

            # Mesh identity: certificates for session peers and Forge-hosted residents,
            # signed with the session-credential key. Revocations ride the Sleipnir bus;
            # without one the certificate lifetime is the only bound.
            mesh_signing = settings.mesh.attestation.required
            app.state.mesh_signing = mesh_signing
            if not mesh_signing:
                logger.warning(
                    "mesh.attestation.required is false: the mesh runs unauthenticated. Forge's "
                    "resident peers and revocation notices are not signed, and the sessions this "
                    "Forge launches accept any claimed sender on the mesh. This is an operator "
                    "decision; set mesh.attestation.required: true to prove who sends what"
                )
            mesh_certificate_issuer = _create_mesh_certificate_issuer(
                settings, forge_session_tokens
            )
            mesh_peer_claims = PostgresMeshPeerClaims(pool)
            mesh_revocation_publisher = None
            if mesh_certificate_issuer is not None and sleipnir_bus is not None and mesh_signing:
                mesh_revocation_publisher = await _create_mesh_revocation_publisher(
                    settings,
                    mesh_certificate_issuer,
                    mesh_peer_claims,
                    sleipnir_bus,
                    settings.mesh.attestation,
                )
            elif mesh_certificate_issuer is not None and mesh_signing:
                logger.warning(
                    "Mesh revocations are not published: Forge has no Sleipnir bus "
                    "(sleipnir.enabled); a stopped session's certificate lasts until it "
                    "expires (forge_mcp.mesh_identity.ttl_seconds)"
                )
            sleipnir_bus = await _sign_forge_platform_events(
                settings, sleipnir_bus, mesh_revocation_publisher
            )
            mesh_identity_service = (
                MeshIdentityService(
                    mesh_certificate_issuer,
                    mesh_peer_claims,
                    repository,
                    revocations=mesh_revocation_publisher,
                )
                if mesh_certificate_issuer is not None
                else None
            )
            app.state.mesh_identity = mesh_identity_service

            # A node whose instance is not recorded yet keeps running: only
            # GET /guild reports it (503), and its items carry null instance fields.
            guild_membership = settings.guild.membership()
            broadcaster = InMemoryEventBroadcaster(
                sleipnir_publisher=sleipnir_bus,
                guild_instance=guild_membership.instance if guild_membership else None,
            )

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
            compute_provider = None
            compute_pool = None
            compute_pool_task = None
            compute_host_service = None
            compute_hosts_task = None
            host_join_listener = None
            host_connect_key_servers = []
            execution_components = None
            guest_metadata_service = None
            if settings.compute is not None:
                from volundr.adapters.outbound.postgres_compute_leases import (
                    PostgresComputeLeaseRepository,
                )
                from volundr.adapters.outbound.postgres_guest_request_watermarks import (
                    PostgresGuestRequestWatermarks,
                )
                from volundr.compute.main import (
                    HostKeySource,
                    build_execution_components,
                    build_guest_credentials,
                    build_provider,
                    build_registered_hosts,
                    build_runtime,
                    provider_fingerprint,
                )
                from volundr.domain.compute import ComputePoolPolicy
                from volundr.domain.services.compute_leases import ComputeLeaseService
                from volundr.domain.services.compute_pool import ComputePoolService
                from volundr.domain.services.guest_metadata import GuestMetadataService
                from volundr.domain.vm_runtime import (
                    CredentialAwareVmRuntime,
                    CredentialDeliveryVmRuntime,
                )

                compute = settings.compute
                if (compute.runtime is None and compute.execution_catalog is None) or not hasattr(
                    pod_manager, "configure_compute"
                ):
                    raise ValueError(
                        "Compute sessions require a VM PodManager and a runtime adapter"
                    )
                if compute.database is not None and compute.database != settings.database:
                    raise ValueError("Forge compute leases must use the Forge database")
                runtime = None
                host_keys = HostKeySource(
                    compute.pool_id, credential_store, PostgresCredentialRefreshLock(pool)
                )
                if compute.runtime is not None:
                    runtime = await build_runtime(
                        compute.runtime, importer=import_class, host_keys=host_keys
                    )
                    compute_resources.push_async_callback(runtime.close)
                    if isinstance(runtime, CredentialAwareVmRuntime):
                        runtime.configure_credentials(credential_store)
                compute_repository = PostgresComputeLeaseRepository(pool)
                if compute.execution_catalog is not None:
                    if runtime is None:
                        active_leases = await compute_repository.list(
                            compute.pool_id, include_released=False
                        )
                        if any(lease.execution_plan is None for lease in active_leases):
                            raise ValueError(
                                "Existing legacy allocations require compute.runtime until drained"
                            )
                    execution_components = await build_execution_components(
                        compute, credential_store, host_keys
                    )
                    for access in execution_components.accesses:
                        compute_resources.push_async_callback(access.close)
                    for preparation in execution_components.preparations.values():
                        compute_resources.push_async_callback(preparation.close)
                    for execution_runtime in execution_components.runtimes.values():
                        compute_resources.push_async_callback(execution_runtime.close)
                    if runtime is None:
                        runtime = execution_components.runtimes[
                            execution_components.default_plan.plan_digest
                        ]
                host_components = (
                    build_registered_hosts(compute, pool, execution_components)
                    if compute.hosts is not None
                    else None
                )
                compute_provider = build_provider(
                    compute,
                    host_components.provider_ports(compute.pool_id) if host_components else None,
                )
                compute_resources.push_async_callback(compute_provider.close)
                compute_service = ComputeLeaseService(
                    compute_repository,
                    compute_provider,
                    pool_id=compute.pool_id,
                    max_machines=compute.max_machines,
                    bootstrap=compute.bootstrap,
                    bootstrap_store=credential_store,
                    provider_binding=compute.provider_binding,
                    provider_fingerprint=provider_fingerprint(compute),
                    provisioning_timeout_seconds=compute.provisioning_timeout_seconds,
                    retry_interval_seconds=compute.retry_interval_seconds,
                    retry_max_seconds=compute.retry_max_seconds,
                )

                async def machine_bootstrap_for(lease_id):
                    lease = await compute_repository.get(lease_id)
                    if lease is None or lease.pool_id != compute.pool_id:
                        return None
                    return await compute_service.machine_bootstrap_for(lease)

                async def machine_for(lease_id):
                    lease = await compute_repository.get(lease_id)
                    if lease is None or lease.pool_id != compute.pool_id:
                        return None
                    return lease.machine

                guest_credentials = build_guest_credentials(
                    compute,
                    {
                        "pool_id": compute.pool_id,
                        "machine_bootstrap_for": machine_bootstrap_for,
                        "machine_for": machine_for,
                        "watermarks": PostgresGuestRequestWatermarks(pool),
                        "machine_provider": compute_provider,
                    },
                )
                for component in (
                    guest_credentials.service_account_tokens,
                    guest_credentials.guest_identity,
                    guest_credentials.delivery,
                ):
                    if component is not None:
                        compute_resources.push_async_callback(component.close)
                if guest_credentials.delivery is not None:
                    vm_runtimes = {
                        id(candidate): candidate
                        for candidate in (
                            runtime,
                            *(
                                execution_components.runtimes.values()
                                if execution_components is not None
                                else ()
                            ),
                        )
                        if candidate is not None
                    }
                    for vm_runtime in vm_runtimes.values():
                        if not isinstance(vm_runtime, CredentialDeliveryVmRuntime):
                            raise ValueError(
                                f"compute.credential_delivery is configured but "
                                f"{type(vm_runtime).__name__} cannot deliver guest credentials"
                            )
                        vm_runtime.configure_credential_delivery(guest_credentials.delivery)
                if guest_credentials.guest_identity is not None:
                    if guest_credentials.service_account_tokens is None:
                        raise ValueError(
                            "compute.guest_identity enables the metadata endpoint, which "
                            "needs compute.service_account_tokens"
                        )

                    async def issue_forge_session(session_id):
                        # session_service is composed below; the endpoint serves only
                        # after startup completes.
                        return await session_service.issue_forge_session(session_id)

                    guest_metadata_service = GuestMetadataService(
                        repository=compute_repository,
                        compute_service=compute_service,
                        pool_id=compute.pool_id,
                        identity=guest_credentials.guest_identity,
                        service_account_tokens=guest_credentials.service_account_tokens,
                        issue_forge_session=issue_forge_session,
                    )
                pod_manager.configure_compute(
                    compute_service,
                    compute_repository,
                    runtime,
                    compute.bootstrap,
                    pool_id=compute.pool_id,
                    max_machines=compute.max_machines,
                    owns_runtime=False,
                )
                compute_resources.push_async_callback(pod_manager.close)
                if execution_components is not None:
                    pod_manager.configure_execution(
                        execution_components.resolver,
                        execution_components.runtimes,
                        execution_components.preparations,
                    )
                compute_pool = ComputePoolService(
                    compute_repository,
                    compute_service,
                    compute_provider,
                    runtime,
                    pool_id=compute.pool_id,
                    bootstrap=compute.bootstrap,
                    interval_seconds=compute.maintenance_interval_seconds,
                    defaults=ComputePoolPolicy(
                        profile=(
                            execution_components.default_plan.provider_profile
                            if execution_components is not None
                            else pod_manager.profile
                        ),
                        max_machines=compute.max_machines,
                        slots_per_machine=compute.slots_per_machine,
                        max_sessions_per_machine=compute.max_sessions_per_machine,
                        max_machine_age_seconds=compute.max_machine_age_seconds,
                        warm_min=compute.warm_min,
                        reuse_policy=compute.reuse_policy,
                        max_provisioning=compute.max_provisioning,
                        idle_timeout_seconds=compute.idle_timeout_seconds,
                        provisioning_timeout_seconds=compute.provisioning_timeout_seconds,
                    ),
                )
                if execution_components is not None:
                    compute_pool.configure_execution(
                        execution_components.resolver,
                        execution_components.runtimes,
                        execution_components.preparations,
                    )
                await compute_pool.validate_policy(await compute_pool.policy())
                pod_manager.configure_pool(compute_pool)
                if host_components is not None:
                    from volundr.compute.main import build_host_connect_key_servers
                    from volundr.domain.services.compute_hosts import ComputeHostService

                    hosts_config = compute.hosts
                    join_endpoint = _create_host_join_endpoint(settings)
                    compute_host_service = ComputeHostService(
                        pool_id=compute.pool_id,
                        repository=host_components.repository,
                        compute_repository=compute_repository,
                        compute_pool=compute_pool,
                        connectors=host_components.connectors,
                        inspector=host_components.inspector,
                        executions=host_components.executions,
                        token_issuer=_create_host_pairing_signer(
                            settings, workload_identity_service
                        ),
                        join_url=join_endpoint.url,
                        join_pin=join_endpoint.tls.pin if join_endpoint.tls else None,
                        pairing_code_ttl_seconds=hosts_config.pairing_code_ttl_seconds,
                        check_interval_seconds=hosts_config.check_interval_seconds,
                        pending_check_interval_seconds=(
                            hosts_config.pending_check_interval_seconds
                        ),
                        reserved_cpu=hosts_config.reserved_cpu,
                        reserved_memory_mib=hosts_config.reserved_memory_mib,
                    )
                    if join_endpoint.tls is not None and hosts_config.join_listener is not None:
                        from volundr.adapters.inbound.host_join_listener import (
                            HostJoinListener,
                            create_host_join_app,
                        )

                        host_join_listener = HostJoinListener(
                            create_host_join_app(compute_host_service),
                            bind_host=hosts_config.join_listener.bind_host,
                            port=hosts_config.join_listener.port,
                            tls=join_endpoint.tls,
                        )
                    host_connect_key_servers = build_host_connect_key_servers(
                        host_components.connectors, compute_host_service.endpoint_key
                    )
            codex_credential_broker = _create_codex_credential_broker(
                settings,
                credential_store=credential_store,
                refresh_lock=(
                    PostgresCredentialRefreshLock(pool) if settings.local_mounts.mini_mode else None
                ),
            )
            credential_enrollment_runner = _create_credential_enrollment_runner(settings)
            credential_service = CredentialService(
                store=credential_store,
                strategies=SecretMountStrategyRegistry(),
                authorization=authorization_adapter,
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
            # Built early (not just at realm-router mount time below) so
            # ResidentRuntimeService can validate a create() call's realm_id
            # as a 422 instead of a bare FK violation during background deploy.
            realm_repository = PostgresRealmRepository(pool)
            resident_runtime_service = ResidentRuntimeService(
                resident_runtime_repository,
                resident_profile_provider,
                resident_controllers,
                resident_session_controllers,
                span_repository=span_repository,
                event_repository=pg_event_sink,
                realm_repository=realm_repository,
            )
            resident_flock_adapter = (
                ResidentFlockAdapter(
                    resident_runtime_repository,
                    resident_session_controllers,
                    sleipnir_bus,
                    persona_provider=session_persona_provider,
                    mesh_certificates=(
                        ResidentMeshCertificates(mesh_certificate_issuer, mesh_peer_claims)
                        if mesh_certificate_issuer is not None and mesh_signing
                        else None
                    ),
                    attestation=settings.mesh.attestation,
                )
                if sleipnir_bus is not None
                else None
            )

            # Integration registry + repository
            from volundr.domain.services.integration_registry import (
                IntegrationRegistry,
                definitions_from_config,
            )

            integration_definitions = definitions_from_config(
                [
                    definition.model_dump()
                    for definition in load_integration_definition_configs(
                        settings.integrations,
                        external_modules=external_modules,
                    )
                ],
            )
            integration_registry = IntegrationRegistry(integration_definitions)
            if settings.integrations.repository is not None:
                repository_config = settings.integrations.repository
                integration_repo = import_class(repository_config.adapter)(
                    **resolve_secret_kwargs(
                        repository_config.kwargs, repository_config.secret_kwargs_env
                    )
                )
            else:
                integration_repo = PostgresIntegrationRepository(integration_pool)
            # Residents get the owner's credentials their profile declares, injected the
            # same way as a session's (OpenBao), bound to the resident's own identity.
            for controller in {id(c): c for c in [pod_manager, *resident_controllers]}.values():
                if hasattr(controller, "set_resident_credentials"):
                    controller.set_resident_credentials(
                        secret_injection=secret_injection,
                        integrations=integration_repo,
                        credential_store=credential_store,
                    )
            # A push device is the owner's integration connection, every token of it in
            # the credential store with every other integration secret; device_tokens
            # keeps only the push gateway's state. Only the gateway node takes the
            # token kinds the gateway alone uses.
            from volundr.adapters.outbound.integration_device_tokens import (
                IntegrationDeviceRegistry,
            )
            from volundr.adapters.outbound.push_device_state import PostgresDeviceStateStore

            device_repository = IntegrationDeviceRegistry(
                integration_repo,
                credential_store,
                PostgresDeviceStateStore(pool),
                token_kinds=settings.push.accepted_token_kinds(),
            )
            moved = await device_repository.move_stored_tokens()
            if moved:
                logger.info("Push devices: moved %d stored tokens into the credential store", moved)
            mapping_repository = PostgresMappingRepository(pool)
            # Who holds a role in a session's room: the participant grants and the people
            # on projects. Session authorization and the notification feed (who is told that
            # something needs an approver) read the roles from the same two stores.
            session_participant_repository = PostgresSessionParticipantRepository(pool)
            project_member_repository = None
            if settings.projects.enabled:
                from volundr.domain.project_ports import ProjectMemberRepository

                project_member_repository = import_class(
                    settings.projects.member_repository_adapter
                )(pool=pool, **settings.projects.member_repository_kwargs)
                if not isinstance(project_member_repository, ProjectMemberRepository):
                    raise TypeError("Project member adapter must implement ProjectMemberRepository")
            notification_feed = _create_notification_service(
                settings,
                pool,
                room_grants=RoomGrantSource(
                    session_participant_repository, project_member_repository
                ),
                broadcaster=broadcaster,
                integration_repository=integration_repo,
                guild_instance=guild_membership.instance if guild_membership else None,
                session_lookup=repository.get,
                broker_target=pod_manager.broker_target,
            )
            notification_service, notification_image_worker = notification_feed or (None, None)
            app.state.notification_service = notification_service
            app.state.notification_image_worker = notification_image_worker
            notification_delivery = _create_notification_delivery(
                settings,
                pool,
                public_origin=public_origin,
                integration_repository=integration_repo,
                credential_store=credential_store,
                device_repository=device_repository,
            )
            # The Guild push gateway (push.gateway.enabled): this node pushes for
            # every Guild node's notifications. Configured-but-impossible (an
            # unreadable APNs key, no HTTP/2, a registry without token kinds) raises
            # here and stops the start.
            push_gateway = create_push_gateway(
                settings.push,
                pool,
                device_repository,
                instance_id=guild_membership.instance.id
                if guild_membership is not None and guild_membership.instance is not None
                else "",
                local_events=broadcaster,
            )
            app.state.push_gateway = push_gateway
            # push.enabled without the gateway: an owner registering a device gets the
            # "Needs-input pushes" rule, so the outbox pushes each session that needs
            # them. The gateway alerts for that moment from its `attention` notification.
            on_device_registered = None
            if settings.push.enabled and push_gateway is None:
                on_device_registered = partial(
                    notification_service.ensure_attention_push_rule,
                    sink=settings.push.attention_sink,
                )
                logger.info(
                    "Needs-input pushes on: through sink %r (adapter=%s)",
                    settings.push.attention_sink,
                    settings.push.adapter.rsplit(".", 1)[-1],
                )
            elif notification_service is not None:
                await _retire_unserved_push_rules(
                    settings, notification_service, gateway_on=push_gateway is not None
                )
            app.state.notification_dispatcher = (
                notification_delivery.dispatcher if notification_delivery is not None else None
            )
            tracker_factory = TrackerFactory(credential_store)
            oauth_clients = create_oauth_client_registry(
                settings,
                credential_store=credential_store,
                integration_registry=integration_registry,
            )
            await oauth_clients.load()
            credential_enrollment_service = CredentialEnrollmentService(
                repository=PostgresCredentialEnrollmentRepository(integration_pool),
                runner=with_oauth_device_runner(
                    credential_enrollment_runner, oauth_clients, integration_registry
                ),
                integration_repository=integration_repo,
                integration_registry=integration_registry,
                credential_store=credential_store,
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
            session_room_port = SkuldRoomAdapter(
                repository,
                internal_base_url=settings.session_room.internal_base_url,
            )
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
                pricing_provider=pricing_provider,
                execution_credential_service=execution_credential_service,
            )

            # Durable presented files (F2): Forge's own copy of every present-file.
            presented_file_store = await create_presented_file_store(settings, pool)
            if presented_file_store is not None:
                app.state.presented_file_store = presented_file_store
                app.state.presented_file_quota = PresentedFileQuota(
                    settings.presented_files.max_total_bytes
                )

            secret_request_service = SecretRequestService(
                PostgresSecretRequestRepository(pool), credential_store
            )
            app.state.secret_request_service = secret_request_service

            session_service = SessionService(
                repository,
                pod_manager,
                git_registry=git_registry,
                user_integration=user_integration_service,
                validate_repos=settings.git.validate_on_create,
                broadcaster=broadcaster,
                launch_spec_provider=launch_spec_provider,
                authorization=authorization_adapter,
                contributors=contributors if contributors else None,
                provisioning_timeout=settings.provisioning.timeout_seconds,
                provisioning_initial_delay=settings.provisioning.initial_delay_seconds,
                startup_poll_seconds=settings.provisioning.startup_poll_seconds,
                integration_repo=integration_repo,
                storage=storage_adapter,
                communication_route_repository=communication_route_repository,
                public_origin=public_origin,
                session_communication_port=session_room_port,
                runtime_backend=runtime_backend,
                execution_resolver=pod_manager if execution_components is not None else None,
                span_repository=span_repository,
                notification_recorder=notification_service,
                forge_session_tokens=forge_session_tokens,
                forge_mcp_default_grants=settings.forge_mcp.default_grants,
                forge_mcp_role_grants=settings.forge_mcp.role_grants,
                audit_repository=PostgresAuditRepository(pool),
                presented_file_store=presented_file_store,
                forge_session_projection=_create_forge_session_projection(settings),
                secret_cleanup=secret_request_service,
                mesh_identity=mesh_identity_service,
            )
            # A workflow run's unit starts its members with it: every pod manager that hosts
            # one resolves their launches (each member's own spec and credential) from here.
            pod_manager.configure_hosted_members(session_service.hosted_member_launches)
            if presented_file_store is not None:
                require_presented_file_uploads(
                    settings, delivers_session_credential=session_service.delivers_forge_session
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
                        target = pod_manager.session_proxy_target(session)
                        return await target if inspect.isawaitable(target) else target
                    return await resident_runtime_service.proxy_target(resource_id)

                skuld_reg.set_target_resolver(_resolve_session_proxy_target)

            if skuld_reg is not None and workload_identity_service.enabled:
                from niuu.domain.models import Principal
                from niuu.domain.services.token_scope import (
                    FORGE_SESSION_PREVIEW_SCOPE,
                    VALKYRIE_BUILD_TOKEN_USE,
                )

                preview_settings = settings.session_preview

                async def _mint_preview_credential(session_id: str, owner: Principal) -> str:
                    """The router credential for one live-preview request (niuu.preview_proxy).

                    Bound to the one session and carrying the issuer-only
                    forge:session:preview scope, which the session's Skuld requires.
                    """
                    return workload_identity_service.issue_token(
                        principal=owner,
                        workload_subject=PREVIEW_ROUTER_WORKLOAD,
                        workload_name=PREVIEW_ROUTER_WORKLOAD,
                        audiences=[preview_settings.credential_audience],
                        token_use=VALKYRIE_BUILD_TOKEN_USE,
                        claims={
                            "scopes": [FORGE_SESSION_PREVIEW_SCOPE],
                            "session_id": session_id,
                        },
                        ttl_seconds=preview_settings.credential_ttl_seconds,
                    ).token

                skuld_reg.set_preview_credential_issuer(_mint_preview_credential)

            if skuld_registry is None and settings.session_preview.enabled:
                from niuu.preview_proxy import PreviewRouter

                app.state.preview_router = PreviewRouter(skuld_reg, settings.session_preview)

            # Enforce session ownership at the WS proxy (the browser's
            # termination point). The broker's ws_auth is defense-in-depth for
            # direct/flock connections; the proxy dials it from loopback, so
            # this is the check that actually covers proxied browser traffic.
            if skuld_reg is not None and hasattr(skuld_reg, "set_ownership_guard"):
                from niuu.domain.models import Principal

                async def _resolve_ws_principal(
                    user_id: str | None, tenant_id: str | None, roles: tuple[str, ...]
                ) -> Principal:
                    """Validate the caller's asserted identity, same as every other
                    proxy guard — shared so _may_attach and _resolve_room_role can
                    never resolve different principals for one connection.

                    Raises:
                        InvalidTokenError: The identity adapter rejected the headers.
                    """
                    from identity.adapters.jwks import JwksIdentityAdapter
                    from niuu.ports.identity import HeaderAuthenticationPort

                    principal = Principal(
                        user_id=user_id or "",
                        email="",
                        tenant_id=tenant_id or "",
                        roles=list(roles),
                    )
                    if isinstance(identity_adapter, JwksIdentityAdapter):
                        # The proxy already verified this caller's bearer JWT
                        # once (extract_principal, before the guard runs), so
                        # there is no fresh token to re-verify here. Re-derive
                        # role mapping and membership for that identity instead.
                        return await identity_adapter.revalidate_verified_principal(principal)
                    if not isinstance(identity_adapter, HeaderAuthenticationPort):
                        return principal
                    keys = settings.identity.kwargs
                    headers = {
                        keys.get("user_id_header", "x-auth-user-id"): principal.user_id,
                        keys.get("tenant_header", "x-auth-tenant"): principal.tenant_id,
                        keys.get("roles_header", "x-auth-roles"): ",".join(principal.roles),
                    }
                    return await identity_adapter.validate_headers(headers)

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
                    from niuu.ports.identity import InvalidTokenError

                    try:
                        principal = await _resolve_ws_principal(user_id, tenant_id, roles)
                    except InvalidTokenError:
                        return False
                    session = await repository.get(resource_id)
                    if session is None:
                        try:
                            await resident_runtime_service.get(principal, resource_id)
                        except ResidentRuntimeNotFoundError:
                            return False
                        return True
                    # Delegate to the ONE authorization policy (the same adapter
                    # the REST API uses) so the WS attach check can never drift
                    # from it. "attach" is the room-entry action: it is granted
                    # to the owner/admin AND to any ACTIVE, unexpired
                    # session_participants grant (see
                    # SessionParticipantService.active_grants), unlike "start"
                    # which is owner/admin only.
                    grants = await session_participant_service.active_grants(session)
                    resource = SessionService.attributed_resource(
                        session_id,
                        owner_id=session.owner_id,
                        tenant_id=session.tenant_id,
                        room_viewers=grants.viewer_ids,
                        room_approvers=grants.approver_ids,
                    )
                    return await authorization_adapter.is_allowed(principal, "attach", resource)

                skuld_reg.set_ownership_guard(_may_attach)

                async def _resolve_room_role(
                    session_id: str,
                    user_id: str | None,
                    tenant_id: str | None,
                    roles: tuple[str, ...],
                ) -> str | None:
                    """Resolve the caller's room role for the stamped proxy header.

                    Derived from the SAME Cedar decisions ``_may_attach`` and the
                    REST API use — never a hand-written owner_id/admin-role
                    comparison (the pattern #1032 removed from this exact file):
                    that silently stops matching the moment authority is granted
                    any way other than literal ownership or a tenant-admin role,
                    which is exactly how it dropped dev-identity callers to no
                    role at all. Only called after ``_may_attach`` already
                    allowed the connection, so this never needs to deny outright
                    — it picks the most senior of owner/approver/viewer Cedar
                    actually grants, for Skuld's broker to gate tool-permission
                    responses and gate resolution.
                    """
                    try:
                        resource_id = UUID(session_id)
                    except ValueError:
                        return None
                    from niuu.ports.identity import InvalidTokenError

                    try:
                        principal = await _resolve_ws_principal(user_id, tenant_id, roles)
                    except InvalidTokenError:
                        return None
                    session = await repository.get(resource_id)
                    if session is None:
                        # Resident runtimes and other non-Forge subjects have no
                        # participant model; may_attach already approved this
                        # caller for full access, matching pre-existing behavior.
                        return "owner"
                    return await session_participant_service.effective_room_role(session, principal)

                if hasattr(skuld_reg, "set_room_role_resolver"):
                    skuld_reg.set_room_role_resolver(_resolve_room_role)

                if hasattr(skuld_reg, "set_display_name_resolver"):

                    async def _resolve_display_name(user_id: str) -> str | None:
                        """The provisioned user's display name, stamped on proxied sessions
                        as the author name of the caller's messages."""
                        user = await user_repository.get(user_id)
                        return user.display_name if user is not None else None

                    skuld_reg.set_display_name_resolver(_resolve_display_name)

            stats_service = StatsService(stats_repository, session_service)
            token_service = TokenService(
                token_tracker, repository, pricing_provider, broadcaster=broadcaster
            )
            repo_service = RepoService(
                git_registry,
                user_integration=user_integration_service,
                provenance_verifier=create_provenance_verifier(settings.git.provenance),
            )

            chronicle_repository = PostgresChronicleRepository(pool)
            timeline_repository = PostgresTimelineRepository(pool)
            session_event_log = PostgresSessionEventLog(
                pool, usage_model_windows=settings.session_usage.model_windows
            )
            chronicle_service = ChronicleService(
                chronicle_repository,
                session_service,
                broadcaster=broadcaster,
                timeline_repository=timeline_repository,
            )
            session_participant_service = SessionParticipantService(
                session_participant_repository,
                session_service,
                user_repository,
                project_members=project_member_repository,
            )
            app.state.session_participant_service = session_participant_service
            if skuld_reg is not None and hasattr(skuld_reg, "close_connections"):

                async def _close_revoked_connections(session_id: UUID, user_id: str) -> None:
                    # Immediate effect: the session proxy's interval
                    # revalidation (SkuldPortRegistry / _revalidate_loop) is
                    # the mechanism of record and would close this socket
                    # within one interval regardless — this just does not
                    # make a revoked participant wait for the next tick.
                    await skuld_reg.close_connections(
                        str(session_id), user_id, reason="Participant grant revoked"
                    )

                session_participant_service.set_revocation_notifier(_close_revoked_connections)
            if notification_service is not None:
                # An invitation is announced to the invitee and settled when answered.
                session_participant_service.set_invitation_notices(notification_service)

            archive_store = _create_archive_store(settings)
            archive_service = SessionArchiveService(
                session_service,
                storage_adapter,
                archive_store,
                chronicle_service=chronicle_service,
                event_log_repository=session_event_log,
                tail_initial_frames=settings.conversation_tail_initial_frames,
                tail_growth=settings.conversation_tail_growth,
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
                    event_log_repository=session_event_log,
                )

            # Projects add durable coordination metadata to ordinary Forge sessions.
            project_service = None
            if settings.projects.enabled:
                from volundr.domain.project_ports import ProjectRepository, ProjectWorkspace
                from volundr.domain.services.projects import ProjectService

                project_config = settings.projects
                project_repository = import_class(project_config.repository_adapter)(
                    pool=pool,
                    dispatch_wait_seconds=project_config.dispatch_wait_seconds,
                    dispatch_poll_seconds=project_config.dispatch_poll_seconds,
                    **project_config.repository_kwargs,
                )
                project_workspace = import_class(project_config.workspace_adapter)(
                    allowed_prefixes=settings.local_mounts.allowed_prefixes,
                    context_bytes=project_config.context_bytes,
                    git_timeout=project_config.git_timeout_seconds,
                    **project_config.workspace_kwargs,
                )
                if not isinstance(project_repository, ProjectRepository):
                    raise TypeError("Project repository adapter must implement ProjectRepository")
                if not isinstance(project_workspace, ProjectWorkspace):
                    raise TypeError("Project workspace adapter must implement ProjectWorkspace")
                project_service = ProjectService(
                    project_repository,
                    project_workspace,
                    session_service,
                    instance_id=project_config.instance_id or settings.server_public_host,
                    members=project_member_repository,
                    users=user_repository,
                )
                app.state.project_service = project_service

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
                on_device_registered=on_device_registered,
                on_environment_assumed=(
                    push_gateway.note_environment_assumed if push_gateway is not None else None
                ),
                prefix="/api/v1/forge",
                server_public_host=settings.server_public_host,
                session_status_host=configured_host(settings),
                openshell_internal_gateway_url=settings.openshell_internal_gateway_url,
                preview_cache_dir=preview_cache_root,
                preview_default_edge=settings.tool_result_preview.default_edge,
                preview_min_edge=settings.tool_result_preview.min_edge,
                preview_edge_limit=settings.tool_result_preview.max_edge,
                project_service=project_service,
                runtime_build=build_identity()
                if pod_manager.runtime_backend == "process"
                else None,
                runtime_health_timeout=settings.runtime_health_timeout_seconds,
                history_max_turns=settings.conversation_recent_max_turns,
                history_max_bytes=settings.conversation_recent_max_bytes,
                session_participant_service=session_participant_service,
                presented_file_store=presented_file_store,
                guild_membership=guild_membership,
                persona_playability=SkuldPersonaPlayability(
                    definitions=settings.session_definitions,
                    default_definition=settings.default_definition,
                    resolve_definition=partial(catalog_session_definition, pricing_provider),
                ),
            )
            app.include_router(forge_router)
            app.include_router(
                create_resident_runtimes_router(
                    resident_runtime_service,
                    a2a_public_base_url=settings.resident_runtimes.a2a_public_base_url,
                )
            )
            app.include_router(
                create_resident_a2a_router(
                    resident_runtime_service,
                    persona_provider=session_persona_provider,
                    public_base_url=settings.resident_runtimes.a2a_public_base_url,
                    reply_timeout_seconds=settings.resident_runtimes.a2a_reply_timeout_seconds,
                )
            )
            if presented_file_store is not None:
                app.include_router(
                    create_presented_files_router(
                        presented_file_store,
                        session_service,
                        public_base_url=settings.presented_files.public_base_url,
                        max_bytes=settings.presented_files.max_bytes,
                        quota=app.state.presented_file_quota,
                        prefix="/api/v1/forge",
                    )
                )
            app.state.resident_runtime_service = resident_runtime_service
            app.include_router(create_codex_credentials_router(codex_credential_broker))
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

            if guest_metadata_service is not None:
                app.include_router(create_compute_metadata_router(guest_metadata_service))
            if compute_host_service is not None:
                from volundr.adapters.inbound.rest_compute_hosts import (
                    create_compute_hosts_router,
                    create_host_join_router,
                )

                app.include_router(create_compute_hosts_router(compute_host_service, compute_pool))
                # With Forge's join listener, the host-facing routes are served only there.
                if host_join_listener is None:
                    app.include_router(create_host_join_router(compute_host_service))

            app.include_router(catalog.router)

            # Resource discovery endpoint
            resources_router = create_resources_router(
                resource_provider,
                prefix="/api/v1/volundr",
            )
            app.include_router(resources_router)
            # The same discovery in the Guild facade's shape, for a UI scoped to this node.
            app.include_router(
                create_forge_cluster_resources_router(resource_provider, guild_membership)
            )
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
                    oauth_clients=oauth_clients,
                    mcp_internal_hosts=settings.oauth.mcp_internal_hosts,
                )
            )
            app.include_router(
                create_canonical_oauth_router(
                    oauth_config=settings.oauth,
                    integration_registry=integration_registry,
                    credential_store=credential_store,
                    integration_repo=integration_repo,
                    oauth_clients=oauth_clients,
                    credential_lock=PostgresCredentialRefreshLock(pool),
                )
            )
            app.include_router(create_canonical_tracker_router(tracker_service=tracker_service))
            app.include_router(create_canonical_issues_router(integration_repo, tracker_factory))

            from volundr.adapters.outbound.postgres_pats import PostgresPATRepository

            pat_repository = PostgresPATRepository(pool)
            pat_validator = _create_pat_validator(settings, pat_repository)
            token_issuer_cls = import_class(settings.pat.token_issuer_adapter)
            token_issuer = token_issuer_cls(**settings.pat.token_issuer_kwargs)
            pat_service = import_class(settings.pat.service_adapter)(
                **settings.pat.service_kwargs,
                repo=pat_repository,
                token_issuer=token_issuer,
                ttl_days=settings.pat.ttl_days,
                validator=pat_validator,
                authorization=authorization_adapter,
            )
            app.state.pat_validator = pat_validator
            app.state.pat_service = pat_service
            app.state.workload_identity_service = workload_identity_service
            app.include_router(create_pats_router(extract_principal, prefix="/api/v1/tokens"))

            # Realm governance — a Valkyrie's build capability, trust, and config
            # readable by ravn over HTTP (shared niuu postgres, no ravn-local db).
            # realm_repository was already built above, before
            # ResidentRuntimeService, so it could validate realm_id at create().
            app.state.realm_service = RealmService(realm_repository)
            app.include_router(
                create_realms_router(
                    extract_principal,
                    prefix="/api/v1/realms",
                    stream_interval_seconds=settings.realm_detail_stream_interval_seconds,
                )
            )
            git_router = create_git_router(
                git_workflow_service,
                prefix="/api/v1/forge",
            )
            app.include_router(git_router)

            if settings.delivery.enabled:
                from niuu.ports.delivery import DeliveryAuthorizer
                from volundr.adapters.inbound.rest_delivery import create_delivery_router

                delivery_service_factory = _create_delivery_service_factory(
                    settings.delivery, user_integration_service
                )
                authorization_config = settings.delivery.authorizer
                delivery_authorizer = import_class(authorization_config.adapter)(
                    **resolve_secret_kwargs(
                        authorization_config.kwargs, authorization_config.secret_kwargs_env
                    )
                )
                if not isinstance(delivery_authorizer, DeliveryAuthorizer):
                    raise TypeError("Delivery authorizer must implement DeliveryAuthorizer")
                app.include_router(
                    create_delivery_router(
                        delivery_service_factory, extract_principal, delivery_authorizer
                    )
                )
                app.state.delivery_service_factory = delivery_service_factory
                app.state.delivery_authorizer = delivery_authorizer

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

            # Admin settings (persisted, runtime-toggleable)
            admin_settings_router = create_admin_settings_router(
                admin_settings_repository,
                compute_pool=compute_pool,
                home_volumes_supported=storage_adapter.supports_home_volumes,
            )
            app.include_router(admin_settings_router)
            app.include_router(create_user_storage_router(storage_adapter))

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
                otel_cfg = settings.event_pipeline.otel
                otel_sink = _build_otel_event_sink(otel_cfg)
                event_sinks.append(otel_sink)
                logger.info(
                    "OTel event sink enabled (endpoint=%s)",
                    otel_cfg.endpoint,
                )

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

            artifact_service = create_artifact_service(
                settings,
                pool,
                notification_service=notification_service,
                session_service=session_service,
            )
            app.state.artifact_service = artifact_service
            # Durable full-fidelity transcript log: ingest (skuld) + cursor replay
            session_log_router = create_session_log_router(
                session_event_log,
                session_service=session_service,
                prefix="/api/v1/forge",
                default_show_internal=settings.replay.default_show_internal,
                notification_service=notification_service,
                resident_runtime_service=resident_runtime_service,
                artifact_service=artifact_service,
            )
            app.include_router(session_log_router)
            app.include_router(create_guild_router(guild_membership, prefix="/api/v1/forge"))
            if artifact_service is not None:
                app.include_router(
                    create_artifacts_router(
                        artifact_service,
                        prefix="/api/v1/forge",
                        default_page_size=settings.artifacts.default_page_size,
                        max_page_size=settings.artifacts.max_page_size,
                    )
                )
            app.include_router(
                create_instance_router(
                    InstanceIdentity(
                        host=resolve_host(settings),
                        tailnet_name=resolve_tailnet_name(settings),
                        instance_id=settings.projects.instance_id,
                    )
                )
            )
            if notification_service is not None:
                app.include_router(
                    create_notifications_router(
                        notification_service,
                        session_service,
                        prefix="/api/v1/forge",
                        default_page_size=settings.notifications.default_page_size,
                        max_page_size=settings.notifications.max_page_size,
                        image_cache_max_age_seconds=(
                            settings.notifications.images.cache_max_age_seconds
                        ),
                        node_pushes_alerts=settings.push.pushes_alerts_itself(),
                    )
                )
            app.include_router(
                create_secret_requests_router(
                    secret_request_service, session_service, prefix="/api/v1/forge"
                )
            )
            app.include_router(
                create_mesh_identity_router(mesh_identity_service, prefix="/api/v1/forge")
            )
            app.include_router(
                create_message_delivery_router(PostgresMessageDelivery(pool), session_service)
            )
            if push_gateway is not None:
                app.include_router(create_push_router(push_gateway, prefix="/api/v1/forge"))
            if settings.forge_mcp.http.enabled:
                app.include_router(create_forge_mcp_router(settings.forge_mcp.http))
            app.state.forge_mcp_http = settings.forge_mcp.http.enabled
            app.include_router(
                create_session_participants_router(
                    session_participant_service,
                    session_service,
                    runtime_backend=runtime_backend,
                    room_role_source=settings.pod_manager.room_role_source,
                    identity_header_names=settings.identity.kwargs,
                )
            )
            app.include_router(
                create_session_people_router(session_participant_service, session_service)
            )
            app.include_router(
                create_person_questions_router(
                    session_participant_service, session_service, notification_service
                )
            )

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
            # Principal-scoped view of the broadcaster; the Niuu host's embedded
            # Forge stream subscribes through this, never the raw broadcaster.
            app.state.session_event_stream = SessionEventStream(
                broadcaster, session_service, stats_service
            )
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
            background_task = asyncio.create_task(_broadcast_periodic_updates(broadcaster))

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
            # Runs its first pass immediately even when the periodic loop is
            # disabled, as the startup pass it replaces did.
            reconcile_task: asyncio.Task | None = asyncio.create_task(
                _reconcile_active_loop(
                    session_service,
                    interval_seconds=settings.session_liveness.reconcile_interval_seconds,
                    periodic=settings.session_liveness.reconcile_enabled,
                )
            )
            resident_reconcile_task = asyncio.create_task(
                _reconcile_resident_runtimes_loop(
                    resident_runtime_service,
                    interval_seconds=settings.resident_runtimes.reconciliation_interval_seconds,
                    flock_adapter=resident_flock_adapter,
                )
            )
            if notification_delivery is not None:
                await notification_delivery.start()
            if notification_image_worker is not None:
                await notification_image_worker.start()
            artifact_hold: ArtifactHold | None = None
            if artifact_service is not None:
                artifact_hold = ArtifactHold(
                    artifact_service,
                    interval_seconds=settings.artifacts.tick_interval_seconds,
                    before_first_tick=lambda: startup_backfill(artifact_service, settings),
                )
                artifact_hold.start()
            if push_gateway is not None:
                await push_gateway.start()
            if settings.telegram_ingress.enabled:
                await telegram_ingress.start()
            else:
                logger.info(
                    "Volundr Telegram ingress disabled via config (telegram_ingress.enabled=false)"
                )

            if execution_credential_service is not None:
                await execution_credential_service.start()

            # Re-launch readiness polls for sessions left PROVISIONING by a
            # restart. This only schedules tasks; the session and resident
            # reconcile passes run in their loops above so a slow runtime never
            # holds the listener past its liveness window.
            await session_service.reconcile_provisioning_sessions()

            if compute_pool is not None:
                compute_pool_task = asyncio.create_task(compute_pool.run())
            if host_join_listener is not None:
                await host_join_listener.start()
            if compute_host_service is not None:
                compute_hosts_task = asyncio.create_task(compute_host_service.run())
            for key_server in host_connect_key_servers:
                await key_server.start()
            try:
                yield
            finally:
                # Not shutdown_observability() here: this composition root may
                # share the process with others (mini mode). configure_observability
                # registers an atexit shutdown hook, which is the correct place
                # to flush/close a provider that might still be owned by, and in
                # use by, a co-located service's own lifespan.
                if execution_credential_service is not None:
                    await execution_credential_service.stop()
                if compute_pool_task is not None:
                    compute_pool_task.cancel()
                    await asyncio.gather(compute_pool_task, return_exceptions=True)
                if compute_hosts_task is not None:
                    compute_hosts_task.cancel()
                    await asyncio.gather(compute_hosts_task, return_exceptions=True)
                if host_join_listener is not None:
                    await host_join_listener.stop()
                for key_server in host_connect_key_servers:
                    await key_server.stop()
                if bifrost_catalog_task is not None:
                    bifrost_catalog_task.cancel()
                    await asyncio.gather(bifrost_catalog_task, return_exceptions=True)
                await telegram_ingress.stop()
                if notification_delivery is not None:
                    await notification_delivery.stop()
                if notification_image_worker is not None:
                    await notification_image_worker.stop()
                if artifact_hold is not None:
                    await artifact_hold.stop()
                if push_gateway is not None:
                    await push_gateway.stop()
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
                    # The reconciliation task was explicitly cancelled above during shutdown.
                    pass
                if resident_flock_adapter is not None:
                    await resident_flock_adapter.stop()
                if mesh_revocation_publisher is not None:
                    await mesh_revocation_publisher.stop()
                await resident_runtime_service.close()
                await event_ingestion.close_all()
                if settings.compute is None and hasattr(pod_manager, "close"):
                    await pod_manager.close()
                for controller in resident_controllers:
                    if controller is not pod_manager and hasattr(controller, "close"):
                        await controller.close()
                if hasattr(gateway_adapter, "close"):
                    await gateway_adapter.close()
                preview_router = getattr(app.state, "preview_router", None)
                if preview_router is not None:
                    await preview_router.aclose()
                await git_registry.close()
                if hasattr(integration_repo, "close"):
                    await integration_repo.close()
                if audit_subscriber is not None:
                    await audit_subscriber.stop()
                if sleipnir_bus is not None and hasattr(sleipnir_bus, "stop"):
                    await sleipnir_bus.stop()
                _release_credential_store(settings)

    app.router.lifespan_context = lifespan

    apply_cors_middleware(app, settings.cors)

    # PAT revocation enforcement
    from niuu.adapters.pat_revocation_middleware import PATRevocationMiddleware

    app.add_middleware(
        PATRevocationMiddleware, websocket_check_interval=settings.pat.websocket_check_interval
    )
    # Verifies forge_session bearers in every identity mode and confines them to
    # their route allow-list (volundr.adapters.inbound.forge_session_auth).
    app.add_middleware(ForgeSessionAuthMiddleware)
    if skuld_registry is None and settings.session_preview.enabled:
        # Standalone Forge serves live previews itself (mini mode's root app does
        # it there). Outermost: an app's traffic meets none of Forge's own
        # middleware. The router is built in the lifespan, once the session
        # registry has its ownership guard and credential issuer.
        from niuu.preview_proxy import PreviewRouterMiddleware

        app.add_middleware(
            PreviewRouterMiddleware,
            domain=settings.session_preview.domain,
            router_provider=lambda: getattr(app.state, "preview_router", None),
        )

    preview_origin_template = _preview_origin_template(
        settings.session_preview, public_origin=public_origin, embedded=skuld_registry is not None
    )

    @app.get("/api/v1/forge/settings/live-preview", tags=["Sessions"])
    async def live_preview_config() -> dict[str, Any]:
        """Where a session's live previews open, for the web UI.

        ``origin_template`` holds ``{port}`` and ``{session_id}``; it is empty
        when this platform serves no live previews.
        """
        return {
            "enabled": bool(preview_origin_template),
            "origin_template": preview_origin_template,
        }

    @app.get("/health", tags=["Health"])
    @app.get("/api/v1/forge/health", include_in_schema=False)
    async def health_check() -> dict[str, Any]:
        """Health check endpoint, with presented-file usage when the store is on."""
        health: dict[str, Any] = {"status": "healthy"}
        signing = getattr(app.state, "mesh_signing", None)
        if signing is not None:
            health["mesh_attestation"] = {
                "required": signing,
                "state": "signed" if signing else "unauthenticated",
            }
        quota = getattr(app.state, "presented_file_quota", None)
        store = getattr(app.state, "presented_file_store", None)
        if quota is not None and store is not None:
            health["presented_files"] = await quota.usage(store)
        return health

    return app
