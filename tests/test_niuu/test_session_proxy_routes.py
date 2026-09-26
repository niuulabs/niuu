"""Tests for the standalone ``/s/{session_id}`` session-proxy mounting.

The K8s deployment runs Volundr via ``uvicorn volundr.main:create_app`` with no
CLI root app in front, so the app itself must terminate browser session
traffic. These tests drive ``register_session_proxy_routes()`` on a bare
FastAPI app — the standalone composition — through the same behaviors the
mini-mode root app is tested for in ``tests/test_cli/test_server.py``.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from niuu.app import SkuldPortRegistry, register_session_proxy_routes
from niuu.ports.session_proxy import SessionProxyTarget
from niuu.session_proxy import SessionProxyGuardMissingError, bridge_websocket
from volundr.adapters.outbound.identity import AllowAllIdentityAdapter


async def _allow_attach(session_id, user_id, tenant_id, roles) -> bool:
    return True


def _bare_app(
    tmp_path, *, guard=_allow_attach, dev_identity: bool = False
) -> tuple[FastAPI, SkuldPortRegistry]:
    # The standalone composition root always wires the ownership guard; tests
    # that are not about authorization use an allow-all one.
    reg = SkuldPortRegistry(state_file=tmp_path / "forge-state.json", dev_identity=dev_identity)
    if guard is not None:
        reg.set_ownership_guard(guard)
    app = FastAPI()
    register_session_proxy_routes(app, reg)
    return app, reg


def _verifying_identity() -> EnvoyHeaderAuthenticationAdapter:
    """An identity adapter that verifies credentials other than client x-auth-*."""
    return EnvoyHeaderAuthenticationAdapter(
        user_id_header="x-verified-user",
        email_header="x-verified-email",
        tenant_header="x-verified-tenant",
        roles_header="x-verified-roles",
    )


# A caller verified as alice who also asserts someone else's identity.
_IMPERSONATION_HEADERS = {
    "x-verified-user": "alice",
    "x-verified-tenant": "t1",
    "x-verified-roles": "volundr:developer",
    "x-auth-user-id": "victim",
    "x-auth-tenant": "victim-tenant",
    "x-auth-roles": "volundr:admin",
}
_IMPERSONATION_QUERY = "devUserId=victim&devTenantId=victim-tenant&devRoles=volundr:admin"


def _external_target() -> SessionProxyTarget:
    return SessionProxyTarget(
        service_url="http://forge-123--skuld.openshell.localhost:8080",
        connect_host="openshell.openshell.svc.cluster.local",
        connect_port=8080,
    )


class TestStandaloneSessionProxyRoutes:
    """register_session_proxy_routes on a bare app (K8s standalone shape)."""

    def test_health_route_mounted_returns_session_not_found(self, tmp_path) -> None:
        # The K8s regression was FastAPI's default {"detail": "Not Found"}:
        # nothing mounted /s/{id} at all. The mounted route must answer with
        # the proxy's own "Session not found".
        app, _reg = _bare_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/s/nonexistent/health")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Session not found"

    def test_http_route_mounted_returns_session_not_found(self, tmp_path) -> None:
        app, _reg = _bare_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/s/nonexistent/api/conversation/history")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Session not found"

    def test_http_route_resolves_external_target_through_gateway(self, tmp_path) -> None:
        app, reg = _bare_app(tmp_path)
        target = _external_target()

        async def _resolve(session_id: str) -> SessionProxyTarget | None:
            return target if session_id == "sess-open" else None

        reg.set_target_resolver(_resolve)
        client = TestClient(app)

        mock_response = MagicMock()
        mock_response.content = b'{"ok": true}'
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            resp = client.get("/s/sess-open/api/conversation/history")

        assert resp.status_code == 200
        request = mock_client.request.await_args
        assert request.kwargs["url"] == (
            "http://openshell.openshell.svc.cluster.local:8080/api/conversation/history"
        )
        assert request.kwargs["headers"]["Host"] == "forge-123--skuld.openshell.localhost:8080"

    def test_ws_route_closes_4411_when_target_is_not_yet_known(self, tmp_path) -> None:
        from starlette.websockets import WebSocketDisconnect

        app, _reg = _bare_app(tmp_path)
        client = TestClient(app)

        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/s/unknown/session") as ws:
                ws.receive_text()

        assert exc.value.code == 4411

    def test_ws_route_dials_gateway_for_external_target(self, tmp_path) -> None:
        from starlette.websockets import WebSocketDisconnect

        app, reg = _bare_app(tmp_path)
        target = _external_target()

        async def _resolve(session_id: str) -> SessionProxyTarget | None:
            return target if session_id == "sess-open" else None

        reg.set_target_resolver(_resolve)
        client = TestClient(app)

        captured: dict = {}

        def _connect(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            raise OSError("gateway unreachable")

        with patch("websockets.asyncio.client.connect", side_effect=_connect):
            with pytest.raises(WebSocketDisconnect) as exc:
                with client.websocket_connect("/s/sess-open/session") as ws:
                    ws.receive_text()

        # The broker leg failed, so the browser leg closes deterministically —
        # what matters here is the dial: the sandbox route stays in the URL
        # (its Host), while the TCP connection goes to the gateway address.
        assert exc.value.code == 4411
        assert captured["url"] == "ws://forge-123--skuld.openshell.localhost:8080/session"
        assert captured["kwargs"]["host"] == "openshell.openshell.svc.cluster.local"
        assert captured["kwargs"]["port"] == 8080
        assert captured["kwargs"]["proxy"] is None


def _mock_http_client(mock_client_cls) -> AsyncMock:
    response = MagicMock()
    response.content = b"{}"
    response.status_code = 200
    response.headers = {"content-type": "application/json"}
    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client_cls.return_value = mock_client
    return mock_client


def _capture_ws_dial(captured: dict):
    def _connect(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs["additional_headers"]
        raise OSError("broker unreachable")

    return _connect


class TestSessionProxyIdentityPolicy:
    """Outside dev identity the session only sees identity the proxy verified."""

    def test_ws_ignores_dev_params_and_strips_client_identity(self, tmp_path) -> None:
        from starlette.websockets import WebSocketDisconnect

        seen: list[tuple] = []

        async def guard(session_id, user_id, tenant_id, roles) -> bool:
            seen.append((user_id, tenant_id, roles))
            return True

        app, reg = _bare_app(tmp_path, guard=guard)
        app.state.identity = _verifying_identity()
        reg.register("sess", 9123)
        captured: dict = {}

        with patch("websockets.asyncio.client.connect", side_effect=_capture_ws_dial(captured)):
            with pytest.raises(WebSocketDisconnect):
                with TestClient(app).websocket_connect(
                    f"/s/sess/session?{_IMPERSONATION_QUERY}",
                    headers=_IMPERSONATION_HEADERS,
                ) as ws:
                    ws.receive_text()

        assert seen == [("alice", "t1", ("volundr:developer",))]
        assert captured["url"] == "ws://127.0.0.1:9123/session"
        identity = {k: v for k, v in captured["headers"].items() if k.startswith("x-auth-")}
        assert identity == {
            "x-auth-user-id": "alice",
            "x-auth-tenant": "t1",
            "x-auth-roles": "volundr:developer",
        }

    def test_ws_forwards_no_identity_when_the_proxy_verified_none(self, tmp_path) -> None:
        from starlette.websockets import WebSocketDisconnect

        app, reg = _bare_app(tmp_path)
        reg.register("sess", 9123)
        captured: dict = {}

        with patch("websockets.asyncio.client.connect", side_effect=_capture_ws_dial(captured)):
            with pytest.raises(WebSocketDisconnect):
                with TestClient(app).websocket_connect(
                    f"/s/sess/session?{_IMPERSONATION_QUERY}",
                    headers=_IMPERSONATION_HEADERS,
                ) as ws:
                    ws.receive_text()

        assert not [k for k in captured["headers"] if k.lower().startswith("x-auth-")]

    def test_http_ignores_dev_params_and_strips_client_identity(self, tmp_path) -> None:
        app, reg = _bare_app(tmp_path)
        app.state.identity = _verifying_identity()
        reg.register("sess", 9123)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = _mock_http_client(mock_client_cls)
            resp = TestClient(app).get(
                f"/s/sess/api/conversation/history?limit=5&{_IMPERSONATION_QUERY}",
                headers=_IMPERSONATION_HEADERS,
            )

        assert resp.status_code == 200
        request = mock_client.request.await_args.kwargs
        assert request["params"] == {"limit": "5"}
        identity = {k: v for k, v in request["headers"].items() if k.startswith("x-auth-")}
        assert identity == {
            "x-auth-user-id": "alice",
            "x-auth-tenant": "t1",
            "x-auth-roles": "volundr:developer",
        }

    def test_dev_identity_forwards_the_asserted_dev_identity(self, tmp_path) -> None:
        from starlette.websockets import WebSocketDisconnect

        app, reg = _bare_app(tmp_path, dev_identity=True)
        reg.register("sess", 9123)
        captured: dict = {}

        with patch("websockets.asyncio.client.connect", side_effect=_capture_ws_dial(captured)):
            with pytest.raises(WebSocketDisconnect):
                with TestClient(app).websocket_connect(
                    "/s/sess/session?devUserId=dev-user&devTenantId=dev-tenant"
                ) as ws:
                    ws.receive_text()

        assert captured["headers"]["x-auth-user-id"] == "dev-user"
        assert captured["headers"]["x-auth-tenant"] == "dev-tenant"

    def test_ws_refuses_to_attach_without_a_guard_outside_dev(self, tmp_path) -> None:
        app, reg = _bare_app(tmp_path, guard=None)
        reg.register("sess", 9123)

        with patch("websockets.asyncio.client.connect") as connect:
            with pytest.raises(SessionProxyGuardMissingError):
                with TestClient(app).websocket_connect("/s/sess/session") as ws:
                    ws.receive_text()

        connect.assert_not_called()


class TestHttpProxyAttachGuard:
    """The HTTP ``/s/{id}/api/{path}`` route must apply the same attach guard
    as the WebSocket legs — a caller who cannot attach to a session's chat
    must not be able to POST to its broker API either (e.g. workflow gate
    resolution, room join)."""

    def test_non_owner_denied(self, tmp_path) -> None:
        async def deny(session_id, user_id, tenant_id, roles) -> bool:
            return False

        app, reg = _bare_app(tmp_path, guard=deny)
        reg.register("sess", 9123)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = _mock_http_client(mock_client_cls)
            resp = TestClient(app).get("/s/sess/api/conversation/history")

        assert resp.status_code == 403
        mock_client.request.assert_not_called()

    def test_owner_allowed(self, tmp_path) -> None:
        seen: list[tuple] = []

        async def guard(session_id, user_id, tenant_id, roles) -> bool:
            seen.append((session_id, user_id, tenant_id, roles))
            return user_id == "alice"

        app, reg = _bare_app(tmp_path, guard=guard)
        app.state.identity = _verifying_identity()
        reg.register("sess", 9123)

        with patch("httpx.AsyncClient") as mock_client_cls:
            _mock_http_client(mock_client_cls)
            resp = TestClient(app).get(
                "/s/sess/api/conversation/history",
                headers={
                    "x-verified-user": "alice",
                    "x-verified-tenant": "t1",
                    "x-verified-roles": "volundr:developer",
                },
            )

        assert resp.status_code == 200
        assert seen == [("sess", "alice", "t1", ("volundr:developer",))]

    def test_refuses_to_attach_without_a_guard_outside_dev(self, tmp_path) -> None:
        app, reg = _bare_app(tmp_path, guard=None)
        reg.register("sess", 9123)

        with patch("httpx.AsyncClient") as mock_client_cls:
            with pytest.raises(SessionProxyGuardMissingError):
                TestClient(app).get("/s/sess/api/conversation/history")
            mock_client_cls.assert_not_called()


class _SocketDouble:
    """In-memory transport only; no provider, gateway, or listening socket."""

    def __init__(self):
        self.headers = Headers(
            {
                "authorization": "Bearer synthetic-test-token",
                "cookie": "synthetic-cookie",
                "x-unrelated": "not-forwarded",
            }
        )
        self.query_params = {"devUserId": "synthetic-user"}
        self.incoming = asyncio.Queue()
        self.sent = []
        self.sent_event = asyncio.Event()
        self.stopped = asyncio.Event()
        self.reader = None
        self.accept = AsyncMock()

    async def _messages(self):
        self.reader = asyncio.current_task()
        try:
            while True:
                item = await self.incoming.get()
                if item is None:
                    return
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            self.stopped.set()

    def __aiter__(self):
        return self._messages()

    def iter_text(self):
        return self._messages()

    async def send(self, text):
        self.sent.append(text)
        self.sent_event.set()

    async def send_text(self, text):
        await self.send(text)


@pytest.mark.parametrize("end", ["browser", "broker", "cancel"])
@pytest.mark.parametrize("transport_error", [False, True])
async def test_bridge_delivers_once_and_closes_both_pumps(end, transport_error, monkeypatch):
    """Closing/cancelling a view never leaves a sender pumping behind it."""
    browser, broker = _SocketDouble(), _SocketDouble()
    upstream_closed = asyncio.Event()
    connected = MagicMock()
    captured = {}

    @asynccontextmanager
    async def connect(url, **kwargs):
        captured.update(url=url, **kwargs)
        try:
            yield broker
        finally:
            upstream_closed.set()

    monkeypatch.setattr("websockets.asyncio.client.connect", connect)
    steering = '{"type":"message","request_id":"synthetic-once","content":"hello"}'
    control = '{"type":"history_gap","history_protocol":2,"recovery":"recent"}'
    await browser.incoming.put(steering)
    await broker.incoming.put(control)
    upstream_headers = {
        "authorization": "Bearer synthetic-test-token",
        "x-auth-user-id": "verified-user",
    }
    task = asyncio.create_task(
        bridge_websocket(
            browser,
            "ws://synthetic.invalid/session?history=recent&history_protocol=2",
            headers=upstream_headers,
            on_connected=connected,
        )
    )
    try:
        async with asyncio.timeout(2):
            await browser.sent_event.wait()
            await broker.sent_event.wait()
            if end == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                closing = browser if end == "browser" else broker
                await closing.incoming.put(
                    OSError("synthetic closed transport") if transport_error else None
                )
                await task
        assert browser.sent == [control]
        assert broker.sent == [steering]
        assert browser.stopped.is_set()
        assert broker.stopped.is_set()
        assert upstream_closed.is_set()
        browser.accept.assert_awaited_once()
        connected.assert_called_once_with()
        # The bridge sends exactly the header set its caller built; identity
        # policy lives in _proxy_forward_headers, not here.
        assert captured["additional_headers"] == upstream_headers
        assert captured["max_size"] > 0
    finally:
        # Also clean up on regression: failed cancellation assertions must not
        # leave test-created tasks behind for the next case.
        readers = [p.reader for p in (browser, broker) if p.reader is not None]
        for pending in [task, *readers]:
            pending.cancel()
        await asyncio.gather(task, *readers, return_exceptions=True)


class TestRoomRoleHeader:
    """x-niuu-room-role is stamped from a verified resolver, never from the client."""

    def test_ws_stamps_the_resolved_role_and_strips_a_client_supplied_copy(self, tmp_path) -> None:
        from starlette.websockets import WebSocketDisconnect

        app, reg = _bare_app(tmp_path)
        reg.register("sess", 9123)

        async def role_resolver(session_id, user_id, tenant_id, roles) -> str:
            return "approver"

        reg.set_room_role_resolver(role_resolver)
        captured: dict = {}

        with patch("websockets.asyncio.client.connect", side_effect=_capture_ws_dial(captured)):
            with pytest.raises(WebSocketDisconnect):
                with TestClient(app).websocket_connect(
                    "/s/sess/session", headers={"x-niuu-room-role": "owner"}
                ) as ws:
                    ws.receive_text()

        assert captured["headers"]["x-niuu-room-role"] == "approver"

    def test_ws_stamps_viewer_when_no_role_can_be_resolved(self, tmp_path) -> None:
        """may_attach allowed the connection, but no resolver could name a
        role for it. Stamp least privilege explicitly rather than omit the
        header: this proxy always dials the broker over loopback, and an
        omitted header there can read as owner under
        ws_auth.room_role_source="proxy" (the process backend this proxy
        serves) — an omitted header would silently hand out owner to a
        connection this proxy could not actually place a role on."""
        from starlette.websockets import WebSocketDisconnect

        app, reg = _bare_app(tmp_path)
        reg.register("sess", 9123)
        captured: dict = {}

        with patch("websockets.asyncio.client.connect", side_effect=_capture_ws_dial(captured)):
            with pytest.raises(WebSocketDisconnect):
                with TestClient(app).websocket_connect(
                    "/s/sess/session", headers={"x-niuu-room-role": "owner"}
                ) as ws:
                    ws.receive_text()

        assert captured["headers"]["x-niuu-room-role"] == "viewer"

    def test_ws_dev_identity_without_a_resolver_stamps_owner(self, tmp_path) -> None:
        from starlette.websockets import WebSocketDisconnect

        app, reg = _bare_app(tmp_path, dev_identity=True)
        reg.register("sess", 9123)
        captured: dict = {}

        with patch("websockets.asyncio.client.connect", side_effect=_capture_ws_dial(captured)):
            with pytest.raises(WebSocketDisconnect):
                with TestClient(app).websocket_connect(
                    "/s/sess/session?devUserId=dev-user&devTenantId=dev-tenant"
                ) as ws:
                    ws.receive_text()

        assert captured["headers"]["x-niuu-room-role"] == "owner"

    def test_http_stamps_the_resolved_role_and_strips_a_client_supplied_copy(
        self, tmp_path
    ) -> None:
        app, reg = _bare_app(tmp_path)
        reg.register("sess", 9123)

        async def role_resolver(session_id, user_id, tenant_id, roles) -> str:
            return "viewer"

        reg.set_room_role_resolver(role_resolver)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = _mock_http_client(mock_client_cls)
            resp = TestClient(app).get(
                "/s/sess/api/conversation/history",
                headers={"x-niuu-room-role": "owner"},
            )

        assert resp.status_code == 200
        headers = mock_client.request.await_args.kwargs["headers"]
        assert headers["x-niuu-room-role"] == "viewer"

    def test_http_stamps_viewer_when_no_role_can_be_resolved(self, tmp_path) -> None:
        app, reg = _bare_app(tmp_path)
        reg.register("sess", 9123)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = _mock_http_client(mock_client_cls)
            resp = TestClient(app).get(
                "/s/sess/api/conversation/history",
                headers={"x-niuu-room-role": "owner"},
            )

        assert resp.status_code == 200
        headers = mock_client.request.await_args.kwargs["headers"]
        assert headers["x-niuu-room-role"] == "viewer"


class _CloseableSocketDouble(_SocketDouble):
    """_SocketDouble plus a close() the revalidation loop can call."""

    def __init__(self):
        super().__init__()
        self.close_calls: list[dict] = []

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_calls.append({"code": code, "reason": reason})
        # Unblocks the pumps exactly like a real disconnect would.
        await self.incoming.put(None)


class TestRevalidationClosesLiveSockets:
    """A revoked or demoted grant must close an ALREADY-OPEN proxied socket,
    not just refuse new connection attempts."""

    async def test_bridge_websocket_closes_when_revalidation_fails(self, monkeypatch) -> None:
        from niuu.session_proxy import bridge_websocket

        browser, broker = _CloseableSocketDouble(), _CloseableSocketDouble()

        @asynccontextmanager
        async def connect(url, **kwargs):
            yield broker

        monkeypatch.setattr("websockets.asyncio.client.connect", connect)
        calls = {"n": 0}

        async def revalidate() -> bool:
            calls["n"] += 1
            return calls["n"] < 2  # allowed once, then revoked

        await bridge_websocket(
            browser,
            "ws://synthetic.invalid/session",
            headers={},
            revalidate=revalidate,
            revalidate_interval=0.01,
        )

        assert browser.close_calls == [{"code": 1008, "reason": "Access revoked or downgraded"}]
        assert calls["n"] >= 2

    async def test_bridge_websocket_stays_open_while_revalidation_passes(self, monkeypatch) -> None:
        from niuu.session_proxy import bridge_websocket

        browser, broker = _CloseableSocketDouble(), _CloseableSocketDouble()

        @asynccontextmanager
        async def connect(url, **kwargs):
            yield broker

        monkeypatch.setattr("websockets.asyncio.client.connect", connect)
        calls = {"n": 0}

        async def revalidate() -> bool:
            calls["n"] += 1
            return True

        async def _disconnect_after_a_few_checks():
            while calls["n"] < 3:
                await asyncio.sleep(0.01)
            await browser.incoming.put(None)

        task = asyncio.create_task(_disconnect_after_a_few_checks())
        try:
            await bridge_websocket(
                browser,
                "ws://synthetic.invalid/session",
                headers={},
                revalidate=revalidate,
                revalidate_interval=0.01,
            )
        finally:
            task.cancel()

        assert browser.close_calls == []  # never force-closed by revalidation
        assert calls["n"] >= 3

    def test_proxy_ws_wires_revalidate_and_the_configured_interval(
        self, tmp_path, monkeypatch
    ) -> None:
        """_proxy_ws must pass a revalidate callable and the configured
        interval through to bridge_websocket — not just perform the
        one-time attach/role check at connect."""
        from starlette.websockets import WebSocketDisconnect

        app, reg = _bare_app(tmp_path)
        reg.register("sess", 9123)

        captured = {}

        async def fake_bridge_websocket(websocket, connect_url, **kwargs):
            captured.update(kwargs)
            raise WebSocketDisconnect()

        monkeypatch.setattr("niuu.session_proxy.bridge_websocket", fake_bridge_websocket)

        with pytest.raises(WebSocketDisconnect):
            with TestClient(app).websocket_connect("/s/sess/session") as ws:
                ws.receive_text()

        assert callable(captured["revalidate"])
        assert captured["revalidate_interval"] > 0


class TestImmediateCloseHook:
    """SkuldPortRegistry.close_connections is the in-process hook that lets a
    revoke close an ALREADY-OPEN socket without waiting for the next
    interval revalidation tick."""

    async def test_close_connections_closes_every_tracked_socket_for_the_pair(self, tmp_path):
        from niuu.session_proxy import SkuldPortRegistry

        reg = SkuldPortRegistry(state_file=tmp_path / "forge-state.json")
        ws_a, ws_b, other = AsyncMock(), AsyncMock(), AsyncMock()
        reg.track_connection("sess", "alice", ws_a)
        reg.track_connection("sess", "alice", ws_b)
        reg.track_connection("sess", "bob", other)

        closed = await reg.close_connections("sess", "alice", reason="revoked")

        assert closed == 2
        ws_a.close.assert_awaited_once_with(code=1008, reason="revoked")
        ws_b.close.assert_awaited_once_with(code=1008, reason="revoked")
        other.close.assert_not_called()

    async def test_close_connections_is_a_noop_for_an_untracked_pair(self, tmp_path):
        from niuu.session_proxy import SkuldPortRegistry

        reg = SkuldPortRegistry(state_file=tmp_path / "forge-state.json")
        assert await reg.close_connections("sess", "nobody") == 0

    async def test_close_connections_skips_a_socket_that_fails_to_close(self, tmp_path):
        from niuu.session_proxy import SkuldPortRegistry

        reg = SkuldPortRegistry(state_file=tmp_path / "forge-state.json")
        broken = AsyncMock()
        broken.close.side_effect = RuntimeError("already gone")
        reg.track_connection("sess", "alice", broken)

        closed = await reg.close_connections("sess", "alice")

        assert closed == 0  # did not raise

    async def test_untrack_removes_only_that_socket(self, tmp_path):
        from niuu.session_proxy import SkuldPortRegistry

        reg = SkuldPortRegistry(state_file=tmp_path / "forge-state.json")
        ws_a, ws_b = AsyncMock(), AsyncMock()
        reg.track_connection("sess", "alice", ws_a)
        reg.track_connection("sess", "alice", ws_b)
        reg.untrack_connection("sess", "alice", ws_a)

        closed = await reg.close_connections("sess", "alice")

        assert closed == 1
        ws_a.close.assert_not_called()
        ws_b.close.assert_awaited_once()

    def test_proxy_ws_tracks_and_untracks_the_socket_around_the_bridge(
        self, tmp_path, monkeypatch
    ) -> None:
        from starlette.websockets import WebSocketDisconnect

        app, reg = _bare_app(tmp_path, dev_identity=True)
        app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
        reg.register("sess", 9123)
        seen_during_bridge = {}

        async def fake_bridge_websocket(websocket, connect_url, **kwargs):
            # Deep-copy: _live_connections' sets are mutated in place by the
            # finally block right after this raises, in the SAME task.
            seen_during_bridge["tracked"] = {k: set(v) for k, v in reg._live_connections.items()}
            raise WebSocketDisconnect()

        monkeypatch.setattr("niuu.session_proxy.bridge_websocket", fake_bridge_websocket)

        with pytest.raises(WebSocketDisconnect):
            with TestClient(app).websocket_connect(
                "/s/sess/session?devUserId=alice&devTenantId=t1"
            ) as ws:
                ws.receive_text()

        assert len(seen_during_bridge["tracked"].get(("sess", "alice"), ())) == 1
        assert reg._live_connections == {}  # untracked once the connection ended
