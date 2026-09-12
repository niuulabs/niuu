"""Tests for FastAPI authentication dependencies."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.auth import (
    check_authorization,
    extract_principal,
    get_current_user,
    require_role,
)
from volundr.adapters.outbound.identity import EnvoyHeaderIdentityAdapter
from volundr.domain.models import Principal, User, UserStatus
from volundr.domain.ports import InvalidTokenError, Resource, UserProvisioningError


def _make_app(identity=None, authorization=None):
    app = FastAPI()
    app.state.identity = identity or AsyncMock()
    app.state.authorization = authorization or AsyncMock()
    return app


def _admin_principal():
    return Principal(
        user_id="u1",
        email="admin@test.com",
        tenant_id="t1",
        roles=["volundr:admin"],
    )


def _viewer_principal():
    return Principal(
        user_id="u2",
        email="viewer@test.com",
        tenant_id="t1",
        roles=["volundr:viewer"],
    )


class TestExtractPrincipal:
    """Tests for extract_principal dependency."""

    @pytest.mark.parametrize(
        "headers,query",
        [
            ({"x-auth-user-id": "attacker", "x-auth-roles": "volundr:admin"}, ""),
            ({}, "?devUserId=attacker&devRoles=volundr:admin&devTenantId=other"),
        ],
    )
    def test_untrusted_identity_cannot_bypass_token_validation(self, headers, query):
        identity = AsyncMock()
        identity.validate_token.side_effect = InvalidTokenError("bad token")
        app = _make_app(identity=identity)

        @app.get("/effect")
        async def effect(principal: Principal = Depends(extract_principal)):
            pytest.fail("Unauthenticated request reached the protected effect")

        response = TestClient(app).get("/effect" + query, headers=headers)
        assert response.status_code == 401

    def test_dev_query_cannot_bypass_envoy_identity(self):
        app = _make_app(identity=EnvoyHeaderIdentityAdapter(user_repository=AsyncMock()))

        @app.get("/effect")
        async def effect(principal: Principal = Depends(extract_principal)):
            pytest.fail("Development identity bypassed Envoy authentication")

        assert (
            TestClient(app).get("/effect?devUserId=admin&devRoles=volundr:admin").status_code == 401
        )

    def test_shared_auth_denies_when_identity_is_not_configured(self):
        from niuu.adapters.inbound.auth import extract_principal as shared_auth

        app = FastAPI()

        @app.get("/effect")
        async def effect(principal: Principal = Depends(shared_auth)):
            pytest.fail("Unconfigured authentication admitted a request")

        response = TestClient(app).get("/effect?devUserId=admin")
        assert response.status_code == 401

    @pytest.mark.parametrize(
        "headers,query,expected",
        [
            ({}, "", "dev-user"),
            ({"x-auth-user-id": "local-user"}, "", "local-user"),
            ({}, "?devUserId=local-query&devRoles=volundr:viewer", "local-query"),
        ],
    )
    def test_explicit_dev_adapter_keeps_local_development_working(self, headers, query, expected):
        from identity.adapters.identity import AllowAllIdentityAdapter

        app = _make_app(identity=AllowAllIdentityAdapter(user_repository=AsyncMock()))

        @app.get("/identity")
        async def who(principal: Principal = Depends(extract_principal)):
            return principal.user_id

        response = TestClient(app).get("/identity" + query, headers=headers)
        assert response.status_code == 200
        assert response.json() == expected

    def test_missing_auth_header_returns_401(self):
        identity = AsyncMock()
        app = _make_app(identity=identity)

        @app.get("/test")
        async def endpoint(principal: Principal = Depends(extract_principal)):
            return {"user_id": principal.user_id}

        client = TestClient(app)
        resp = client.get("/test")
        assert resp.status_code == 401
        assert "Missing Authorization" in resp.json()["detail"]

    def test_valid_token_returns_principal(self):
        identity = AsyncMock()
        identity.validate_token.return_value = _admin_principal()
        app = _make_app(identity=identity)

        @app.get("/test")
        async def endpoint(principal: Principal = Depends(extract_principal)):
            return {"user_id": principal.user_id}

        client = TestClient(app)
        resp = client.get("/test", headers={"Authorization": "Bearer tok"})
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "u1"

    def test_invalid_token_returns_401(self):
        identity = AsyncMock()
        identity.validate_token.side_effect = InvalidTokenError("bad")
        app = _make_app(identity=identity)

        @app.get("/test")
        async def endpoint(principal: Principal = Depends(extract_principal)):
            return {}

        client = TestClient(app)
        resp = client.get("/test", headers={"Authorization": "Bearer bad"})
        assert resp.status_code == 401
        assert "bad" in resp.json()["detail"]


class TestGetCurrentUser:
    """Tests for get_current_user dependency."""

    def test_returns_user(self):
        identity = AsyncMock()
        identity.validate_token.return_value = _admin_principal()
        user = User(id="u1", email="admin@test.com", status=UserStatus.ACTIVE)
        identity.get_or_provision_user.return_value = user
        app = _make_app(identity=identity)

        @app.get("/test")
        async def endpoint(user: User = Depends(get_current_user)):
            return {"id": user.id}

        client = TestClient(app)
        resp = client.get("/test", headers={"Authorization": "Bearer tok"})
        assert resp.status_code == 200
        assert resp.json()["id"] == "u1"

    def test_provisioning_error_returns_503(self):
        identity = AsyncMock()
        identity.validate_token.return_value = _admin_principal()
        identity.get_or_provision_user.side_effect = UserProvisioningError("in progress")
        app = _make_app(identity=identity)

        @app.get("/test")
        async def endpoint(user: User = Depends(get_current_user)):
            return {}

        client = TestClient(app)
        resp = client.get("/test", headers={"Authorization": "Bearer tok"})
        assert resp.status_code == 503
        assert "Retry-After" in resp.headers


class TestRequireRole:
    """Tests for require_role dependency factory."""

    def test_allowed_role_passes(self):
        identity = AsyncMock()
        identity.validate_token.return_value = _admin_principal()
        app = _make_app(identity=identity)

        @app.get("/test", dependencies=[Depends(require_role("volundr:admin"))])
        async def endpoint():
            return {"ok": True}

        client = TestClient(app)
        resp = client.get("/test", headers={"Authorization": "Bearer tok"})
        assert resp.status_code == 200

    def test_missing_role_returns_403(self):
        identity = AsyncMock()
        identity.validate_token.return_value = _viewer_principal()
        app = _make_app(identity=identity)

        @app.get("/test", dependencies=[Depends(require_role("volundr:admin"))])
        async def endpoint():
            return {"ok": True}

        client = TestClient(app)
        resp = client.get("/test", headers={"Authorization": "Bearer tok"})
        assert resp.status_code == 403
        assert "Requires one of" in resp.json()["detail"]


class TestEnvoyHeaderMode:
    """Tests for extract_principal with EnvoyHeaderIdentityAdapter."""

    def test_envoy_headers_extract_principal(self):
        user_repo = AsyncMock()
        identity = EnvoyHeaderIdentityAdapter(user_repository=user_repo)
        app = _make_app(identity=identity)

        @app.get("/test")
        async def endpoint(principal: Principal = Depends(extract_principal)):
            return {
                "user_id": principal.user_id,
                "email": principal.email,
                "tenant_id": principal.tenant_id,
            }

        client = TestClient(app)
        resp = client.get(
            "/test",
            headers={
                "x-auth-user-id": "envoy-user",
                "x-auth-email": "envoy@test.com",
                "x-auth-tenant": "acme",
                "x-auth-roles": "volundr:admin",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "envoy-user"
        assert data["tenant_id"] == "acme"

    def test_envoy_headers_apply_role_mapping(self):
        user_repo = AsyncMock()
        identity = EnvoyHeaderIdentityAdapter(
            user_repository=user_repo,
            role_mapping={"admin": "volundr:admin"},
        )
        app = _make_app(identity=identity)

        @app.get("/test")
        async def endpoint(principal: Principal = Depends(extract_principal)):
            return {
                "user_id": principal.user_id,
                "roles": principal.roles,
            }

        client = TestClient(app)
        resp = client.get(
            "/test",
            headers={
                "x-auth-user-id": "envoy-user",
                "x-auth-email": "envoy@test.com",
                "x-auth-tenant": "acme",
                "x-auth-roles": "admin",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "envoy-user"
        assert data["roles"] == ["volundr:admin"]

    def test_envoy_headers_blank_tenant_does_not_grant_default_membership(self):
        user_repo = AsyncMock()
        identity = EnvoyHeaderIdentityAdapter(user_repository=user_repo)
        app = _make_app(identity=identity)

        @app.get("/test")
        async def endpoint(principal: Principal = Depends(extract_principal)):
            return {
                "user_id": principal.user_id,
                "tenant_id": principal.tenant_id,
            }

        client = TestClient(app)
        resp = client.get(
            "/test",
            headers={
                "x-auth-user-id": "envoy-user",
                "x-auth-email": "envoy@test.com",
                "x-auth-tenant": "",
                "x-auth-roles": "volundr:admin",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "envoy-user"
        assert data["tenant_id"] == ""

    def test_envoy_missing_user_id_returns_401(self):
        user_repo = AsyncMock()
        identity = EnvoyHeaderIdentityAdapter(user_repository=user_repo)
        app = _make_app(identity=identity)

        @app.get("/test")
        async def endpoint(principal: Principal = Depends(extract_principal)):
            return {}

        client = TestClient(app)
        resp = client.get("/test", headers={"x-auth-email": "a@b.com"})
        assert resp.status_code == 401


class TestCheckAuthorization:
    """Tests for check_authorization helper."""

    async def test_allowed_passes(self):
        authz = AsyncMock()
        authz.is_allowed.return_value = True
        request = MagicMock()
        request.app.state.authorization = authz
        principal = _admin_principal()
        resource = Resource(kind="session", id="s1", attr={})

        # Should not raise
        await check_authorization(request, principal, "read", resource)

    async def test_denied_raises_403(self):
        authz = AsyncMock()
        authz.is_allowed.return_value = False
        request = MagicMock()
        request.app.state.authorization = authz
        principal = _viewer_principal()
        resource = Resource(kind="session", id="s1", attr={})

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await check_authorization(request, principal, "delete", resource)
        assert exc_info.value.status_code == 403
