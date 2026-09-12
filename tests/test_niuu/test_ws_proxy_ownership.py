"""Tests for the WS-proxy session-ownership guard (niuu.app.SkuldPortRegistry)."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from niuu.app import SkuldPortRegistry, _proxy_ws_identity


def _ws(headers: dict | None = None, query: dict | None = None):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(identity=EnvoyHeaderAuthenticationAdapter())),
        headers=(headers or {}),
        query_params=(query or {}),
    )


def _jwt(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{payload}.sig"


class TestProxyWsIdentity:
    async def test_envoy_headers(self):
        user, tenant, roles = await _proxy_ws_identity(
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

    @pytest.mark.parametrize(
        "carrier", ["authorization", "token", "access_token", "protocol", "dev"]
    )
    async def test_unsigned_credentials_cannot_establish_identity(self, carrier):
        token = _jwt({"sub": "alice", "tenant": "t1", "roles": ["volundr:admin"]})
        ws = {
            "authorization": _ws(headers={"authorization": f"Bearer {token}"}),
            "token": _ws(query={"token": token}),
            "access_token": _ws(query={"access_token": token}),
            "protocol": _ws(headers={"sec-websocket-protocol": f"volundr.bearer.{token}"}),
            "dev": _ws(query={"devUserId": "alice", "devRoles": "volundr:admin"}),
        }[carrier]
        assert await _proxy_ws_identity(ws) == (None, None, ())

    async def test_missing_claims_are_not_invented(self):
        assert await _proxy_ws_identity(_ws(headers={"x-auth-user-id": "alice"})) == (
            "alice",
            "",
            (),
        )

    async def test_no_identity(self):
        assert await _proxy_ws_identity(_ws()) == (None, None, ())


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
