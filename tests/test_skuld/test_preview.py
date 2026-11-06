"""Tests for Skuld's live-preview half: port watching and the last forwarding hop."""

from __future__ import annotations

import asyncio
import logging
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from niuu.config import DynamicAdapterConfig
from skuld import broker_api
from skuld.config import PreviewConfig, SkuldSettings
from skuld.preview import (
    PREVIEW_PORTS_FRAME,
    PreviewForwarder,
    PreviewMiddleware,
    PreviewPortWatcher,
    build_preview,
)
from skuld.preview_adapters import NoRouterAuth, ProcNetListeningPortScanner
from skuld.preview_port import (
    ListeningPort,
    ListeningPortScanner,
    PortScanError,
    PreviewAuthError,
    PreviewRouterAuth,
)
from skuld.transport_lifecycle import TransportLifecycleMixin

SESSION = "0a1b2c3d-4e5f-6789-abcd-ef0123456789"


class _Scanner(ListeningPortScanner):
    """Returns (or raises) the next scripted result on every scan."""

    def __init__(self, *results: set[ListeningPort] | Exception) -> None:
        self.results = list(results)
        self.scans = 0

    async def scan(self) -> set[ListeningPort]:
        self.scans += 1
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return result


class _Auth(PreviewRouterAuth):
    def __init__(self, *, reject: bool = False) -> None:
        self.reject = reject
        self.calls: list[tuple[str, str]] = []

    async def verify(self, authorization: str, session_id: str) -> None:
        self.calls.append((authorization, session_id))
        if self.reject:
            raise PreviewAuthError("invalid router credential: nope")


def _ports(*numbers: int, host: str = "127.0.0.1") -> set[ListeningPort]:
    return {ListeningPort(n, host) for n in numbers}


class _Frames:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def emit(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)


