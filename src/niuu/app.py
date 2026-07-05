"""Niuu composition root for shared HTTP hosting and mount selection."""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import logging
import os
import urllib.parse
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.openapi.utils import get_openapi
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from niuu.config import CorsConfig
from niuu.cors import apply_cors_middleware
from niuu.ports.plugin import APIRouteDomain, Service
from niuu.service_databases import (
    bootstrap_database,
    bootstrap_sql_for_service,
    database_name_for_service,
    local_service_database_names,
    service_database_env_var,
)

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
        # Optional async hook invoked when the WS proxy cannot reach a live pod,
        # so the persisted Session row self-heals (status corrected, endpoint
        # cleared) instead of leaving a stale RUNNING tombstone. Set by the host
        # composition root via set_reconcile_hook(). The hook is pod-authoritative
        # and reports back whether the session is CONFIRMED dead (True) or still
        # genuinely RUNNING (False) — a transient broker-leg blip on a live pod
        # must NOT drop the port (M-8).
        self._reconcile_hook: Callable[[str], Awaitable[bool]] | None = None
        # Optional async guard invoked before proxying a browser WebSocket, so
        # the proxy (where the browser terminates) enforces session ownership.
        # The broker's own ws_auth check cannot cover the proxied path: the
        # proxy dials the broker from loopback, so identity resolution there
        # only works when Envoy x-auth-* headers are forwarded. The guard
        # receives the resolved caller identity and the session id and returns
        # True when the caller may attach. Set by the composition root.
        self._ownership_guard: (
            Callable[[str, str | None, str | None, tuple[str, ...]], Awaitable[bool]] | None
        ) = None

    def set_reconcile_hook(self, hook: Callable[[str], Awaitable[bool]]) -> None:
        """Inject the session-row reconcile callback used on a dead-pod proxy.

        The hook returns ``True`` when the pod-authoritative reconcile CONFIRMS the
        session is dead (STOPPED/FAILED) and ``False`` when it is still RUNNING.
        """
        self._reconcile_hook = hook

    def set_ownership_guard(
        self,
        guard: Callable[[str, str | None, str | None, tuple[str, ...]], Awaitable[bool]],
    ) -> None:
        """Inject the per-connection session-ownership check for the WS proxy.

        ``guard(session_id, user_id, tenant_id, roles) -> bool`` returns True
        when the caller owns (or may administer) the session.
        """
        self._ownership_guard = guard

    async def may_attach(
        self,
        session_id: str,
        user_id: str | None,
        tenant_id: str | None,
        roles: tuple[str, ...],
    ) -> bool:
        """Return True when the caller may attach to *session_id*'s chat.

        Fail-closed only when a guard is configured; with no guard (pure
        local dev without a session store) the proxy stays permissive so the
        existing single-user flow is unaffected.
        """
        if self._ownership_guard is None:
            return True
        return await self._ownership_guard(session_id, user_id, tenant_id, roles)

    async def reconcile_dead(self, session_id: str) -> bool:
        """Reconcile a session whose pod the proxy could not reach (best effort).

        Pod-status authoritative on the Volundr side: if the pod is in fact still
        alive the row is left untouched. Failures must never block the proxy's
        close path, so they are swallowed.

        Returns ``True`` only when the reconcile CONFIRMS the session is dead
        (STOPPED/FAILED). On a still-RUNNING pod (transient blip), a missing hook,
        or a hook error it returns ``False`` so the caller RETAINS the live port
        rather than dropping a port it could not confirm dead (M-8).
        """
        if self._reconcile_hook is None:
            return False
        try:
            return await self._reconcile_hook(session_id)
        except Exception:
            logger.warning(
                "Reconcile hook failed for dead-pod session %s",
                _sanitize_log(session_id),
                exc_info=True,
            )
            return False

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


