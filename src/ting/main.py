"""Ting — saga coordinator FastAPI application."""

from __future__ import annotations

import logging
import sys
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, Response

from niuu.adapters.http_integrations import HTTPIntegrationRepository
from niuu.adapters.pat_revocation_middleware import PATRevocationMiddleware
from niuu.adapters.postgres_integrations import PostgresIntegrationRepository
from niuu.cors import apply_cors_middleware
from niuu.domain.models import Principal
from niuu.domain.services.pat_validator import PATValidator
from niuu.ports.integrations import IntegrationRepository
from niuu.service_runtime import create_workload_identity_service
from niuu.utils import import_class, resolve_secret_kwargs
from ravn.adapters.personas.loader import FilesystemPersonaAdapter
from ravn.ports.persona import PersonaPort
from ting.adapters.a2a_push_dispatcher import A2APushDispatcher
from ting.adapters.github_git import GitHubGitAdapter
from ting.adapters.guild_instances import GuildInstanceRegistryClient
from ting.adapters.inbound.rest_integrations import create_telegram_setup_router
from ting.adapters.inbound.rest_pats import create_pats_router
from ting.adapters.inbound.rest_telegram_webhook import create_telegram_webhook_router
from ting.adapters.notification_channel_factory import NotificationChannelFactory
from ting.adapters.postgres_a2a_push import PostgresA2APushConfigRepository
from ting.adapters.postgres_dispatcher import PostgresDispatcherRepository
from ting.adapters.postgres_notification_subscriptions import (
    PostgresNotificationSubscriptionRepository,
)
from ting.adapters.postgres_sagas import PostgresSagaRepository
from ting.adapters.postgres_workflow_campaigns import PostgresWorkflowCampaignRepository
from ting.adapters.postgres_workflows import PostgresWorkflowRepository
from ting.adapters.tracker_factory import TrackerAdapterFactory
from ting.adapters.volundr_factory import VolundrAdapterFactory
from ting.api.a2a import create_a2a_router
from ting.api.a2a_card import create_agent_card_router
from ting.api.audit import create_audit_router
from ting.api.dispatch import (
    create_dispatch_router,
    resolve_dispatch_service,
    resolve_volundr,
    resolve_volundr_factory,
)
from ting.api.dispatch import resolve_dispatcher_repo as dispatch_resolve_dispatcher_repo
from ting.api.dispatch import resolve_saga_repo as dispatch_resolve_saga_repo
from ting.api.dispatcher import create_dispatcher_router, resolve_dispatcher_repo
from ting.api.dispatcher import resolve_event_bus as dispatcher_resolve_event_bus
from ting.api.events import create_events_router, resolve_event_bus
from ting.api.flock_config import create_flock_config_router
from ting.api.flock_flows import (
    create_flock_flows_router,
    resolve_flow_provider,
    resolve_persona_names,
)
from ting.api.health import create_health_router
from ting.api.persona_names import build_persona_names_dependency
from ting.api.phases import create_saga_phases_router
from ting.api.research import create_research_router, resolve_workflow_campaign_repo
from ting.api.runs import create_runs_router, resolve_git, resolve_run_repo
from ting.api.runs import resolve_tracker as resolve_runs_tracker
from ting.api.runs import resolve_volundr as resolve_runs_volundr
from ting.api.saga_previews import create_saga_previews_router
from ting.api.sagas import create_sagas_router, resolve_llm, resolve_saga_repo
from ting.api.sagas import resolve_git as sagas_resolve_git
from ting.api.sagas import resolve_volundr as sagas_resolve_volundr
from ting.api.sessions import create_sessions_router
from ting.api.settings import create_settings_router
from ting.api.specs import create_specs_router
from ting.api.tracker import (
    create_canonical_tracker_router,
    create_tracker_router,
    resolve_trackers,
)
from ting.api.workflows import create_workflows_router, resolve_workflow_repo
from ting.config import Settings
from ting.domain.services.activity_subscriber import SessionActivitySubscriber
from ting.domain.services.dispatch_service import (
    DispatchConfig as DispatchServiceConfig,
)
from ting.domain.services.dispatch_service import (
    DispatchService,
)
from ting.domain.services.notification import NotificationService
from ting.domain.services.review_engine import ReviewEngine
from ting.domain.services.reviewer_session import ReviewerSessionService
from ting.domain.services.workflow_campaign_projector import WorkflowCampaignProjector
from ting.infrastructure.database import database_pool
from ting.ports.dispatcher_repository import DispatcherRepository
from ting.ports.event_bus import EventBusPort
from ting.ports.flock_flow import FlockFlowProvider
from ting.ports.git import GitPort
from ting.ports.saga_repository import SagaRepository
from ting.ports.tracker import TrackerPort
from ting.ports.volundr import VolundrPort
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_repository import WorkflowRepository
from ting.system_workflows import seed_system_workflows

logger = logging.getLogger(__name__)


