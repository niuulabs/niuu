"""Tests for REST credential endpoints (CredentialService-based)."""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.rest_credentials import create_credentials_router
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
    app.include_router(create_credentials_router(service))
    return app, service


AUTH = {"Authorization": "Bearer tok"}
USER_PREFIX = "/api/v1/credentials/user"


class TestListCredentialTypes:
    def test_returns_all_types(self):
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.get("/api/v1/credentials/types")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 6
        type_values = {t["type"] for t in data}
        assert "api_key" in type_values
        assert "generic" in type_values

    def test_secret_types_route_returns_camel_case_shape(self):
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.get("/api/v1/credentials/secrets/types")
        assert resp.status_code == 200
        data = resp.json()
        assert "defaultMountType" in data[0]
        assert "default_mount_type" not in data[0]


class TestListUserCredentials:
    def test_empty_list(self):
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.get(USER_PREFIX, headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == {"credentials": []}

    def test_returns_created_credentials(self):
        app, _ = _make_app()
        client = TestClient(app)

        client.post(
            USER_PREFIX,
            json={"name": "my-key", "secret_type": "api_key", "data": {"api_key": "secret"}},
            headers=AUTH,
        )

        resp = client.get(USER_PREFIX, headers=AUTH)
        assert resp.status_code == 200
        creds = resp.json()["credentials"]
        assert len(creds) == 1
        assert creds[0]["name"] == "my-key"
        assert creds[0]["keys"] == ["api_key"]

    def test_filter_by_type(self):
        app, _ = _make_app()
        client = TestClient(app)

        client.post(
            USER_PREFIX,
            json={"name": "a", "secret_type": "api_key", "data": {"api_key": "v"}},
            headers=AUTH,
        )
        client.post(
            USER_PREFIX,
            json={"name": "b", "secret_type": "generic", "data": {"k": "v"}},
            headers=AUTH,
        )

        resp = client.get(f"{USER_PREFIX}?secret_type=api_key", headers=AUTH)
        creds = resp.json()["credentials"]
        assert len(creds) == 1
        assert creds[0]["name"] == "a"


class TestCreateUserCredential:
    def test_create_credential(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.post(
            USER_PREFIX,
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
            USER_PREFIX,
            json={"name": "INVALID NAME!", "data": {"k": "v"}},
            headers=AUTH,
        )
        assert resp.status_code == 422

    def test_validation_error_for_empty_api_key(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.post(
            USER_PREFIX,
            json={"name": "key", "secret_type": "api_key", "data": {}},
            headers=AUTH,
        )
        assert resp.status_code == 422

    def test_invalid_secret_type(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.post(
            USER_PREFIX,
            json={"name": "key", "secret_type": "nonsense", "data": {"k": "v"}},
            headers=AUTH,
        )
        assert resp.status_code == 400


class TestGetUserCredential:
    def test_get_existing(self):
        app, _ = _make_app()
        client = TestClient(app)

        client.post(
            USER_PREFIX,
            json={"name": "my-key", "secret_type": "generic", "data": {"k": "v"}},
            headers=AUTH,
        )

        resp = client.get(f"{USER_PREFIX}/my-key", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["name"] == "my-key"

    def test_get_missing_returns_404(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.get(f"{USER_PREFIX}/nonexistent", headers=AUTH)
        assert resp.status_code == 404


class TestDeleteUserCredential:
    def test_delete_existing(self):
        app, _ = _make_app()
        client = TestClient(app)

        client.post(
            USER_PREFIX,
            json={"name": "my-key", "secret_type": "generic", "data": {"k": "v"}},
            headers=AUTH,
        )

        resp = client.delete(f"{USER_PREFIX}/my-key", headers=AUTH)
        assert resp.status_code == 204

        resp = client.get(f"{USER_PREFIX}/my-key", headers=AUTH)
        assert resp.status_code == 404

    def test_delete_missing_returns_404(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.delete(f"{USER_PREFIX}/gone", headers=AUTH)
        assert resp.status_code == 404


class TestLegacyStoreCredentialRoutes:
    def test_list_store_returns_camel_case_shape(self):
        app, _ = _make_app()
        client = TestClient(app)

        client.post(
            USER_PREFIX,
            json={"name": "my-key", "secret_type": "api_key", "data": {"api_key": "secret"}},
            headers=AUTH,
        )

        resp = client.get("/api/v1/credentials/secrets/store", headers=AUTH)
        assert resp.status_code == 200
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
            USER_PREFIX,
            json={"name": "a", "secret_type": "api_key", "data": {"api_key": "v"}},
            headers=AUTH,
        )
        client.post(
            USER_PREFIX,
            json={"name": "b", "secret_type": "generic", "data": {"k": "v"}},
            headers=AUTH,
        )

        resp = client.get("/api/v1/credentials/secrets/store?type=api_key", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "a"

    def test_get_store_item_returns_camel_case_shape(self):
        app, _ = _make_app()
        client = TestClient(app)

        client.post(
            USER_PREFIX,
            json={"name": "my-key", "secret_type": "generic", "data": {"k": "v"}},
            headers=AUTH,
        )

        resp = client.get("/api/v1/credentials/secrets/store/my-key", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "my-key"
        assert data["secretType"] == "generic"

    def test_get_store_missing_returns_404(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.get("/api/v1/credentials/secrets/store/missing", headers=AUTH)
        assert resp.status_code == 404

    def test_create_store_accepts_secret_type_alias(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/api/v1/credentials/secrets/store",
            json={"name": "my-key", "secretType": "generic", "data": {"token": "abc"}},
            headers=AUTH,
        )
        assert resp.status_code == 201
        assert resp.json()["secretType"] == "generic"

    def test_store_invalid_secret_type(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/api/v1/credentials/secrets/store",
            json={"name": "my-key", "secretType": "nonsense", "data": {"token": "abc"}},
            headers=AUTH,
        )
        assert resp.status_code == 400

    def test_store_validation_error(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/api/v1/credentials/secrets/store",
            json={"name": "my-key", "secretType": "api_key", "data": {}},
            headers=AUTH,
        )
        assert resp.status_code == 422

    def test_delete_store_removes_credential(self):
        app, _ = _make_app()
        client = TestClient(app)

        client.post(
            "/api/v1/credentials/secrets/store",
            json={"name": "my-key", "secretType": "generic", "data": {"token": "abc"}},
            headers=AUTH,
        )

        resp = client.delete("/api/v1/credentials/secrets/store/my-key", headers=AUTH)
        assert resp.status_code == 204

        missing = client.get("/api/v1/credentials/secrets/store/my-key", headers=AUTH)
        assert missing.status_code == 404

    def test_delete_store_missing_returns_404(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.delete("/api/v1/credentials/secrets/store/gone", headers=AUTH)
        assert resp.status_code == 404


class TestTenantCredentialEndpoints:
    def test_list_tenant_credentials(self):
        app, _service = _make_app()
        client = TestClient(app)

        client.post(
            "/api/v1/credentials/tenant",
            json={"name": "db-cred", "secret_type": "generic", "data": {"host": "db.local"}},
            headers=AUTH,
        )

        resp = client.get("/api/v1/credentials/tenant/list", headers=AUTH)
        assert resp.status_code == 200
        creds = resp.json()["credentials"]
        assert len(creds) == 1
        assert creds[0]["name"] == "db-cred"

    def test_create_tenant_credential(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/api/v1/credentials/tenant",
            json={"name": "db-cred", "secret_type": "generic", "data": {"host": "db.local"}},
            headers=AUTH,
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "db-cred"

    def test_create_tenant_credential_invalid_secret_type(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/api/v1/credentials/tenant",
            json={"name": "db-cred", "secret_type": "nonsense", "data": {"host": "db.local"}},
            headers=AUTH,
        )
        assert resp.status_code == 400

    def test_create_tenant_credential_validation_error(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/api/v1/credentials/tenant",
            json={"name": "db-cred", "secret_type": "api_key", "data": {}},
            headers=AUTH,
        )
        assert resp.status_code == 422

    def test_delete_tenant_credential(self):
        app, _ = _make_app()
        client = TestClient(app)

        client.post(
            "/api/v1/credentials/tenant",
            json={"name": "db-cred", "secret_type": "generic", "data": {"k": "v"}},
            headers=AUTH,
        )

        resp = client.delete("/api/v1/credentials/tenant/db-cred", headers=AUTH)
        assert resp.status_code == 204

    def test_delete_missing_tenant_credential_returns_404(self):
        app, _ = _make_app()
        client = TestClient(app)

        resp = client.delete("/api/v1/credentials/tenant/gone", headers=AUTH)
        assert resp.status_code == 404


class TestTenantEndpointsRequireAdmin:
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
        resp = client.get("/api/v1/credentials/tenant/list", headers=AUTH)
        assert resp.status_code == 403

    def test_create_forbidden(self):
        client = self._viewer_app()
        resp = client.post(
            "/api/v1/credentials/tenant",
            json={"name": "x", "data": {"k": "v"}},
            headers=AUTH,
        )
        assert resp.status_code == 403

    def test_delete_forbidden(self):
        client = self._viewer_app()
        resp = client.delete("/api/v1/credentials/tenant/x", headers=AUTH)
        assert resp.status_code == 403
