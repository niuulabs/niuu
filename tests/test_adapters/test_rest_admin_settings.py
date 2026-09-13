"""Tests for admin settings REST endpoint."""

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.inbound.rest_admin_settings import create_admin_settings_router
from volundr.domain.models import Principal
from volundr.domain.ports import AdminSettingsRepository


class _FakeRepository(AdminSettingsRepository):
    """Remembers every save so tests can prove the write-through."""

    def __init__(self) -> None:
        self.stored: dict[str, dict[str, Any]] = {}
        self.saves: list[tuple[str, dict[str, Any]]] = []

    async def load(self) -> dict[str, dict[str, Any]]:
        return dict(self.stored)

    async def save(self, section: str, values: dict[str, Any]) -> None:
        self.stored[section] = dict(values)
        self.saves.append((section, dict(values)))


def _mock_admin_principal() -> Principal:
    return Principal(
        user_id="admin-1",
        email="admin@test.com",
        tenant_id="t1",
        roles=["volundr:admin"],
    )


def _build_app(repository: _FakeRepository, *, home_volumes_supported: bool = True) -> FastAPI:
    app = FastAPI()
    app.state.admin_settings = {"storage": {"home_enabled": True}}
    app.include_router(
        create_admin_settings_router(repository, home_volumes_supported=home_volumes_supported)
    )
    app.dependency_overrides[extract_principal] = _mock_admin_principal
    return app


@pytest.fixture
def repository() -> _FakeRepository:
    return _FakeRepository()


@pytest.fixture
def app(repository: _FakeRepository) -> FastAPI:
    return _build_app(repository)


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
        assert [f["key"] for f in data["sections"][0]["fields"]] == [
            "homeEnabled",
            "fileManagerEnabled",
        ]

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

    def test_patch_update_settings_disable_home(
        self, client: TestClient, repository: _FakeRepository
    ) -> None:
        response = client.patch(
            "/api/v1/forge/admin/settings",
            json={"storage": {"homeEnabled": False}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["storage"]["home_enabled"] is False
        assert data["storage"]["homeEnabled"] is False
        assert repository.stored["storage"]["home_enabled"] is False

    def test_put_update_settings_snake_case(self, client: TestClient) -> None:
        response = client.put(
            "/api/v1/forge/admin/settings",
            json={"storage": {"home_enabled": False}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["storage"]["home_enabled"] is False

    def test_update_persists_across_requests(self, client: TestClient) -> None:
        client.patch("/api/v1/forge/admin/settings", json={"storage": {"homeEnabled": False}})
        response = client.get("/api/v1/forge/admin/settings")
        assert response.json()["storage"]["home_enabled"] is False

    def test_update_re_enable(self, app: FastAPI, client: TestClient) -> None:
        app.state.admin_settings = {"storage": {"home_enabled": False}}
        response = client.patch(
            "/api/v1/forge/admin/settings",
            json={"storage": {"homeEnabled": True}},
        )
        assert response.status_code == 200
        assert response.json()["storage"]["home_enabled"] is True

    def test_patch_update_settings_accepts_file_manager_camel_case(
        self, client: TestClient
    ) -> None:
        response = client.patch(
            "/api/v1/forge/admin/settings",
            json={"storage": {"homeEnabled": True, "fileManagerEnabled": False}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["storage"]["file_manager_enabled"] is False
        assert data["storage"]["fileManagerEnabled"] is False

    def test_update_without_storage_is_rejected(
        self, client: TestClient, repository: _FakeRepository
    ) -> None:
        response = client.put(
            "/api/v1/forge/admin/settings",
            json={},
        )
        assert response.status_code == 422
        assert repository.saves == []

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

    def test_saves_the_section_form_and_persists_it(
        self, client: TestClient, repository: _FakeRepository
    ) -> None:
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
        assert repository.saves == [
            ("storage", {"home_enabled": False, "file_manager_enabled": False})
        ]

    def test_partial_update_keeps_the_other_field(
        self, client: TestClient, repository: _FakeRepository
    ) -> None:
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
        assert repository.stored["storage"] == {
            "home_enabled": True,
            "file_manager_enabled": False,
        }

    def test_empty_and_unknown_bodies_are_rejected(
        self, client: TestClient, repository: _FakeRepository
    ) -> None:
        assert client.patch("/api/v1/forge/admin/settings/storage", json={}).status_code == 422
        unknown = client.patch("/api/v1/forge/admin/settings/storage", json={"other": True})
        assert unknown.status_code == 422
        assert repository.saves == []

    def test_needs_the_admin_role(
        self, app: FastAPI, client: TestClient, repository: _FakeRepository
    ) -> None:
        app.dependency_overrides[extract_principal] = lambda: Principal(
            user_id="u-1", email="u@test.com", tenant_id="t1", roles=[]
        )
        response = client.patch(
            "/api/v1/forge/admin/settings/storage", json={"fileManagerEnabled": False}
        )
        assert response.status_code == 403
        assert repository.saves == []


class TestWithoutHomeVolumes:
    """A storage adapter that cannot provision home volumes hides and refuses the toggle."""

    @pytest.fixture
    def client(self, repository: _FakeRepository) -> TestClient:
        return TestClient(_build_app(repository, home_volumes_supported=False))

    def test_schema_offers_only_the_file_manager_toggle(self, client: TestClient) -> None:
        data = client.get("/api/v1/forge/settings").json()
        assert [f["key"] for f in data["sections"][0]["fields"]] == ["fileManagerEnabled"]

    def test_home_enabled_is_refused(self, client: TestClient, repository: _FakeRepository) -> None:
        response = client.patch("/api/v1/forge/admin/settings/storage", json={"homeEnabled": False})
        assert response.status_code == 422
        assert "not available with this storage adapter" in response.json()["detail"]
        assert repository.saves == []

    def test_file_manager_still_saves(
        self, client: TestClient, repository: _FakeRepository
    ) -> None:
        response = client.patch(
            "/api/v1/forge/admin/settings/storage", json={"fileManagerEnabled": False}
        )
        assert response.status_code == 200
        assert repository.stored["storage"]["file_manager_enabled"] is False
