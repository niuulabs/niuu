"""Tests for the WS-proxy session-ownership guard (niuu.app.SkuldPortRegistry)."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

from niuu.app import SkuldPortRegistry, _proxy_ws_identity


def _ws(headers: dict | None = None, query: dict | None = None):
    return SimpleNamespace(
        headers=(headers or {}),
        query_params=(query or {}),
    )


def _jwt(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{payload}.sig"


class TestProxyWsIdentity:
    def test_envoy_headers(self):
        user, tenant, roles = _proxy_ws_identity(
            _ws(
                headers={
                    "x-auth-user-id": "alice",
                    "x-auth-tenant": "t1",
                    "x-auth-roles": "volundr:developer,volundr:admin",
                }
            )
        )
        assert user == "alice"
        assert tenant == "t1"
        assert "volundr:admin" in roles

    def test_dev_query_params(self):
        user, tenant, roles = _proxy_ws_identity(
            _ws(query={"devUserId": "bob", "devTenantId": "t2", "devRoles": "volundr:viewer"})
        )
        assert user == "bob"
        assert tenant == "t2"
        assert roles == ("volundr:viewer",)

    def test_headers_win_over_query(self):
        user, _tenant, _roles = _proxy_ws_identity(
            _ws(headers={"x-auth-user-id": "alice"}, query={"devUserId": "bob"})
        )
        assert user == "alice"

    def test_no_identity(self):
        user, tenant, roles = _proxy_ws_identity(_ws())
        assert user is None
        assert tenant is None
        assert roles == ()

    def test_bearer_access_token_query_param(self):
        # web-next getWebSocketAuth appends ?access_token=<jwt> in token-auth
        # deployments — the owner must resolve, not be locked out.
        token = _jwt({"sub": "carol", "tenant": "t3", "roles": ["volundr:developer"]})
        user, tenant, roles = _proxy_ws_identity(_ws(query={"access_token": token}))
        assert user == "carol"
        assert tenant == "t3"
        assert roles == ("volundr:developer",)

    def test_bearer_authorization_header(self):
        token = _jwt({"sub": "dave"})
        user, _tenant, _roles = _proxy_ws_identity(
            _ws(headers={"authorization": f"Bearer {token}"})
        )
        assert user == "dave"

    def test_bearer_subprotocol(self):
        token = _jwt({"sub": "erin"})
        user, _tenant, _roles = _proxy_ws_identity(
            _ws(headers={"sec-websocket-protocol": f"volundr.bearer.{token}"})
        )
        assert user == "erin"

    def test_bearer_keycloak_realm_roles(self):
        token = _jwt({"sub": "frank", "realm_access": {"roles": ["volundr:admin"]}})
        _user, _tenant, roles = _proxy_ws_identity(_ws(query={"access_token": token}))
        assert roles == ("volundr:admin",)

    def test_bearer_without_sub_is_no_identity(self):
        token = _jwt({"name": "nobody"})
        user, _tenant, _roles = _proxy_ws_identity(_ws(query={"access_token": token}))
        assert user is None


class TestMayAttach:
    async def test_permissive_without_guard(self):
        reg = SkuldPortRegistry()
        assert await reg.may_attach("s1", "anyone", None, ()) is True

    async def test_guard_allows_owner(self):
        reg = SkuldPortRegistry()

        async def guard(session_id, user_id, tenant_id, roles):
            return user_id == "alice"

        reg.set_ownership_guard(guard)
        assert await reg.may_attach("s1", "alice", None, ()) is True
        assert await reg.may_attach("s1", "mallory", None, ()) is False


def _owned_session(owner_id: str | None, tenant_id: str | None = None):
    return SimpleNamespace(owner_id=owner_id, tenant_id=tenant_id)


class TestOwnershipGuardPolicy:
    """The policy the composition root installs (mirrored here for coverage)."""

    @staticmethod
    def _make_guard(sessions: dict):
        async def guard(session_id, user_id, tenant_id, roles):
            session = sessions.get(session_id)
            if session is None or not session.owner_id:
                return True
            if session.tenant_id and tenant_id and session.tenant_id != tenant_id:
                return False
            if "volundr:admin" in roles:
                return True
            return user_id == session.owner_id

        return guard

    async def test_owner_allowed(self):
        guard = self._make_guard({"s1": _owned_session("alice")})
        assert await guard("s1", "alice", None, ()) is True

    async def test_non_owner_denied(self):
        guard = self._make_guard({"s1": _owned_session("alice")})
        assert await guard("s1", "mallory", None, ()) is False

    async def test_admin_bypass(self):
        guard = self._make_guard({"s1": _owned_session("alice")})
        assert await guard("s1", "root", None, ("volundr:admin",)) is True

    async def test_cross_tenant_denied_even_admin(self):
        guard = self._make_guard({"s1": _owned_session("alice", "t1")})
        assert await guard("s1", "root", "t2", ("volundr:admin",)) is False

    async def test_unowned_session_permissive(self):
        guard = self._make_guard({"s1": _owned_session(None)})
        assert await guard("s1", "anyone", None, ()) is True

    async def test_unknown_session_permissive(self):
        guard = self._make_guard({})
        assert await guard("missing", "anyone", None, ()) is True
