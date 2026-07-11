"""Session registry and HTTP/WebSocket proxying for Niuu-hosted Skuld sessions."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.parse
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, WebSocket
from starlette.responses import JSONResponse, Response

from niuu.config import NiuuSettings
from niuu.ports.session_proxy import SessionProxyTarget
from niuu.ws_identity import claims_to_identity, decode_jwt_claims

logger = logging.getLogger(__name__)


def _configured_cors_origins() -> list[str]:
    """Return explicitly configured CORS origins for the unified niuu host."""

    return NiuuSettings().host.cors_origins


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


class SkuldPortRegistry:
    """Maps session IDs to their Skuld subprocess ports."""

    def __init__(self, state_file: Path | None = None) -> None:
        self._ports: dict[str, int] = {}
        self._state_file = (
            state_file or Path(NiuuSettings().host.forge_state_file).expanduser()
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
        self._target_resolver: Callable[[str], Awaitable[SessionProxyTarget | None]] | None = None

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

    def set_target_resolver(
        self,
        resolver: Callable[[str], Awaitable[SessionProxyTarget | None]],
    ) -> None:
        """Inject resolution for non-local session service targets."""
        self._target_resolver = resolver

    async def resolve_target(self, session_id: str) -> SessionProxyTarget | None:
        """Resolve an externally hosted session service, when configured."""
        if self._target_resolver is None:
            return None
        return await self._target_resolver(session_id)

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

# The broker replays the FULL conversation history as one frame on connect;
# long sessions exceed the websockets client's 1 MiB default (which killed the
# broker leg with 1009 "message too big" and trapped clients in a reconnect
# loop). 64 MiB headroom, shared by both the /session and /ws/ravn proxy legs.
_WS_PROXY_MAX_FRAME_BYTES = 2**26


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
    return str(
        websocket.query_params.get("access_token") or websocket.query_params.get("token") or ""
    ).strip()


def _proxy_ws_identity(websocket: WebSocket) -> tuple[str | None, str | None, tuple[str, ...]]:
    """Resolve caller identity from a browser WebSocket for the proxy guard.

    Mirrors Volundr's ``extract_principal`` / the broker's WS identity
    resolution: Envoy ``x-auth-*`` headers first, then developer query
    parameters, then a bearer token (``Authorization``, the
    ``volundr.bearer.<jwt>`` subprotocol, or query token) decoded for
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
        user_id, tenant, roles = claims_to_identity(decode_jwt_claims(token))
        if user_id:
            return (user_id, tenant or None, roles)

    return (None, None, ())


# Auth headers forwarded verbatim from the browser leg to the broker leg.
_FORWARDED_AUTH_HEADERS = frozenset(
    {
        "authorization",
        "x-auth-user-id",
        "x-auth-email",
        "x-auth-tenant",
        "x-auth-roles",
    }
)
# Dev-identity query params mapped onto x-auth-* headers for the broker leg.
_DEV_QUERY_TO_HEADER = (
    ("devUserId", "x-auth-user-id"),
    ("devEmail", "x-auth-email"),
    ("devTenantId", "x-auth-tenant"),
    ("devRoles", "x-auth-roles"),
)


def _proxy_forward_headers(
    websocket: WebSocket,
    *,
    include_cookie: bool,
    forward_dev_params: bool,
) -> dict[str, str]:
    """Build the header set forwarded from the browser leg to the broker leg."""
    allow = set(_FORWARDED_AUTH_HEADERS)
    if include_cookie:
        allow.add("cookie")
    headers = {
        k.decode(): v.decode() for k, v in websocket.headers.raw if k.decode().lower() in allow
    }
    if forward_dev_params:
        for query_key, header in _DEV_QUERY_TO_HEADER:
            if value := websocket.query_params.get(query_key):
                headers[header] = value
    return headers


