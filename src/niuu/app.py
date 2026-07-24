"""Niuu composition root for shared HTTP hosting and mount selection."""

from __future__ import annotations

import inspect
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from niuu.config import CorsConfig, NiuuSettings
from niuu.cors import apply_cors_middleware
from niuu.ports.plugin import APIRouteDomain, Service
from niuu.service_databases import bootstrap_sql_for_service as bootstrap_sql_for_service
from niuu.session_proxy import (  # noqa: F401
    SkuldPortRegistry,
    _bearer_token_from_ws,
    _configured_cors_origins,
    _install_skuld_registry,
    _proxy_forward_headers,
    _proxy_ws,
    _proxy_ws_identity,
    _sanitize_log,
    _session_http_connect_url,
    _session_target_url,
    get_skuld_registry,
    register_session_proxy_routes,
)

if TYPE_CHECKING:
    from cli.registry import PluginRegistry

logger = logging.getLogger(__name__)


def _local_service_host(host: str) -> str:
    """Return a loopback-safe host for intra-stack HTTP calls."""
    normalized = host.strip() or "127.0.0.1"
    if normalized in {"0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    return normalized


def _plugin_api_base_url(host: str, port: int) -> str:
    """Return the intra-stack base URL used by host-mounted plugin apps."""
    return f"http://{_local_service_host(host)}:{port}"


def _plugin_public_origin(public_host: str | None, host: str, port: int) -> str:
    """Return the browser-facing origin passed to hosted plugin apps."""
    normalized = str(public_host or host).strip() or "127.0.0.1"
    if normalized.startswith(("http://", "https://")):
        return normalized.rstrip("/")
    if normalized in {"0.0.0.0", "::", "[::]", "127.0.0.1"}:
        normalized = "localhost"
    return f"http://{normalized}:{port}"


def _create_plugin_api_app(plugin: Service, *, base_url: str) -> Any:
    """Create a plugin API app, passing root context to opt-in plugins only."""
    context: dict[str, Any] = {"base_url": base_url}
    return _create_plugin_api_app_with_context(plugin, **context)


def _create_plugin_api_app_with_context(plugin: Service, **context: Any) -> Any:
    """Create a plugin API app, passing only context keys its factory accepts."""
    factory = plugin.create_api_app
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return factory()

    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    if accepts_kwargs:
        return factory(**context)
    accepted_context = {key: value for key, value in context.items() if key in signature.parameters}
    return factory(**accepted_context)


_PLUGIN_API_PREFIXES: dict[str, list[str]] = {
    "volundr": ["/api/v1/volundr"],
    "audit": ["/api/v1/audit"],
    "identity": ["/api/v1/identity"],
    "features": ["/api/v1/features"],
    "credentials": ["/api/v1/credentials"],
    "integrations": ["/api/v1/integrations"],
    "tracker": ["/api/v1/tracker"],
    "ting": ["/api/v1/ting"],
    "niuu": ["/api/v1/niuu"],
}

_PLUGIN_ROUTE_DOMAINS: dict[str, str] = {
    "a2a-card-api": "ting",
    "admin-api": "volundr",
    "audit-api": "audit",
    "bifrost-api": "bifrost",
    "bifrost-observability-api": "bifrost",
    "credentials-api": "credentials",
    "features-api": "features",
    "forge-api": "guild",
    "guild-instances-api": "guild",
    "identity-api": "identity",
    "integrations-api": "integrations",
    "mimir-api": "mimir",
    "niuu-api": "niuu",
    "niuu-repos-api": "niuu",
    "niuu-shared-api": "niuu",
    "observatory-api": "observatory",
    "observatory-agents-api": "observatory",
    "observatory-events-api": "observatory",
    "observatory-registry-api": "observatory",
    "observatory-topology-api": "observatory",
    "persona-api": "personas",
    "ravn-aggregate-api": "guild",
    "ravn-api": "ravn",
    "ravn-budget-api": "ravn",
    "ravn-odin-api": "ravn",
    "ravn-runtime-api": "ravn",
    "ravn-session-api": "ravn",
    "ravn-trigger-api": "ravn",
    "ravn-valkyrie-api": "ravn",
    "llm-api": "bifrost",
    "catalog-api": "volundr",
    "dispatch-api": "ting",
    "event-api": "ting",
    "review-api": "ting",
    "session-api": "guild",
    "saga-api": "ting",
    "settings-api": "ting",
    "tenancy-api": "identity",
    "tracker-api": "tracker",
    "tracker-intake-api": "ting",
    "tokens-api": "identity",
    "ting-channel-api": "ting",
    "workflow-api": "ting",
    "ting-api": "ting",
}
_LEGACY_PLUGIN_DOMAIN_NAMES: dict[str, str] = {
    "ting": "ting-api",
    "niuu": "niuu-api",
}

_STATIC_ROUTE_DOMAINS = frozenset({"skuld-proxy", "runtime-config", "web-ui"})
_FULL_ROUTE_DOMAINS = frozenset({*_PLUGIN_ROUTE_DOMAINS.keys(), *_STATIC_ROUTE_DOMAINS})

DEFAULT_HOST_PROFILE = "full"
HOST_PROFILES: dict[str, frozenset[str]] = {
    "full": _FULL_ROUTE_DOMAINS,
    "api": frozenset(domain for domain in _FULL_ROUTE_DOMAINS if domain != "web-ui"),
}
_STATIC_ROUTE_PREFIXES: dict[str, tuple[str, ...]] = {
    "skuld-proxy": (
        "/s/{session_id}/session",
        "/s/{ravn_id}/sessions/{session_id}/session",
        "/s/{session_id}/ws/ravn/{peer_id}",
        "/s/{session_id}/api/{path:path}",
        "/s/{session_id}/health",
    ),
    "runtime-config": ("/config.json",),
    "web-ui": ("/assets", "/fonts", "/favicon.svg", "/favicon.ico", "/{path:path}"),
}


@dataclass(frozen=True)
class MountedRouteDomain:
    """Inventory record for a route domain selected by the niuu host."""

    name: str
    prefixes: tuple[str, ...]
    source: str
    plugin_name: str | None = None


def available_route_domains() -> frozenset[str]:
    """Return all currently known mountable route-domain names."""
    return _FULL_ROUTE_DOMAINS


def parse_enabled_mounts(raw_mounts: str | None) -> set[str] | None:
    """Parse a comma-separated mount list from CLI input."""
    if raw_mounts is None:
        return None
    mounts = {part.strip() for part in raw_mounts.split(",") if part.strip()}
    if not mounts:
        return None
    unknown = sorted(mounts - available_route_domains())
    if unknown:
        known = ", ".join(sorted(available_route_domains()))
        raise ValueError(f"Unknown route domains: {', '.join(unknown)}. Known domains: {known}")
    return mounts


def resolve_enabled_mounts(
    host_profile: str = DEFAULT_HOST_PROFILE,
    enabled_mounts: set[str] | None = None,
    *,
    no_web: bool = False,
) -> frozenset[str]:
    """Resolve the final mount set from a host profile and optional overrides."""
    if host_profile not in HOST_PROFILES:
        known = ", ".join(sorted(HOST_PROFILES))
        raise ValueError(f"Unknown host profile '{host_profile}'. Known profiles: {known}")

    mounts = set(enabled_mounts or HOST_PROFILES[host_profile])
    unknown = sorted(mounts - available_route_domains())
    if unknown:
        known = ", ".join(sorted(available_route_domains()))
        raise ValueError(f"Unknown route domains: {', '.join(unknown)}. Known domains: {known}")

    if no_web:
        mounts.discard("web-ui")

    return frozenset(mounts)


class _PrefixRestoreApp:
    """ASGI wrapper that restores the stripped mount prefix on the path."""

    def __init__(self, app: ASGIApp, prefix: str) -> None:
        self._app = app
        self._prefix = prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope)
            scope["path"] = self._prefix + scope["path"]
            raw = scope.get("raw_path")
            if raw:
                scope["raw_path"] = self._prefix.encode() + raw
        await self._app(scope, receive, send)


