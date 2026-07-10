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

    def test_bearer_envoy_token_query_param(self):
        # web-next uses Envoy's configured ?token=<jwt> extraction on browser
        # WebSocket upgrades; the app accepts the same token for direct mode.
        token = _jwt({"sub": "carol", "tenant": "t3", "roles": ["volundr:developer"]})
        user, tenant, roles = _proxy_ws_identity(_ws(query={"token": token}))
        assert user == "carol"
        assert tenant == "t3"
        assert roles == ("volundr:developer",)

    def test_bearer_access_token_query_param_remains_compatible(self):
        token = _jwt({"sub": "carol"})
        user, _tenant, _roles = _proxy_ws_identity(_ws(query={"access_token": token}))
        assert user == "carol"

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


class TestOwnershipGuardPolicy:
    """The guard delegates owned-session decisions to the REAL authorization
    adapter (the same one the REST API uses), via the Principal/Resource shape
    _may_attach builds. These exercise that actual policy target so the WS
    attach check can't drift from REST."""

    @staticmethod
    async def _attach(
        adapter,
        *,
        owner_id: str,
        tenant_id: str = "",
        user_id: str,
        principal_tenant: str = "",
        roles: tuple[str, ...] = (),
    ) -> bool:
        from niuu.domain.models import Principal
        from volundr.domain.ports import Resource

        principal = Principal(
            user_id=user_id, email="", tenant_id=principal_tenant, roles=list(roles)
        )
        resource = Resource(
            kind="session", id="s1", attr={"owner_id": owner_id, "tenant_id": tenant_id}
        )
        return await adapter.is_allowed(principal, "start", resource)

    @staticmethod
    def _adapter():
        from volundr.adapters.outbound.authorization import SimpleRoleAuthorizationAdapter

        return SimpleRoleAuthorizationAdapter()

    async def test_owner_allowed(self):
        assert await self._attach(self._adapter(), owner_id="alice", user_id="alice") is True

    async def test_non_owner_denied(self):
        assert await self._attach(self._adapter(), owner_id="alice", user_id="mallory") is False

    async def test_admin_bypass(self):
        assert (
            await self._attach(
                self._adapter(), owner_id="alice", user_id="root", roles=("volundr:admin",)
            )
            is True
        )

    async def test_cross_tenant_denied_even_admin(self):
        assert (
            await self._attach(
                self._adapter(),
                owner_id="alice",
                tenant_id="t1",
                user_id="root",
                principal_tenant="t2",
                roles=("volundr:admin",),
            )
            is False
        )

    async def test_viewer_only_owner_denied_on_mutating_attach(self):
        # Chat is bidirectional (the attacher can send), so it is the mutating
        # "start" action — a viewer-only principal is denied even on their own
        # session. This is the intended tightening from routing through the
        # canonical adapter rather than the old hand-rolled owner==user check.
        assert (
            await self._attach(
                self._adapter(), owner_id="alice", user_id="alice", roles=("volundr:viewer",)
            )
            is False
        )
