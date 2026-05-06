"""Tests for admin settings REST endpoint."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.inbound.rest_admin_settings import create_admin_settings_router
from volundr.domain.models import Principal


def _mock_admin_principal() -> Principal:
    return Principal(
        user_id="admin-1",
        email="admin@test.com",
        tenant_id="t1",
        roles=["volundr:admin"],
    )


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.state.admin_settings = {"storage": {"home_enabled": True}}
    router = create_admin_settings_router()
    app.include_router(router)
    app.dependency_overrides[extract_principal] = _mock_admin_principal
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


class TestAdminSettings:
    def test_get_mounted_settings_schema(self, client: TestClient) -> None:
        response = client.get("/api/v1/volundr/settings")
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Volundr"
        assert data["scope"] == "admin"
        assert data["sections"][0]["path"] == "/admin/settings"
        assert data["sections"][0]["saveLabel"] == "Save storage settings"

    def test_get_settings(self, client: TestClient) -> None:
        response = client.get("/api/v1/volundr/admin/settings")
        assert response.status_code == 200
        data = response.json()
        assert data["storage"]["home_enabled"] is True
        assert data["storage"]["homeEnabled"] is True

    def test_patch_update_settings_disable_home(self, client: TestClient) -> None:
        response = client.patch(
            "/api/v1/volundr/admin/settings",
            json={"storage": {"homeEnabled": False}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["storage"]["home_enabled"] is False
        assert data["storage"]["homeEnabled"] is False
        assert "Deprecation" not in response.headers

    def test_update_settings_disable_home(self, client: TestClient) -> None:
        response = client.put(
            "/api/v1/volundr/admin/settings",
            json={"storage": {"home_enabled": False}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["storage"]["home_enabled"] is False
        assert response.headers["Deprecation"] == "true"

        # Verify it persists in subsequent GET
        response = client.get("/api/v1/volundr/admin/settings")
        assert response.json()["storage"]["home_enabled"] is False

    def test_update_settings_enable_home(self, app: FastAPI) -> None:
        app.state.admin_settings = {"storage": {"home_enabled": False}}
        client = TestClient(app)
        response = client.put(
            "/api/v1/volundr/admin/settings",
            json={"storage": {"homeEnabled": True}},
        )
        assert response.status_code == 200
        assert response.json()["storage"]["home_enabled"] is True

    def test_patch_update_settings_accepts_file_manager_camel_case(
        self,
        client: TestClient,
    ) -> None:
        response = client.patch(
            "/api/v1/volundr/admin/settings",
            json={"storage": {"homeEnabled": True, "fileManagerEnabled": False}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["storage"]["file_manager_enabled"] is False
        assert data["storage"]["fileManagerEnabled"] is False

    def test_update_without_storage_is_noop(self, client: TestClient) -> None:
        response = client.put(
            "/api/v1/volundr/admin/settings",
            json={},
        )
        assert response.status_code == 200
        assert response.json()["storage"]["home_enabled"] is True