class _PrefixDispatchMiddleware:
    """Dispatch selected path prefixes to sub-apps without relying on Starlette mounts."""

    def __init__(self, app: ASGIApp, *, prefix_apps: list[tuple[str, ASGIApp]]) -> None:
        self._app = app
        self._prefix_apps = sorted(prefix_apps, key=lambda item: len(item[0]), reverse=True)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            raw_path = scope.get("raw_path")
            for prefix, sub_app in self._prefix_apps:
                if path != prefix and not path.startswith(f"{prefix}/"):
                    continue
                delegated_scope = dict(scope)
                delegated_scope["path"] = path[len(prefix) :]
                if raw_path:
                    delegated_scope["raw_path"] = raw_path[len(prefix.encode()) :]
                await sub_app(delegated_scope, receive, send)
                return
        await self._app(scope, receive, send)


class _ResidentSessionDispatchMiddleware:
    """Route registry-owned resident chat sockets through Guild."""

    def __init__(self, app: ASGIApp, *, guild_app: ASGIApp) -> None:
        self._app = app
        self._guild_app = guild_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            parts = scope.get("path", "").split("/")
            if (
                len(parts) == 6
                and parts[1] == "s"
                and parts[3] == "sessions"
                and parts[5] == "session"
            ):
                await self._guild_app(scope, receive, send)
                return
        await self._app(scope, receive, send)