class TestPreviewPortWatcher:
    async def test_emits_only_on_change_with_opened_and_closed(self) -> None:
        frames = _Frames()
        scanner = _Scanner(
            _ports(3000),
            _ports(3000),
            _ports(3000, 5173),
            _ports(5173),
            _ports(5173),
            set(),
        )
        watcher = PreviewPortWatcher(scanner, interval_seconds=1, emit=frames.emit)

        for _ in range(6):
            await watcher.scan_once()

        assert frames.frames == [
            {"type": PREVIEW_PORTS_FRAME, "ports": [3000], "opened": [3000], "closed": []},
            {
                "type": PREVIEW_PORTS_FRAME,
                "ports": [3000, 5173],
                "opened": [5173],
                "closed": [],
            },
            {"type": PREVIEW_PORTS_FRAME, "ports": [5173], "opened": [], "closed": [3000]},
            {"type": PREVIEW_PORTS_FRAME, "ports": [], "opened": [], "closed": [5173]},
        ]

    async def test_nothing_listening_from_the_start_emits_nothing(self) -> None:
        frames = _Frames()
        watcher = PreviewPortWatcher(_Scanner(set()), interval_seconds=1, emit=frames.emit)

        await watcher.scan_once()

        assert frames.frames == []
        assert watcher.snapshot()["ports"] == []
        assert watcher.snapshot()["last_scan_at"] is not None

    async def test_port_lookup_and_snapshot(self) -> None:
        watcher = PreviewPortWatcher(
            _Scanner({ListeningPort(3000, "::1"), ListeningPort(80)}),
            interval_seconds=2.5,
            emit=_Frames().emit,
        )
        assert watcher.snapshot()["last_scan_at"] is None

        await watcher.scan_once()

        assert watcher.port(3000) == ListeningPort(3000, "::1")
        assert watcher.port(81) is None
        snapshot = watcher.snapshot()
        assert snapshot["ports"] == [80, 3000]
        assert snapshot["scan_interval_seconds"] == 2.5
        assert snapshot["scan_failures"] == 0
        assert snapshot["last_error"] == ""

    async def test_a_failed_scan_is_counted_logged_at_error_and_keeps_the_last_ports(
        self, caplog
    ) -> None:
        frames = _Frames()
        scanner = _Scanner(_ports(3000), PortScanError("cannot read /proc/net/tcp"), _ports(3000))
        watcher = PreviewPortWatcher(scanner, interval_seconds=1, emit=frames.emit)
        await watcher.scan_once()

        with caplog.at_level(logging.ERROR, logger="skuld.preview"):
            await watcher.scan_once()

        snapshot = watcher.snapshot()
        assert snapshot["scan_failures"] == 1
        assert snapshot["last_error"] == "cannot read /proc/net/tcp"
        assert snapshot["ports"] == [3000]  # one unreadable /proc is not "every server stopped"
        assert watcher.port(3000) is not None
        assert len(frames.frames) == 1  # no close frame for the failed scan
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert "scan failed" in errors[0].getMessage()
        assert "cannot read /proc/net/tcp" in errors[0].getMessage()

    async def test_a_good_scan_clears_the_last_error_but_not_the_count(self) -> None:
        scanner = _Scanner(PortScanError("boom"), _ports(3000))
        watcher = PreviewPortWatcher(scanner, interval_seconds=1, emit=_Frames().emit)

        await watcher.scan_once()
        await watcher.scan_once()

        snapshot = watcher.snapshot()
        assert snapshot["last_error"] == ""
        assert snapshot["scan_failures"] == 1

    async def test_failures_accumulate(self) -> None:
        watcher = PreviewPortWatcher(
            _Scanner(PortScanError("a"), PortScanError("b")),
            interval_seconds=1,
            emit=_Frames().emit,
        )

        await watcher.scan_once()
        await watcher.scan_once()

        assert watcher.snapshot()["scan_failures"] == 2
        assert watcher.snapshot()["last_error"] == "b"

    async def test_unexpected_scanner_errors_are_not_swallowed(self) -> None:
        watcher = PreviewPortWatcher(
            _Scanner(RuntimeError("bug")), interval_seconds=1, emit=_Frames().emit
        )

        with pytest.raises(RuntimeError, match="bug"):
            await watcher.scan_once()

    async def test_start_polls_until_stopped(self) -> None:
        frames = _Frames()
        scanner = _Scanner(_ports(3000))
        watcher = PreviewPortWatcher(scanner, interval_seconds=0.01, emit=frames.emit)

        watcher.start()
        watcher.start()  # idempotent: one task
        async with asyncio.timeout(5):
            while scanner.scans < 3:
                await asyncio.sleep(0.01)
        await watcher.stop()
        scans_at_stop = scanner.scans
        await asyncio.sleep(0.05)

        assert scanner.scans == scans_at_stop
        assert len(frames.frames) == 1
        assert watcher._task is None

    async def test_stop_before_start_is_a_no_op(self) -> None:
        watcher = PreviewPortWatcher(_Scanner(set()), interval_seconds=1, emit=_Frames().emit)

        await watcher.stop()

    async def test_can_restart_after_stop(self) -> None:
        scanner = _Scanner(set())
        watcher = PreviewPortWatcher(scanner, interval_seconds=0.01, emit=_Frames().emit)
        watcher.start()
        await watcher.stop()
        scans = scanner.scans

        watcher.start()
        async with asyncio.timeout(5):
            while scanner.scans <= scans:
                await asyncio.sleep(0.01)
        await watcher.stop()


# --------------------------------------------------------------------------- forwarder


