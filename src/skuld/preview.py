"""Live preview, Skuld's half: find the session's ports and do the last hop.

Forge's preview router (``niuu.preview_proxy``) sends each request for
``<port>--<session>.<preview domain>`` here as ``/preview/<port>/<path>``. This
module checks the router's credential, checks the port is one the session
listens on, and forwards to that port on loopback — never another host, never
Skuld's own listeners.

:class:`PreviewPortWatcher` keeps the set of listening ports current and reports
every change to the browser as a ``preview_ports`` broker frame.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from niuu.preview_forwarding import (
    Receive,
    Scope,
    Send,
    bridge_websocket,
    forward_http,
    header_value,
    scope_headers,
    send_plain_response,
    without_headers,
)
from skuld.preview_port import (
    ListeningPort,
    ListeningPortScanner,
    PortScanError,
    PreviewAuthError,
    PreviewRouterAuth,
)

logger = logging.getLogger(__name__)

PREVIEW_PORTS_FRAME = "preview_ports"
_PREVIEW_PATH = re.compile(r"/preview/(?P<port>[0-9]{1,5})(?P<rest>/.*)?")

#: Request headers that belong to the hops, not the app.
_HOP_REQUEST_HEADERS = ("host", "authorization", "cookie", "x-forwarded-for", "x-real-ip")
_HOP_REQUEST_PREFIXES = ("x-auth-", "x-niuu-", "x-envoy-")


class PreviewPortWatcher:
    """Poll the session's listening ports and report each change.

    A scan that fails is logged at error and counted (``scan_failures``,
    ``last_error`` in :meth:`snapshot`), and the last good port set stays: one
    unreadable ``/proc`` must not tell the browser every server just stopped.
    """

    def __init__(
        self,
        scanner: ListeningPortScanner,
        *,
        interval_seconds: float,
        emit: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self._scanner = scanner
        self._interval = interval_seconds
        self._emit = emit
        self._ports: dict[int, ListeningPort] = {}
        self._scan_failures = 0
        self._last_error = ""
        self._last_scan_at: float | None = None
        self._task: asyncio.Task[None] | None = None

    def port(self, number: int) -> ListeningPort | None:
        return self._ports.get(number)

    def snapshot(self) -> dict[str, Any]:
        return {
            "ports": sorted(self._ports),
            "scan_interval_seconds": self._interval,
            "last_scan_at": self._last_scan_at,
            "scan_failures": self._scan_failures,
            "last_error": self._last_error,
        }

    async def scan_once(self) -> None:
        try:
            found = await self._scanner.scan()
        except PortScanError as exc:
            self._scan_failures += 1
            self._last_error = str(exc)
            logger.error("Live preview port scan failed (%d so far): %s", self._scan_failures, exc)
            return
        self._last_scan_at = time.time()
        self._last_error = ""
        current = {listener.port: listener for listener in found}
        opened = sorted(set(current) - set(self._ports))
        closed = sorted(set(self._ports) - set(current))
        self._ports = current
        if not opened and not closed:
            return
        await self._emit(
            {
                "type": PREVIEW_PORTS_FRAME,
                "ports": sorted(current),
                "opened": opened,
                "closed": closed,
            }
        )

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="skuld-preview-port-watcher")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            await self.scan_once()
            await asyncio.sleep(self._interval)


class PreviewForwarder:
    """Forward ``/preview/<port>/...`` from the router to a session port on loopback."""

    def __init__(
        self,
        *,
        session_id: str,
        watcher: PreviewPortWatcher,
        router_auth: PreviewRouterAuth,
        connect_timeout_seconds: float,
    ) -> None:
        self._session_id = session_id
        self._watcher = watcher
        self._auth = router_auth
        self._connect_timeout = connect_timeout_seconds
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(None, connect=connect_timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        match = _PREVIEW_PATH.fullmatch(scope.get("path", ""))
        if match is None:
            await self._refuse(scope, send, 404, "Not a preview path.")
            return
        try:
            await self._auth.verify(header_value(scope, "authorization"), self._session_id)
        except PreviewAuthError as exc:
            logger.warning("Refused a preview request: %s", exc)
            await self._refuse(scope, send, 403, "Previews are served through the platform.")
            return
        port = int(match["port"])
        listener = self._watcher.port(port)
        if listener is None:
            await self._refuse(
                scope, send, 404, f"Nothing in this session is listening on port {port}."
            )
            return

        address = f"[{listener.host}]" if ":" in listener.host else listener.host
        path = _raw_rest(scope, port)
        headers = self._app_headers(scope, port)
        if scope["type"] == "websocket":
            query = scope.get("query_string", b"").decode("latin-1")
            await bridge_websocket(
                scope,
                receive,
                send,
                # The URI's host becomes the Host header; the socket goes to loopback.
                url=f"ws://localhost:{port}{path}" + (f"?{query}" if query else ""),
                headers=headers,
                connect_kwargs={"host": listener.host, "port": port},
                open_timeout_seconds=self._connect_timeout,
            )
            return
        await forward_http(
            scope,
            receive,
            send,
            client=self._client,
            url=f"http://{address}:{port}{path}",
            headers=[*headers, ("host", f"localhost:{port}")],
            unreachable_message=f"Port {port} stopped answering; is the server still running?",
        )

    @staticmethod
    def _app_headers(scope: Scope, port: int) -> list[tuple[str, str]]:
        """The browser's request as the app should see it: from its own localhost.

        A dev server checks ``Host`` (Vite refuses names it does not know) and,
        for some requests, ``Origin``; a request from the preview origin itself
        is presented as coming from ``localhost:<port>``.
        """
        preview_origin = (
            f"{header_value(scope, 'x-forwarded-proto') or 'https'}://"
            f"{header_value(scope, 'x-forwarded-host')}"
        )
        headers = without_headers(
            scope_headers(scope), names=_HOP_REQUEST_HEADERS, prefixes=_HOP_REQUEST_PREFIXES
        )
        return [
            (key, f"http://localhost:{port}")
            if key.lower() == "origin" and value == preview_origin
            else (key, value)
            for key, value in headers
        ]

    @staticmethod
    async def _refuse(scope: Scope, send: Send, status: int, text: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": text})
            return
        await send_plain_response(send, status, text)


def _raw_rest(scope: Scope, port: int) -> str:
    """The app path after ``/preview/<port>``, as the router sent it."""
    raw = scope.get("raw_path")
    path = raw.decode("latin-1") if raw else scope.get("path", "")
    rest = path[len(f"/preview/{port}") :]
    return rest or "/"


class PreviewMiddleware:
    """Send ``/preview/...`` to the forwarder before any other broker middleware.

    It wraps the whole broker app, so CORS handling and the room-role gate never
    touch an app's own traffic. *forwarder_provider* returns ``None`` until the
    broker has started (or when preview is disabled), and the path is then
    answered 404.
    """

    def __init__(
        self,
        app: Callable[..., Any],
        *,
        forwarder_provider: Callable[[], PreviewForwarder | None],
    ) -> None:
        self._app = app
        self._forwarder_provider = forwarder_provider

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"} or not scope.get("path", "").startswith(
            "/preview/"
        ):
            await self._app(scope, receive, send)
            return
        forwarder = self._forwarder_provider()
        if forwarder is None:
            await PreviewForwarder._refuse(scope, send, 404, "Live preview is not enabled here.")
            return
        await forwarder.handle(scope, receive, send)


def build_preview(
    config: Any,
    *,
    session_id: str,
    emit: Callable[[dict[str, Any]], Awaitable[None]],
) -> tuple[PreviewPortWatcher, PreviewForwarder]:
    """Construct the configured scanner and router auth (dynamic adapters).

    Raises at startup, never later, when either adapter cannot be built.
    """
    from niuu.utils import import_class

    def _adapter(adapter_config: Any, port: type) -> Any:
        instance = import_class(adapter_config.adapter)(**dict(adapter_config.kwargs))
        if not isinstance(instance, port):
            raise TypeError(f"{adapter_config.adapter} is not a {port.__name__}")
        return instance

    watcher = PreviewPortWatcher(
        _adapter(config.port_scanner, ListeningPortScanner),
        interval_seconds=config.port_scan_interval_seconds,
        emit=emit,
    )
    forwarder = PreviewForwarder(
        session_id=session_id,
        watcher=watcher,
        router_auth=_adapter(config.router_auth, PreviewRouterAuth),
        connect_timeout_seconds=config.connect_timeout_seconds,
    )
    return watcher, forwarder
