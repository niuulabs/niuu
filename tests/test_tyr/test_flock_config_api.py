"""Tests for flock configuration REST API endpoints."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tyr.api.flock_config import create_flock_config_router
from tyr.api.settings import create_settings_router
from tyr.config import AuthConfig, FlockConfig, PersonaOverride, Settings


def _make_app(flock: FlockConfig | None = None) -> FastAPI:
    """Build a minimal FastAPI app with the flock config router."""
    settings = Settings(auth=AuthConfig(allow_anonymous_dev=True, default_user_id="dev-user"))
    # Always set flock explicitly so local tyr.yaml doesn't leak into tests
    settings.dispatch.flock = flock if flock is not None else FlockConfig()

    app = FastAPI()
    app.state.settings = settings
    app.include_router(create_settings_router())
    app.include_router(create_flock_config_router())
    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_make_app())


@pytest.fixture()
def client_enabled() -> TestClient:
    flock = FlockConfig(
        enabled=True,
        default_personas=[PersonaOverride(name="coordinator"), PersonaOverride(name="reviewer")],
        llm_config={"model": "claude-sonnet-4-6"},
        sleipnir_publish_urls=["http://sleipnir:4222"],
    )
    return TestClient(_make_app(flock=flock))


class TestGetFlockConfig:
    def test_returns_disabled_by_default(self, client: TestClient) -> None:
        resp = client.get("/api/v1/tyr/flock/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["flock_enabled"] is False

    def test_returns_enabled_when_configured(self, client_enabled: TestClient) -> None:
        resp = client_enabled.get("/api/v1/tyr/flock/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["flock_enabled"] is True

    def test_returns_personas(self, client_enabled: TestClient) -> None:
        resp = client_enabled.get("/api/v1/tyr/flock/config")
        data = resp.json()
        names = [p["name"] for p in data["flock_default_personas"]]
        assert "coordinator" in names
        assert "reviewer" in names

    def test_returns_llm_config(self, client_enabled: TestClient) -> None:
        resp = client_enabled.get("/api/v1/tyr/flock/config")
        data = resp.json()
        assert data["flock_llm_config"]["model"] == "claude-sonnet-4-6"

    def test_returns_sleipnir_urls(self, client_enabled: TestClient) -> None:
        resp = client_enabled.get("/api/v1/tyr/flock/config")
        data = resp.json()
        assert "http://sleipnir:4222" in data["flock_sleipnir_publish_urls"]


class TestPatchFlockConfig:
    def test_toggle_flock_enabled(self, client: TestClient) -> None:
        resp = client.patch("/api/v1/tyr/flock/config", json={"flock_enabled": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["flock_enabled"] is True

    def test_toggle_flock_disabled(self, client_enabled: TestClient) -> None:
        resp = client_enabled.patch("/api/v1/tyr/flock/config", json={"flock_enabled": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["flock_enabled"] is False

    def test_update_personas(self, client: TestClient) -> None:
        resp = client.patch(
            "/api/v1/tyr/flock/config",
            json={"flock_default_personas": ["coordinator", "security-auditor"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        names = [p["name"] for p in data["flock_default_personas"]]
        assert names == ["coordinator", "security-auditor"]

    def test_update_llm_config(self, client: TestClient) -> None:
        resp = client.patch(
            "/api/v1/tyr/flock/config",
            json={"flock_llm_config": {"model": "Qwen/Qwen2.5-Coder-32B", "provider": "openai"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["flock_llm_config"]["model"] == "Qwen/Qwen2.5-Coder-32B"

    def test_update_sleipnir_urls(self, client: TestClient) -> None:
        resp = client.patch(
            "/api/v1/tyr/flock/config",
            json={"flock_sleipnir_publish_urls": ["http://bus1:4222", "http://bus2:4222"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["flock_sleipnir_publish_urls"] == ["http://bus1:4222", "http://bus2:4222"]

    def test_patch_is_idempotent_for_unchanged_fields(self, client_enabled: TestClient) -> None:
        resp = client_enabled.patch(
            "/api/v1/tyr/flock/config",
            json={"flock_enabled": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["flock_enabled"] is True

    def test_empty_patch_returns_current(self, client_enabled: TestClient) -> None:
        resp = client_enabled.patch("/api/v1/tyr/flock/config", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert "flock_enabled" in data

    def test_runtime_flock_config_does_not_change_operator_settings(
        self,
        client: TestClient,
    ) -> None:
        resp = client.patch("/api/v1/tyr/flock/config", json={"flock_enabled": True})
        settings_resp = client.get("/api/v1/tyr/settings/flock")

        assert resp.status_code == 200
        assert settings_resp.status_code == 200
        assert settings_resp.json()["flock_name"] == "default"