class _Env:
    def __init__(self, *, auth: PreviewRouterAuth | None = None) -> None:
        self.auth = auth or _Auth()
        self.listeners = _ports(3000) | {ListeningPort(4000, "::1")}
        self.watcher = PreviewPortWatcher(
            _Scanner(self.listeners), interval_seconds=1, emit=_Frames().emit
        )
        self.forwarder = PreviewForwarder(
            session_id=SESSION,
            watcher=self.watcher,
            router_auth=self.auth,
            connect_timeout_seconds=3,
        )
        self.upstream: list[httpx.Request] = []
        self.handler = self._ok
        self.forwarder._client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))
        self.provider: PreviewForwarder | None = self.forwarder

        async def inner(scope, receive, send) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"broker"})

        self.app = PreviewMiddleware(inner, forwarder_provider=lambda: self.provider)

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        self.upstream.append(request)
        await request.aread()
        return await self.handler(request)

    @staticmethod
    async def _ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_Body(b"app"))

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://skuld.test"
        )


class _Body(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


@pytest.fixture
async def env():
    environment = _Env()
    await environment.watcher.scan_once()
    yield environment
    await environment.forwarder.aclose()


class TestPreviewMiddleware:
    async def test_other_paths_reach_the_broker(self, env) -> None:
        async with env.client() as client:
            response = await client.get("/api/health")

        assert response.text == "broker"
        assert env.upstream == []

    @pytest.mark.parametrize("path", ["/preview", "/previews/3000/", "/api/preview/3000/"])
    async def test_only_slash_preview_slash_is_diverted(self, env, path) -> None:
        async with env.client() as client:
            response = await client.get(path)

        assert response.text == "broker"

    async def test_lifespan_reaches_the_broker(self, env) -> None:
        seen: list[str] = []

        async def inner(scope, receive, send) -> None:
            seen.append(scope["type"])

        await PreviewMiddleware(inner, forwarder_provider=lambda: None)({"type": "lifespan"}, 0, 0)

        assert seen == ["lifespan"]

    async def test_no_forwarder_yet_is_404(self, env) -> None:
        env.provider = None
        async with env.client() as client:
            response = await client.get("/preview/3000/")

        assert response.status_code == 404
        assert response.text == "Live preview is not enabled here."
        assert env.auth.calls == []

    async def test_no_forwarder_closes_a_websocket_1008(self, env) -> None:
        env.provider = None
        sent: list[dict[str, Any]] = []

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await env.app({"type": "websocket", "path": "/preview/3000/x", "headers": []}, None, send)

        assert sent == [
            {
                "type": "websocket.close",
                "code": 1008,
                "reason": "Live preview is not enabled here.",
            }
        ]


class TestPreviewForwarderHttp:
    async def test_a_bad_credential_is_403_and_nothing_is_dialled(self, env, caplog) -> None:
        env.auth.reject = True
        async with env.client() as client:
            with caplog.at_level(logging.WARNING, logger="skuld.preview"):
                response = await client.get("/preview/3000/", headers={"authorization": "Bearer x"})

        assert response.status_code == 403
        assert response.text == "Previews are served through the platform."
        assert env.upstream == []
        assert env.auth.calls == [("Bearer x", SESSION)]
        assert any("Refused a preview request" in r.getMessage() for r in caplog.records)

    async def test_a_missing_credential_header_reaches_the_auth_as_empty(self, env) -> None:
        async with env.client() as client:
            await client.get("/preview/3000/")

        assert env.auth.calls == [("", SESSION)]

    async def test_auth_runs_before_port_discovery(self, env) -> None:
        env.auth.reject = True
        async with env.client() as client:
            response = await client.get("/preview/9999/")

        assert response.status_code == 403  # a stranger learns nothing about open ports

    async def test_a_port_the_session_is_not_listening_on_is_404(self, env) -> None:
        async with env.client() as client:
            response = await client.get("/preview/8081/")

        assert response.status_code == 404
        assert response.text == "Nothing in this session is listening on port 8081."
        assert env.upstream == []

    @pytest.mark.parametrize("path", ["/preview/abc/", "/preview/123456/x", "/preview/3000x"])
    async def test_a_malformed_preview_path_is_404(self, env, path) -> None:
        async with env.client() as client:
            response = await client.get(path)

        assert response.status_code == 404
        assert response.text == "Not a preview path."

    async def test_the_app_sees_a_request_from_its_own_localhost(self, env) -> None:
        async with env.client() as client:
            response = await client.post(
                "/preview/3000/api/items?x=1&y=%20z",
                content=b"payload",
                headers={
                    "authorization": "Bearer router-cred",
                    "cookie": "a=b",
                    "x-auth-user-id": "u",
                    "x-niuu-room-role": "owner",
                    "x-envoy-original-path": "/p",
                    "x-forwarded-for": "1.2.3.4",
                    "x-real-ip": "1.2.3.4",
                    "host": "skuld.test",
                    "accept": "application/json",
                    "x-forwarded-host": f"3000--{SESSION}.preview.test",
                    "x-forwarded-proto": "https",
                    "origin": f"https://3000--{SESSION}.preview.test",
                },
            )

        assert response.text == "app"
        (request,) = env.upstream
        assert str(request.url) == "http://127.0.0.1:3000/api/items?x=1&y=%20z"
        assert request.method == "POST"
        assert request.content == b"payload"
        assert request.headers.get_list("host") == ["localhost:3000"]
        for dropped in ("authorization", "cookie", "x-forwarded-for", "x-real-ip"):
            assert dropped not in request.headers
        assert not [n for n in request.headers if n.startswith(("x-auth-", "x-niuu-", "x-envoy-"))]
        assert request.headers["accept"] == "application/json"
        assert request.headers["origin"] == "http://localhost:3000"
        assert request.headers["x-forwarded-host"] == f"3000--{SESSION}.preview.test"

    async def test_a_foreign_origin_is_left_alone(self, env) -> None:
        async with env.client() as client:
            await client.get(
                "/preview/3000/",
                headers={
                    "x-forwarded-proto": "https",
                    "x-forwarded-host": "3000--abc.preview.test",
                    "origin": "https://evil.example",
                },
            )
            await client.get(
                "/preview/3000/",
                headers={
                    "x-forwarded-proto": "https",
                    "x-forwarded-host": "3000--abc.preview.test",
                    "origin": "http://3000--abc.preview.test",
                },
            )

        assert [r.headers["origin"] for r in env.upstream] == [
            "https://evil.example",
            "http://3000--abc.preview.test",
        ]

    async def test_origin_defaults_to_https_when_the_scheme_header_is_absent(self, env) -> None:
        async with env.client() as client:
            await client.get(
                "/preview/3000/",
                headers={"x-forwarded-host": "h.test", "origin": "https://h.test"},
            )

        assert env.upstream[0].headers["origin"] == "http://localhost:3000"

    async def test_the_bare_preview_port_path_forwards_to_slash(self, env) -> None:
        async with env.client() as client:
            await client.get("/preview/3000")

        assert env.upstream[0].url.raw_path == b"/"

    async def test_an_ipv6_listener_is_dialled_on_bracketed_loopback(self, env) -> None:
        async with env.client() as client:
            await client.get("/preview/4000/hello")

        assert str(env.upstream[0].url) == "http://[::1]:4000/hello"
        assert env.upstream[0].headers["host"] == "localhost:4000"

    async def test_raw_encoded_paths_reach_the_app_unchanged(self, env) -> None:
        async with env.client() as client:
            await client.get("/preview/3000/@vite/client/a%2Fb?t=1")

        assert env.upstream[0].url.raw_path == b"/@vite/client/a%2Fb?t=1"

    async def test_response_status_headers_and_streamed_body_pass_through(self, env) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                206,
                headers=[
                    ("set-cookie", "a=1; Domain=x.test"),
                    ("set-cookie", "b=2"),
                    ("x-app", "yes"),
                ],
                stream=_Body(b"one", b"two"),
            )

        env.handler = handler
        async with env.client() as client:
            response = await client.get("/preview/3000/")

        assert response.status_code == 206
        assert response.content == b"onetwo"
        # Skuld does not touch cookies; Forge's router owns Set-Cookie policy.
        assert response.headers.get_list("set-cookie") == ["a=1; Domain=x.test", "b=2"]

    async def test_a_port_that_stopped_answering_is_502(self, env) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        env.handler = handler
        async with env.client() as client:
            response = await client.get("/preview/3000/")

        assert response.status_code == 502
        assert response.text == "Port 3000 stopped answering; is the server still running?"