def _declared_plugin_route_domains(
    registry: PluginRegistry,
) -> dict[str, list[tuple[str, APIRouteDomain]]]:
    """Collect plugin-declared route domains, with legacy fallback names."""
    declared: dict[str, list[tuple[str, APIRouteDomain]]] = {}
    for plugin_name, plugin in sorted(registry.plugins.items()):
        route_domains = tuple(plugin.api_route_domains())
        if not route_domains and plugin_name in _LEGACY_PLUGIN_DOMAIN_NAMES:
            route_domains = (
                APIRouteDomain(
                    name=_LEGACY_PLUGIN_DOMAIN_NAMES[plugin_name],
                    prefixes=tuple(_PLUGIN_API_PREFIXES.get(plugin_name, [])),
                    description=f"Legacy route-domain mapping for {plugin_name}.",
                ),
            )

        for route_domain in route_domains:
            declared.setdefault(route_domain.name, []).append((plugin_name, route_domain))
    return declared


def _backend_prefix_for_mount(plugin_name: str, public_prefix: str) -> str:
    """Map public mount prefixes to the backend route prefix a plugin actually serves."""
    if plugin_name == "bifrost":
        bifrost_prefix = "/api/v1/bifrost"
        if public_prefix == bifrost_prefix:
            return ""
        if public_prefix.startswith(f"{bifrost_prefix}/"):
            return public_prefix[len(bifrost_prefix) :]
    if plugin_name == "mimir" and public_prefix.startswith("/api/v1/mimir/mcp"):
        return public_prefix.replace("/api/v1/mimir/mcp", "/mcp", 1)
    if plugin_name == "mimir" and public_prefix.startswith("/api/v1/mimir"):
        return public_prefix.replace("/api/v1/mimir", "/mimir", 1)
    return public_prefix


def collect_route_inventory(
    *,
    registry: PluginRegistry,
    host_profile: str = DEFAULT_HOST_PROFILE,
    enabled_mounts: set[str] | None = None,
) -> tuple[MountedRouteDomain, ...]:
    """Return a normalized inventory of route domains selected for mounting."""
    active_mounts = resolve_enabled_mounts(
        host_profile,
        enabled_mounts,
        no_web=NiuuSettings().host.no_web,
    )
    declared_domains = _declared_plugin_route_domains(registry)

    inventory: list[MountedRouteDomain] = []
    for domain_name in sorted(active_mounts):
        if domain_name in declared_domains:
            entries = declared_domains[domain_name]
            prefixes = tuple(
                dict.fromkeys(
                    prefix for _, route_domain in entries for prefix in route_domain.prefixes
                )
            )
            plugin_name = ",".join(sorted({plugin_name for plugin_name, _ in entries}))
            inventory.append(
                MountedRouteDomain(
                    name=domain_name,
                    prefixes=prefixes,
                    source="plugin",
                    plugin_name=plugin_name,
                )
            )
            continue
        if domain_name in _PLUGIN_ROUTE_DOMAINS:
            continue
        inventory.append(
            MountedRouteDomain(
                name=domain_name,
                prefixes=_STATIC_ROUTE_PREFIXES.get(domain_name, ()),
                source="internal",
            )
        )
    return tuple(inventory)


