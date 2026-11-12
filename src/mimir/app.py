"""Standalone Mímir FastAPI application.

Used when running Mímir as an independent service (``python -m mimir serve``).
The same ``MimirRouter`` can also be mounted on another FastAPI app without any
code changes.

Usage (standalone)::

    from mimir.app import create_app
    from mimir.config import MimirServiceConfig
    import uvicorn

    config = MimirServiceConfig(
        backend={"adapter": "mimir.adapters.gbrain.GBrainMimirAdapter", "mcp_url": "..."},
        name="shared",
        role="shared",
    )
    app = create_app(config)
    uvicorn.run(app, host=config.host, port=config.port)
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from identity.adapters.jwks import JwksBearerAuthenticationAdapter
from mimir.adapters.memory_rest import MemoryRouter, is_session_route
from mimir.backend import build_backend
from mimir.config import MimirServiceConfig
from mimir.facts import FactCallerResolver, FactService
from mimir.live_activity import LiveActivityRecorder
from mimir.mcp import MimirMcpServer
from mimir.registry import MimirRegistryStore
from mimir.router import MimirRouter
from niuu.domain.services.token_scope import credential_allows_route
from niuu.ports.identity import HeaderAuthenticationPort, InvalidTokenError
from niuu.ports.mimir_facts import MimirFactPort
from niuu.settings_schema import (
    SettingsFieldSchema,
    SettingsProviderSchema,
    SettingsSectionSchema,
)

logger = logging.getLogger(__name__)


def create_app(config: MimirServiceConfig) -> FastAPI:
    """Create the standalone Mímir FastAPI application.

    Args:
        config: Service configuration (backend, host, port, name, role).

    Returns:
        A configured FastAPI application with the Mímir router mounted at
        ``/mimir``.

    Raises:
        MimirConfigurationError: ``config.backend`` is missing or does not name a
            ``MimirPort``. There is no default store.
    """
    from niuu.service_runtime import _get_auth_mode, _validate_identity_adapter_class
    from niuu.utils import import_class

    auth_mode = _get_auth_mode(config)
    if auth_mode == "none" and config.tenant_id:
        # AllowAllHeaderAuthenticationAdapter always asserts a fixed
        # principal (tenant "default" unless identity_kwargs overrides it),
        # never config.tenant_id's real value — every request would then
        # permanently fail the tenant match in enforce_identity below,
        # a self-inflicted, permanent 403 rather than a real security
        # boundary. auth_mode: none means "every caller is the unrestricted
        # host operator"; a configured tenant_id is a multi-tenant
        # deployment's concept and contradicts that.
        raise ValueError(
            f"auth_mode: none cannot be combined with tenant_id={config.tenant_id!r}: "
            "the allow-all identity adapter does not assert this instance's real "
            "tenant, so every request would be refused by the tenant check. Set "
            "auth_mode: oidc with a real identity adapter for a tenant-scoped "
            "instance, or clear tenant_id for a single-tenant 'none' instance."
        )
    identity_cls = import_class(config.identity_adapter)
    _validate_identity_adapter_class(identity_cls, auth_mode)
    identity_adapter = identity_cls(**config.identity_kwargs)

    adapter = build_backend(config.backend, setting="MIMIR__BACKEND (backend:)")
    registry_store = MimirRegistryStore(Path(config.path).expanduser() / ".mimir-registry.json")
    registry_store.ensure_entry(
        name=config.name,
        role=config.role,
        kind="local" if not config.announce_url else "remote",
        url=config.announce_url or "",
        categories=config.categories,
        default_read_priority=0,
        desc="Current Mimir service instance",
    )
    eval_capture_dir = Path(config.path).expanduser() / "evals" if config.eval_capture else None
    deployment = None
    if config.deployment:
        import importlib

        settings = dict(config.deployment)
        module, name = settings.pop("adapter").rsplit(".", 1)
        deployment = getattr(importlib.import_module(module), name)(**settings)
    # Shared between the REST router and the MCP server so a resident
    # reading/writing through either surface shows up in the same
    # GET /activity/live window (see .claude/rules/no-magic-numbers.md —
    # the buffer size and window come from config, not literals here).
    live_activity = LiveActivityRecorder(
        buffer_size=config.live_activity.buffer_size,
        window_seconds=config.live_activity.window_seconds,
    )
    mimir_router = MimirRouter(
        deployment=deployment,
        public_url=config.announce_url or "",
        tenant_id=config.tenant_id,
        platform_tenant_id=config.platform_tenant_id,
        adapter=adapter,
        name=config.name,
        role=config.role,
        registry_store=registry_store,
        eval_capture_dir=eval_capture_dir,
        auth=identity_adapter,
        auth_mode=auth_mode,
        live_activity=live_activity,
    )
    fact_port = None
    fact_service = None
    fact_callers = None
    if config.facts is not None:
        fact_cls = import_class(config.facts.adapter)
        if not (isinstance(fact_cls, type) and issubclass(fact_cls, MimirFactPort)):
            raise ValueError(
                f"facts.adapter {config.facts.adapter!r} is not a MimirFactPort; "
                "name a class that implements niuu.ports.mimir_facts.MimirFactPort"
            )
        fact_port = fact_cls(**config.facts.kwargs)
        fact_service = FactService(
            fact_port, source=config.name, chars_per_token=config.facts.chars_per_token
        )
        fact_callers = FactCallerResolver(
            config.facts.trust.role_levels, config.facts.trust.scope_levels
        )
    memory_router = _build_memory_router(
        config, registry_store=registry_store, identity=identity_adapter
    )
    mcp_server = MimirMcpServer(
        adapter=adapter,
        name=config.name,
        auth=identity_adapter,
        auth_mode=auth_mode,
        live_activity=live_activity,
        platform_tenant_id=config.platform_tenant_id,
        facts=fact_service,
        fact_callers=fact_callers,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if memory_router is not None:
            memory_router.start()
        try:
            yield
        finally:
            if deployment is not None:
                await deployment.close()
            close_facts = getattr(fact_port, "close", None)
            if close_facts is not None:
                await close_facts()
            close_backend = getattr(adapter, "close", None)
            if close_backend is not None:
                await close_backend()
            if memory_router is not None:
                await memory_router.close()

    app = FastAPI(
        title=f"Mímir — {config.name}",
        description=(
            "Standalone Mímir knowledge service. "
            f"Role: {config.role}. "
            "Exposes the Mímir wiki over HTTP for Ravens, Valkyries, and Pi room nodes."
        ),
        version="1.0.0",
        docs_url="/mimir/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    # Configured and instrumented here, not in lifespan: Starlette builds and
    # caches its middleware stack on the app's first ASGI __call__ (which is
    # also how the lifespan startup event arrives), so instrumenting from
    # inside a lifespan handler has no effect.
    from niuu.observability import (
        configure_observability,
        install_uvicorn_log_redaction,
        instrument_fastapi_app,
        instrument_httpx_client,
    )

    telemetry = configure_observability(
        config.observability,
        resource_attributes={"service.namespace": "mimir", "mimir.instance.name": config.name},
        component="mimir",
        default_service_name="mimir",
    )
    instrument_fastapi_app(app, telemetry, component="mimir")
    install_uvicorn_log_redaction()
    instrument_httpx_client(telemetry)

    health_paths = {"/health", "/mimir/health", "/api/v1/mimir/health"}

    @app.middleware("http")
    async def enforce_identity(request: Request, call_next):
        """Two independent claims enforced app-wide, before any router logic:

        - ``auth_mode: oidc`` means every non-health inbound path is
          signature-verified. Anonymous is never a degraded-but-allowed
          state here (unlike ``envoy``/``none``): a missing or invalid
          bearer is always 401, never treated as an anonymous caller — see
          .claude/rules/no-fallbacks.md. This also covers the MCP router
          (mounted below), which has no auth dependency of its own —
          without this, an anonymous caller could reach `/mcp` tool calls
          (including writes) or `/mimir/registry/mounts` (including a
          local-path mount naming an arbitrary host directory) with no
          credential at all.
        - A configured ``tenant_id`` means this instance belongs to exactly
          one tenant; a caller — verified or not — from a different tenant
          is refused, regardless of auth_mode (unchanged from before).
        - Under ``oidc``, a scoped workload token or scoped PAT (see
          ``niuu.domain.services.token_scope``) is denied — Mímir is not one
          of those credentials' named entry points, the same posture Bifröst's
          ``OidcAuthAdapter``/``PATAuthAdapter`` enforce for model access.

        Identity comes from the configured identity adapter, never from
        caller-supplied ``x-auth-*`` headers directly: on a host without
        Envoy those headers are not trustworthy.
        """
        if request.url.path in health_paths:
            return await call_next(request)
        if memory_router is not None and is_session_route(request.method, request.url.path):
            # The memory session routes authenticate every call through Forge with the
            # session's own credential (mimir.adapters.memory_rest), never anonymously; the
            # OIDC and credential_allows_route checks below are for Mímir's own callers.
            return await call_next(request)
        if auth_mode != "oidc" and not config.tenant_id:
            return await call_next(request)

        try:
            principal = await identity_adapter.validate_headers(dict(request.headers))
        except InvalidTokenError:
            # 'oidc' returns here — a missing/invalid bearer is always 401,
            # never merely anonymous (see the docstring above). For every
            # other auth_mode, an unverified caller is anonymous: principal
            # stays None and only the tenant check below (if configured)
            # can still refuse the request.
            if auth_mode == "oidc":
                return JSONResponse(
                    status_code=401, content={"detail": "Invalid or missing bearer token"}
                )
            principal = None

        # identity_adapter.validate_headers always returns a real Principal
        # on success (never None) — so reaching here with auth_mode == "oidc"
        # means principal is guaranteed non-None; the exception branch above
        # is the only path that leaves it None, and that path already
        # returned when auth_mode == "oidc".
        if auth_mode == "oidc":
            token = JwksBearerAuthenticationAdapter._extract_bearer(dict(request.headers))
            if not credential_allows_route(token, request.method, request.url.path):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Credential does not grant access to this route"},
                )
        if config.tenant_id and (principal is None or principal.tenant_id != config.tenant_id):
            return JSONResponse(
                status_code=403,
                content={"detail": "Knowledge instance belongs to a different tenant"},
            )
        return await call_next(request)

    if memory_router is not None:
        memory_routes = memory_router.router()
        app.include_router(memory_routes, prefix="/mimir")
        app.include_router(memory_routes, prefix="/api/v1/mimir", include_in_schema=False)
    app.include_router(mimir_router.router, prefix="/mimir")
    app.include_router(mimir_router.router, prefix="/api/v1/mimir", include_in_schema=False)
    app.include_router(mcp_server.router(), prefix="/mcp")
    app.include_router(mcp_server.router(), prefix="/api/v1/mimir/mcp", include_in_schema=False)
    app.state.mimir_config = config

    @app.get("/health", tags=["Health"])
    @app.get("/mimir/health", include_in_schema=False)
    @app.get("/api/v1/mimir/health", include_in_schema=False)
    async def health() -> dict[str, object]:
        return {
            "status": "healthy",
            "name": config.name,
            "role": config.role,
        }

    @app.get("/settings", response_model=SettingsProviderSchema)
    @app.get("/mimir/settings", response_model=SettingsProviderSchema, include_in_schema=False)
    @app.get(
        "/api/v1/mimir/settings",
        response_model=SettingsProviderSchema,
        include_in_schema=False,
    )
    async def settings() -> SettingsProviderSchema:
        categories = ", ".join(config.categories or ["all"])
        return SettingsProviderSchema(
            title="Mimir",
            subtitle="knowledge system settings",
            scope="service",
            sections=[
                SettingsSectionSchema(
                    id="service",
                    label="Service",
                    description=(
                        "Mounted Mimir instance characteristics exposed by the "
                        "current host profile."
                    ),
                    fields=[
                        SettingsFieldSchema(
                            key="instance_name",
                            label="Instance Name",
                            type="text",
                            value=config.name,
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="role",
                            label="Role",
                            type="text",
                            value=config.role,
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="backend",
                            label="Backend",
                            type="text",
                            value=str(config.backend["adapter"]),
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="category_scope",
                            label="Category Scope",
                            type="text",
                            value=categories,
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="announce_url",
                            label="Announce URL",
                            type="text",
                            value=config.announce_url or "disabled",
                            read_only=True,
                        ),
                    ],
                )
            ],
        )

    # Wire compression — see niuu.gzip_sse for SSE safety. The standalone
    # service had none, so /mimir/graph shipped 1.1 MB and /mimir/pages 880 KB
    # of repetitive JSON raw to every browser that opened the Mímir page.
    # Added last, so it is the outermost layer and covers every router above.
    from niuu.gzip_sse import SSESafeGZipMiddleware

    app.add_middleware(SSESafeGZipMiddleware, minimum_size=4096)

    return app


def _service_auth(spec: dict | None):
    """The gateway's service credential (``memory.service_auth``), a dynamic HttpAuthPort."""
    from niuu.utils import import_class

    if not spec or not str(spec.get("adapter") or "").strip():
        raise ValueError(
            "memory.service_auth is not set: the gateway authenticates as a service to Forge "
            "(retention) and to the Bifröst catalog. Set {adapter: "
            "niuu.adapters.outbound.http_auth.WorkloadIdentityBearerTokenAuthAdapter, ...} on "
            "a cluster, or {adapter: niuu.adapters.outbound.http_auth.NoAuthHeaderAdapter} in "
            "mini mode without auth"
        )
    adapter = import_class(str(spec["adapter"]))
    auth = adapter(**{k: v for k, v in spec.items() if k != "adapter"})
    if not (
        callable(getattr(auth, "headers", None)) and callable(getattr(auth, "invalidate", None))
    ):
        raise ValueError(f"memory.service_auth adapter {spec['adapter']!r} is not an HttpAuthPort")
    return auth


