"""Tests for presets API."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.rest_presets import create_presets_router
from volundr.domain.models import Preset
from volundr.domain.ports import PresetRepository
from volundr.domain.services.preset import (
    PresetDuplicateNameError,
    PresetNotFoundError,
    PresetService,
)

# --- In-memory repository for testing ---


class InMemoryPresetRepository(PresetRepository):
    """In-memory preset repository for testing."""

    def __init__(self):
        self._presets: dict[UUID, Preset] = {}

    async def create(self, preset: Preset) -> Preset:
        self._presets[preset.id] = preset
        return preset

    async def get(self, preset_id: UUID) -> Preset | None:
        return self._presets.get(preset_id)

    async def get_by_name(self, name: str) -> Preset | None:
        for p in self._presets.values():
            if p.name == name:
                return p
        return None

    async def list(
        self,
        cli_tool: str | None = None,
        is_default: bool | None = None,
    ) -> list[Preset]:
        results = list(self._presets.values())
        if cli_tool is not None:
            results = [p for p in results if p.cli_tool == cli_tool]
        if is_default is not None:
            results = [p for p in results if p.is_default == is_default]
        results.sort(key=lambda p: p.updated_at, reverse=True)
        return results

    async def update(self, preset: Preset) -> Preset:
        self._presets[preset.id] = preset
        return preset

    async def delete(self, preset_id: UUID) -> bool:
        if preset_id in self._presets:
            del self._presets[preset_id]
            return True
        return False

    async def clear_default(self, cli_tool: str) -> None:
        for p in self._presets.values():
            if p.cli_tool == cli_tool and p.is_default:
                p.is_default = False


@pytest.fixture
def preset_repository() -> InMemoryPresetRepository:
    return InMemoryPresetRepository()


@pytest.fixture
def preset_service(preset_repository: InMemoryPresetRepository) -> PresetService:
    return PresetService(preset_repository)


@pytest.fixture
def preset_client(preset_service: PresetService) -> TestClient:
    app = FastAPI()
    router = create_presets_router(preset_service)
    app.include_router(router)
    return TestClient(app)


# --- Service tests ---


class TestPresetService:
    """Tests for PresetService."""

    async def test_create_preset(self, preset_service: PresetService):
        preset = await preset_service.create_preset(
            name="Default Claude",
            description="Standard Claude config",
            cli_tool="claude-code",
        )
        assert preset.name == "Default Claude"
        assert preset.cli_tool == "claude-code"
        assert preset.is_default is False

    async def test_create_preset_duplicate_name(self, preset_service: PresetService):
        await preset_service.create_preset(name="Unique", cli_tool="claude-code")
        with pytest.raises(PresetDuplicateNameError):
            await preset_service.create_preset(name="Unique", cli_tool="claude-code")

    async def test_get_preset(self, preset_service: PresetService):
        created = await preset_service.create_preset(name="Test", cli_tool="claude-code")
        fetched = await preset_service.get_preset(created.id)
        assert fetched.id == created.id
        assert fetched.name == "Test"

    async def test_get_preset_not_found(self, preset_service: PresetService):
        with pytest.raises(PresetNotFoundError):
            await preset_service.get_preset(uuid4())

    async def test_list_presets(self, preset_service: PresetService):
        await preset_service.create_preset(name="A", cli_tool="claude-code")
        await preset_service.create_preset(name="B", cli_tool="aider")
        all_presets = await preset_service.list_presets()
        assert len(all_presets) == 2

    async def test_list_presets_filtered_by_cli_tool(self, preset_service: PresetService):
        await preset_service.create_preset(name="A", cli_tool="claude-code")
        await preset_service.create_preset(name="B", cli_tool="aider")
        filtered = await preset_service.list_presets(cli_tool="claude-code")
        assert len(filtered) == 1
        assert filtered[0].name == "A"

    async def test_list_presets_filtered_by_is_default(self, preset_service: PresetService):
        await preset_service.create_preset(name="A", is_default=True)
        await preset_service.create_preset(name="B", is_default=False)
        defaults = await preset_service.list_presets(is_default=True)
        assert len(defaults) == 1
        assert defaults[0].name == "A"

    async def test_update_preset(self, preset_service: PresetService):
        created = await preset_service.create_preset(name="Old", cli_tool="claude-code")
        updated = await preset_service.update_preset(
            created.id, {"name": "New", "description": "Updated"}
        )
        assert updated.name == "New"
        assert updated.description == "Updated"

    async def test_update_preset_not_found(self, preset_service: PresetService):
        with pytest.raises(PresetNotFoundError):
            await preset_service.update_preset(uuid4(), {"name": "X"})

    async def test_update_preset_duplicate_name(self, preset_service: PresetService):
        await preset_service.create_preset(name="Existing")
        created = await preset_service.create_preset(name="Other")
        with pytest.raises(PresetDuplicateNameError):
            await preset_service.update_preset(created.id, {"name": "Existing"})

    async def test_default_uniqueness(self, preset_service: PresetService):
        first = await preset_service.create_preset(
            name="First", is_default=True, cli_tool="claude-code"
        )
        await preset_service.create_preset(name="Second", is_default=True, cli_tool="claude-code")
        # Refresh first to see cleared default
        refreshed = await preset_service.get_preset(first.id)
        assert refreshed.is_default is False

    async def test_delete_preset(self, preset_service: PresetService):
        created = await preset_service.create_preset(name="Del")
        result = await preset_service.delete_preset(created.id)
        assert result is True

    async def test_delete_preset_not_found(self, preset_service: PresetService):
        with pytest.raises(PresetNotFoundError):
            await preset_service.delete_preset(uuid4())

    async def test_create_preset_with_config(self, preset_service: PresetService):
        preset = await preset_service.create_preset(
            name="Full Config",
            resource_config={"cpu": "2", "memory": "4Gi"},
            mcp_servers=[{"name": "linear", "type": "stdio"}],
            env_vars={"KEY": "value"},
            env_secret_refs=["my-secret"],
        )
        assert preset.resource_config == {"cpu": "2", "memory": "4Gi"}
        assert len(preset.mcp_servers) == 1
        assert preset.env_vars == {"KEY": "value"}
        assert preset.env_secret_refs == ["my-secret"]


# --- REST endpoint tests ---


class TestPresetEndpoints:
    """Tests for preset REST endpoints."""

    def test_create_preset(self, preset_client: TestClient):
        resp = preset_client.post(
            "/api/v1/forge/presets",
            json={"name": "Test Preset", "cli_tool": "claude-code"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Test Preset"
        assert data["cli_tool"] == "claude-code"

    def test_create_preset_duplicate_name(self, preset_client: TestClient):
        preset_client.post(
            "/api/v1/forge/presets",
            json={"name": "Unique"},
        )
        resp = preset_client.post(
            "/api/v1/forge/presets",
            json={"name": "Unique"},
        )
        assert resp.status_code == 409

    def test_list_presets(self, preset_client: TestClient):
        preset_client.post(
            "/api/v1/forge/presets",
            json={"name": "A"},
        )
        preset_client.post(
            "/api/v1/forge/presets",
            json={"name": "B"},
        )
        resp = preset_client.get("/api/v1/forge/presets")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_presets_with_filter(self, preset_client: TestClient):
        preset_client.post(
            "/api/v1/forge/presets",
            json={"name": "Claude", "cli_tool": "claude-code"},
        )
        preset_client.post(
            "/api/v1/forge/presets",
            json={"name": "Aider", "cli_tool": "aider"},
        )
        resp = preset_client.get("/api/v1/forge/presets?cli_tool=claude-code")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_get_preset(self, preset_client: TestClient):
        create_resp = preset_client.post(
            "/api/v1/forge/presets",
            json={"name": "Test"},
        )
        preset_id = create_resp.json()["id"]
        resp = preset_client.get(f"/api/v1/forge/presets/{preset_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test"

    def test_get_preset_not_found(self, preset_client: TestClient):
        resp = preset_client.get(f"/api/v1/forge/presets/{uuid4()}")
        assert resp.status_code == 404

    def test_update_preset(self, preset_client: TestClient):
        create_resp = preset_client.post(
            "/api/v1/forge/presets",
            json={"name": "Old"},
        )
        preset_id = create_resp.json()["id"]
        resp = preset_client.put(
            f"/api/v1/forge/presets/{preset_id}",
            json={"name": "New", "description": "Updated"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "New"
        assert resp.json()["description"] == "Updated"

    def test_update_preset_not_found(self, preset_client: TestClient):
        resp = preset_client.put(
            f"/api/v1/forge/presets/{uuid4()}",
            json={"name": "X"},
        )
        assert resp.status_code == 404

    def test_update_preset_duplicate_name(self, preset_client: TestClient):
        preset_client.post(
            "/api/v1/forge/presets",
            json={"name": "Existing"},
        )
        create_resp = preset_client.post(
            "/api/v1/forge/presets",
            json={"name": "Other"},
        )
        preset_id = create_resp.json()["id"]
        resp = preset_client.put(
            f"/api/v1/forge/presets/{preset_id}",
            json={"name": "Existing"},
        )
        assert resp.status_code == 409

    def test_delete_preset(self, preset_client: TestClient):
        create_resp = preset_client.post(
            "/api/v1/forge/presets",
            json={"name": "Del"},
        )
        preset_id = create_resp.json()["id"]
        resp = preset_client.delete(f"/api/v1/forge/presets/{preset_id}")
        assert resp.status_code == 204

    def test_delete_preset_not_found(self, preset_client: TestClient):
        resp = preset_client.delete(f"/api/v1/forge/presets/{uuid4()}")
        assert resp.status_code == 404

    def test_create_with_full_config(self, preset_client: TestClient):
        resp = preset_client.post(
            "/api/v1/forge/presets",
            json={
                "name": "Full",
                "cli_tool": "claude-code",
                "resource_config": {"cpu": "2"},
                "mcp_servers": [{"name": "linear"}],
                "terminal_sidecar": {"enabled": True},
                "skills": [{"name": "commit"}],
                "rules": [{"content": "always test"}],
                "env_vars": {"KEY": "val"},
                "env_secret_refs": ["secret1"],
                "workload_config": {"timeout": 300},
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["resource_config"] == {"cpu": "2"}
        assert data["mcp_servers"] == [{"name": "linear"}]
        assert data["env_vars"] == {"KEY": "val"}