def _rewrite_public_openapi_path(
    *,
    plugin_name: str,
    public_prefix: str,
    backend_path: str,
) -> str | None:
    """Rewrite a plugin-local OpenAPI path onto the host's public prefix."""
    backend_prefix = _backend_prefix_for_mount(plugin_name, public_prefix)

    if backend_prefix:
        if backend_path == backend_prefix:
            return public_prefix
        if backend_path.startswith(f"{backend_prefix}/"):
            return f"{public_prefix}{backend_path[len(backend_prefix) :]}"
        return None

    if not backend_path.startswith("/"):
        return None
    if backend_path == "/":
        return public_prefix
    return f"{public_prefix}{backend_path}"


def _merge_openapi_components(target: dict, source: dict, *, namespace: str) -> None:
    """Merge OpenAPI component dictionaries conservatively."""
    for key, value in source.items():
        if key not in target:
            target[key] = deepcopy(value)
            continue
        if isinstance(target[key], dict) and isinstance(value, dict):
            _merge_openapi_components(target[key], value, namespace=namespace)
            continue
        if target[key] != value:
            logger.warning(
                "Skipping conflicting OpenAPI component '%s' from %s",
                _sanitize_log(key),
                _sanitize_log(namespace),
            )


def _install_merged_openapi(
    *,
    root: FastAPI,
    sub_apps: list[tuple[str, FastAPI]],
    plugin_prefixes: dict[str, list[str]],
) -> None:
    """Install an OpenAPI generator that merges root and mounted plugin apps."""

    def merged_openapi() -> dict:
        cached = getattr(root, "openapi_schema", None)
        if cached is not None:
            return cached

        schema = get_openapi(
            title=root.title,
            version=root.version,
            description=root.description,
            routes=root.routes,
        )

        for plugin_name, sub_app in sub_apps:
            prefixes = tuple(dict.fromkeys(plugin_prefixes.get(plugin_name, [])))
            if not prefixes:
                continue

            sub_schema = sub_app.openapi()
            for backend_path, path_item in sub_schema.get("paths", {}).items():
                for public_prefix in prefixes:
                    public_path = _rewrite_public_openapi_path(
                        plugin_name=plugin_name,
                        public_prefix=public_prefix,
                        backend_path=backend_path,
                    )
                    if public_path is None:
                        continue
                    schema.setdefault("paths", {}).setdefault(public_path, {})
                    schema["paths"][public_path].update(deepcopy(path_item))

            sub_components = sub_schema.get("components")
            if isinstance(sub_components, dict):
                schema.setdefault("components", {})
                _merge_openapi_components(
                    schema["components"],
                    sub_components,
                    namespace=plugin_name,
                )

            existing_tags = {
                tag.get("name") for tag in schema.get("tags", []) if isinstance(tag, dict)
            }
            for tag in sub_schema.get("tags", []):
                tag_name = tag.get("name") if isinstance(tag, dict) else None
                if tag_name and tag_name not in existing_tags:
                    schema.setdefault("tags", []).append(deepcopy(tag))
                    existing_tags.add(tag_name)

        root.openapi_schema = schema
        return schema

    root.openapi = merged_openapi


