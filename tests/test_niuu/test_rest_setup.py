"""Tests for the setup REST router."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.adapters.file_setup_state import FileSetupStateStore
from niuu.adapters.inbound.rest_setup import create_setup_router
from niuu.domain.models import Principal
from niuu.domain.services.setup import SetupService

HEADERS = {"Authorization": "Bearer test-token"}


def _identity(roles: list[str]) -> AsyncMock:
    identity = AsyncMock()
    identity.validate_token = AsyncMock(
        return_value=Principal(user_id="u1", email="u@example.com", tenant_id="t1", roles=roles)
    )
    return identity


def _client(tmp_path: Path, *, roles: list[str], enabled: bool = True) -> TestClient:
    service = SetupService(
        FileSetupStateStore(path=str(tmp_path / "state.json")),
        enabled=enabled,
        mode="docker",
        docker_socket_path=str(tmp_path / "docker.sock"),
        database_probe=AsyncMock(return_value=True),
    )
    app = FastAPI()
    app.state.identity = _identity(roles)
    app.include_router(create_setup_router(service))
    return TestClient(app)


@pytest.fixture
def admin(tmp_path: Path) -> TestClient:
    return _client(tmp_path, roles=["volundr:admin"])


@pytest.fixture
def viewer(tmp_path: Path) -> TestClient:
    return _client(tmp_path, roles=["volundr:developer"])


class TestState:
    def test_initial_state(self, admin: TestClient) -> None:
        response = admin.get("/api/v1/niuu/setup", headers=HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["enabled"] is True
        assert body["mode"] == "docker"
        assert body["completed"] is False
        assert body["completedAt"] is None
        assert body["steps"][0] == "welcome"
        assert body["completedSteps"] == []

    def test_disabled_reports_enabled_false(self, tmp_path: Path) -> None:
        client = _client(tmp_path, roles=[], enabled=False)
        assert client.get("/api/v1/niuu/setup", headers=HEADERS).json()["enabled"] is False

    def test_requires_auth(self, admin: TestClient) -> None:
        assert admin.get("/api/v1/niuu/setup").status_code == 401


class TestSteps:
    def test_complete_step_and_finish(self, admin: TestClient) -> None:
        response = admin.put(
            "/api/v1/niuu/setup/steps/providers",
            headers=HEADERS,
            json={"data": {"anthropic": True}},
        )
        assert response.status_code == 200
        steps = response.json()["completedSteps"]
        assert steps[0]["step"] == "providers"
        assert steps[0]["data"] == {"anthropic": True}
        assert steps[0]["completedAt"]

        done = admin.post("/api/v1/niuu/setup/complete", headers=HEADERS)
        assert done.status_code == 200
        assert done.json()["completed"] is True
        assert done.json()["completedAt"]

        reset = admin.post("/api/v1/niuu/setup/reset", headers=HEADERS)
        assert reset.json()["completed"] is False
        assert reset.json()["completedSteps"] == []

    def test_unknown_step_is_404(self, admin: TestClient) -> None:
        response = admin.put("/api/v1/niuu/setup/steps/bogus", headers=HEADERS, json={})
        assert response.status_code == 404
        assert "Unknown setup step" in response.json()["detail"]

    def test_writes_require_admin(self, viewer: TestClient) -> None:
        assert (
            viewer.put("/api/v1/niuu/setup/steps/git", headers=HEADERS, json={}).status_code == 403
        )
        assert viewer.post("/api/v1/niuu/setup/complete", headers=HEADERS).status_code == 403
        assert viewer.post("/api/v1/niuu/setup/reset", headers=HEADERS).status_code == 403
        assert viewer.get("/api/v1/niuu/setup", headers=HEADERS).status_code == 200


class TestSystem:
    def test_system_report(self, admin: TestClient) -> None:
        with patch("niuu.domain.services.setup.shutil.which", return_value="/usr/bin/git"):
            response = admin.get("/api/v1/niuu/setup/system", headers=HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["host"] is None
        names = [check["name"] for check in body["checks"]]
        assert names == ["host facts", "database", "docker socket", "git"]
        assert body["checks"][1]["passed"] is True
        assert body["checks"][2]["warnOnly"] is True
        assert body["healthy"] is True
