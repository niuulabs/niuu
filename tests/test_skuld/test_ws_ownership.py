"""Tests for WebSocket session-ownership enforcement.

The broker rejects WebSocket connections whose resolved identity does not own
the session (``skuld.config.WsAuthConfig``). Identity resolution mirrors
Volundr's ``extract_principal`` (Envoy headers → dev query params → bearer
claims); the verdict mirrors ``SimpleRoleAuthorizationAdapter`` (tenant
scoping, admin bypass, owner match).
"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocket, WebSocketDisconnect

from skuld.broker import (
    Broker,
    _is_loopback_ws_client,
    _resolve_ws_principal,
)
from skuld.config import SkuldSettings


def _fake_ws(headers=None, query=None, host="203.0.113.7"):
    """Minimal WebSocket stand-in for pre-accept authorization checks."""
    return SimpleNamespace(
        headers={"x-auth-tenant": "t1", "x-auth-roles": "volundr:developer", **dict(headers or {})},
        query_params=dict(query or {}),
        client=SimpleNamespace(host=host),
        accept=AsyncMock(),
        close=AsyncMock(),
        receive_text=AsyncMock(),
        send_json=AsyncMock(),
    )


def _jwt(claims: dict) -> str:
    """Unsigned JWT-shaped token; the broker decodes claims without verifying."""
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{payload}.sig"


def _broker(owner_id="", tenant_id="t1", **ws_auth) -> Broker:
    settings = SkuldSettings(
        session={"id": "test-session", "owner_id": owner_id, "tenant_id": tenant_id},
        transport="subprocess",
        ws_auth={"enforce_ownership": True, **ws_auth},
    )
    return Broker(settings=settings)


class TestResolveWsPrincipal:
    async def test_envoy_headers_win(self):
        ws = _fake_ws(
            headers={
                "x-auth-user-id": "alice",
                "x-auth-tenant": "t1",
                "x-auth-roles": "volundr:developer,volundr:admin",
            },
            query={"devUserId": "mallory"},
        )
        principal = _resolve_ws_principal(ws)
        assert principal is not None
        assert principal.user_id == "alice"
        assert principal.tenant_id == "t1"
        assert "volundr:admin" in principal.roles

    @pytest.mark.parametrize(
        "carrier", ["authorization", "token", "access_token", "protocol", "dev"]
    )
    async def test_unverified_identity_is_not_accepted(self, carrier):
        token = _jwt({"sub": "alice", "tenant": "t1", "roles": ["volundr:admin"]})
        ws = {
            "authorization": _fake_ws(headers={"authorization": f"Bearer {token}"}),
            "token": _fake_ws(query={"token": token}),
            "access_token": _fake_ws(query={"access_token": token}),
            "protocol": _fake_ws(headers={"sec-websocket-protocol": f"volundr.bearer.{token}"}),
            "dev": _fake_ws(query={"devUserId": "alice", "devTenantId": "t1"}),
        }[carrier]
        assert _resolve_ws_principal(ws) is None

    async def test_missing_roles_grant_nothing(self):
        assert (
            _resolve_ws_principal(
                _fake_ws(headers={"x-auth-user-id": "alice", "x-auth-roles": ""})
            ).roles
            == ()
        )

    async def test_no_identity(self):
        assert _resolve_ws_principal(_fake_ws()) is None

    async def test_token_without_sub(self):
        token = _jwt({"name": "nobody"})
        assert _resolve_ws_principal(_fake_ws(query={"access_token": token})) is None


class TestLoopbackDetection:
    async def test_loopback_hosts(self):
        assert _is_loopback_ws_client(_fake_ws(host="127.0.0.1"))
        assert _is_loopback_ws_client(_fake_ws(host="::1"))

    async def test_remote_host(self):
        assert not _is_loopback_ws_client(_fake_ws(host="203.0.113.7"))

    async def test_missing_client(self):
        ws = _fake_ws()
        ws.client = None
        assert not _is_loopback_ws_client(ws)


class TestAuthorizeWebsocket:
    async def test_no_owner_denies_when_enforced(self):
        broker = _broker(owner_id="")
        assert await broker._authorize_websocket(_fake_ws(), endpoint="t") is False

    async def test_enforcement_disabled_allows_everyone(self):
        broker = _broker(owner_id="alice", enforce_ownership=False)
        ws = _fake_ws(headers={"x-auth-user-id": "mallory"})
        assert await broker._authorize_websocket(ws, endpoint="t") is True

    async def test_owner_match_allows(self):
        broker = _broker(owner_id="alice")
        ws = _fake_ws(headers={"x-auth-user-id": "alice"})
        assert await broker._authorize_websocket(ws, endpoint="t") is True

    async def test_non_owner_denied(self):
        broker = _broker(owner_id="alice")
        ws = _fake_ws(headers={"x-auth-user-id": "mallory"})
        assert await broker._authorize_websocket(ws, endpoint="t") is False

    async def test_admin_bypass(self):
        broker = _broker(owner_id="alice")
        ws = _fake_ws(headers={"x-auth-user-id": "root", "x-auth-roles": "volundr:admin"})
        assert await broker._authorize_websocket(ws, endpoint="t") is True

    async def test_cross_tenant_denied_even_for_admin(self):
        broker = _broker(owner_id="alice", tenant_id="t1")
        ws = _fake_ws(
            headers={
                "x-auth-user-id": "root",
                "x-auth-tenant": "t2",
                "x-auth-roles": "volundr:admin",
            }
        )
        assert await broker._authorize_websocket(ws, endpoint="t") is False

    async def test_unknown_identity_tenant_denied(self):
        broker = _broker(owner_id="alice", tenant_id="t1")
        token = _jwt({"sub": "alice"})
        ws = _fake_ws(query={"access_token": token})
        assert await broker._authorize_websocket(ws, endpoint="t") is False

    async def test_unauthenticated_loopback_denied_by_default(self):
        broker = _broker(owner_id="alice")
        assert await broker._authorize_websocket(_fake_ws(host="127.0.0.1"), endpoint="t") is False

    async def test_unauthenticated_remote_denied(self):
        broker = _broker(owner_id="alice")
        assert (
            await broker._authorize_websocket(_fake_ws(host="203.0.113.7"), endpoint="t") is False
        )

    async def test_loopback_disallowed_when_configured(self):
        broker = _broker(owner_id="alice", allow_loopback=False)
        assert await broker._authorize_websocket(_fake_ws(host="127.0.0.1"), endpoint="t") is False

    async def test_dev_identity_does_not_bypass_enforcement(self):
        broker = _broker(owner_id="dev-user")
        ws = _fake_ws(query={"devUserId": "dev-user"})
        assert await broker._authorize_websocket(ws, endpoint="t") is False


class TestHandlerRejection:
    @pytest.mark.asyncio
    async def test_browser_websocket_rejected_before_accept(self):
        broker = _broker(owner_id="alice")
        ws = _fake_ws(headers={"x-auth-user-id": "mallory"})
        await broker.handle_websocket(ws)
        ws.close.assert_awaited_once()
        assert ws.close.await_args.kwargs.get("code") == 1008
        ws.accept.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejected_caller_does_not_overwrite_jwt(self):
        broker = _broker(owner_id="alice")
        broker._user_jwt = "existing-jwt"
        token = _jwt({"sub": "mallory"})
        ws = _fake_ws(headers={"authorization": f"Bearer {token}"})
        await broker.handle_websocket(ws)
        assert broker._user_jwt == "existing-jwt"

    @pytest.mark.asyncio
    async def test_ravn_websocket_rejected_before_register(self):
        broker = _broker(owner_id="alice")
        broker._room_bridge = MagicMock(register=AsyncMock(), unregister=AsyncMock())
        ws = _fake_ws(headers={"x-auth-user-id": "mallory"})
        await broker.handle_ravn_websocket(ws, peer_id="steward")
        ws.close.assert_awaited_once()
        assert ws.close.await_args.kwargs.get("code") == 1008
        broker._room_bridge.register.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cli_websocket_rejected(self):
        broker = _broker(owner_id="alice")
        ws = _fake_ws(headers={"x-auth-user-id": "mallory"})
        await broker.handle_cli_websocket(ws, session_id="test-session")
        ws.close.assert_awaited_once()
        assert ws.close.await_args.kwargs.get("code") == 1008

    @pytest.mark.asyncio
    async def test_ravn_websocket_loopback_peer_allowed(self):
        # In-pod flock daemons carry no user token; loopback keeps them working.
        broker = _broker(owner_id="alice", allow_loopback=True)
        register = AsyncMock()
        unregister = AsyncMock()
        broker._room_bridge = MagicMock(register=register, unregister=unregister)
        ws = _fake_ws(host="127.0.0.1")
        ws.receive_text = AsyncMock(side_effect=WebSocketDisconnect(code=1000))
        await broker.handle_ravn_websocket(ws, peer_id="flock-coder")
        ws.accept.assert_awaited_once()
        register.assert_awaited()
        unregister.assert_awaited_once()


async def test_mini_mode_without_auth_accepts_unauthenticated_browser():
    broker = Broker(settings=SkuldSettings(transport="subprocess"))
    assert await broker._authorize_websocket(_fake_ws(), endpoint="handle_websocket")


async def test_loopback_trust_never_applies_to_browser_endpoint():
    broker = _broker(owner_id="alice", allow_loopback=True)
    assert not await broker._authorize_websocket(
        _fake_ws(host="127.0.0.1"), endpoint="handle_websocket"
    )


async def test_missing_tenant_denies_even_verified_owner():
    broker = _broker(owner_id="alice")
    assert not await broker._authorize_websocket(
        _fake_ws(headers={"x-auth-user-id": "alice", "x-auth-tenant": ""}), endpoint="t"
    )


@pytest.mark.parametrize("endpoint", ["handle_cli_websocket", "handle_ravn_websocket"])
async def test_proxied_loopback_cannot_claim_in_pod_trust(endpoint):
    broker = _broker(owner_id="alice", allow_loopback=True)
    ws = _fake_ws(host="127.0.0.1", headers={"x-forwarded-for": "203.0.113.7"})
    assert not await broker._authorize_websocket(ws, endpoint=endpoint)


@pytest.mark.parametrize("role", ["volundr:viewer", "unknown", ""])
async def test_cedar_denies_interactive_attachment_without_write_role(role):
    broker = _broker(owner_id="alice")
    ws = _fake_ws(headers={"x-auth-user-id": "alice", "x-auth-roles": role})
    assert not await broker._authorize_websocket(ws, endpoint="handle_websocket")


async def test_cedar_failure_denies_before_socket_accept():
    from identity.ports import AuthorizationEvaluationError

    broker = _broker(owner_id="alice")
    broker._ws_authorization = AsyncMock()
    broker._ws_authorization.is_allowed.side_effect = AuthorizationEvaluationError("unavailable")
    ws = _fake_ws(headers={"x-auth-user-id": "alice"})
    await broker.handle_websocket(ws)
    ws.accept.assert_not_awaited()
    ws.close.assert_awaited_once()


async def test_custom_proxy_headers_and_roles_reach_cedar():
    broker = _broker(
        owner_id="alice",
        user_id_header="x-verified-user",
        tenant_header="x-verified-tenant",
        roles_header="x-verified-roles",
        role_mapping={"builder": "volundr:developer"},
    )
    ws = _fake_ws(
        headers={
            "x-verified-user": "alice",
            "x-verified-tenant": "t1",
            "x-verified-roles": "builder",
            "x-auth-user-id": "forged",
            "x-auth-roles": "volundr:admin",
        }
    )
    assert await broker._authorize_websocket(ws, endpoint="handle_websocket") is True
    ws.headers["x-verified-roles"] = "unknown"
    assert await broker._authorize_websocket(ws, endpoint="handle_websocket") is False


def test_enforced_broker_closes_expired_browser_connection(monkeypatch):
    import time
    from collections import deque

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from skuld import broker_api

    app = FastAPI()
    monkeypatch.setattr(broker_api, "app", app)
    monkeypatch.setattr(broker_api, "_broker_getter", broker_api._broker_getter)
    monkeypatch.setattr(broker_api, "_log_buffer", broker_api._log_buffer)
    secured = _broker(owner_id="alice", websocket_check_interval=0.01)
    broker_api.bind_broker(lambda: secured, deque())

    async def connection(websocket: WebSocket):
        await websocket.accept()
        await websocket.receive_text()

    app.websocket("/socket")(connection)
    with TestClient(app) as client:
        with client.websocket_connect(
            f"/socket?token={_jwt({'exp': time.time() + 0.2})}"
        ) as socket:
            with pytest.raises(WebSocketDisconnect) as exc:
                socket.receive_text()
            assert exc.value.code == 1008
