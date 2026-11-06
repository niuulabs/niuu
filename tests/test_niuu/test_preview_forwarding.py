"""Tests for the streaming HTTP and WebSocket forwarding behind live preview."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from niuu.preview_forwarding import (
    HOP_BY_HOP_HEADERS,
    bridge_websocket,
    forward_http,
    header_value,
    scope_headers,
    send_plain_response,
    strip_cookie_domain,
    websocket_forward_headers,
    without_headers,
)


class _Channel:
    """An ASGI receive/send pair backed by queues."""

    def __init__(self, incoming: list[dict[str, Any]] | None = None) -> None:
        self.inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        for message in incoming or []:
            self.inbox.put_nowait(message)
        self.sent: list[dict[str, Any]] = []
        self.sent_event = asyncio.Event()

    async def receive(self) -> dict[str, Any]:
        return await self.inbox.get()

    async def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)
        self.sent_event.set()

    async def wait_for(self, kind: str, *, count: int = 1) -> None:
        async with asyncio.timeout(5):
            while sum(1 for m in self.sent if m["type"] == kind) < count:
                self.sent_event.clear()
                await self.sent_event.wait()


def _http_scope(
    method: str = "GET",
    headers: list[tuple[bytes, bytes]] | None = None,
    query: bytes = b"",
) -> dict[str, Any]:
    return {
        "type": "http",
        "method": method,
        "path": "/",
        "query_string": query,
        "headers": headers or [],
    }


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class _Chunks(httpx.AsyncByteStream):
    """A response body that is still a stream when forward_http reads it."""

    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


def _response(status: int = 200, headers=None, *chunks: bytes) -> httpx.Response:
    return httpx.Response(status, headers=headers, stream=_Chunks(*chunks))


class TestHeaderHelpers:
    def test_scope_headers_preserve_order_and_repeats(self) -> None:
        scope = {"headers": [(b"A", b"1"), (b"set-cookie", b"x"), (b"set-cookie", b"y")]}
        assert scope_headers(scope) == [("A", "1"), ("set-cookie", "x"), ("set-cookie", "y")]

    def test_scope_headers_empty_when_scope_has_none(self) -> None:
        assert scope_headers({}) == []

    def test_header_value_is_case_insensitive_and_first_wins(self) -> None:
        scope = {"headers": [(b"X-Thing", b"first"), (b"x-thing", b"second")]}
        assert header_value(scope, "x-THING") == "first"

    def test_header_value_missing_is_empty(self) -> None:
        assert header_value({"headers": []}, "host") == ""
        assert header_value({}, "host") == ""

    def test_without_headers_drops_hop_by_hop_names_and_prefixes(self) -> None:
        kept = without_headers(
            [
                ("Connection", "keep-alive"),
                ("Transfer-Encoding", "chunked"),
                ("Cookie", "a=b"),
                ("X-Auth-User-Id", "u"),
                ("Accept", "text/html"),
            ],
            names=["COOKIE"],
            prefixes=["x-auth-"],
        )
        assert kept == [("Accept", "text/html")]

    def test_without_headers_always_drops_every_hop_by_hop_header(self) -> None:
        headers = [(name, "x") for name in sorted(HOP_BY_HOP_HEADERS)] + [("accept", "*/*")]
        assert without_headers(headers) == [("accept", "*/*")]

    def test_websocket_forward_headers_drop_handshake_headers(self) -> None:
        kept = websocket_forward_headers(
            [
                ("Host", "h"),
                ("Sec-WebSocket-Key", "k"),
                ("sec-websocket-version", "13"),
                ("Sec-WebSocket-Extensions", "permessage-deflate"),
                ("Sec-WebSocket-Protocol", "vite-hmr"),
                ("Content-Length", "0"),
                ("Origin", "https://app"),
            ]
        )
        assert kept == [("Origin", "https://app")]


class TestStripCookieDomain:
    def test_removes_domain_and_keeps_other_attributes(self) -> None:
        cookie = "sid=abc; Domain=niuu.world; Path=/; Secure; HttpOnly; SameSite=Lax"
        assert strip_cookie_domain(cookie) == "sid=abc; Path=/; Secure; HttpOnly; SameSite=Lax"

    def test_case_and_spacing_of_domain_attribute_do_not_matter(self) -> None:
        assert strip_cookie_domain("a=b;DOMAIN = .niuu.world;Path=/") == "a=b;Path=/"

    def test_cookie_without_domain_is_unchanged(self) -> None:
        assert strip_cookie_domain("a=b; Path=/; Max-Age=60") == "a=b; Path=/; Max-Age=60"

    def test_bare_cookie_is_unchanged(self) -> None:
        assert strip_cookie_domain("a=b") == "a=b"

    def test_value_containing_domain_text_is_not_an_attribute(self) -> None:
        assert strip_cookie_domain("domain=x; Path=/") == "domain=x; Path=/"
        assert strip_cookie_domain("a=domain=y; Path=/") == "a=domain=y; Path=/"

    def test_every_domain_attribute_goes(self) -> None:
        assert strip_cookie_domain("a=b; Domain=x.com; Path=/; Domain=y.com") == "a=b; Path=/"


class TestSendPlainResponse:
    async def test_sends_text_plain_with_length_and_no_store(self) -> None:
        channel = _Channel()
        await send_plain_response(channel.send, 418, "teapot ☕")

        start, body = channel.sent
        headers = dict(start["headers"])
        assert start["status"] == 418
        assert headers[b"content-type"] == b"text/plain; charset=utf-8"
        assert headers[b"cache-control"] == b"no-store"
        assert body["body"] == "teapot ☕".encode()
        assert headers[b"content-length"] == str(len(body["body"])).encode()
        assert body["more_body"] is False


class TestForwardHttp:
    async def test_streams_request_and_response_bodies(self) -> None:
        seen: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            seen["headers"] = request.headers
            seen["body"] = await request.aread()

            return _response(201, [("x-app", "yes")], b"one", b"two")

        channel = _Channel(
            [
                {"type": "http.request", "body": b"he", "more_body": True},
                {"type": "http.request", "body": b"", "more_body": True},
                {"type": "http.request", "body": b"llo", "more_body": False},
            ]
        )
        scope = _http_scope("POST", [(b"content-length", b"5")], query=b"a=1&b=%20x")
        await forward_http(
            scope,
            channel.receive,
            channel.send,
            client=_client(handler),
            url="http://upstream:9000/path",
            headers=[("content-type", "text/plain"), ("x-from", "browser")],
            unreachable_message="down",
        )

        assert seen["url"] == "http://upstream:9000/path?a=1&b=%20x"
        assert seen["method"] == "POST"
        assert seen["body"] == b"hello"
        assert seen["headers"]["x-from"] == "browser"
        start, *bodies = channel.sent
        assert start["status"] == 201
        assert (b"x-app", b"yes") in start["headers"]
        assert [b["body"] for b in bodies] == [b"one", b"two", b""]
        assert [b["more_body"] for b in bodies] == [True, True, False]

    async def test_request_body_stops_on_disconnect(self) -> None:
        seen: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = await request.aread()
            return _response(204)

        channel = _Channel(
            [
                {"type": "http.request", "body": b"abc", "more_body": True},
                {"type": "http.disconnect"},
            ]
        )
        await forward_http(
            _http_scope("PUT", [(b"transfer-encoding", b"chunked")]),
            channel.receive,
            channel.send,
            client=_client(handler),
            url="http://upstream/x",
            headers=[],
            unreachable_message="down",
        )

        assert seen["body"] == b"abc"
        assert channel.sent[0]["status"] == 204

    @pytest.mark.parametrize(
        "headers",
        [[], [(b"content-length", b"0")], [(b"content-length", b" 00 ")]],
    )
    async def test_bodyless_request_sends_no_body(self, headers) -> None:
        seen: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = await request.aread()
            seen["transfer_encoding"] = request.headers.get("transfer-encoding")
            return _response(200)

        async def receive() -> dict[str, Any]:
            raise AssertionError("a request with no body must not read the ASGI body")

        channel = _Channel()
        await forward_http(
            _http_scope("GET", headers),
            receive,
            channel.send,
            client=_client(handler),
            url="http://upstream/x",
            headers=[],
            unreachable_message="down",
        )

        assert seen["body"] == b""
        assert seen["transfer_encoding"] is None

    async def test_response_headers_survive_and_hop_by_hop_ones_do_not(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return _response(
                200,
                [
                    ("set-cookie", "a=1; Domain=x.test"),
                    ("set-cookie", "b=2; Path=/"),
                    ("connection", "close"),
                    ("keep-alive", "timeout=5"),
                    ("Date", "Fri, 06 Nov 2026 12:00:00 GMT"),
                    ("Server", "uvicorn"),
                    ("content-type", "text/html"),
                ],
                b"x",
            )

        channel = _Channel()
        await forward_http(
            _http_scope(),
            channel.receive,
            channel.send,
            client=_client(handler),
            url="http://upstream/x",
            headers=[],
            unreachable_message="down",
        )

        names = [k for k, _ in channel.sent[0]["headers"]]
        assert names.count(b"set-cookie") == 2
        assert b"connection" not in names
        assert b"keep-alive" not in names
        # date/server are written again by the ASGI server answering the browser.
        assert b"Date" not in names and b"date" not in names
        assert b"Server" not in names and b"server" not in names
        assert b"content-type" in names

    async def test_rewrite_response_headers_is_applied(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return _response(200, [("x-a", "1")])

        channel = _Channel()
        await forward_http(
            _http_scope(),
            channel.receive,
            channel.send,
            client=_client(handler),
            url="http://upstream/x",
            headers=[],
            rewrite_response_headers=lambda raw: [*raw, (b"x-added", b"yes")],
            unreachable_message="down",
        )

        assert (b"x-added", b"yes") in channel.sent[0]["headers"]

    async def test_unreachable_upstream_answers_502_with_the_message(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        channel = _Channel()
        await forward_http(
            _http_scope(),
            channel.receive,
            channel.send,
            client=_client(handler),
            url="http://upstream/x",
            headers=[],
            unreachable_message="nothing listens there yet",
        )

        start, body = channel.sent
        assert start["status"] == 502
        assert body["body"] == b"nothing listens there yet"

    async def test_non_transport_errors_are_not_swallowed(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise RuntimeError("bug")

        channel = _Channel()
        with pytest.raises(RuntimeError, match="bug"):
            await forward_http(
                _http_scope(),
                channel.receive,
                channel.send,
                client=_client(handler),
                url="http://upstream/x",
                headers=[],
                unreachable_message="down",
            )
        assert channel.sent == []

    async def test_upstream_response_is_closed_when_the_browser_goes_away(self) -> None:
        closed = asyncio.Event()

        class _Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"chunk"

            async def aclose(self) -> None:
                closed.set()

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=_Stream())

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.body":
                raise ConnectionResetError("browser left")

        with pytest.raises(ConnectionResetError):
            await forward_http(
                _http_scope(),
                _Channel().receive,
                send,
                client=_client(handler),
                url="http://upstream/x",
                headers=[],
                unreachable_message="down",
            )
        assert closed.is_set()


@pytest.fixture
async def ws_upstream():
    """A real WebSocket server echoing frames, recording each handshake."""
    handshakes: list[dict[str, Any]] = []
    behaviour: dict[str, Any] = {"close_after": None}

    async def handler(connection: ServerConnection) -> None:
        handshakes.append(
            {
                "path": connection.request.path,
                "headers": connection.request.headers,
                "subprotocol": connection.subprotocol,
            }
        )
        try:
            count = 0
            async for message in connection:
                count += 1
                await connection.send(message)
                if behaviour["close_after"] == count:
                    await connection.close(code=4001, reason="enough")
                    return
        except ConnectionClosed:
            return

    def select_subprotocol(connection: ServerConnection, offered: list[str]) -> str | None:
        return "vite-hmr" if "vite-hmr" in offered else None

    async with serve(handler, "127.0.0.1", 0, select_subprotocol=select_subprotocol) as server:
        port = server.sockets[0].getsockname()[1]
        yield port, handshakes, behaviour


def _ws_scope(subprotocols: list[str] | None = None) -> dict[str, Any]:
    return {"type": "websocket", "subprotocols": subprotocols or [], "headers": []}


class TestBridgeWebsocket:
    async def test_frames_and_subprotocol_pass_through(self, ws_upstream) -> None:
        port, handshakes, _ = ws_upstream
        channel = _Channel([{"type": "websocket.connect"}])

        task = asyncio.create_task(
            bridge_websocket(
                _ws_scope(["vite-hmr"]),
                channel.receive,
                channel.send,
                url=f"ws://localhost:{port}/@vite/client?token=abc",
                headers=[
                    ("Host", "ignored.example"),
                    ("Sec-WebSocket-Key", "ignored"),
                    ("Origin", "http://localhost:5173"),
                    ("X-Trace", "t1"),
                ],
                connect_kwargs={"host": "127.0.0.1", "port": port},
                open_timeout_seconds=5,
            )
        )
        await channel.wait_for("websocket.accept")
        channel.inbox.put_nowait({"type": "websocket.receive", "text": "hello"})
        channel.inbox.put_nowait({"type": "websocket.receive", "bytes": b"\x00\x01\xff"})
        await channel.wait_for("websocket.send", count=2)
        channel.inbox.put_nowait({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, 5)

        accept = channel.sent[0]
        assert accept == {"type": "websocket.accept", "subprotocol": "vite-hmr"}
        frames = [m for m in channel.sent if m["type"] == "websocket.send"]
        assert frames == [
            {"type": "websocket.send", "text": "hello"},
            {"type": "websocket.send", "bytes": b"\x00\x01\xff"},
        ]
        (handshake,) = handshakes
        assert handshake["path"] == "/@vite/client?token=abc"
        assert handshake["subprotocol"] == "vite-hmr"
        assert handshake["headers"]["x-trace"] == "t1"
        assert handshake["headers"]["origin"] == "http://localhost:5173"
        assert handshake["headers"]["host"] == f"localhost:{port}"

    async def test_no_subprotocol_offered_negotiates_none(self, ws_upstream) -> None:
        port, handshakes, _ = ws_upstream
        channel = _Channel([{"type": "websocket.connect"}])
        task = asyncio.create_task(
            bridge_websocket(
                _ws_scope(),
                channel.receive,
                channel.send,
                url=f"ws://127.0.0.1:{port}/",
                headers=[],
                open_timeout_seconds=5,
            )
        )
        await channel.wait_for("websocket.accept")
        channel.inbox.put_nowait({"type": "websocket.disconnect", "code": 1001})
        await asyncio.wait_for(task, 5)

        assert channel.sent[0] == {"type": "websocket.accept", "subprotocol": None}
        assert handshakes[0]["subprotocol"] is None

    async def test_upstream_close_is_relayed_with_its_code_and_reason(self, ws_upstream) -> None:
        port, _, behaviour = ws_upstream
        behaviour["close_after"] = 1
        channel = _Channel([{"type": "websocket.connect"}])
        task = asyncio.create_task(
            bridge_websocket(
                _ws_scope(),
                channel.receive,
                channel.send,
                url=f"ws://127.0.0.1:{port}/",
                headers=[],
                open_timeout_seconds=5,
            )
        )
        await channel.wait_for("websocket.accept")
        channel.inbox.put_nowait({"type": "websocket.receive", "text": "ping"})
        await asyncio.wait_for(task, 5)

        assert channel.sent[-1] == {"type": "websocket.close", "code": 4001, "reason": "enough"}

    async def test_refused_upstream_closes_the_browser_leg_with_1011(self) -> None:
        channel = _Channel([{"type": "websocket.connect"}])
        await bridge_websocket(
            _ws_scope(),
            channel.receive,
            channel.send,
            url="ws://127.0.0.1:1/",  # nothing listens on port 1
            headers=[],
            open_timeout_seconds=2,
        )

        assert channel.sent == [{"type": "websocket.close", "code": 1011}]

    async def test_a_non_connect_first_message_ends_the_bridge_quietly(self) -> None:
        channel = _Channel([{"type": "websocket.disconnect", "code": 1001}])
        await bridge_websocket(
            _ws_scope(),
            channel.receive,
            channel.send,
            url="ws://127.0.0.1:1/",
            headers=[],
            open_timeout_seconds=2,
        )

        assert channel.sent == []

    async def test_a_failing_browser_send_is_raised_not_swallowed(self, ws_upstream) -> None:
        port, _, _ = ws_upstream
        channel = _Channel([{"type": "websocket.connect"}])

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "websocket.send":
                raise RuntimeError("browser leg broke")
            await channel.send(message)

        task = asyncio.create_task(
            bridge_websocket(
                _ws_scope(),
                channel.receive,
                send,
                url=f"ws://127.0.0.1:{port}/",
                headers=[],
                open_timeout_seconds=5,
            )
        )
        await channel.wait_for("websocket.accept")
        channel.inbox.put_nowait({"type": "websocket.receive", "text": "boom"})

        with pytest.raises(RuntimeError, match="browser leg broke"):
            await asyncio.wait_for(task, 5)
