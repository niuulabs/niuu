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
from niuu.domain.stack import (
    ApplyStatus,
    ModelOption,
    StackSettings,
    StackView,
    VllmSettings,
    VllmStatus,
)
from niuu.ports.stack_control import StackControlPort

HEADERS = {"Authorization": "Bearer test-token"}


def _identity(roles: list[str]) -> AsyncMock:
    identity = AsyncMock()
    identity.validate_token = AsyncMock(
        return_value=Principal(user_id="u1", email="u@example.com", tenant_id="t1", roles=roles)
    )
    return identity


def _settings(bind_host: str = "127.0.0.1") -> StackSettings:
    return StackSettings(
        bind_host=bind_host,
        external_host="192.168.1.5",
        port=8080,
        project_name="niuu",
        skuld_image="skuld:test",
        vllm=VllmSettings(
            enabled=False,
            model="",
            image="vllm:test",
            max_model_len=4096,
            gpu_memory_utilization=0.6,
        ),
    )


class _FakeStack(StackControlPort):
    def __init__(self) -> None:
        self.staged: dict = {}
        self.applied = 0
        self.fail_with: Exception | None = None

    def _view(self) -> StackView:
        effective = _settings(self.staged.get("docker", {}).get("bind_host", "127.0.0.1"))
        return StackView(
            current=_settings(),
            staged=self.staged,
            effective=effective,
            models=[
                ModelOption(
                    id="m",
                    model="org/m",
                    name="M",
                    description="d",
                    weight_gib=10,
                    recommended=True,
                    fits=True,
                    memory_needed_gib=22,
                )
            ],
            accelerator_memory_gib=128,
        )

    async def view(self) -> StackView:
        if self.fail_with:
            raise self.fail_with
        return self._view()

    async def stage(self, changes: dict) -> StackView:
        if "bad" in changes:
            raise ValueError("Unknown stack setting 'bad'")
        self.staged = {"docker": changes}
        return self._view()

    async def discard(self) -> StackView:
        self.staged = {}
        return self._view()

    async def apply(self) -> ApplyStatus:
        if not self.staged:
            raise ValueError("Nothing is staged")
        self.applied += 1
        return ApplyStatus(state="applying", started_at="now", detail="d", changes=self.staged)

    async def status(self) -> ApplyStatus:
        return ApplyStatus(state="idle", vllm=VllmStatus(state="ready", detail="ok"))


def _client(
    tmp_path: Path,
    *,
    roles: list[str],
    enabled: bool = True,
    stack: StackControlPort | None = None,
) -> TestClient:
    service = SetupService(
        FileSetupStateStore(path=str(tmp_path / "state.json")),
        enabled=enabled,
        mode="docker",
        docker_socket_path=str(tmp_path / "docker.sock"),
        database_probe=AsyncMock(return_value=True),
    )
    app = FastAPI()
    app.state.identity = _identity(roles)
    app.include_router(create_setup_router(service, stack=stack))
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


class TestStackRoutes:
    def test_unavailable_without_a_controller(self, admin: TestClient) -> None:
        assert admin.get("/api/v1/niuu/setup/stack", headers=HEADERS).status_code == 503
        assert admin.post("/api/v1/niuu/setup/stack/apply", headers=HEADERS).status_code == 503

    def test_view_stage_apply_status(self, tmp_path: Path) -> None:
        stack = _FakeStack()
        client = _client(tmp_path, roles=["volundr:admin"], stack=stack)
        view = client.get("/api/v1/niuu/setup/stack", headers=HEADERS).json()
        assert view["current"]["bindHost"] == "127.0.0.1"
        assert view["current"]["accessUrls"] == ["http://127.0.0.1:8080"]
        assert view["models"][0]["memoryNeededGib"] == 22
        assert view["acceleratorMemoryGib"] == 128
        assert view["hasStagedChanges"] is False

        staged = client.put(
            "/api/v1/niuu/setup/stack",
            json={"changes": {"bind_host": "0.0.0.0"}},
            headers=HEADERS,
        ).json()
        assert staged["hasStagedChanges"] is True
        assert staged["effective"]["bindHost"] == "0.0.0.0"
        assert staged["effective"]["accessUrls"] == [
            "http://127.0.0.1:8080",
            "http://192.168.1.5:8080",
        ]
        bad = client.put("/api/v1/niuu/setup/stack", json={"changes": {"bad": 1}}, headers=HEADERS)
        assert bad.status_code == 422

        applied = client.post("/api/v1/niuu/setup/stack/apply", headers=HEADERS).json()
        assert applied["state"] == "applying"
        assert applied["changes"] == {"docker": {"bind_host": "0.0.0.0"}}
        status = client.get("/api/v1/niuu/setup/stack/status", headers=HEADERS).json()
        assert status["state"] == "idle"
        assert status["vllm"] == {"state": "ready", "detail": "ok"}

        assert (
            client.delete("/api/v1/niuu/setup/stack", headers=HEADERS).json()["hasStagedChanges"]
            is False
        )
        assert client.post("/api/v1/niuu/setup/stack/apply", headers=HEADERS).status_code == 422

    def test_stack_requires_admin_and_reports_missing_files(self, tmp_path: Path) -> None:
        stack = _FakeStack()
        viewer = _client(tmp_path, roles=["volundr:developer"], stack=stack)
        assert viewer.get("/api/v1/niuu/setup/stack", headers=HEADERS).status_code == 200
        assert (
            viewer.put(
                "/api/v1/niuu/setup/stack", json={"changes": {}}, headers=HEADERS
            ).status_code
            == 403
        )
        assert viewer.delete("/api/v1/niuu/setup/stack", headers=HEADERS).status_code == 403
        assert viewer.post("/api/v1/niuu/setup/stack/apply", headers=HEADERS).status_code == 403
        stack.fail_with = FileNotFoundError("stack.yaml is missing; `niuu up` writes it")
        response = viewer.get("/api/v1/niuu/setup/stack", headers=HEADERS)
        assert response.status_code == 503
        assert "niuu up" in response.json()["detail"]