@pytest.fixture
async def app_ws():
    """A local WebSocket server standing in for the session's dev server."""
    handshakes: list[dict[str, Any]] = []

    async def handler(connection: ServerConnection) -> None:
        handshakes.append(
            {
                "path": connection.request.path,
                "headers": connection.request.headers,
                "subprotocol": connection.subprotocol,
            }
        )
        try:
            async for message in connection:
                await connection.send(message)
        except ConnectionClosed:
            return

    def select_subprotocol(connection: ServerConnection, offered: list[str]) -> str | None:
        return "vite-hmr" if "vite-hmr" in offered else None

    async with serve(handler, "127.0.0.1", 0, select_subprotocol=select_subprotocol) as server:
        yield server.sockets[0].getsockname()[1], handshakes


class _Ws:
    def __init__(self) -> None:
        self.inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.event = asyncio.Event()

    async def receive(self) -> dict[str, Any]:
        return await self.inbox.get()

    async def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)
        self.event.set()

    async def wait_for(self, kind: str, count: int = 1) -> None:
        async with asyncio.timeout(5):
            while sum(1 for m in self.sent if m["type"] == kind) < count:
                self.event.clear()
                await self.event.wait()


def _ws_scope(port: int, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
    merged = {
        "authorization": "Bearer router-cred",
        "cookie": "a=b",
        "x-auth-user-id": "u",
        "x-forwarded-proto": "https",
        "x-forwarded-host": "5173--abc.preview.test",
        "origin": "https://5173--abc.preview.test",
        **(headers or {}),
    }
    return {
        "type": "websocket",
        "path": f"/preview/{port}/@vite/client",
        "raw_path": f"/preview/{port}/@vite/client".encode(),
        "query_string": b"token=t1",
        "headers": [(k.encode(), v.encode()) for k, v in merged.items()],
        "subprotocols": ["vite-hmr"],
    }


class TestPreviewForwarderWebsocket:
    async def test_frames_and_subprotocol_pass_through_on_loopback(self, env, app_ws) -> None:
        port, handshakes = app_ws
        env.watcher._ports = {port: ListeningPort(port, "127.0.0.1")}
        ws = _Ws()
        ws.inbox.put_nowait({"type": "websocket.connect"})

        task = asyncio.create_task(env.app(_ws_scope(port), ws.receive, ws.send))
        await ws.wait_for("websocket.accept")
        ws.inbox.put_nowait({"type": "websocket.receive", "text": "full-reload"})
        ws.inbox.put_nowait({"type": "websocket.receive", "bytes": b"\x00\xff"})
        await ws.wait_for("websocket.send", count=2)
        ws.inbox.put_nowait({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, 5)

        assert ws.sent[0] == {"type": "websocket.accept", "subprotocol": "vite-hmr"}
        assert ws.sent[1:3] == [
            {"type": "websocket.send", "text": "full-reload"},
            {"type": "websocket.send", "bytes": b"\x00\xff"},
        ]
        (handshake,) = handshakes
        assert handshake["path"] == "/@vite/client?token=t1"
        assert handshake["subprotocol"] == "vite-hmr"
        headers = handshake["headers"]
        assert headers["host"] == f"localhost:{port}"
        assert "authorization" not in headers
        assert "cookie" not in headers
        assert "x-auth-user-id" not in headers
        assert env.auth.calls == [("Bearer router-cred", SESSION)]

    async def test_origin_is_rewritten_when_it_is_the_forwarded_preview_origin(
        self, env, app_ws
    ) -> None:
        port, handshakes = app_ws
        env.watcher._ports = {port: ListeningPort(port, "127.0.0.1")}
        ws = _Ws()
        ws.inbox.put_nowait({"type": "websocket.connect"})

        task = asyncio.create_task(env.app(_ws_scope(port), ws.receive, ws.send))
        await ws.wait_for("websocket.accept")
        ws.inbox.put_nowait({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, 5)

        assert handshakes[0]["headers"]["origin"] == f"http://localhost:{port}"

    async def test_a_bad_credential_closes_1008_before_accept(self, env) -> None:
        env.auth.reject = True
        ws = _Ws()

        await env.app(_ws_scope(3000), ws.receive, ws.send)

        assert ws.sent == [
            {
                "type": "websocket.close",
                "code": 1008,
                "reason": "Previews are served through the platform.",
            }
        ]

    async def test_an_undiscovered_port_closes_1008_before_accept(self, env) -> None:
        ws = _Ws()

        await env.app(_ws_scope(8081), ws.receive, ws.send)

        assert ws.sent[0]["type"] == "websocket.close"
        assert ws.sent[0]["code"] == 1008
        assert "port 8081" in ws.sent[0]["reason"]

    async def test_a_malformed_path_closes_1008(self, env) -> None:
        ws = _Ws()
        scope = _ws_scope(3000)
        scope["path"] = "/preview/abc"

        await env.app(scope, ws.receive, ws.send)

        assert ws.sent[0] == {
            "type": "websocket.close",
            "code": 1008,
            "reason": "Not a preview path.",
        }

    async def test_a_dead_app_closes_the_browser_leg_1011(self, env) -> None:
        env.watcher._ports = {1: ListeningPort(1, "127.0.0.1")}  # nothing listens on port 1
        ws = _Ws()
        ws.inbox.put_nowait({"type": "websocket.connect"})

        await env.app(_ws_scope(1), ws.receive, ws.send)

        assert ws.sent == [{"type": "websocket.close", "code": 1011}]


# --------------------------------------------------------------------------- build_preview


class TestBuildPreview:
    def test_builds_the_configured_adapters_with_their_kwargs(self) -> None:
        config = PreviewConfig(
            enabled=True,
            port_scan_interval_seconds=0.5,
            connect_timeout_seconds=7,
            port_scanner=DynamicAdapterConfig(
                adapter="skuld.preview_adapters.ProcNetListeningPortScanner",
                kwargs={"excluded_ports": [8080], "proc_root": "/fake/proc"},
            ),
            router_auth=DynamicAdapterConfig(adapter="skuld.preview_adapters.NoRouterAuth"),
        )

        watcher, forwarder = build_preview(config, session_id=SESSION, emit=_Frames().emit)

        assert isinstance(watcher._scanner, ProcNetListeningPortScanner)
        assert watcher._scanner._excluded == frozenset({8080})
        assert str(watcher._scanner._proc) == "/fake/proc"
        assert watcher._interval == 0.5
        assert isinstance(forwarder._auth, NoRouterAuth)
        assert forwarder._session_id == SESSION
        assert forwarder._watcher is watcher
        assert forwarder._connect_timeout == 7

    def test_a_scanner_adapter_of_the_wrong_class_raises(self) -> None:
        config = PreviewConfig(
            enabled=True,
            port_scanner=DynamicAdapterConfig(adapter="skuld.preview_adapters.NoRouterAuth"),
            router_auth=DynamicAdapterConfig(adapter="skuld.preview_adapters.NoRouterAuth"),
        )

        with pytest.raises(TypeError, match="NoRouterAuth is not a ListeningPortScanner"):
            build_preview(config, session_id=SESSION, emit=_Frames().emit)

    def test_a_router_auth_adapter_of_the_wrong_class_raises(self) -> None:
        scanner = DynamicAdapterConfig(adapter="skuld.preview_adapters.ProcNetListeningPortScanner")
        config = PreviewConfig(enabled=True, port_scanner=scanner, router_auth=scanner)

        with pytest.raises(TypeError, match="is not a PreviewRouterAuth"):
            build_preview(config, session_id=SESSION, emit=_Frames().emit)

    def test_an_unimportable_adapter_raises(self) -> None:
        config = PreviewConfig(
            enabled=True,
            port_scanner=DynamicAdapterConfig(adapter="skuld.preview_adapters.NoSuchScanner"),
            router_auth=DynamicAdapterConfig(adapter="skuld.preview_adapters.NoRouterAuth"),
        )

        with pytest.raises((ImportError, AttributeError)):
            build_preview(config, session_id=SESSION, emit=_Frames().emit)

    def test_adapter_constructor_errors_are_not_swallowed(self) -> None:
        config = PreviewConfig(
            enabled=True,
            port_scanner=DynamicAdapterConfig(
                adapter="skuld.preview_adapters.ProcNetListeningPortScanner"
            ),
            router_auth=DynamicAdapterConfig(
                adapter="skuld.preview_adapters.WorkloadJwtRouterAuth", kwargs={}
            ),
        )

        with pytest.raises(TypeError):  # required keyword-only issuer/audience/jwks_uri
            build_preview(config, session_id=SESSION, emit=_Frames().emit)


class TestPreviewConfig:
    def test_disabled_needs_no_adapters(self) -> None:
        config = PreviewConfig()
        assert config.enabled is False
        assert config.port_scanner is None
        assert config.router_auth is None

    def test_skuld_settings_default_to_preview_off(self) -> None:
        assert SkuldSettings().preview.enabled is False

    def test_enabled_without_adapters_names_both(self) -> None:
        with pytest.raises(ValidationError) as raised:
            PreviewConfig(enabled=True)

        message = str(raised.value)
        assert "preview.port_scanner and preview.router_auth is not set" in message
        assert "set preview.enabled to false" in message

    def test_enabled_without_router_auth_names_only_it(self) -> None:
        with pytest.raises(ValidationError) as raised:
            PreviewConfig(
                enabled=True,
                port_scanner=DynamicAdapterConfig(adapter="a.b.C"),
            )

        assert "preview.router_auth is not set" in str(raised.value)
        assert "preview.port_scanner" not in str(raised.value)

    def test_an_adapter_with_an_empty_class_path_counts_as_unset(self) -> None:
        with pytest.raises(ValidationError, match="preview.port_scanner is not set"):
            PreviewConfig(
                enabled=True,
                port_scanner=DynamicAdapterConfig(adapter=""),
                router_auth=DynamicAdapterConfig(adapter="a.b.C"),
            )

    def test_enabled_with_both_adapters_is_valid(self) -> None:
        config = PreviewConfig(
            enabled=True,
            port_scanner=DynamicAdapterConfig(adapter="a.b.C"),
            router_auth=DynamicAdapterConfig(adapter="a.b.D"),
        )
        assert config.enabled

    def test_scan_interval_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            PreviewConfig(port_scan_interval_seconds=0)


# --------------------------------------------------------------------------- broker wiring


class TestPreviewPortsEndpoint:
    async def test_disabled_when_the_broker_has_no_watcher(self, monkeypatch) -> None:
        monkeypatch.setattr(broker_api, "broker", SimpleNamespace(preview_watcher=None))

        assert await broker_api.get_preview_ports() == {"enabled": False, "ports": []}

    async def test_enabled_returns_the_watchers_snapshot(self, monkeypatch) -> None:
        watcher = PreviewPortWatcher(
            _Scanner(PortScanError("proc unreadable")), interval_seconds=2, emit=_Frames().emit
        )
        await watcher.scan_once()
        monkeypatch.setattr(broker_api, "broker", SimpleNamespace(preview_watcher=watcher))

        body = await broker_api.get_preview_ports()

        assert body == {
            "enabled": True,
            "ports": [],
            "scan_interval_seconds": 2,
            "last_scan_at": None,
            "scan_failures": 1,
            "last_error": "proc unreadable",
        }

    def test_the_broker_app_diverts_preview_paths_to_the_forwarder(self) -> None:
        names = [m.cls.__name__ for m in broker_api.app.user_middleware]
        assert names[0] == "PreviewMiddleware"  # outermost: added last


class _Lifecycle(TransportLifecycleMixin):
    """Only the attributes _start_preview reads."""

    def __init__(self, preview: PreviewConfig) -> None:
        self._settings = SimpleNamespace(preview=preview)
        self.session_id = SESSION
        self.preview_watcher = None
        self.preview_forwarder = None
        self.frames: list[dict[str, Any]] = []

    async def _emit_broker_frame(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)


class TestStartPreview:
    def test_disabled_builds_nothing(self) -> None:
        broker = _Lifecycle(PreviewConfig())

        broker._start_preview()

        assert broker.preview_watcher is None
        assert broker.preview_forwarder is None

    async def test_enabled_builds_and_starts_the_watcher(self, tmp_path) -> None:
        proc = tmp_path / "proc"
        (proc / "net").mkdir(parents=True)
        (proc / "net" / "tcp").write_text("header\n")
        (proc / str(os.getpid()) / "fd").mkdir(parents=True)
        broker = _Lifecycle(
            PreviewConfig(
                enabled=True,
                port_scan_interval_seconds=0.01,
                port_scanner=DynamicAdapterConfig(
                    adapter="skuld.preview_adapters.ProcNetListeningPortScanner",
                    kwargs={"proc_root": str(proc)},
                ),
                router_auth=DynamicAdapterConfig(adapter="skuld.preview_adapters.NoRouterAuth"),
            )
        )

        broker._start_preview()
        try:
            assert isinstance(broker.preview_watcher, PreviewPortWatcher)
            assert isinstance(broker.preview_forwarder, PreviewForwarder)
            assert broker.preview_forwarder._session_id == SESSION
            async with asyncio.timeout(5):
                while broker.preview_watcher.snapshot()["last_scan_at"] is None:
                    await asyncio.sleep(0.01)
        finally:
            await broker.preview_watcher.stop()
            await broker.preview_forwarder.aclose()

    async def test_build_failures_are_fatal(self) -> None:
        broker = _Lifecycle(
            PreviewConfig(
                enabled=True,
                port_scanner=DynamicAdapterConfig(adapter="skuld.preview_adapters.NoRouterAuth"),
                router_auth=DynamicAdapterConfig(adapter="skuld.preview_adapters.NoRouterAuth"),
            )
        )

        with pytest.raises(TypeError, match="is not a ListeningPortScanner"):
            broker._start_preview()
        assert broker.preview_watcher is None


class TestBrokerShutdown:
    @pytest.fixture
    def broker(self, tmp_path):
        from skuld.broker import Broker

        settings = SkuldSettings(
            session={"id": SESSION, "workspace_dir": str(tmp_path)}, transport="sdk"
        )
        return Broker(settings=settings)

    def test_a_new_broker_has_no_preview(self, broker) -> None:
        assert broker.preview_watcher is None
        assert broker.preview_forwarder is None

    async def test_shutdown_stops_the_watcher_and_closes_the_forwarder(self, broker) -> None:
        broker.preview_watcher = AsyncMock()
        broker.preview_forwarder = AsyncMock()

        await broker.shutdown()

        broker.preview_watcher.stop.assert_awaited_once()
        broker.preview_forwarder.aclose.assert_awaited_once()
