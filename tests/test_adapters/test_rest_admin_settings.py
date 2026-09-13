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
        response = client.get("/api/v1/forge/admin/settings/schema")
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Forge"
        assert data["scope"] == "admin"
        assert data["sections"][0]["path"] == "/admin/settings/storage"
        assert data["sections"][0]["saveLabel"] == "Save storage settings"

    def test_get_canonical_settings_schema_alias(self, client: TestClient) -> None:
        response = client.get("/api/v1/forge/settings")
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Forge"
        assert data["sections"][0]["id"] == "storage"

    def test_get_settings(self, client: TestClient) -> None:
        response = client.get("/api/v1/forge/admin/settings")
        assert response.status_code == 200
        data = response.json()
        assert data["storage"]["home_enabled"] is True
        assert data["storage"]["homeEnabled"] is True

    def test_patch_update_settings_disable_home(self, client: TestClient) -> None:
        response = client.patch(
            "/api/v1/forge/admin/settings",
            json={"storage": {"homeEnabled": False}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["storage"]["home_enabled"] is False
        assert data["storage"]["homeEnabled"] is False

    def test_update_settings_disable_home(self, client: TestClient) -> None:
        response = client.put(
            "/api/v1/forge/admin/settings",
            json={"storage": {"home_enabled": False}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["storage"]["home_enabled"] is False

        # Verify it persists in subsequent GET
        response = client.get("/api/v1/forge/admin/settings")
        assert response.json()["storage"]["home_enabled"] is False

    def test_update_settings_enable_home(self, app: FastAPI) -> None:
        app.state.admin_settings = {"storage": {"home_enabled": False}}
        client = TestClient(app)
        response = client.put(
            "/api/v1/forge/admin/settings",
            json={"storage": {"homeEnabled": True}},
        )
        assert response.status_code == 200
        assert response.json()["storage"]["home_enabled"] is True

    def test_patch_update_settings_accepts_file_manager_camel_case(
        self,
        client: TestClient,
    ) -> None:
        response = client.patch(
            "/api/v1/forge/admin/settings",
            json={"storage": {"homeEnabled": True, "fileManagerEnabled": False}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["storage"]["file_manager_enabled"] is False
        assert data["storage"]["fileManagerEnabled"] is False

    def test_update_without_storage_is_rejected(self, client: TestClient) -> None:
        response = client.put(
            "/api/v1/forge/admin/settings",
            json={},
        )
        assert response.status_code == 422

    def test_flat_keys_at_the_nested_endpoint_are_rejected(self, client: TestClient) -> None:
        """The settings shell used to post this shape here and got a 200 that changed nothing."""
        response = client.patch(
            "/api/v1/forge/admin/settings",
            json={"homeEnabled": False, "fileManagerEnabled": False},
        )
        assert response.status_code == 422
        assert client.get("/api/v1/forge/admin/settings").json()["storage"]["homeEnabled"] is True


class TestStorageSection:
    """The Storage section of the settings page saves through its own flat endpoint."""

    def test_saves_the_section_form(self, client: TestClient) -> None:
        response = client.patch(
            "/api/v1/forge/admin/settings/storage",
            json={"homeEnabled": False, "fileManagerEnabled": False},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["homeEnabled"] is False
        assert data["fileManagerEnabled"] is False
        stored = client.get("/api/v1/forge/admin/settings").json()["storage"]
        assert stored == {
            "home_enabled": False,
            "file_manager_enabled": False,
            "homeEnabled": False,
            "fileManagerEnabled": False,
        }

    def test_partial_update_keeps_the_other_field(self, client: TestClient) -> None:
        response = client.patch(
            "/api/v1/forge/admin/settings/storage",
            json={"file_manager_enabled": False},
        )
        assert response.status_code == 200
        assert response.json() == {
            "home_enabled": True,
            "file_manager_enabled": False,
            "homeEnabled": True,
            "fileManagerEnabled": False,
        }

    def test_empty_and_unknown_bodies_are_rejected(self, client: TestClient) -> None:
        assert client.patch("/api/v1/forge/admin/settings/storage", json={}).status_code == 422
        unknown = client.patch("/api/v1/forge/admin/settings/storage", json={"other": True})
        assert unknown.status_code == 422

    def test_needs_the_admin_role(self, app: FastAPI, client: TestClient) -> None:
        app.dependency_overrides[extract_principal] = lambda: Principal(
            user_id="u-1", email="u@test.com", tenant_id="t1", roles=[]
        )
        response = client.patch(
            "/api/v1/forge/admin/settings/storage", json={"fileManagerEnabled": False}
        )
        assert response.status_code == 403
