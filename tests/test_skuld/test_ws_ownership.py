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
from fastapi import WebSocketDisconnect

from skuld.broker import (
    Broker,
    _is_loopback_ws_client,
    _resolve_ws_principal,
)
from skuld.config import SkuldSettings


def _fake_ws(headers=None, query=None, host="203.0.113.7"):
    """Minimal WebSocket stand-in for pre-accept authorization checks."""
    return SimpleNamespace(
        headers=dict(headers or {}),
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


def _broker(owner_id="", tenant_id="", **ws_auth) -> Broker:
    settings = SkuldSettings(
        session={"id": "test-session", "owner_id": owner_id, "tenant_id": tenant_id},
        transport="subprocess",
        **({"ws_auth": ws_auth} if ws_auth else {}),
    )
    return Broker(settings=settings)


class TestResolveWsPrincipal:
    def test_envoy_headers_win(self):
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

    def test_dev_query_params(self):
        ws = _fake_ws(query={"devUserId": "bob", "devTenantId": "t2", "devRoles": "volundr:viewer"})
        principal = _resolve_ws_principal(ws)
        assert principal is not None
        assert principal.user_id == "bob"
        assert principal.tenant_id == "t2"
        assert principal.roles == ("volundr:viewer",)

    def test_dev_query_default_role(self):
        principal = _resolve_ws_principal(_fake_ws(query={"devUserId": "bob"}))
        assert principal is not None
        assert principal.roles == ("volundr:developer",)

    def test_bearer_token_claims(self):
        token = _jwt({"sub": "carol", "tenant": "t3", "roles": ["volundr:developer"]})
        ws = _fake_ws(headers={"authorization": f"Bearer {token}"})
        principal = _resolve_ws_principal(ws)
        assert principal is not None
        assert principal.user_id == "carol"
        assert principal.tenant_id == "t3"
        assert principal.roles == ("volundr:developer",)

    def test_envoy_token_query_param(self):
        token = _jwt({"sub": "dave"})
        principal = _resolve_ws_principal(_fake_ws(query={"token": token}))
        assert principal is not None
        assert principal.user_id == "dave"

    def test_legacy_access_token_query_param(self):
        token = _jwt({"sub": "dave"})
        principal = _resolve_ws_principal(_fake_ws(query={"access_token": token}))
        assert principal is not None
        assert principal.user_id == "dave"
        assert principal.tenant_id == ""
        assert principal.roles == ()

    def test_subprotocol_token(self):
        token = _jwt({"sub": "erin"})
        ws = _fake_ws(headers={"sec-websocket-protocol": f"volundr.bearer.{token}"})
        principal = _resolve_ws_principal(ws)
        assert principal is not None
        assert principal.user_id == "erin"

    def test_keycloak_realm_access_roles(self):
        token = _jwt({"sub": "frank", "realm_access": {"roles": ["volundr:admin"]}})
        principal = _resolve_ws_principal(_fake_ws(headers={"authorization": f"Bearer {token}"}))
        assert principal is not None
        assert principal.roles == ("volundr:admin",)

    def test_no_identity(self):
        assert _resolve_ws_principal(_fake_ws()) is None

    def test_token_without_sub(self):
        token = _jwt({"name": "nobody"})
        assert _resolve_ws_principal(_fake_ws(query={"access_token": token})) is None


class TestLoopbackDetection:
    def test_loopback_hosts(self):
        assert _is_loopback_ws_client(_fake_ws(host="127.0.0.1"))
        assert _is_loopback_ws_client(_fake_ws(host="::1"))

    def test_remote_host(self):
        assert not _is_loopback_ws_client(_fake_ws(host="203.0.113.7"))

    def test_missing_client(self):
        ws = _fake_ws()
        ws.client = None
        assert not _is_loopback_ws_client(ws)


class TestAuthorizeWebsocket:
    def test_no_owner_allows_everyone(self):
        broker = _broker(owner_id="")
        assert broker._authorize_websocket(_fake_ws(), endpoint="t") is True

    def test_enforcement_disabled_allows_everyone(self):
        broker = _broker(owner_id="alice", enforce_ownership=False)
        ws = _fake_ws(headers={"x-auth-user-id": "mallory"})
        assert broker._authorize_websocket(ws, endpoint="t") is True

    def test_owner_match_allows(self):
        broker = _broker(owner_id="alice")
        ws = _fake_ws(headers={"x-auth-user-id": "alice"})
        assert broker._authorize_websocket(ws, endpoint="t") is True

    def test_non_owner_denied(self):
        broker = _broker(owner_id="alice")
        ws = _fake_ws(headers={"x-auth-user-id": "mallory"})
        assert broker._authorize_websocket(ws, endpoint="t") is False

    def test_admin_bypass(self):
        broker = _broker(owner_id="alice")
        ws = _fake_ws(headers={"x-auth-user-id": "root", "x-auth-roles": "volundr:admin"})
        assert broker._authorize_websocket(ws, endpoint="t") is True

    def test_cross_tenant_denied_even_for_admin(self):
        broker = _broker(owner_id="alice", tenant_id="t1")
        ws = _fake_ws(
            headers={
                "x-auth-user-id": "root",
                "x-auth-tenant": "t2",
                "x-auth-roles": "volundr:admin",
            }
        )
        assert broker._authorize_websocket(ws, endpoint="t") is False

    def test_unknown_identity_tenant_skips_tenant_check(self):
        # PATs carry only ``sub`` — an empty identity tenant must not deny
        # the owner.
        broker = _broker(owner_id="alice", tenant_id="t1")
        token = _jwt({"sub": "alice"})
        ws = _fake_ws(query={"access_token": token})
        assert broker._authorize_websocket(ws, endpoint="t") is True

    def test_unauthenticated_loopback_allowed(self):
        broker = _broker(owner_id="alice")
        assert broker._authorize_websocket(_fake_ws(host="127.0.0.1"), endpoint="t") is True

    def test_unauthenticated_remote_denied(self):
        broker = _broker(owner_id="alice")
        assert broker._authorize_websocket(_fake_ws(host="203.0.113.7"), endpoint="t") is False

    def test_loopback_disallowed_when_configured(self):
        broker = _broker(owner_id="alice", allow_loopback=False)
        assert broker._authorize_websocket(_fake_ws(host="127.0.0.1"), endpoint="t") is False

    def test_dev_identity_owner_match(self):
        broker = _broker(owner_id="dev-user")
        ws = _fake_ws(query={"devUserId": "dev-user"})
        assert broker._authorize_websocket(ws, endpoint="t") is True


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
        broker = _broker(owner_id="alice")
        register = AsyncMock()
        unregister = AsyncMock()
        broker._room_bridge = MagicMock(register=register, unregister=unregister)
        ws = _fake_ws(host="127.0.0.1")
        ws.receive_text = AsyncMock(side_effect=WebSocketDisconnect(code=1000))
        await broker.handle_ravn_websocket(ws, peer_id="flock-coder")
        ws.accept.assert_awaited_once()
        register.assert_awaited()
        unregister.assert_awaited_once()