def _create_http_auth_adapter(config):
    """Create a dynamic outbound HTTP auth adapter."""
    cls = import_class(config.adapter)
    kwargs = resolve_secret_kwargs(config.kwargs, config.secret_kwargs_env)
    return cls(**kwargs)


def _use_local_volundr_factory(settings: Settings) -> bool:
    """Return True when Ting should force the single local Volundr adapter."""
    return settings.auth.allow_anonymous_dev and not settings.volundr.use_connection_factory_in_dev


def _configure_logging(settings: Settings) -> None:
    """Configure structured logging based on settings."""
    level_name = settings.logging.level.upper()
    log_format = settings.logging.format.lower()
    level = getattr(logging, level_name, logging.INFO)

    if log_format == "json":
        fmt = (
            '{"time":"%(asctime)s","level":"%(levelname)s",'
            '"logger":"%(name)s","message":"%(message)s"}'
        )
    else:
        fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

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

    # Silence noisy loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def _ensure_telegram_subscription_from_integration(
    pool: object,
    integration_repo: IntegrationRepository,
    credential_store: object,
) -> None:
    """Idempotently bind the seeded Telegram chat for inbound auth.

    The webhook router authenticates incoming messages against
    ``notification_subscriptions`` (Ting-side table, ``chat_id → owner_id``),
    while the seeded MESSAGING integration stores the chat_id in
    ``integration_connections`` (niuu-shared table, used only for outbound
    credentials). They don't talk to each other. This helper copies the
    chat_id from the integration credential into a subscription row so
    /status, /approve, etc. work out of the box without manual SQL.

    Runs once per Ting boot; the INSERT is gated by an existence check so it
    is safe to call repeatedly.
    """
    import json as _json

    from niuu.domain.models import IntegrationType

    connections = await integration_repo.list_connections(
        owner_id="dev-user",
        integration_type=IntegrationType.MESSAGING,
    )
    for conn in connections:
        if not conn.enabled or conn.slug != "telegram":
            continue
        cred = await credential_store.get_value(  # type: ignore[attr-defined]
            "user", "dev-user", conn.credential_name
        )
        if not isinstance(cred, dict):
            continue
        chat_id = cred.get("chat_id", "")
        if not chat_id:
            continue
        existing = await pool.fetchrow(  # type: ignore[attr-defined]
            """
            SELECT 1 FROM notification_subscriptions
            WHERE channel = 'telegram'
              AND config->>'chat_id' = $1
              AND owner_id = $2
            LIMIT 1
            """,
            str(chat_id),
            conn.owner_id,
        )
        if existing is not None:
            return
        await pool.execute(  # type: ignore[attr-defined]
            """
            INSERT INTO notification_subscriptions (owner_id, channel, config, enabled)
            VALUES ($1, 'telegram', $2::jsonb, true)
            """,
            conn.owner_id,
            _json.dumps({"chat_id": str(chat_id)}),
        )
        logger.info(
            "Telegram subscription auto-bound from integration: chat=%s owner=%s",
            chat_id,
            conn.owner_id,
        )
        return


async def _resolve_dev_telegram_bot_token(
    integration_repo: IntegrationRepository,
    credential_store: object,
) -> str:
    """Look up the dev-user's Telegram bot_token from integration_connections.

    Returns "" if no enabled MESSAGING connection with slug "telegram" exists,
    or if the credential record is missing/empty. The seeded value lives in
    Volundr's ``integrations.seed_connections`` block in ~/.niuu/config.yaml
    and gets persisted on Volundr boot — Ting reads from the same shared
    ``integration_connections`` table.
    """
    from niuu.domain.models import IntegrationType

    connections = await integration_repo.list_connections(
        owner_id="dev-user",
        integration_type=IntegrationType.MESSAGING,
    )
    for conn in connections:
        if not conn.enabled or conn.slug != "telegram":
            continue
        cred = await credential_store.get_value(  # type: ignore[attr-defined]
            "user", "dev-user", conn.credential_name
        )
        if isinstance(cred, dict):
            token = cred.get("bot_token", "")
            if token:
                return str(token)
    return ""


async def _seed_webhook_integration(
    integration_repo: IntegrationRepository,
    credential_store: object,
    url: str,
    secret: str = "",
    min_urgency: str = "low",
) -> None:
    """Seed the outbound webhook channel as a MESSAGING IntegrationConnection.

    Idempotent — uses a fixed ID so repeated calls update the same row.
    The NotificationService picks the channel up via NotificationChannelFactory
    alongside Telegram and any other MESSAGING integrations.
    """
    from datetime import UTC, datetime

    from niuu.domain.models import IntegrationConnection, IntegrationType, SecretType

    owner_id = "dev-user"
    cred_name = "webhook-config"

    await credential_store.store(
        owner_type="user",
        owner_id=owner_id,
        name=cred_name,
        secret_type=SecretType.API_KEY,
        data={"secret": secret} if secret else {},
    )

    connection = IntegrationConnection(
        id="0e1b3a82-7c4f-5d62-9e8b-1e4cf5ab21d3",
        owner_id=owner_id,
        integration_type=IntegrationType.MESSAGING,
        adapter="ting.adapters.webhook_notification.WebhookNotificationAdapter",
        credential_name=cred_name,
        config={"url": url, "min_urgency": min_urgency},
        enabled=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        slug="webhook",
    )
    await integration_repo.save_connection(connection)


