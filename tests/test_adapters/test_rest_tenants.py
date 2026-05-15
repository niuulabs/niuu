"""Tests for REST tenant endpoints."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.rest_tenants import create_identity_router
from volundr.domain.models import (
    Principal,
    Tenant,
    TenantMembership,
    TenantRole,
    TenantTier,
)
from volundr.domain.services.tenant import (
    TenantAlreadyExistsError,
    TenantNotFoundError,
    TenantService,
)


def _mock_identity(principal=None):
    identity = AsyncMock()
    if principal is None:
        principal = Principal(
            user_id="u1",
            email="admin@test.com",
            tenant_id="t1",
            roles=["volundr:admin"],
        )
    identity.validate_token.return_value = principal
    return identity


def _make_app(tenant_service, identity=None):
    app = FastAPI()
    app.state.identity = identity or _mock_identity()
    app.include_router(create_identity_router(tenant_service))
    return app


def _sample_tenant(**overrides):
    defaults = dict(
        id="t1",
        path="t1",
        name="Test",
        tier=TenantTier.DEVELOPER,
        max_sessions=5,
        max_storage_gb=50,
    )
    defaults.update(overrides)
    return Tenant(**defaults)


AUTH = {"Authorization": "Bearer tok"}


class TestGetMe:
    """Tests for GET /me."""

    def test_returns_principal_info(self):
        svc = AsyncMock(spec=TenantService)
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.get("/api/v1/identity/me", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "u1"
        assert data["userId"] == "u1"
        assert data["email"] == "admin@test.com"
        assert data["tenantId"] == "t1"
        assert data["displayName"] == "admin"
        assert "volundr:admin" in data["roles"]

    def test_identity_settings_returns_profile_schema(self):
        svc = AsyncMock(spec=TenantService)
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.get("/api/v1/identity/settings", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "You"
        assert data["scope"] == "user"
        assert data["sections"][0]["id"] == "profile"
        assert any(field["key"] == "email" for field in data["sections"][0]["fields"])
        assert data["sections"][1]["id"] == "tokens"
        assert data["sections"][1]["resources"][0]["type"] == "tokens"


class TestListTenants:
    """Tests for GET /tenants."""

    def test_returns_tenants(self):
        svc = AsyncMock(spec=TenantService)
        svc.list_tenants.return_value = [
            _sample_tenant(),
            _sample_tenant(id="t2", path="t2", name="T2"),
        ]
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.get("/api/v1/identity/tenants", headers=AUTH)
        assert resp.status_code == 200
        assert len(resp.json()) == 2
        assert resp.json()[0]["maxSessions"] == 5
        assert resp.json()[0]["maxStorageGb"] == 50

    def test_filter_by_parent(self):
        svc = AsyncMock(spec=TenantService)
        svc.list_tenants.return_value = []
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.get("/api/v1/identity/tenants?parent_id=root", headers=AUTH)
        assert resp.status_code == 200
        svc.list_tenants.assert_called_once_with("root")

class _ProvisioningResult:
    def __init__(self, *, success: bool, user_id: str, home_pvc: str | None, errors: list[str]):
        self.success = success
        self.user_id = user_id
        self.home_pvc = home_pvc
        self.errors = errors


class TestUsers:
    def test_list_users_exposes_camel_case_fields(self):
        svc = AsyncMock(spec=TenantService)
        svc.list_users.return_value = [
            SimpleNamespace(
                id="u1",
                email="admin@test.com",
                display_name="Admin",
                status=SimpleNamespace(value="active"),
                home_pvc="home-u1",
                created_at=None,
            )
        ]
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.get("/api/v1/identity/users", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()[0]
        assert data["display_name"] == "Admin"
        assert data["displayName"] == "Admin"
        assert data["homePvc"] == "home-u1"

    def test_user_reprovision_returns_payload(self):
        svc = AsyncMock(spec=TenantService)
        svc.reprovision_user.return_value = _ProvisioningResult(
            success=True,
            user_id="u1",
            home_pvc="home-u1",
            errors=[],
        )
        app = _make_app(svc)
        app.state.storage = None

        client = TestClient(app)
        response = client.post("/api/v1/identity/users/u1/reprovision", headers=AUTH)

        assert response.status_code == 202
        assert response.json()["user_id"] == "u1"
        assert response.json()["userId"] == "u1"
        assert response.json()["homePvc"] == "home-u1"


class TestCreateTenant:
    """Tests for POST /tenants."""

    def test_create_success(self):
        svc = AsyncMock(spec=TenantService)
        svc.create_tenant.return_value = _sample_tenant()
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.post(
            "/api/v1/identity/tenants",
            json={"name": "Test", "tenant_id": "t1"},
            headers=AUTH,
        )
        assert resp.status_code == 201
        assert resp.json()["id"] == "t1"
        assert resp.json()["maxSessions"] == 5

    def test_canonical_identity_create_tenant(self):
        svc = AsyncMock(spec=TenantService)
        svc.create_tenant.return_value = _sample_tenant()
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.post(
            "/api/v1/identity/tenants",
            json={"name": "Test", "tenantId": "t1"},
            headers=AUTH,
        )
        assert resp.status_code == 201
        assert resp.json()["id"] == "t1"

    def test_create_accepts_camel_case_fields(self):
        svc = AsyncMock(spec=TenantService)
        svc.create_tenant.return_value = _sample_tenant(max_sessions=9, max_storage_gb=75)
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.post(
            "/api/v1/identity/tenants",
            json={
                "name": "Test",
                "tenantId": "t1",
                "parentId": "root",
                "maxSessions": 9,
                "maxStorageGb": 75,
            },
            headers=AUTH,
        )
        assert resp.status_code == 201
        svc.create_tenant.assert_awaited_once_with(
            name="Test",
            parent_id="root",
            tenant_id="t1",
            tier=TenantTier.DEVELOPER,
            max_sessions=9,
            max_storage_gb=75,
        )

    def test_create_duplicate_returns_409(self):
        svc = AsyncMock(spec=TenantService)
        svc.create_tenant.side_effect = TenantAlreadyExistsError("exists")
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.post(
            "/api/v1/identity/tenants",
            json={"name": "Dup"},
            headers=AUTH,
        )
        assert resp.status_code == 409

    def test_create_parent_not_found_returns_404(self):
        svc = AsyncMock(spec=TenantService)
        svc.create_tenant.side_effect = TenantNotFoundError("not found")
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.post(
            "/api/v1/identity/tenants",
            json={"name": "Child", "parent_id": "bad"},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_create_requires_admin(self):
        svc = AsyncMock(spec=TenantService)
        viewer = Principal(
            user_id="u2",
            email="v@test.com",
            tenant_id="t1",
            roles=["volundr:viewer"],
        )
        app = _make_app(svc, identity=_mock_identity(viewer))
        client = TestClient(app)

        resp = client.post(
            "/api/v1/identity/tenants",
            json={"name": "Test"},
            headers=AUTH,
        )
        assert resp.status_code == 403


class TestGetTenant:
    """Tests for GET /tenants/{id}."""

    def test_get_existing(self):
        svc = AsyncMock(spec=TenantService)
        svc.get_tenant.return_value = _sample_tenant()
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.get("/api/v1/identity/tenants/t1", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test"
        assert resp.json()["maxSessions"] == 5

    def test_get_not_found(self):
        svc = AsyncMock(spec=TenantService)
        svc.get_tenant.side_effect = TenantNotFoundError("not found")
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.get("/api/v1/identity/tenants/bad", headers=AUTH)
        assert resp.status_code == 404


class TestDeleteTenant:
    """Tests for DELETE /tenants/{id}."""

    def test_delete_success(self):
        svc = AsyncMock(spec=TenantService)
        svc.delete_tenant.return_value = True
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.delete("/api/v1/identity/tenants/t1", headers=AUTH)
        assert resp.status_code == 204

    def test_delete_not_found(self):
        svc = AsyncMock(spec=TenantService)
        svc.delete_tenant.return_value = False
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.delete("/api/v1/identity/tenants/t1", headers=AUTH)
        assert resp.status_code == 404

    def test_canonical_identity_delete_tenant(self):
        svc = AsyncMock(spec=TenantService)
        svc.delete_tenant.return_value = True
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.delete("/api/v1/identity/tenants/t1", headers=AUTH)
        assert resp.status_code == 204


class TestUpdateTenant:
    def test_patch_update_tenant(self):
        svc = AsyncMock(spec=TenantService)
        svc.update_tenant_settings.return_value = _sample_tenant(max_sessions=9)
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.patch(
            "/api/v1/identity/tenants/t1",
            json={"max_sessions": 9},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.json()["max_sessions"] == 9
        assert resp.json()["maxSessions"] == 9

    def test_patch_update_tenant_accepts_camel_case(self):
        svc = AsyncMock(spec=TenantService)
        svc.update_tenant_settings.return_value = _sample_tenant(max_sessions=9)
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.patch(
            "/api/v1/identity/tenants/t1",
            json={"maxSessions": 9},
            headers=AUTH,
        )
        assert resp.status_code == 200
        svc.update_tenant_settings.assert_awaited_once_with(
            "t1",
            max_sessions=9,
            max_storage_gb=None,
            tier=None,
        )

class TestMembers:
    """Tests for member management endpoints."""

    def test_list_members(self):
        svc = AsyncMock(spec=TenantService)
        svc.get_members.return_value = [
            TenantMembership(user_id="u1", tenant_id="t1", role=TenantRole.ADMIN),
        ]
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.get("/api/v1/identity/tenants/t1/members", headers=AUTH)
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["user_id"] == "u1"
        assert resp.json()[0]["userId"] == "u1"
        assert resp.json()[0]["tenantId"] == "t1"

    def test_add_member_success(self):
        svc = AsyncMock(spec=TenantService)
        svc.add_member.return_value = TenantMembership(
            user_id="u2",
            tenant_id="t1",
            role=TenantRole.DEVELOPER,
        )
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.post(
            "/api/v1/identity/tenants/t1/members",
            json={"user_id": "u2"},
            headers=AUTH,
        )
        assert resp.status_code == 201
        assert resp.json()["user_id"] == "u2"
        assert resp.json()["userId"] == "u2"

    def test_add_member_accepts_camel_case_user_id(self):
        svc = AsyncMock(spec=TenantService)
        svc.add_member.return_value = TenantMembership(
            user_id="u2",
            tenant_id="t1",
            role=TenantRole.DEVELOPER,
        )
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.post(
            "/api/v1/identity/tenants/t1/members",
            json={"userId": "u2"},
            headers=AUTH,
        )
        assert resp.status_code == 201
        svc.add_member.assert_awaited_once_with(
            tenant_id="t1",
            user_id="u2",
            role=TenantRole.DEVELOPER,
        )

    def test_add_member_tenant_not_found(self):
        svc = AsyncMock(spec=TenantService)
        svc.add_member.side_effect = TenantNotFoundError("not found")
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.post(
            "/api/v1/identity/tenants/bad/members",
            json={"user_id": "u1"},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_add_member_user_not_found(self):
        svc = AsyncMock(spec=TenantService)
        svc.add_member.side_effect = ValueError("User not found")
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.post(
            "/api/v1/identity/tenants/t1/members",
            json={"user_id": "bad"},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_remove_member_success(self):
        svc = AsyncMock(spec=TenantService)
        svc.remove_member.return_value = True
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.delete("/api/v1/identity/tenants/t1/members/u1", headers=AUTH)
        assert resp.status_code == 204

    def test_remove_member_not_found(self):
        svc = AsyncMock(spec=TenantService)
        svc.remove_member.return_value = False
        app = _make_app(svc)
        client = TestClient(app)

        resp = client.delete("/api/v1/identity/tenants/t1/members/u1", headers=AUTH)
        assert resp.status_code == 404