def _decode_jwt_claims(token: str) -> dict:
    """Decode a JWT payload without verifying the signature (Envoy's job)."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return {}


def _bearer_token_from_ws(websocket: WebSocket) -> str:
    """Extract a bearer token from a WS: Authorization, subprotocol, or query."""
    headers = {k.lower(): v for k, v in websocket.headers.items()}
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    for proto in headers.get("sec-websocket-protocol", "").split(","):
        proto = proto.strip()
        if proto.startswith("volundr.bearer."):
            return urllib.parse.unquote(proto.removeprefix("volundr.bearer.").strip())
    return str(websocket.query_params.get("access_token") or "").strip()


def _proxy_ws_identity(websocket: WebSocket) -> tuple[str | None, str | None, tuple[str, ...]]:
    """Resolve caller identity from a browser WebSocket for the proxy guard.

    Mirrors Volundr's ``extract_principal`` / the broker's WS identity
    resolution: Envoy ``x-auth-*`` headers first, then developer query
    parameters, then a bearer token (``Authorization``, the
    ``volundr.bearer.<jwt>`` subprotocol, or ``?access_token=``) decoded for
    its ``sub``/tenant/roles claims. Returns ``(user_id, tenant_id, roles)``;
    user_id is None when no identity is present (the guard decides whether
    that is allowed).
    """
    headers = {k.lower(): v for k, v in websocket.headers.items()}

    def _roles(raw: str) -> tuple[str, ...]:
        return tuple(r.strip() for r in raw.split(",") if r.strip())

    forwarded = headers.get("x-auth-user-id", "").strip()
    if forwarded:
        return (
            forwarded,
            headers.get("x-auth-tenant", "").strip() or None,
            _roles(headers.get("x-auth-roles", "volundr:developer")),
        )

    params = websocket.query_params
    dev_user = str(params.get("devUserId") or "").strip()
    if dev_user:
        return (
            dev_user,
            str(params.get("devTenantId") or "").strip() or None,
            _roles(str(params.get("devRoles") or "volundr:developer")),
        )

    token = _bearer_token_from_ws(websocket)
    if token:
        claims = _decode_jwt_claims(token)
        sub = str(claims.get("sub") or "").strip()
        if sub:
            roles_claim = claims.get("roles")
            if isinstance(roles_claim, list):
                roles = tuple(str(r) for r in roles_claim)
            elif isinstance(roles_claim, str):
                roles = _roles(roles_claim)
            else:
                realm = claims.get("realm_access")
                roles = (
                    tuple(str(r) for r in realm["roles"])
                    if isinstance(realm, dict) and isinstance(realm.get("roles"), list)
                    else ()
                )
            tenant = str(claims.get("tenant") or claims.get("tenant_id") or "").strip() or None
            return (sub, tenant, roles)

    return (None, None, ())


def get_skuld_registry() -> SkuldPortRegistry | None:
    """Return the active SkuldPortRegistry, if any."""
    return _skuld_registry


def _local_service_host(host: str) -> str:
    """Return a loopback-safe host for intra-stack HTTP calls."""
    normalized = host.strip() or "127.0.0.1"
    if normalized in {"0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    return normalized


def _plugin_api_base_url(host: str, port: int) -> str:
    """Return the intra-stack base URL used by host-mounted plugin apps."""
    return f"http://{_local_service_host(host)}:{port}"


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
    "observatory-events-api": "observatory",
    "observatory-registry-api": "observatory",
    "observatory-topology-api": "observatory",
    "persona-api": "personas",
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
    host_profile: str = DEFAULT_HOST_PROFILE,
    enabled_mounts: set[str] | None = None,
    skuld_registry: SkuldPortRegistry | None = None,
) -> FastAPI:
    """Build the root FastAPI app that hosts selected route domains."""
    plugin_api_base_url = _plugin_api_base_url(host, port)
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
                    base_url=plugin_api_base_url,
                    embedded_forge_app=embedded_forge_app,
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
            # Ownership check first — the browser terminates here, so this is
            # the enforcement point that covers dev (query-param identity) and
            # Envoy (x-auth-* header) alike. A denied caller must never reach
            # the broker leg.
            user_id, tenant_id, roles = _proxy_ws_identity(websocket)
            if not await skuld_reg.may_attach(session_id, user_id, tenant_id, roles):
                await websocket.close(code=1008, reason="Not authorized for this session")
                return

            port = skuld_reg.get_port(session_id)
            if port is None:
                # No live port — the broker is gone. Reconcile the row so a stale
                # RUNNING tombstone self-heals, then close with a deterministic
                # "session gone" code (4410) the client can branch on.
                await skuld_reg.reconcile_dead(session_id)
                await websocket.close(code=4410, reason="Session is no longer running")
                return

            await websocket.accept()
            import websockets.asyncio.client as ws_client

            connected = False
            try:
                async with ws_client.connect(
                    f"ws://127.0.0.1:{port}/session",
                    # The broker replays the FULL conversation history as one
                    # frame on connect; long sessions exceed the websockets
                    # client's 1 MiB default, which killed the broker leg with
                    # 1009 ("message too big") and trapped browsers in an
                    # infinite reconnect loop. 64 MiB headroom.
                    max_size=2**26,
                    additional_headers={
                        **{
                            k.decode(): v.decode()
                            for k, v in websocket.headers.raw
                            if k.decode().lower()
                            in (
                                "authorization",
                                "cookie",
                                "x-auth-user-id",
                                "x-auth-email",
                                "x-auth-tenant",
                                "x-auth-roles",
                            )
                        },
                        **{
                            header: value
                            for query_key, header in (
                                ("devUserId", "x-auth-user-id"),
                                ("devEmail", "x-auth-email"),
                                ("devTenantId", "x-auth-tenant"),
                                ("devRoles", "x-auth-roles"),
                            )
                            if (value := websocket.query_params.get(query_key))
                        },
                    },
                ) as skuld_ws:
                    connected = True

                    async def browser_to_skuld() -> None:
                        with suppress(Exception):
                            async for msg in websocket.iter_text():
                                await skuld_ws.send(msg)

                    async def skuld_to_browser() -> None:
                        with suppress(Exception):
                            async for msg in skuld_ws:
                                await websocket.send_text(str(msg))

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
                # If we never established the broker leg, ask the pod-authoritative
                # reconcile whether the pod is actually gone. Only when it CONFIRMS
                # the session is dead (STOPPED/FAILED) do we drop the registered
                # port — otherwise a transient broker-leg blip on a still-RUNNING
                # pod would orphan a live session at port-is-None forever (M-8). The
                # browser is always closed with 4410 so it can branch/retry; on a
                # transient blip the retained port lets the very next connect succeed.
                if not connected:
                    confirmed_dead = await skuld_reg.reconcile_dead(session_id)
                    if confirmed_dead:
                        skuld_reg.unregister(session_id)
                    with suppress(Exception):
                        await websocket.close(code=4410, reason="Session is no longer running")
                    return
                with suppress(Exception):
                    await websocket.close()

        @root.websocket("/s/{session_id}/ws/ravn/{peer_id}")
        async def skuld_ravn_ws_proxy(
            websocket: WebSocket,  # noqa: F811
            session_id: str,
            peer_id: str,
        ) -> None:
            """Proxy a ravn participant WebSocket to the session's broker.

            This is how a resident joins ANOTHER session's room through the
            gateway (session_join): the browser-facing chat endpoint is
            proxied at ``/s/{id}/session``, and the sibling ravn endpoint must
            be proxied too, or cross-session joins only work in strip-prefix
            k8s ingress and fail in mini/gateway mode. Same ownership guard —
            a resident can only join sessions its (owner's) identity permits.
            """
            user_id, tenant_id, roles = _proxy_ws_identity(websocket)
            if not await skuld_reg.may_attach(session_id, user_id, tenant_id, roles):
                await websocket.close(code=1008, reason="Not authorized for this session")
                return

            port = skuld_reg.get_port(session_id)
            if port is None:
                await skuld_reg.reconcile_dead(session_id)
                await websocket.close(code=4410, reason="Session is no longer running")
                return

            await websocket.accept()
            import websockets.asyncio.client as ws_client

            try:
                async with ws_client.connect(
                    f"ws://127.0.0.1:{port}/ws/ravn/{peer_id}",
                    max_size=2**26,
                    additional_headers={
                        k.decode(): v.decode()
                        for k, v in websocket.headers.raw
                        if k.decode().lower()
                        in (
                            "authorization",
                            "x-auth-user-id",
                            "x-auth-email",
                            "x-auth-tenant",
                            "x-auth-roles",
                        )
                    },
                ) as broker_ws:

                    async def ravn_to_broker() -> None:
                        with suppress(Exception):
                            async for msg in websocket.iter_text():
                                await broker_ws.send(msg)

                    async def broker_to_ravn() -> None:
                        with suppress(Exception):
                            async for msg in broker_ws:
                                await websocket.send_text(str(msg))

                    _done, pending = await asyncio.wait(
                        [
                            asyncio.create_task(ravn_to_broker()),
                            asyncio.create_task(broker_to_ravn()),
                        ],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
            except Exception:
                logger.debug(
                    "Ravn WS proxy ended for session %s peer %s",
                    _sanitize_log(session_id),
                    _sanitize_log(peer_id),
                )
            finally:
                with suppress(Exception):
                    await websocket.close()

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

            # Skuld workflow gate ids use ":" as an internal delimiter, so the
            # session proxy needs to accept it in path segments while still
            # rejecting slashes and traversal tokens.
            allowed_segment = re.compile(r"^[A-Za-z0-9._~:-]+$")
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


class RootServer(Service):
    """Single in-process uvicorn server that hosts selected route domains."""

    def __init__(
        self,
        registry: PluginRegistry,
        host: str = "127.0.0.1",
        port: int = 8080,
        *,
        public_host: str | None = None,
        host_profile: str = DEFAULT_HOST_PROFILE,
        enabled_mounts: set[str] | None = None,
    ) -> None:
        self._registry = registry
        self._host = host
        self._public_host = (public_host or host).strip() or host
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
        database_mode = os.environ.get("NIUU_DATABASE_MODE", "").strip().lower()
        if database_mode == "external":
            logger.info("Skipping embedded PostgreSQL because NIUU_DATABASE_MODE=external")
            return
        if os.environ.get("DATABASE__HOST", "").strip():
            logger.info("Skipping embedded PostgreSQL because DATABASE__HOST is already set")
            return

        from niuu.adapters.embedded_postgres import EmbeddedPostgresDatabase

        data_dir = os.environ.get("NIUU_PGDATA_DIR") or str(Path.home() / ".niuu" / "pgdata")
        db = EmbeddedPostgresDatabase()
        info = await db.start(data_dir)
        await db.ensure_databases(local_service_database_names())
        self._embedded_db = db

        os.environ["DATABASE__HOST"] = info.host
        os.environ["DATABASE__PORT"] = str(info.port)
        os.environ["DATABASE__USER"] = info.user
        os.environ["DATABASE__PASSWORD"] = ""
        os.environ["DATABASE__NAME"] = database_name_for_service("volundr")
        for service_name in ("volundr", "niuu-shared", "guild", "observatory"):
            os.environ[service_database_env_var(service_name)] = database_name_for_service(
                service_name
            )

        logger.info(
            "Embedded PostgreSQL ready at %s:%s/%s",
            info.host,
            info.port,
            database_name_for_service("volundr"),
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
        os.environ["NIUU_SERVER_PUBLIC_HOST"] = self._public_host
        os.environ["NIUU_SERVER_PORT"] = str(self._port)
        os.environ["VOLUNDR__URL"] = f"http://{_local_service_host(self._host)}:{self._port}"

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

            from cli.resources import migration_dir, ordered_migration_files

            info = self._embedded_db._connection_info
            volundr_conn = await asyncpg.connect(
                host=info.host,
                port=info.port,
                user=info.user,
                database=database_name_for_service("volundr"),
            )
            try:
                for variant in ("volundr", "ting"):
                    try:
                        mig_dir = migration_dir(variant)
                    except FileNotFoundError:
                        logger.debug("No migrations found for %s", variant)
                        continue
                    sql_files = ordered_migration_files(mig_dir)
                    applied = 0
                    for sql_file in sql_files:
                        sql = sql_file.read_text()
                        try:
                            await volundr_conn.execute(sql)
                            applied += 1
                        except Exception:
                            logger.debug("Migration %s skipped: %s", sql_file.name, exc_info=True)
                    logger.info("Applied %d/%d %s migrations", applied, len(sql_files), variant)
            finally:
                await volundr_conn.close()

            for service_name in ("niuu-shared", "guild", "observatory"):
                bootstrap_sql = bootstrap_sql_for_service(service_name)
                if not bootstrap_sql:
                    continue
                await bootstrap_database(
                    host=info.host,
                    port=info.port,
                    user=info.user,
                    password="",
                    database=database_name_for_service(service_name),
                    statements=bootstrap_sql,
                )
                logger.info(
                    "Bootstrapped local %s database %s",
                    service_name,
                    database_name_for_service(service_name),
                )
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