async def _seed_linear_integration(
    integration_repo: IntegrationRepository,
    credential_store: object,
    api_key: str,
    team_id: str = "",
    adapter_class: str = "ting.adapters.linear.LinearTrackerAdapter",
) -> None:
    """Seed Linear integration from config into the DB.

    Idempotent — uses a fixed ID so repeated calls update the same row.
    """
    from datetime import UTC, datetime

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

    config: dict = {}
    if team_id:
        config["team_id"] = team_id

    connection = IntegrationConnection(
        id="6a397506-ccc6-5f89-be1e-47108ad702c8",
        owner_id=owner_id,
        integration_type=IntegrationType.ISSUE_TRACKER,
        adapter=adapter_class,
        credential_name=cred_name,
        config=config,
        enabled=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        slug="linear",
    )
    await integration_repo.save_connection(connection)


def create_app(
    settings: Settings | None = None,
    *,
    public_origin: str | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    if settings is None:
        settings = Settings()

    _configure_logging(settings)

    app = FastAPI(
        title="Ting — Saga Coordinator",
        description="Decomposes specs into sagas, phases, and runs.",
        version="0.1.0",
    )

    app.state.settings = settings
    app.state.workload_identity_service = create_workload_identity_service(
        settings.workload_identity
    )

    # -- Routers --
    app.include_router(create_health_router())
    app.include_router(create_canonical_tracker_router())
    app.include_router(create_tracker_router())
    app.include_router(create_saga_previews_router())
    app.include_router(create_sagas_router())
    app.include_router(create_saga_phases_router())
    app.include_router(create_runs_router())
    app.include_router(create_dispatch_router())
    app.include_router(create_dispatcher_router())
    app.include_router(create_events_router(settings.events.keepalive_interval))
    app.include_router(create_audit_router())
    app.include_router(create_sessions_router())
    app.include_router(create_settings_router())
    app.include_router(create_workflows_router())
    app.include_router(create_agent_card_router(settings.a2a))
    app.include_router(create_a2a_router())
    app.include_router(create_research_router())
    app.include_router(create_specs_router())
    app.include_router(create_flock_flows_router())
    app.include_router(create_flock_config_router())
    from ting.adapters.inbound.auth import extract_principal as _extract_principal

    app.include_router(create_pats_router(_extract_principal))
    # Shared integration CRUD lives in niuu-shared. Ting keeps only its
    # Telegram setup bridge until that flow moves to the shared surface too.
    app.include_router(
        create_telegram_setup_router(
            telegram_bot_username=settings.telegram.bot_username,
            telegram_hmac_key=settings.telegram.hmac_key,
            telegram_hmac_sig_length=settings.telegram.hmac_signature_length,
        )
    )
    app.include_router(create_telegram_webhook_router())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """Manage application lifecycle."""
        settings = app.state.settings
        async with database_pool(settings.database) as pool:
            app.state.pool = pool

            # Wire shared credential/integration infrastructure
            if settings.shared_integrations.base_url:
                integration_repo = HTTPIntegrationRepository(
                    settings.shared_integrations.base_url,
                    timeout=settings.shared_integrations.timeout_seconds,
                )
                logger.info(
                    "Integration repository: shared HTTP (%s)",
                    settings.shared_integrations.base_url,
                )
            else:
                integration_repo = PostgresIntegrationRepository(pool)
                logger.info("Integration repository: local postgres")
            cs_cfg = settings.credential_store
            cs_cls = import_class(cs_cfg.adapter)
            cs_kwargs = resolve_secret_kwargs(cs_cfg.kwargs, cs_cfg.secret_kwargs_env)
            credential_store = cs_cls(**cs_kwargs)
            logger.info("Credential store: %s", cs_cfg.adapter.rsplit(".", 1)[-1])

            # Expose shared infrastructure on app.state for REST routers
            app.state.integration_repo = integration_repo
            app.state.credential_store = credential_store

            # Wire adapter factories (used by autonomous dispatcher)
            guild_registry_client: GuildInstanceRegistryClient | None = None
            if _use_local_volundr_factory(settings):
                from ting.adapters.volundr_factory import LocalVolundrAdapterFactory

                app.state.volundr_factory = LocalVolundrAdapterFactory(
                    url=settings.volundr.url,
                )
                app.state.instance_registry = None
                logger.info("Volundr factory: local (no PAT required)")
            else:
                if not settings.guild_registry.base_url:
                    raise RuntimeError(
                        "Ting requires guild_registry.base_url outside anonymous local mode"
                    )
                guild_registry_client = GuildInstanceRegistryClient(
                    settings.guild_registry.base_url,
                    timeout=settings.guild_registry.timeout_seconds,
                    auth=_create_http_auth_adapter(settings.guild_registry.auth),
                )
                app.state.instance_registry = guild_registry_client
                logger.info(
                    "Instance registry: Guild HTTP (%s)",
                    settings.guild_registry.base_url,
                )
                app.state.volundr_factory = VolundrAdapterFactory(
                    guild_registry_client,
                    credential_store,
                    allow_unauthenticated=settings.auth.allow_anonymous_dev,
                    target_auth=_create_http_auth_adapter(settings.volundr.auth),
                )
            # Default Volundr adapter for code paths that don't have a per-owner
            # context (e.g. the Telegram webhook router's _get_volundr). In
            # mini/anonymous-dev mode the factory always returns the same
            # singleton; in multi-owner production the per-owner factory
            # lookup is the right path and this default is a fallback.
            app.state.volundr = await app.state.volundr_factory.primary_for_owner("dev-user")
            app.state.tracker_factory = TrackerAdapterFactory(
                integration_repo, credential_store, pool=pool
            )

            # Seed Linear integration from config so the tracker factory
            # finds it in the DB (same seed as Volundr).
            if settings.linear.api_key:
                await _seed_linear_integration(
                    integration_repo,
                    credential_store,
                    api_key=settings.linear.api_key,
                    team_id=settings.linear.team_id,
                    adapter_class="ting.adapters.linear.LinearTrackerAdapter",
                )
                logger.info("Linear integration seeded from config")

            if settings.webhook.url:
                await _seed_webhook_integration(
                    integration_repo,
                    credential_store,
                    url=settings.webhook.url,
                    secret=settings.webhook.secret,
                    min_urgency=settings.webhook.min_urgency,
                )
                logger.info("Webhook integration seeded from config")

            # Override the tracker resolver dependency with factory delegation
            from ting.adapters.inbound.auth import extract_principal

            async def _resolve(
                principal: Principal = Depends(extract_principal),
            ) -> list[TrackerPort]:
                return await app.state.tracker_factory.for_owner(principal.user_id)

            app.dependency_overrides[resolve_trackers] = _resolve

            # Wire saga repository
            saga_repo = PostgresSagaRepository(pool)
            app.state.saga_repo = saga_repo

            async def _resolve_saga_repo() -> SagaRepository:
                return saga_repo

            app.dependency_overrides[resolve_saga_repo] = _resolve_saga_repo
            app.dependency_overrides[dispatch_resolve_saga_repo] = _resolve_saga_repo
            app.dependency_overrides[resolve_run_repo] = _resolve_saga_repo

            # Wire notification subscription repository (Telegram webhook auth)
            notification_sub_repo = PostgresNotificationSubscriptionRepository(pool)
            app.state.notification_sub_repo = notification_sub_repo

            # Wire dispatcher repository — seed auto_continue from config so
            # solo-dev setups don't have to flip the flag via the API on every
            # fresh DB. Once a row exists, the API/UI is the source of truth.
            dispatcher_repo = PostgresDispatcherRepository(
                pool,
                auto_continue_default=settings.dispatch.auto_continue,
            )
            app.state.dispatcher_repo = dispatcher_repo

            async def _resolve_dispatcher_repo() -> DispatcherRepository:
                return dispatcher_repo

            app.dependency_overrides[resolve_dispatcher_repo] = _resolve_dispatcher_repo
            app.dependency_overrides[dispatch_resolve_dispatcher_repo] = _resolve_dispatcher_repo

            # Wire Sleipnir publisher early (bus only — bridge wired after event_bus is ready)
            sleipnir_bus = None
            if settings.sleipnir.enabled:
                sl_cls = import_class(settings.sleipnir.adapter)
                sleipnir_bus = sl_cls(**settings.sleipnir.kwargs)

            # Wire flock flow provider (dynamic adapter pattern)
            ff_cfg = settings.flock_flows
            ff_cls = import_class(ff_cfg.adapter)
            flow_provider: FlockFlowProvider = ff_cls(**ff_cfg.kwargs)
            app.state.flow_provider = flow_provider
            logger.info("Flock flow provider: %s", ff_cfg.adapter.rsplit(".", 1)[-1])

            async def _resolve_flow_provider() -> FlockFlowProvider:
                return flow_provider

            app.dependency_overrides[resolve_flow_provider] = _resolve_flow_provider

            persona_source: PersonaPort | None = FilesystemPersonaAdapter()
            app.state.persona_source = persona_source
            app.dependency_overrides[resolve_persona_names] = build_persona_names_dependency(
                persona_source
            )

            workflow_repo = PostgresWorkflowRepository(pool)
            app.state.workflow_repo = workflow_repo
            seeded_system_workflows = await seed_system_workflows(workflow_repo)
            if seeded_system_workflows:
                logger.info(
                    "Seeded %d bundled system workflow(s)",
                    len(seeded_system_workflows),
                )

            async def _resolve_workflow_repo() -> WorkflowRepository:
                return workflow_repo

            app.dependency_overrides[resolve_workflow_repo] = _resolve_workflow_repo

            workflow_campaign_repo = PostgresWorkflowCampaignRepository(pool)
            app.state.workflow_campaign_repo = workflow_campaign_repo

            a2a_push_dispatcher = None
            if settings.a2a.push_notifications_enabled:
                a2a_push_repo = PostgresA2APushConfigRepository(
                    pool,
                    settings.a2a.push_encryption_key.get_secret_value(),
                    max_error_chars=settings.a2a.push_max_error_chars,
                )
                a2a_push_dispatcher = A2APushDispatcher(
                    repo=a2a_push_repo,
                    auth=_create_http_auth_adapter(settings.a2a.push_auth),
                    allowed_callback_origins=settings.a2a.push_callback_allowed_origins,
                    timeout_seconds=settings.a2a.push_timeout_seconds,
                    poll_seconds=settings.a2a.push_poll_seconds,
                    retry_initial_seconds=settings.a2a.push_retry_initial_seconds,
                    retry_max_seconds=settings.a2a.push_retry_max_seconds,
                    claim_limit=settings.a2a.push_claim_limit,
                    lease_seconds=settings.a2a.push_lease_seconds,
                    max_url_chars=settings.a2a.push_max_url_chars,
                    max_credential_chars=settings.a2a.push_max_credential_chars,
                    max_configs_page_size=settings.a2a.push_max_configs_page_size,
                )
                await a2a_push_dispatcher.start()
                logger.info("A2A push dispatcher started")
            app.state.a2a_push_dispatcher = a2a_push_dispatcher

            async def _resolve_workflow_campaign_repo() -> WorkflowCampaignRepository:
                return workflow_campaign_repo

            app.dependency_overrides[resolve_workflow_campaign_repo] = (
                _resolve_workflow_campaign_repo
            )

            # Wire DispatchService
            dispatch_svc = DispatchService(
                tracker_factory=app.state.tracker_factory,
                volundr_factory=app.state.volundr_factory,
                saga_repo=saga_repo,
                dispatcher_repo=dispatcher_repo,
                config=DispatchServiceConfig(
                    default_system_prompt=settings.dispatch.default_system_prompt,
                    default_model=settings.dispatch.default_model,
                    default_session_definition=settings.dispatch.default_session_definition,
                    dispatch_prompt_template=settings.dispatch.dispatch_prompt_template,
                    session_definitions=settings.session_definitions,
                    configured_models=list(settings.bifrost.models),
                    live_flock=settings.dispatch.flock,
                ),
                sleipnir_publisher=sleipnir_bus,
                flow_provider=flow_provider,
                workflow_repo=workflow_repo,
            )
            app.state.dispatch_service = dispatch_svc

            async def _resolve_dispatch_service() -> DispatchService:
                return dispatch_svc

            app.dependency_overrides[resolve_dispatch_service] = _resolve_dispatch_service

            # Wire Volundr adapter — per-user resolution via factory.
            async def _resolve_volundr_per_user(
                principal: Principal = Depends(extract_principal),
            ) -> VolundrPort:
                adapter = await app.state.volundr_factory.primary_for_owner(principal.user_id)
                if adapter is None:
                    from fastapi import HTTPException

                    raise HTTPException(
                        status_code=503,
                        detail="No Volundr adapter available from Guild registry",
                    )
                return adapter

            app.dependency_overrides[resolve_volundr] = _resolve_volundr_per_user
            app.dependency_overrides[resolve_runs_volundr] = _resolve_volundr_per_user
            app.dependency_overrides[sagas_resolve_volundr] = _resolve_volundr_per_user

            async def _resolve_factory() -> VolundrAdapterFactory:
                return app.state.volundr_factory

            app.dependency_overrides[resolve_volundr_factory] = _resolve_factory

            # Wire Git adapter
            git_adapter = GitHubGitAdapter(settings.git.token)

            async def _resolve_git() -> GitPort:
                return git_adapter

            app.dependency_overrides[resolve_git] = _resolve_git
            app.dependency_overrides[sagas_resolve_git] = _resolve_git

            # Wire tracker for runs (uses first available tracker)
            async def _resolve_runs_tracker_dep(
                principal: Principal = Depends(extract_principal),
            ) -> TrackerPort:
                trackers = await app.state.tracker_factory.for_owner(principal.user_id)
                if not trackers:
                    from fastapi import HTTPException, status

                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="No tracker configured",
                    )
                return trackers[0]

            app.dependency_overrides[resolve_runs_tracker] = _resolve_runs_tracker_dep

            # Wire personal access token service
            from ting.adapters.postgres_pats import PostgresPATRepository
            from ting.domain.services.pat import PATService

            pat_repo = PostgresPATRepository(pool)

            # Wire PAT revocation validator
            pat_validator = PATValidator(
                repo=pat_repo,
                cache_ttl=settings.pat.revocation_cache_ttl,
                revoked_cache_ttl=settings.pat.revoked_cache_ttl,
            )
            app.state.pat_validator = pat_validator

            # Resolve the token issuer (IDP adapter) via dynamic import
            token_issuer_cls = import_class(settings.pat.token_issuer_adapter)
            token_issuer = token_issuer_cls(**settings.pat.token_issuer_kwargs)

            pat_service = PATService(
                repo=pat_repo,
                token_issuer=token_issuer,
                ttl_days=settings.pat.ttl_days,
                validator=pat_validator,
            )
            app.state.pat_service = pat_service

            # Wire event bus (dynamic adapter pattern)
            eb_cfg = settings.event_bus
            eb_cls = import_class(eb_cfg.adapter)
            eb_kwargs = {
                "max_clients": settings.events.max_sse_clients,
                "log_size": settings.events.activity_log_size,
                **eb_cfg.kwargs,
            }
            event_bus: EventBusPort = eb_cls(**eb_kwargs)
            app.state.event_bus = event_bus
            # DispatchService was constructed before the event bus existed
            # (it has no hard dependency on it), so back-fill the bus now
            # so events like run.state_changed (RUNNING) actually emit.
            dispatch_svc._event_bus = event_bus
            logger.info("Event bus: %s", eb_cfg.adapter.rsplit(".", 1)[-1])

            async def _resolve_event_bus() -> EventBusPort:
                return event_bus

            app.dependency_overrides[resolve_event_bus] = _resolve_event_bus
            app.dependency_overrides[dispatcher_resolve_event_bus] = _resolve_event_bus

            workflow_campaign_projector = WorkflowCampaignProjector(
                repo=workflow_campaign_repo,
                volundr_factory=app.state.volundr_factory,
                event_bus=event_bus,
                push_dispatcher=a2a_push_dispatcher,
            )
            app.state.workflow_campaign_projector = workflow_campaign_projector
            await workflow_campaign_projector.start()

            # Wire Sleipnir bridge (sleipnir_bus already created above; bridge needs event_bus)
            ting_sleipnir_bridge = None
            if sleipnir_bus is not None:
                from ting.adapters.sleipnir_event_bridge import TingSleipnirBridge  # noqa: PLC0415

                ting_sleipnir_bridge = TingSleipnirBridge(
                    event_bus=event_bus,
                    publisher=sleipnir_bus,
                )
                await ting_sleipnir_bridge.start()
                logger.info(
                    "Ting Sleipnir bridge started: adapter=%s",
                    settings.sleipnir.adapter.rsplit(".", 1)[-1],
                )
            # Store publisher on app.state for route handlers
            app.state.sleipnir_publisher = sleipnir_bus

            # Wire Telegram reply client (shared httpx.AsyncClient)
            from ting.adapters.inbound.rest_telegram_webhook import (
                TelegramReplyClient,
            )

            # Bot token resolution: prefer the explicit settings.telegram.bot_token,
            # fall back to the value stored on the dev-user's MESSAGING integration
            # (slug "telegram") so the seeded credential in
            # integrations.seed_connections doesn't have to be duplicated.
            bot_token = settings.telegram.bot_token
            if not bot_token:
                bot_token = await _resolve_dev_telegram_bot_token(
                    integration_repo, credential_store
                )
                if bot_token:
                    logger.info("Telegram bot_token resolved from MESSAGING integration")

            # Bridge the seeded chat_id into notification_subscriptions so the
            # webhook router's inbound auth check passes without any manual
            # SQL or deeplink dance.
            await _ensure_telegram_subscription_from_integration(
                pool, integration_repo, credential_store
            )

            telegram_reply_client = TelegramReplyClient(
                bot_token=bot_token,
                timeout=settings.telegram.reply_timeout,
            )
            app.state.telegram_reply_client = telegram_reply_client

            from ting.adapters.telegram_polling import (  # noqa: PLC0415
                TelegramPollingService,
            )

            telegram_polling: TelegramPollingService | None = None
            if settings.telegram.polling:
                self_url = settings.telegram.polling_self_url
                if not self_url:
                    # Match the platform's bound interface so the in-process
                    # forward succeeds. uvicorn typically binds to a single
                    # NIC, so 127.0.0.1 is unreachable when start-dev pinned
                    # the host to a LAN IP.
                    self_url = (
                        f"http://{settings.local_platform_host}:{settings.local_platform_port}"
                    )
                telegram_polling = TelegramPollingService(
                    bot_token=bot_token,
                    webhook_secret=settings.telegram.webhook_secret,
                    self_url=self_url,
                    timeout=settings.telegram.reply_timeout,
                )
                await telegram_polling.start()
            app.state.telegram_polling = telegram_polling

            # Wire LLM adapter (dynamic adapter pattern)
            from ting.ports.llm import LLMPort as _LLMPort

            llm_cfg = settings.llm
            llm_cls = import_class(llm_cfg.adapter)
            llm_kwargs = resolve_secret_kwargs(llm_cfg.kwargs, llm_cfg.secret_kwargs_env)
            llm_kwargs.setdefault("min_estimate_hours", llm_cfg.min_estimate_hours)
            llm_kwargs.setdefault("max_estimate_hours", llm_cfg.max_estimate_hours)
            if llm_cfg.decomposition_system_prompt:
                llm_kwargs.setdefault(
                    "decomposition_system_prompt", llm_cfg.decomposition_system_prompt
                )
            llm_adapter = llm_cls(**llm_kwargs)
            logger.info("LLM adapter: %s", llm_cfg.adapter.rsplit(".", 1)[-1])

            # Determine effective LLM config for in-process RavnDispatcher.
            # Prefers dispatch.in_process.llm_config; falls back to
            # dispatch.flock.llm_config so both paths share the same model by default.
            in_process_llm_config: dict = dict(settings.dispatch.in_process.llm_config) or dict(
                settings.dispatch.flock.llm_config
            )

            # Wire Sleipnir publisher into the LLM adapter when both are enabled.
            if sleipnir_bus is not None and hasattr(llm_adapter, "set_publisher"):
                from ting.adapters.bifrost_publisher import BifrostPublisher  # noqa: PLC0415

                bifrost_pub = BifrostPublisher(
                    sleipnir_bus,
                    agent_id=llm_cfg.agent_id,
                )
                llm_adapter.set_publisher(bifrost_pub)
                logger.info("Bifrost publisher wired to Sleipnir")

            # Wire default LLM config into the LLM adapter (BifrostAdapter decomposer path).
            if in_process_llm_config and hasattr(llm_adapter, "set_default_llm_config"):
                llm_adapter.set_default_llm_config(in_process_llm_config)
                logger.info(
                    "BifrostAdapter: default_llm_config injected keys=%s",
                    list(in_process_llm_config.keys()),
                )

            async def _resolve_llm() -> _LLMPort:
                return llm_adapter

            app.dependency_overrides[resolve_llm] = _resolve_llm

            # Wire notification service
            channel_factory = NotificationChannelFactory(integration_repo, credential_store)
            app.state.channel_factory = channel_factory

            notification_service = NotificationService(
                event_bus=event_bus,
                channel_factory=channel_factory,
                public_origin=public_origin or settings.notification.public_origin,
                confidence_threshold=settings.notification.confidence_threshold,
            )
            app.state.notification_service = notification_service
            if settings.notification.enabled:
                await notification_service.start()

            # Wire RavnDispatcher (shared instance for in-process single-turn calls)
            from ting.adapters.ravn_dispatcher import RavnDispatcher  # noqa: PLC0415

            ravn_dispatcher: RavnDispatcher | None = None
            if settings.review.ravn_arbiter_enabled:
                llm_kwargs_for_ravn = resolve_secret_kwargs(
                    settings.llm.kwargs, settings.llm.secret_kwargs_env
                )
                ravn_dispatcher = RavnDispatcher(
                    base_url=llm_kwargs_for_ravn.get("base_url", "https://api.anthropic.com"),
                    api_key=llm_kwargs_for_ravn.get("api_key", ""),
                    model=settings.review.ravn_arbiter_model,
                    timeout=settings.review.ravn_arbiter_timeout,
                    default_llm_config=in_process_llm_config or None,
                )
                logger.info(
                    "RavnDispatcher wired: model=%s llm_config_keys=%s",
                    settings.review.ravn_arbiter_model,
                    list(in_process_llm_config.keys()) if in_process_llm_config else [],
                )

            # Wire automated review engine (subscribes to run.state_changed events)
            reviewer_service = ReviewerSessionService(
                volundr_factory=app.state.volundr_factory,
                review_config=settings.review,
            )
            review_engine = ReviewEngine(
                tracker_factory=app.state.tracker_factory,
                volundr_factory=app.state.volundr_factory,
                git=git_adapter,
                review_config=settings.review,
                event_bus=event_bus,
                reviewer_service=reviewer_service,
                dispatcher_repo=dispatcher_repo,
                dispatch_service=dispatch_svc,
                ravn_dispatcher=ravn_dispatcher,
                saga_repo=saga_repo,
            )
            app.state.review_engine = review_engine
            await review_engine.start()

            # Wire event-driven session completion subscriber
            # Uses VolundrAdapterFactory for per-owner authenticated SSE subscriptions
            subscriber = SessionActivitySubscriber(
                volundr_factory=app.state.volundr_factory,
                tracker_factory=app.state.tracker_factory,
                dispatcher_repo=dispatcher_repo,
                event_bus=event_bus,
                config=settings.watcher,
                review_engine=review_engine,
                sleipnir_publisher=sleipnir_bus,
                ravn_scope_adherence_threshold=settings.ravn_outcome.scope_adherence_threshold,
                workflow_campaign_projector=workflow_campaign_projector,
            )
            app.state.subscriber = subscriber
            await subscriber.start()

            # Wire EventTriggerAdapter (requires Sleipnir subscriber)
            event_trigger_adapter = None
            if settings.event_triggers.enabled and sleipnir_bus is not None:
                from ting.adapters.event_trigger import build_event_trigger_adapter  # noqa: PLC0415

                if not hasattr(sleipnir_bus, "subscribe"):
                    logger.warning(
                        "EventTriggerAdapter: sleipnir adapter does not support subscribe(), "
                        "skipping event trigger setup"
                    )
                else:
                    et_cfg = settings.event_triggers
                    event_trigger_adapter = build_event_trigger_adapter(
                        subscriber=sleipnir_bus,
                        saga_repo=saga_repo,
                        volundr_factory=app.state.volundr_factory,
                        event_bus=event_bus,
                        config=et_cfg,
                        initial_confidence=settings.review.initial_confidence,
                    )
                    await event_trigger_adapter.start()
                    app.state.event_trigger_adapter = event_trigger_adapter
                    logger.info(
                        "EventTriggerAdapter started: %d rule(s)",
                        len(et_cfg.rules),
                    )

            # Wire RavnOutcomeHandler (canonical ravn.session.ended plus compatibility task events)
            ravn_outcome_handler = None
            ravn_help_needed_handler = None
            if (
                settings.ravn_outcome.enabled
                and sleipnir_bus is not None
                and hasattr(sleipnir_bus, "subscribe")
            ):
                from ting.adapters.ravn_help_needed_handler import (  # noqa: PLC0415
                    RavnHelpNeededHandler,
                )
                from ting.adapters.ravn_outcome_handler import RavnOutcomeHandler  # noqa: PLC0415

                ravn_help_needed_handler = RavnHelpNeededHandler(
                    subscriber=sleipnir_bus,
                    tracker_factory=app.state.tracker_factory,
                    event_bus=event_bus,
                    owner_id=settings.ravn_outcome.owner_id,
                )
                await ravn_help_needed_handler.start()
                app.state.ravn_help_needed_handler = ravn_help_needed_handler
                logger.info("RavnHelpNeededHandler started")

                ravn_outcome_handler = RavnOutcomeHandler(
                    subscriber=sleipnir_bus,
                    tracker_factory=app.state.tracker_factory,
                    review_engine=review_engine,
                    owner_id=settings.ravn_outcome.owner_id,
                    scope_adherence_threshold=settings.ravn_outcome.scope_adherence_threshold,
                )
                await ravn_outcome_handler.start()
                app.state.ravn_outcome_handler = ravn_outcome_handler
                logger.info("RavnOutcomeHandler started")

            logger.info("Ting started — database pool ready")
            yield

            # Lifecycle cleanup
            if ravn_help_needed_handler is not None:
                await ravn_help_needed_handler.stop()
            if ravn_outcome_handler is not None:
                await ravn_outcome_handler.stop()
            if event_trigger_adapter is not None:
                await event_trigger_adapter.stop()
            await subscriber.stop()
            if ting_sleipnir_bridge is not None:
                await ting_sleipnir_bridge.stop()
            await workflow_campaign_projector.stop()
            if a2a_push_dispatcher is not None:
                await a2a_push_dispatcher.stop()
            await review_engine.stop()
            if ravn_dispatcher is not None:
                await ravn_dispatcher.close()
            await notification_service.stop()
            if telegram_polling is not None:
                await telegram_polling.stop()
            await telegram_reply_client.close()
            if isinstance(integration_repo, HTTPIntegrationRepository):
                await integration_repo.close()
            if guild_registry_client is not None:
                await guild_registry_client.close()
            if hasattr(llm_adapter, "close"):
                await llm_adapter.close()
            logger.info("Ting shutting down")

    app.router.lifespan_context = lifespan
    apply_cors_middleware(app, settings.cors)
    app.add_middleware(PATRevocationMiddleware)

    @app.middleware("http")
    async def correlation_id_middleware(request: Request, call_next):  # noqa: ANN001
        """Attach a correlation ID to every request."""
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        request.state.correlation_id = correlation_id
        response: Response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response

    @app.get("/health")
    @app.get("/api/v1/ting/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()


def main() -> None:  # pragma: no cover
    """Run the Ting API server."""
    import uvicorn

    settings = Settings()

    uvicorn.run(
        "ting.main:app",
        host=settings.server_host,
        port=settings.server_port,
        workers=settings.server_workers,
        access_log=False,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
