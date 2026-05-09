"""Niuu composition root for shared HTTP hosting and mount selection."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.openapi.utils import get_openapi
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from niuu.config import CorsConfig
from niuu.cors import apply_cors_middleware
from niuu.ports.plugin import APIRouteDomain, Service

if TYPE_CHECKING:
    from cli.registry import PluginRegistry
    from niuu.ports.embedded_database import EmbeddedDatabasePort

logger = logging.getLogger(__name__)


def _configured_cors_origins() -> list[str]:
    """Return explicitly configured CORS origins for the unified niuu host."""
    raw = os.environ.get("NIUU_CORS_ORIGINS", "").strip()
    if not raw:
        return []
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


class SkuldPortRegistry:
    """Maps session IDs to their Skuld subprocess ports."""

    def __init__(self, state_file: Path | None = None) -> None:
        self._ports: dict[str, int] = {}
        self._state_file = (
            state_file
            or Path(
                os.environ.get("NIUU_FORGE_STATE_FILE", "~/.niuu/forge-state.json")
            ).expanduser()
        )

    def register(self, session_id: str, port: int) -> None:
        self._ports[session_id] = port

    def unregister(self, session_id: str) -> None:
        self._ports.pop(session_id, None)

    def get_port(self, session_id: str) -> int | None:
        port = self._ports.get(session_id)
        if port is not None:
            return port
        recovered_port = self._recover_port(session_id)
        if recovered_port is not None:
            self._ports[session_id] = recovered_port
        return recovered_port

    def _recover_port(self, session_id: str) -> int | None:
        if not self._state_file.exists():
            return None
        try:
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(payload, dict):
            return None
        info = payload.get(session_id)
        if not isinstance(info, dict):
            return None
        if info.get("state") not in {"running", "starting"}:
            return None
        port = info.get("port")
        return port if isinstance(port, int) else None


_skuld_registry: SkuldPortRegistry | None = None


def get_skuld_registry() -> SkuldPortRegistry | None:
    """Return the active SkuldPortRegistry, if any."""
    return _skuld_registry


def _local_service_host(host: str) -> str:
    """Return a loopback-safe host for intra-stack HTTP calls."""
    normalized = host.strip() or "127.0.0.1"
    if normalized in {"0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    return normalized


_PLUGIN_API_PREFIXES: dict[str, list[str]] = {
    "volundr": ["/api/v1/forge"],
    "audit": ["/api/v1/audit"],
    "identity": ["/api/v1/identity"],
    "features": ["/api/v1/features"],
    "credentials": ["/api/v1/credentials"],
    "integrations": ["/api/v1/integrations"],
    "tracker": ["/api/v1/tracker"],
    "tyr": ["/api/v1/tyr"],
    "niuu": ["/api/v1/niuu"],
}

_PLUGIN_ROUTE_DOMAINS: dict[str, str] = {
    "admin-api": "volundr",
    "audit-api": "audit",
    "bifrost-api": "bifrost",
    "bifrost-observability-api": "bifrost",
    "credentials-api": "credentials",
    "features-api": "features",
    "forge-api": "volundr",
    "git-api": "volundr",
    "identity-api": "identity",
    "integrations-api": "integrations",
    "mimir-api": "mimir",
    "niuu-api": "niuu",
    "observatory-api": "observatory",
    "observatory-events-api": "observatory",
    "observatory-registry-api": "observatory",
    "observatory-topology-api": "observatory",
    "persona-api": "personas",
    "ravn-api": "ravn",
    "ravn-budget-api": "ravn",
    "ravn-runtime-api": "ravn",
    "ravn-session-api": "ravn",
    "ravn-trigger-api": "ravn",
    "llm-api": "bifrost",
    "catalog-api": "volundr",
    "dispatch-api": "tyr",
    "event-api": "tyr",
    "review-api": "tyr",
    "session-api": "volundr",
    "saga-api": "tyr",
    "settings-api": "tyr",
    "tenancy-api": "identity",
    "tracker-api": "tracker",
    "tracker-project-api": "tyr",
    "tokens-api": "identity",
    "tyr-integrations-api": "tyr",
    "workflow-api": "tyr",
    "workspace-api": "volundr",
    "tyr-api": "tyr",
}
_LEGACY_PLUGIN_DOMAIN_NAMES: dict[str, str] = {
    "tyr": "tyr-api",
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
    if plugin_name == "bifrost" and public_prefix.startswith("/api/v1/bifrost"):
        return public_prefix.replace("/api/v1/bifrost", "", 1)
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
        no_web=os.environ.get("NIUU_NO_WEB") == "true",
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
    host_profile: str = DEFAULT_HOST_PROFILE,
    enabled_mounts: set[str] | None = None,
    skuld_registry: SkuldPortRegistry | None = None,
) -> FastAPI:
    """Build the root FastAPI app that hosts selected route domains."""
    active_mounts = resolve_enabled_mounts(
        host_profile,
        enabled_mounts,
        no_web=os.environ.get("NIUU_NO_WEB") == "true",
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

    sub_apps: list[tuple[str, FastAPI]] = []
    shared_api_apps: dict[str, FastAPI] = {}
    for name, plugin in sorted(registry.plugins.items()):
        if name not in requested_plugins:
            continue
        try:
            shared_key = plugin.shared_api_app_key()
            if shared_key and shared_key in shared_api_apps:
                sub_app = shared_api_apps[shared_key]
            else:
                sub_app = plugin.create_api_app()
                if shared_key and sub_app is not None:
                    shared_api_apps[shared_key] = sub_app
            if sub_app is None:
                continue
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

    _install_merged_openapi(
        root=root,
        sub_apps=sub_apps,
        plugin_prefixes=plugin_prefixes,
    )

    skuld_reg = skuld_registry or SkuldPortRegistry()

    if "skuld-proxy" in active_mounts:

        @root.websocket("/s/{session_id}/session")
        async def skuld_ws_proxy(
            websocket: WebSocket,  # noqa: F811
            session_id: str,
        ) -> None:
            """Proxy browser WebSocket to the Skuld subprocess."""
            port = skuld_reg.get_port(session_id)
            if port is None:
                await websocket.close(code=4004, reason="Session not found")
                return

            await websocket.accept()
            import websockets.asyncio.client as ws_client

            try:
                async with ws_client.connect(
                    f"ws://127.0.0.1:{port}/session",
                    additional_headers={
                        k.decode(): v.decode()
                        for k, v in websocket.headers.raw
                        if k.decode().lower() in ("authorization", "cookie", "x-auth-user-id")
                    },
                ) as skuld_ws:

                    async def browser_to_skuld() -> None:
                        try:
                            async for msg in websocket.iter_text():
                                await skuld_ws.send(msg)
                        except Exception:
                            pass

                    async def skuld_to_browser() -> None:
                        try:
                            async for msg in skuld_ws:
                                await websocket.send_text(str(msg))
                        except Exception:
                            pass

                    done, pending = await asyncio.wait(
                        [
                            asyncio.create_task(browser_to_skuld()),
                            asyncio.create_task(skuld_to_browser()),
                        ],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    for task in done:
                        task.result()
            except Exception:
                logger.debug("Skuld WS proxy ended for session %s", _sanitize_log(session_id))
            finally:
                try:
                    await websocket.close()
                except Exception:
                    pass

        @root.api_route(
            "/s/{session_id}/api/{path:path}",
            methods=["GET", "POST", "PUT", "DELETE"],
            include_in_schema=False,
        )
        async def skuld_http_proxy(request: Request, session_id: str, path: str) -> Response:
            """Proxy HTTP requests to the Skuld subprocess."""
            port = skuld_reg.get_port(session_id)
            if port is None:
                return JSONResponse({"detail": "Session not found"}, status_code=404)

            import re
            from urllib.parse import quote

            import httpx

            allowed_segment = re.compile(r"^[A-Za-z0-9._~-]+$")
            raw_segments = path.split("/")
            normalized_segments: list[str] = []
            for seg in raw_segments:
                if seg in ("", ".", ".."):
                    return JSONResponse({"detail": "Invalid path"}, status_code=400)
                if "\\" in seg or not allowed_segment.fullmatch(seg):
                    return JSONResponse({"detail": "Invalid path"}, status_code=400)
                normalized_segments.append(seg)

            sanitized_path = "/".join(quote(seg, safe="") for seg in normalized_segments)
            url = f"http://127.0.0.1:{port}/api/{sanitized_path}"
            params = dict(request.query_params)
            headers = {
                k: v
                for k, v in request.headers.items()
                if k.lower() not in ("host", "content-length", "transfer-encoding")
            }
            body = await request.body()

            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.request(
                        method=request.method,
                        url=url,
                        params=params,
                        headers=headers,
                        content=body if body else None,
                    )
                return Response(
                    content=resp.content,
                    status_code=resp.status_code,
                    headers=dict(resp.headers),
                )
            except httpx.ConnectError:
                return JSONResponse(
                    {"detail": "Skuld broker not ready"},
                    status_code=502,
                )

        @root.get("/s/{session_id}/health", include_in_schema=False)
        async def skuld_health_proxy(request: Request, session_id: str) -> Response:
            """Proxy health check to the Skuld subprocess."""
            del request
            port = skuld_reg.get_port(session_id)
            if port is None:
                return JSONResponse({"detail": "Session not found"}, status_code=404)

            import httpx

            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(f"http://127.0.0.1:{port}/health")
                return Response(
                    content=resp.content,
                    status_code=resp.status_code,
                    headers=dict(resp.headers),
                )
            except httpx.ConnectError:
                return JSONResponse(
                    {"detail": "Skuld broker not ready"},
                    status_code=502,
                )

    live_config_template: str | None = None

    def render_live_config(origin: str) -> str:
        assert live_config_template is not None
        payload = live_config_template.replace("http://localhost:8080", origin)
        payload = payload.replace("http://127.0.0.1:8080", origin)
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
        async def spa_fallback(path: str) -> HTMLResponse:
            del path
            return HTMLResponse(content=index_html)

        logger.info("Serving web UI from %s", dist)
    except FileNotFoundError:
        logger.warning("Web UI assets not found — skipping static file serving")

    return root


class RootServer(Service):
    """Single in-process uvicorn server that hosts selected route domains."""

    def __init__(
        self,
        registry: PluginRegistry,
        host: str = "127.0.0.1",
        port: int = 8080,
        *,
        host_profile: str = DEFAULT_HOST_PROFILE,
        enabled_mounts: set[str] | None = None,
    ) -> None:
        self._registry = registry
        self._host = host
        self._port = port
        self._host_profile = host_profile
        self._enabled_mounts = enabled_mounts
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None
        self._embedded_db: EmbeddedDatabasePort | None = None
        global _skuld_registry  # noqa: PLW0603
        self.skuld_registry = SkuldPortRegistry()
        _skuld_registry = self.skuld_registry

    async def _start_embedded_db(self) -> None:
        """Start embedded PostgreSQL and set env vars for sub-apps."""
        from niuu.adapters.embedded_postgres import EmbeddedPostgresDatabase

        data_dir = str(Path.home() / ".niuu" / "pgdata")
        db = EmbeddedPostgresDatabase()
        info = await db.start(data_dir)
        self._embedded_db = db

        os.environ["DATABASE__HOST"] = info.host
        os.environ["DATABASE__PORT"] = str(info.port)
        os.environ["DATABASE__USER"] = info.user
        os.environ["DATABASE__PASSWORD"] = ""
        os.environ["DATABASE__NAME"] = info.dbname

        logger.info(
            "Embedded PostgreSQL ready at %s:%s/%s",
            info.host,
            info.port,
            info.dbname,
        )

    def _build_app(self) -> FastAPI:
        """Compose the root FastAPI app from the selected route domains."""
        return build_root_app(
            registry=self._registry,
            host=self._host,
            port=self._port,
            host_profile=self._host_profile,
            enabled_mounts=self._enabled_mounts,
            skuld_registry=self.skuld_registry,
        )

    async def start(self) -> None:
        await self._start_embedded_db()
        await self._run_migrations()

        os.environ["NIUU_SERVER_HOST"] = self._host
        os.environ["NIUU_SERVER_PORT"] = str(self._port)
        os.environ["VOLUNDR__URL"] = (
            f"http://{_local_service_host(self._host)}:{self._port}"
        )

        app = self._build_app()
        config = uvicorn.Config(
            app,
            host=self._host,
            port=self._port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())

    async def _run_migrations(self) -> None:
        """Run database migrations for all services."""
        if self._embedded_db is None:
            return
        try:
            import asyncpg

            from cli.resources import migration_dir

            info = self._embedded_db._connection_info
            conn = await asyncpg.connect(
                host=info.host,
                port=info.port,
                user=info.user,
                database=info.dbname,
            )
            try:
                for variant in ("volundr", "tyr"):
                    try:
                        mig_dir = migration_dir(variant)
                    except FileNotFoundError:
                        logger.debug("No migrations found for %s", variant)
                        continue
                    sql_files = sorted(mig_dir.glob("*.up.sql"))
                    applied = 0
                    for sql_file in sql_files:
                        sql = sql_file.read_text()
                        try:
                            await conn.execute(sql)
                            applied += 1
                        except Exception:
                            logger.debug("Migration %s skipped: %s", sql_file.name, exc_info=True)
                    logger.info("Applied %d/%d %s migrations", applied, len(sql_files), variant)
            finally:
                await conn.close()
        except Exception:
            logger.exception("Failed to run migrations")

    async def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._task:
            await self._task
            self._task = None
        if self._embedded_db:
            await self._embedded_db.stop()
            self._embedded_db = None

    async def health_check(self) -> bool:
        return self._server is not None and self._server.started