def build_root_app(
    *,
    registry: PluginRegistry,
    host: str,
    port: int,
    public_host: str | None = None,
    host_profile: str = DEFAULT_HOST_PROFILE,
    enabled_mounts: set[str] | None = None,
    skuld_registry: SkuldPortRegistry | None = None,
) -> FastAPI:
    """Build the root FastAPI app that hosts selected route domains."""
    plugin_public_origin = _plugin_public_origin(public_host, host, port)
    plugin_api_base_url = _plugin_api_base_url(host, port)
    active_mounts = resolve_enabled_mounts(
        host_profile,
        enabled_mounts,
        no_web=NiuuSettings().host.no_web,
    )
    route_inventory = collect_route_inventory(
        registry=registry,
        host_profile=host_profile,
        enabled_mounts=enabled_mounts,
    )
    declared_domains = _declared_plugin_route_domains(registry)
    requested_plugins = {
        plugin_name
        for domain_name, entries in declared_domains.items()
        if domain_name in active_mounts
        for plugin_name, _ in entries
    }
    plugin_prefixes: dict[str, list[str]] = {}
    for domain_name, entries in declared_domains.items():
        if domain_name not in active_mounts:
            continue
        for plugin_name, route_domain in entries:
            plugin_prefixes.setdefault(plugin_name, []).extend(route_domain.prefixes)

    skuld_reg = skuld_registry or SkuldPortRegistry()
    sub_apps: list[tuple[str, FastAPI]] = []
    shared_api_apps: dict[str, FastAPI] = {}
    embedded_forge_app: ASGIApp | None = None
    plugin_order = sorted(
        registry.plugins.items(),
        # Build Volundr before Guild so Guild can use it as an embedded local
        # target in single-process/local mode.
        key=lambda item: (item[0] == "guild", item[0]),
    )
    for name, plugin in plugin_order:
        if name not in requested_plugins:
            continue
        try:
            shared_key = plugin.shared_api_app_key()
            if shared_key and shared_key in shared_api_apps:
                sub_app = shared_api_apps[shared_key]
            else:
                sub_app = _create_plugin_api_app_with_context(
                    plugin,
                    public_origin=plugin_public_origin,
                    base_url=plugin_api_base_url,
                    embedded_forge_app=embedded_forge_app,
                    skuld_registry=skuld_reg,
                )
                if shared_key and sub_app is not None:
                    shared_api_apps[shared_key] = sub_app
            if sub_app is None:
                continue
            if name == "volundr":
                embedded_forge_app = sub_app
            sub_apps.append((name, sub_app))
        except Exception:
            logger.exception("Failed to create API app for plugin: %s", name)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        exit_stacks: list[tuple[str, AsyncGenerator]] = []
        started_app_ids: set[int] = set()
        for name, sub_app in sub_apps:
            app_id = id(sub_app)
            if app_id in started_app_ids:
                logger.info("Reusing %s shared API app", name)
                continue
            lf = sub_app.router.lifespan_context
            if lf:
                gen = lf(sub_app)
                try:
                    await gen.__aenter__()
                    exit_stacks.append((name, gen))
                    started_app_ids.add(app_id)
                    logger.info("Started %s lifespan", name)
                except Exception:
                    logger.exception("Failed to start %s lifespan", name)

        yield

        for name, gen in reversed(exit_stacks):
            try:
                await gen.__aexit__(None, None, None)
                logger.info("Stopped %s lifespan", name)
            except Exception:
                logger.exception("Failed to stop %s lifespan", name)

    root = FastAPI(
        title="Niuu Platform",
        description="Unified API gateway for selected Niuu route domains.",
        version="0.1.0",
        lifespan=lifespan,
    )
    cors_origins = _configured_cors_origins()
    if cors_origins:
        apply_cors_middleware(
            root,
            CorsConfig(
                allowed_origins=cors_origins,
                allow_credentials=True,
            ),
        )
    root.state.legacy_route_hits = {}
    root.state.route_inventory = route_inventory

    logger.info(
        "Selected route domains: %s",
        ", ".join(f"{item.name}[{item.source}]" for item in route_inventory) or "(none)",
    )

    @root.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    prefix_apps: list[tuple[str, ASGIApp]] = []
    for name, sub_app in sub_apps:
        prefixes = plugin_prefixes.get(name, [])
        if not prefixes:
            logger.debug("No API prefix configured for plugin: %s", name)
            continue
        for prefix in prefixes:
            backend_prefix = _backend_prefix_for_mount(name, prefix)
            wrapped = _PrefixRestoreApp(sub_app, backend_prefix)
            prefix_apps.append((prefix, wrapped))
        logger.info("Mounted %s API at %s", name, ", ".join(prefixes))

    if prefix_apps:
        root.add_middleware(_PrefixDispatchMiddleware, prefix_apps=prefix_apps)

    guild_app = next((sub_app for name, sub_app in sub_apps if name == "guild"), None)
    if guild_app is not None and "skuld-proxy" in active_mounts:
        root.add_middleware(_ResidentSessionDispatchMiddleware, guild_app=guild_app)

    _install_merged_openapi(
        root=root,
        sub_apps=sub_apps,
        plugin_prefixes=plugin_prefixes,
    )

    if "skuld-proxy" in active_mounts:
        register_session_proxy_routes(root, skuld_reg)

    live_config_template: str | None = None

    def render_live_config(origin: str) -> str:
        assert live_config_template is not None
        ws_origin = (
            origin.replace("https://", "wss://", 1)
            if origin.startswith("https://")
            else origin.replace("http://", "ws://", 1)
        )
        payload = live_config_template.replace("http://localhost:8080", origin)
        payload = payload.replace("http://127.0.0.1:8080", origin)
        payload = payload.replace("ws://localhost:8080", ws_origin)
        payload = payload.replace("ws://127.0.0.1:8080", ws_origin)
        payload = payload.replace("wss://localhost:8080", ws_origin)
        payload = payload.replace("wss://127.0.0.1:8080", ws_origin)
        return payload

    if "web-ui" not in active_mounts:
        if "runtime-config" in active_mounts:

            @root.get("/config.json")
            async def config_json() -> dict[str, str]:
                return {"apiBaseUrl": f"http://{host}:{port}"}

        logger.info("Web UI disabled by host profile or --no-web")
        return root

    try:
        from starlette.staticfiles import StaticFiles

        from cli.resources import web_dist_dir

        dist = web_dist_dir()
        root.mount("/assets", StaticFiles(directory=str(dist / "assets")), name="web-assets")
        if (dist / "fonts").is_dir():
            root.mount("/fonts", StaticFiles(directory=str(dist / "fonts")), name="web-fonts")

        from starlette.responses import FileResponse

        favicon_path = dist / "favicon.svg"
        if favicon_path.exists():

            @root.get("/favicon.svg", include_in_schema=False)
            @root.get("/favicon.ico", include_in_schema=False)
            async def favicon() -> FileResponse:
                return FileResponse(str(favicon_path), media_type="image/svg+xml")

        live_config_path = dist / "config.live.json"
        if live_config_path.exists():
            live_config_template = live_config_path.read_text(encoding="utf-8")

            @root.get("/config.live.json", include_in_schema=False)
            async def live_config(request: Request) -> Response:
                origin = str(request.base_url).rstrip("/")
                return Response(content=render_live_config(origin), media_type="application/json")

        if "runtime-config" in active_mounts:

            @root.get("/config.json", include_in_schema=False)
            async def config_json(request: Request):
                if live_config_template is None:
                    return {"apiBaseUrl": f"http://{host}:{port}"}

                origin = str(request.base_url).rstrip("/")
                return Response(content=render_live_config(origin), media_type="application/json")

        index_html = (dist / "index.html").read_bytes()

        from starlette.responses import HTMLResponse

        @root.get("/{path:path}", include_in_schema=False)
        async def spa_fallback(path: str) -> HTMLResponse | JSONResponse:
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            return HTMLResponse(content=index_html)

        logger.info("Serving web UI from %s", dist)
    except FileNotFoundError:
        logger.warning("Web UI assets not found — skipping static file serving")

    return root


from niuu.root_server import RootServer  # noqa: E402,F401