def _build_memory_router(
    config: MimirServiceConfig,
    *,
    registry_store: MimirRegistryStore,
    identity: HeaderAuthenticationPort,
) -> MemoryRouter | None:
    """The memory gateway's routes, or None when ``memory.enabled`` is off.

    Enabled means every part must be buildable now: Forge's URL, each memory
    system, every tenant's system and the capabilities its layers need, Bifröst's
    URL, the service credential, and the answer model when memory_ask is on.
    """
    memory = config.memory
    if not memory.enabled:
        return None
    from mimir.adapters.attached_registry import RegistryAttachedMemories
    from mimir.adapters.bifrost_answers import BifrostAnswerModel
    from mimir.adapters.forge_directory import ForgeSessionDirectory
    from mimir.adapters.forge_lifetimes import ForgeLayerLifetimes
    from mimir.adapters.forge_people import ForgePeopleDirectory
    from mimir.memory.answers import MemoryAnswers
    from mimir.memory.curation import Curation
    from mimir.memory.gateway import MemoryGateway
    from mimir.memory.retention import RetentionSweeper
    from mimir.memory.systems import MemorySystems

    if memory.ask_enabled and not memory.answer_model.strip():
        raise ValueError(
            "memory.answer_model is empty while memory.ask_enabled: name the Bifröst model "
            "memory_ask answers with, or set ask_enabled: false to go without memory_ask"
        )
    service_auth = _service_auth(memory.service_auth)
    directory = ForgeSessionDirectory(
        memory.forge_base_url, timeout_seconds=memory.forge_timeout_seconds
    )
    systems = MemorySystems.from_config(memory.systems, memory.tenants)
    attached = RegistryAttachedMemories(
        registry_store, platform_tenant_id=config.platform_tenant_id
    )
    gateway = MemoryGateway(
        systems,
        default_result_count=memory.default_result_count,
        attached=attached,
    )
    answers = MemoryAnswers(
        gateway,
        BifrostAnswerModel(
            memory.bifrost_url,
            timeout_seconds=memory.bifrost_timeout_seconds,
            service_auth=service_auth,
        ),
        answer_model=memory.answer_model if memory.ask_enabled else "",
        retrieval_count=memory.ask_retrieval_count,
        context_pack_share=memory.context_pack_share,
        chars_per_token=memory.chars_per_token,
    )
    curation = Curation(
        gateway,
        ForgePeopleDirectory(memory.forge_base_url, timeout_seconds=memory.forge_timeout_seconds),
        org_admin_roles=memory.org_admin_roles,
    )
    sweeper = RetentionSweeper(
        systems,
        ForgeLayerLifetimes(
            memory.forge_base_url,
            timeout_seconds=memory.forge_timeout_seconds,
            service_auth=service_auth,
        ),
        retention_days=memory.retention_days,
        interval_seconds=memory.retention_sweep_seconds,
        forget=gateway.forget,
    )
    return MemoryRouter(
        gateway,
        directory,
        tenant_id=config.tenant_id,
        systems=systems,
        answers=answers,
        curation=curation,
        identity=identity,
        sweeper=sweeper,
        attached=attached,
    )