async def _proxy_ws(
    websocket: WebSocket,
    session_id: str,
    skuld_reg: SkuldPortRegistry,
    broker_path: str,
    *,
    log_label: str,
    include_cookie: bool = False,
    forward_dev_params: bool = False,
) -> None:
    """Proxy a WebSocket to a session's Skuld broker (ownership-guarded).

    Shared by the browser ``/session`` and the ravn ``/ws/ravn/{peer}`` legs:
    identity guard → port lookup → bidirectional pump → M-8 self-heal on a
    never-connected broker leg. The only per-route differences are the broker
    path, whether the browser cookie / dev query params are forwarded, and the
    log label.
    """
    user_id, tenant_id, roles = _proxy_ws_identity(websocket)
    if not await skuld_reg.may_attach(session_id, user_id, tenant_id, roles):
        await websocket.close(code=1008, reason="Not authorized for this session")
        return

    port = skuld_reg.get_port(session_id)
    target = None if port is not None else await skuld_reg.resolve_target(session_id)
    if port is None and target is None:
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
        connect_url = f"ws://127.0.0.1:{port}{broker_path}"
        connect_kwargs: dict[str, object] = {}
        if target is not None:
            connect_url = _session_target_url(target.service_url, broker_path, websocket=True)
            connect_kwargs = {
                "host": target.connect_host,
                "port": target.connect_port,
                "proxy": None,
            }
        async with ws_client.connect(
            connect_url,
            max_size=_WS_PROXY_MAX_FRAME_BYTES,
            additional_headers=_proxy_forward_headers(
                websocket,
                include_cookie=include_cookie,
                forward_dev_params=forward_dev_params,
            ),
            **connect_kwargs,
        ) as broker_ws:
            connected = True

            async def browser_to_broker() -> None:
                with suppress(Exception):
                    async for msg in websocket.iter_text():
                        await broker_ws.send(msg)

            async def broker_to_browser() -> None:
                with suppress(Exception):
                    async for msg in broker_ws:
                        await websocket.send_text(str(msg))

            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(browser_to_broker()),
                    asyncio.create_task(broker_to_browser()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            # Await the cancelled tasks so neither is destroyed while pending,
            # and surface any exception from the completed leg (fail loudly).
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
    except Exception:
        logger.debug("%s ended for session %s", log_label, _sanitize_log(session_id))
    finally:
        # M-8 self-heal: if the broker leg never connected, only drop a stale
        # RUNNING port when the pod-authoritative reconcile CONFIRMS the
        # session is dead — never on a transient blip against a live pod.
        if not connected:
            confirmed_dead = await skuld_reg.reconcile_dead(session_id)
            if confirmed_dead:
                skuld_reg.unregister(session_id)
            with suppress(Exception):
                await websocket.close(code=4410, reason="Session is no longer running")
            return
        with suppress(Exception):
            await websocket.close()


def get_skuld_registry() -> SkuldPortRegistry | None:
    """Return the active SkuldPortRegistry, if any."""
    return _skuld_registry


def _install_skuld_registry(registry: SkuldPortRegistry) -> None:
    """Expose the active registry to mini-mode composition without hidden wiring."""
    global _skuld_registry  # noqa: PLW0603
    _skuld_registry = registry


def _session_target_url(
    service_url: str,
    path: str,
    *,
    websocket: bool = False,
) -> str:
    """Build a session-service URL while preserving its routing authority."""
    parsed = urllib.parse.urlsplit(service_url)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.netloc:
        raise ValueError("Session proxy target must be an absolute URL")
    scheme = parsed.scheme
    if websocket:
        scheme = "wss" if parsed.scheme in {"https", "wss"} else "ws"
    return urllib.parse.urlunsplit((scheme, parsed.netloc, path, "", ""))


def _session_http_connect_url(target: SessionProxyTarget, path: str) -> tuple[str, str]:
    """Return the gateway URL and Host header for an HTTP session request."""
    service = urllib.parse.urlsplit(target.service_url)
    scheme = "https" if target.connect_secure else "http"
    host = target.connect_host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{target.connect_port}"
    url = urllib.parse.urlunsplit((scheme, netloc, path, "", ""))
    return url, service.netloc


def register_session_proxy_routes(app: FastAPI, skuld_reg: SkuldPortRegistry) -> None:
    """Register the ``/s/{session_id}`` session-proxy routes on *app*.

    Shared by the mini-mode root app and the standalone Volundr deployment
    (K8s), where no CLI root app exists to terminate the browser's session
    traffic. Sessions resolve first by local broker port, then through the
    registry's target resolver (e.g. the OpenShell gateway).
    """

    @app.websocket("/s/{session_id}/session")
    async def skuld_ws_proxy(
        websocket: WebSocket,
        session_id: str,
    ) -> None:
        """Proxy the browser chat WebSocket to the session's Skuld broker."""
        await _proxy_ws(
            websocket,
            session_id,
            skuld_reg,
            "/session",
            log_label="Skuld WS proxy",
            include_cookie=True,
            forward_dev_params=True,
        )

    @app.websocket("/s/{session_id}/ws/ravn/{peer_id}")
    async def skuld_ravn_ws_proxy(
        websocket: WebSocket,
        session_id: str,
        peer_id: str,
    ) -> None:
        """Proxy a ravn participant WebSocket to the session's broker.

        This is how a resident joins ANOTHER session's room through the
        gateway (session_join): the browser chat endpoint is proxied at
        ``/s/{id}/session``, and the sibling ravn endpoint must be proxied
        too, or cross-session joins only work in strip-prefix k8s ingress
        and fail in mini/gateway mode. Same ownership guard.
        """
        await _proxy_ws(
            websocket,
            session_id,
            skuld_reg,
            f"/ws/ravn/{peer_id}",
            log_label="Ravn WS proxy",
        )

    @app.api_route(
        "/s/{session_id}/api/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE"],
        include_in_schema=False,
    )
    async def skuld_http_proxy(request: Request, session_id: str, path: str) -> Response:
        """Proxy HTTP requests to the Skuld subprocess."""
        port = skuld_reg.get_port(session_id)
        target = None if port is not None else await skuld_reg.resolve_target(session_id)
        if port is None and target is None:
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
        proxy_path = f"/api/{sanitized_path}"
        url = f"http://127.0.0.1:{port}{proxy_path}"
        params = dict(request.query_params)
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in ("host", "content-length", "transfer-encoding")
        }
        if target is not None:
            url, service_host = _session_http_connect_url(target, proxy_path)
            headers["Host"] = service_host
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

    @app.get("/s/{session_id}/health", include_in_schema=False)
    async def skuld_health_proxy(request: Request, session_id: str) -> Response:
        """Proxy health check to the Skuld subprocess."""
        del request
        port = skuld_reg.get_port(session_id)
        target = None if port is not None else await skuld_reg.resolve_target(session_id)
        if port is None and target is None:
            return JSONResponse({"detail": "Session not found"}, status_code=404)

        import httpx

        try:
            url = f"http://127.0.0.1:{port}/health"
            headers: dict[str, str] = {}
            if target is not None:
                url, service_host = _session_http_connect_url(target, "/health")
                headers["Host"] = service_host
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(url, headers=headers)
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
