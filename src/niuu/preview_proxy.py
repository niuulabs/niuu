"""Live-preview router: each port a session listens on gets its own origin.

``https://<port>--<session-id>.<preview domain>/`` reaches port ``<port>`` of
that session, served at ``/``. The router runs where the platform already
terminates session traffic (the mini-mode root app, the standalone Forge) as an
ASGI middleware keyed on the ``Host`` header:

1. Only the session's owner gets through (the same Cedar room-role decision the
   ``/s/{id}`` proxy uses, see :class:`niuu.session_proxy.SkuldPortRegistry`).
2. The browser's ``Authorization`` and ``Cookie`` never reach the app: the
   platform's credentials stay with the platform.
3. The request goes to the session's own Skuld (``/preview/<port>/...``), with a
   router credential minted for that one session, and Skuld dials loopback.
4. Every ``Set-Cookie`` loses its ``Domain`` attribute, so an app can only set
   host-only cookies and cannot toss one onto the parent domain.

WebSockets (hot reload) and streamed bodies pass through both ways.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from starlette.requests import Request
from starlette.websockets import WebSocket

from niuu.config_models import SessionPreviewConfig
from niuu.domain.models import Principal
from niuu.ports.session_proxy import SessionProxyTarget
from niuu.preview_forwarding import (
    RawHeaders,
    Receive,
    Scope,
    Send,
    bridge_websocket,
    forward_http,
    header_value,
    scope_headers,
    send_plain_response,
    strip_cookie_domain,
    without_headers,
)
from niuu.session_proxy import (
    SkuldPortRegistry,
    _proxy_principal,
    _session_http_connect_url,
    _session_target_url,
)

logger = logging.getLogger(__name__)

#: ``<port>--<session uuid>``: the single DNS label that names one preview.
_PREVIEW_LABEL = re.compile(
    r"(?P<port>[0-9]{1,5})--"
    r"(?P<session>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)
_MAX_PORT = 65535

#: Request headers that are the platform's, never the app's.
_PLATFORM_REQUEST_HEADERS = (
    "host",
    "authorization",
    "cookie",
    "forwarded",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-forwarded-port",
    "x-niuu-room-role",
    "x-niuu-room-caller-session",
)
_PLATFORM_REQUEST_PREFIXES = ("x-auth-", "x-envoy-")


@dataclass(frozen=True)
class PreviewAddress:
    """The session and port a preview hostname names."""

    port: int
    session_id: str


def parse_preview_host(host: str, domain: str) -> PreviewAddress | None:
    """Read ``<port>--<session-id>.<domain>`` from a ``Host`` header value.

    Returns ``None`` for any other host, so ordinary platform traffic passes on.
    """
    if not domain:
        return None
    name = host.strip().lower()
    head, sep, tail = name.rpartition(":")
    if sep and tail.isdigit():
        name = head
    suffix = "." + domain.lower().strip(".")
    if not name.endswith(suffix):
        return None
    match = _PREVIEW_LABEL.fullmatch(name[: -len(suffix)])
    if match is None:
        return None
    port = int(match["port"])
    if not 1 <= port <= _MAX_PORT:
        return None
    return PreviewAddress(port=port, session_id=match["session"])


def _request_scheme(scope: Scope) -> str:
    forwarded = header_value(scope, "x-forwarded-proto").split(",", 1)[0].strip().lower()
    if forwarded in {"http", "https"}:
        return forwarded
    return "https" if scope.get("scheme") in {"https", "wss"} else "http"


def _raw_path(scope: Scope) -> str:
    """The request path exactly as the browser sent it (``/@vite/client`` stays put)."""
    raw = scope.get("raw_path")
    if raw:
        return raw.decode("latin-1")
    return urllib.parse.quote(scope.get("path") or "/", safe="/:@!$&'()*+,;=-._~")


def _rewrite_set_cookie(raw: RawHeaders) -> RawHeaders:
    return [
        (key, strip_cookie_domain(value.decode("latin-1")).encode("latin-1"))
        if key.lower() == b"set-cookie"
        else (key, value)
        for key, value in raw
    ]


class PreviewRouter:
    """Route one preview request to its session's Skuld, owner-only."""

    def __init__(self, registry: SkuldPortRegistry, config: SessionPreviewConfig) -> None:
        self._registry = registry
        self._config = config
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(None, connect=config.upstream_connect_timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def handle(
        self, scope: Scope, receive: Receive, send: Send, address: PreviewAddress
    ) -> None:
        is_websocket = scope["type"] == "websocket"
        connection: Request | WebSocket = (
            WebSocket(scope, receive, send) if is_websocket else Request(scope, receive)
        )
        principal = await _proxy_principal(connection)
        refusal = await self._refusal(address, principal)
        if refusal is not None:
            status, text = refusal
            await self._refuse(scope, send, status, text)
            return
        assert principal is not None  # _refusal returns 401 for a missing principal

        target = await self._target(address.session_id)
        if target is None:
            await self._refuse(scope, send, 404, "This session is not running.")
            return
        credential = await self._registry.preview_credential(address.session_id, principal)

        host = header_value(scope, "host")
        path = f"/preview/{address.port}{_raw_path(scope)}"
        headers = without_headers(
            scope_headers(scope),
            names=_PLATFORM_REQUEST_HEADERS,
            prefixes=_PLATFORM_REQUEST_PREFIXES,
        )
        headers += [("x-forwarded-host", host), ("x-forwarded-proto", _request_scheme(scope))]
        if credential:
            headers.append(("authorization", f"Bearer {credential}"))

        if is_websocket:
            await self._websocket(scope, receive, send, target, path, headers)
            return
        url, service_host = target(path)
        if service_host:
            headers.append(("host", service_host))
        await forward_http(
            scope,
            receive,
            send,
            client=self._client,
            url=url,
            headers=headers,
            rewrite_response_headers=_rewrite_set_cookie,
            unreachable_message="The session's Skuld is not reachable yet; retry.",
        )

    async def _refusal(
        self, address: PreviewAddress, principal: Principal | None
    ) -> tuple[int, str] | None:
        if principal is None or not principal.user_id:
            return 401, "Sign in to open this preview."
        roles = tuple(principal.roles)
        if not await self._registry.may_attach(
            address.session_id, principal.user_id, principal.tenant_id, roles
        ):
            return 403, "This preview belongs to a session you do not own."
        role = await self._registry.resolve_room_role(
            address.session_id, principal.user_id, principal.tenant_id, roles
        )
        if role != "owner":
            # A room participant may watch the chat; the app's origin is the owner's.
            return 403, "Only the session's owner can open its preview."
        return None

    async def _target(self, session_id: str) -> _SkuldTarget | None:
        port = self._registry.get_port(session_id)
        if port is not None:
            return _SkuldTarget(local_port=port)
        resolved = await self._registry.resolve_target(session_id)
        if resolved is None:
            return None
        return _SkuldTarget(remote=resolved)

    async def _websocket(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        target: _SkuldTarget,
        path: str,
        headers: list[tuple[str, str]],
    ) -> None:
        url, connect_kwargs = target.websocket(path)
        query = scope.get("query_string", b"").decode("latin-1")
        await bridge_websocket(
            scope,
            receive,
            send,
            url=f"{url}?{query}" if query else url,
            headers=headers,
            connect_kwargs=connect_kwargs,
            open_timeout_seconds=self._config.upstream_connect_timeout_seconds,
        )

    @staticmethod
    async def _refuse(scope: Scope, send: Send, status: int, text: str) -> None:
        if scope["type"] == "websocket":
            # Closing before accept answers the upgrade with 403.
            await send({"type": "websocket.close", "code": 1008, "reason": text})
            return
        await send_plain_response(send, status, text)


@dataclass(frozen=True)
class _SkuldTarget:
    """Where the session's Skuld listens: a local broker port or a remote service."""

    local_port: int | None = None
    remote: SessionProxyTarget | None = None

    def __call__(self, path: str) -> tuple[str, str]:
        """HTTP URL to dial, plus the ``Host`` header a remote route needs."""
        if self.remote is not None:
            return _session_http_connect_url(self.remote, path)
        return f"http://127.0.0.1:{self.local_port}{path}", ""

    def websocket(self, path: str) -> tuple[str, dict[str, Any]]:
        if self.remote is not None:
            return _session_target_url(self.remote.service_url, path, websocket=True), {
                "host": self.remote.connect_host,
                "port": self.remote.connect_port,
            }
        return f"ws://127.0.0.1:{self.local_port}{path}", {}


class PreviewRouterMiddleware:
    """Answer preview hostnames; hand every other request to the wrapped app.

    *router_provider* returns the router once the composition root has wired the
    session registry (its ownership guard and credential issuer). A preview host
    that arrives before then is answered 503 rather than served unguarded.
    """

    def __init__(
        self,
        app: Callable[..., Any],
        *,
        domain: str,
        router_provider: Callable[[], PreviewRouter | None],
    ) -> None:
        self._app = app
        self._domain = domain
        self._router_provider = router_provider

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self._app(scope, receive, send)
            return
        address = parse_preview_host(header_value(scope, "host"), self._domain)
        if address is None:
            await self._app(scope, receive, send)
            return
        router = self._router_provider()
        if router is None:
            await PreviewRouter._refuse(
                scope, send, 503, "Live preview is starting on this host; retry."
            )
            return
        await router.handle(scope, receive, send, address)
