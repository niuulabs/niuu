"""Tests for REST credential endpoints (CredentialService-based)."""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.helpers.http_contracts import RouteCallSpec, assert_route_equivalence
from volundr.adapters.inbound.rest_credentials import (
    create_canonical_credentials_router,
    create_credentials_router,
    create_legacy_secret_store_router,
)
from volundr.adapters.outbound.memory_credential_store import MemoryCredentialStore
from volundr.domain.models import Principal
from volundr.domain.services.credential import CredentialService
from volundr.domain.services.mount_strategies import SecretMountStrategyRegistry


def _mock_identity(principal: Principal | None = None):
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


def _make_app(identity=None) -> tuple[FastAPI, CredentialService]:
    store = MemoryCredentialStore()
    strategies = SecretMountStrategyRegistry()
    service = CredentialService(store, strategies)
    app = FastAPI()
    app.state.identity = identity or _mock_identity()
    app.include_router(create_canonical_credentials_router(service))
    app.include_router(create_legacy_secret_store_router(service))
    router = create_credentials_router(service)
    app.include_router(router)
    return app, service


AUTH = {"Authorization": "Bearer tok"}
PREFIX = "/api/v1/volundr/credentials"


class TestListCredentialTypes:
    """Tests for GET /types."""

    def test_returns_all_types(self):
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.get(f"{PREFIX}/types")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 6
        type_values = {t["type"] for t in data}
        assert "api_key" in type_values
        assert "generic" in type_values

    def test_canonical_types_match_legacy(self):
        app, _ = _make_app()
        client = TestClient(app)
        assert_route_equivalence(
            client,
            legacy=RouteCallSpec(path=f"{PREFIX}/types"),
            canonical=RouteCallSpec(path="/api/v1/credentials/types"),
        )

    def test_legacy_secret_types_route_returns_camel_case_shape(self):
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.get("/api/v1/volundr/secrets/types")
        assert resp.status_code == 200
        assert resp.headers["Deprecation"] == "true"
        data = resp.json()
        assert "defaultMountType" in data[0]
        assert "default_mount_type" not in data[0]

    def test_deprecated_credentials_secret_types_route_returns_camel_case_shape(self):
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.get(f"{PREFIX}/secrets/types")
        assert resp.status_code == 200
        assert resp.headers["Deprecation"] == "true"
        data = resp.json()
        assert "defaultMountType" in data[0]
        assert "default_mount_type" not in data[0]


class TestListUserCredentials:
    """Tests for GET /credentials."""

    def test_empty_list(self):
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.get(PREFIX, headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == []
        assert resp.headers["Deprecation"] == "true"
        assert resp.headers["X-Niuu-Canonical-Route"] == "/api/v1/credentials/user"

    def test_canonical_user_list_keeps_wrapped_shape(self):
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.get("/api/v1/credentials/user", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == {"credentials": []}

    def test_returns_created_credentials(self):
        app, _ = _make_app()
        client = TestClient(app)

        client.post(
            PREFIX,
            json={"name": "my-key", "secret_type": "api_key", "data": {"api_key": "secret"}},
            headers=AUTH,
        )

        resp = client.get(PREFIX, headers=AUTH)
        assert resp.status_code == 200
        creds = resp.json()
        assert len(creds) == 1
        assert creds[0]["name"] == "my-key"
        assert creds[0]["keys"] == ["api_key"]

    def test_filter_by_type(self):
        app, _ = _make_app()
        client = TestClient(app)

        client.post(
            PREFIX,
            json={"name": "a", "secret_type": "api_key", "data": {"api_key": "v"}},
            headers=AUTH,
        )
        client.post(
            PREFIX,
            json={"name": "b", "secret_type": "generic", "data": {"k": "v"}},
            headers=AUTH,
        )

        resp = client.get(f"{PREFIX}?secret_type=api_key", headers=AUTH)
        creds = resp.json()
        assert len(creds) == 1
        assert creds[0]["name"] == "a"


class TestCreateUserCredential:
    """Tests for POST /credentials."""

    def test_create_credential(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.post(
            PREFIX,
            json={"name": "my-key", "secret_type": "generic", "data": {"token": "abc"}},
            headers=AUTH,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "my-key"
        assert body["secret_type"] == "generic"
        assert "token" in body["keys"]
        assert "id" in body
        assert "created_at" in body

    def test_invalid_name_rejected(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.post(
            PREFIX,
            json={"name": "INVALID NAME!", "data": {"k": "v"}},
            headers=AUTH,
        )
        assert resp.status_code == 422

    def test_validation_error_for_empty_api_key(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.post(
            PREFIX,
            json={"name": "key", "secret_type": "api_key", "data": {}},
            headers=AUTH,
        )
        assert resp.status_code == 422

    def test_invalid_secret_type(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.post(
            PREFIX,
            json={"name": "key", "secret_type": "nonsense", "data": {"k": "v"}},
            headers=AUTH,
        )
        assert resp.status_code == 400


class TestGetUserCredential:
    """Tests for GET /credentials/{name}."""

    def test_get_existing(self):
        app, _ = _make_app()
        client = TestClient(app)

        client.post(
            PREFIX,
            json={"name": "my-key", "secret_type": "generic", "data": {"k": "v"}},
            headers=AUTH,
        )

        resp = client.get(f"{PREFIX}/my-key", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["name"] == "my-key"

    def test_get_missing_returns_404(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/nonexistent", headers=AUTH)
        assert resp.status_code == 404


class TestDeleteUserCredential:
    """Tests for DELETE /credentials/{name}."""

    def test_delete_existing(self):
        app, _ = _make_app()
        client = TestClient(app)

        client.post(
            PREFIX,
            json={"name": "my-key", "secret_type": "generic", "data": {"k": "v"}},
            headers=AUTH,
        )

        resp = client.delete(f"{PREFIX}/my-key", headers=AUTH)
        assert resp.status_code == 204

        resp = client.get(f"{PREFIX}/my-key", headers=AUTH)
        assert resp.status_code == 404

    def test_delete_missing_returns_404(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.delete(f"{PREFIX}/gone", headers=AUTH)
        assert resp.status_code == 404


class TestLegacyStoreCredentialRoutes:
    def test_nested_store_route_returns_camel_case_shape(self):
        app, _ = _make_app()
        client = TestClient(app)

        client.post(
            f"{PREFIX}/user",
            json={"name": "my-key", "secret_type": "api_key", "data": {"api_key": "secret"}},
            headers=AUTH,
        )

        resp = client.get(f"{PREFIX}/secrets/store", headers=AUTH)
        assert resp.status_code == 200
        assert resp.headers["Deprecation"] == "true"
        item = resp.json()[0]
        assert item["secretType"] == "api_key"
        assert "createdAt" in item

    def test_list_store_returns_camel_case_shape(self):
        app, _ = _make_app()
        client = TestClient(app)

        client.post(
            PREFIX,
            json={"name": "my-key", "secret_type": "api_key", "data": {"api_key": "secret"}},
            headers=AUTH,
        )

        resp = client.get("/api/v1/volundr/secrets/store", headers=AUTH)
        assert resp.status_code == 200
        assert resp.headers["Deprecation"] == "true"
        item = resp.json()[0]
        assert item["name"] == "my-key"
        assert item["secretType"] == "api_key"
        assert "createdAt" in item
        assert "updatedAt" in item
        assert "secret_type" not in item

    def test_list_store_filters_by_type_query(self):
        app, _ = _make_app()
        client = TestClient(app)

        client.post(
            PREFIX,
            json={"name": "a", "secret_type": "api_key", "data": {"api_key": "v"}},
            headers=AUTH,
        )
        client.post(
            PREFIX,
            json={"name": "b", "secret_type": "generic", "data": {"k": "v"}},
            headers=AUTH,
        )

        resp = client.get("/api/v1/volundr/secrets/store?type=api_key", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "a"

    def test_get_store_item_returns_camel_case_shape(self):
        app, _ = _make_app()
        client = TestClient(app)

        client.post(
            PREFIX,
            json={"name": "my-key", "secret_type": "generic", "data": {"k": "v"}},
            headers=AUTH,
        )

        resp = client.get("/api/v1/volundr/secrets/store/my-key", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "my-key"
        assert data["secretType"] == "generic"

    def test_nested_get_store_missing_returns_404(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/secrets/store/missing", headers=AUTH)
        assert resp.status_code == 404

    def test_create_store_accepts_secret_type_alias(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/api/v1/volundr/secrets/store",
            json={"name": "my-key", "secretType": "generic", "data": {"token": "abc"}},
            headers=AUTH,
        )
        assert resp.status_code == 201
        assert resp.json()["secretType"] == "generic"

    def test_nested_create_store_invalid_secret_type(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.post(
            f"{PREFIX}/secrets/store",
            json={"name": "my-key", "secretType": "nonsense", "data": {"token": "abc"}},
            headers=AUTH,
        )
        assert resp.status_code == 400

    def test_root_store_invalid_secret_type(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/api/v1/volundr/secrets/store",
            json={"name": "my-key", "secretType": "nonsense", "data": {"token": "abc"}},
            headers=AUTH,
        )
        assert resp.status_code == 400

    def test_root_store_validation_error(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/api/v1/volundr/secrets/store",
            json={"name": "my-key", "secretType": "api_key", "data": {}},
            headers=AUTH,
        )
        assert resp.status_code == 422

    def test_delete_store_removes_credential(self):
        app, _ = _make_app()
        client = TestClient(app)

        client.post(
            "/api/v1/volundr/secrets/store",
            json={"name": "my-key", "secretType": "generic", "data": {"token": "abc"}},
            headers=AUTH,
        )

        resp = client.delete("/api/v1/volundr/secrets/store/my-key", headers=AUTH)
        assert resp.status_code == 204

        missing = client.get("/api/v1/volundr/secrets/store/my-key", headers=AUTH)
        assert missing.status_code == 404

    def test_nested_delete_store_missing_returns_404(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.delete(f"{PREFIX}/secrets/store/gone", headers=AUTH)
        assert resp.status_code == 404


class TestTenantCredentialEndpoints:
    """Tests for tenant credential endpoints (admin only)."""

    def test_list_tenant_credentials(self):
        app, _service = _make_app()
        client = TestClient(app)

        client.post(
            f"{PREFIX}/tenant",
            json={"name": "db-cred", "secret_type": "generic", "data": {"host": "db.local"}},
            headers=AUTH,
        )

        resp = client.get(f"{PREFIX}/tenant/list", headers=AUTH)
        assert resp.status_code == 200
        creds = resp.json()
        assert len(creds) == 1
        assert creds[0]["name"] == "db-cred"

    def test_canonical_tenant_list_keeps_wrapped_shape(self):
        app, _service = _make_app()
        client = TestClient(app)

        client.post(
            f"{PREFIX}/tenant",
            json={"name": "db-cred", "secret_type": "generic", "data": {"host": "db.local"}},
            headers=AUTH,
        )

        resp = client.get("/api/v1/credentials/tenant", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["credentials"][0]["name"] == "db-cred"

    def test_create_tenant_credential(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.post(
            f"{PREFIX}/tenant",
            json={"name": "db-cred", "secret_type": "generic", "data": {"host": "db.local"}},
            headers=AUTH,
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "db-cred"

    def test_create_tenant_credential_invalid_secret_type(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.post(
            f"{PREFIX}/tenant",
            json={"name": "db-cred", "secret_type": "nonsense", "data": {"host": "db.local"}},
            headers=AUTH,
        )
        assert resp.status_code == 400

    def test_create_tenant_credential_validation_error(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.post(
            f"{PREFIX}/tenant",
            json={"name": "db-cred", "secret_type": "api_key", "data": {}},
            headers=AUTH,
        )
        assert resp.status_code == 422

    def test_delete_tenant_credential(self):
        app, _ = _make_app()
        client = TestClient(app)

        client.post(
            f"{PREFIX}/tenant",
            json={"name": "db-cred", "secret_type": "generic", "data": {"k": "v"}},
            headers=AUTH,
        )

        resp = client.delete(f"{PREFIX}/tenant/db-cred", headers=AUTH)
        assert resp.status_code == 204

    def test_delete_missing_tenant_credential_returns_404(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.delete(f"{PREFIX}/tenant/gone", headers=AUTH)
        assert resp.status_code == 404


class TestTenantEndpointsRequireAdmin:
    """Non-admin users should be blocked from tenant endpoints."""

    def _viewer_app(self):
        viewer = Principal(
            user_id="u2",
            email="viewer@test.com",
            tenant_id="t1",
            roles=["volundr:viewer"],
        )
        app, _ = _make_app(identity=_mock_identity(viewer))
        return TestClient(app)

    def test_list_forbidden(self):
        client = self._viewer_app()
        resp = client.get(f"{PREFIX}/tenant/list", headers=AUTH)
        assert resp.status_code == 403

    def test_create_forbidden(self):
        client = self._viewer_app()
        resp = client.post(
            f"{PREFIX}/tenant",
            json={"name": "x", "data": {"k": "v"}},
            headers=AUTH,
        )
        assert resp.status_code == 403

    def test_delete_forbidden(self):
        client = self._viewer_app()
        resp = client.delete(f"{PREFIX}/tenant/x", headers=AUTH)
        assert resp.status_code == 403
