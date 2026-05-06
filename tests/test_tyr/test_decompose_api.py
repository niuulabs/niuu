"""Tests for POST /sagas/decompose endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tyr.adapters.bifrost import DecompositionError
from tyr.api.saga_previews import create_saga_previews_router
from tyr.api.sagas import create_sagas_router, resolve_llm, resolve_saga_repo
from tyr.api.tracker import resolve_trackers
from tyr.config import AuthConfig, LLMConfig
from tyr.domain.models import PhaseSpec, RaidSpec, SagaStructure
from tyr.ports.llm import LLMPort

from .test_tracker_api import MockSagaRepo, MockTracker

# ---------------------------------------------------------------------------
# Mock LLM
# ---------------------------------------------------------------------------


class MockLLM(LLMPort):
    """In-memory mock LLM for API tests."""

    def __init__(self) -> None:
        self.last_spec: str | None = None
        self.last_repo: str | None = None
        self.last_model: str | None = None
        self.result: SagaStructure | None = None
        self.error: Exception | None = None

    async def decompose_spec(self, spec: str, repo: str, *, model: str) -> SagaStructure:
        self.last_spec = spec
        self.last_repo = repo
        self.last_model = model
        if self.error:
            raise self.error
        if self.result:
            return self.result
        return SagaStructure(
            name="Test Saga",
            phases=[
                PhaseSpec(
                    name="Phase 1",
                    raids=[
                        RaidSpec(
                            name="Raid 1",
                            description="Do the thing",
                            acceptance_criteria=["Tests pass"],
                            declared_files=["src/main.py"],
                            estimate_hours=3.0,
                            confidence=0.8,
                        )
                    ],
                )
            ],
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dev_settings() -> MagicMock:
    s = MagicMock()
    s.auth = AuthConfig(allow_anonymous_dev=True)
    s.llm = LLMConfig()
    return s


def _make_client(llm: MockLLM) -> TestClient:
    app = FastAPI()
    app.include_router(create_saga_previews_router())
    app.include_router(create_sagas_router())
    app.dependency_overrides[resolve_trackers] = lambda: [MockTracker()]
    app.dependency_overrides[resolve_saga_repo] = lambda: MockSagaRepo()
    app.dependency_overrides[resolve_llm] = lambda: llm
    app.state.settings = _dev_settings()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDecomposeEndpoint:
    @pytest.fixture
    def llm(self) -> MockLLM:
        return MockLLM()

    @pytest.fixture
    def client(self, llm: MockLLM) -> TestClient:
        return _make_client(llm)

    def test_successful_decomposition(self, client: TestClient, llm: MockLLM) -> None:
        resp = client.post(
            "/api/v1/tyr/sagas/decompose",
            json={"spec": "Build auth system", "repo": "org/repo", "model": "claude-opus-4-6"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Test Saga"
        assert len(data["phases"]) == 1
        assert data["phases"][0]["name"] == "Phase 1"
        raids = data["phases"][0]["raids"]
        assert len(raids) == 1
        assert raids[0]["name"] == "Raid 1"
        assert raids[0]["confidence"] == 0.8
        assert raids[0]["estimate_hours"] == 3.0
        assert raids[0]["declared_files"] == ["src/main.py"]
        assert raids[0]["acceptance_criteria"] == ["Tests pass"]
        # Verify model was passed through
        assert llm.last_model == "claude-opus-4-6"
        assert llm.last_spec == "Build auth system"
        assert llm.last_repo == "org/repo"

    def test_uses_default_model_when_empty(self, client: TestClient, llm: MockLLM) -> None:
        resp = client.post(
            "/api/v1/tyr/sagas/decompose",
            json={"spec": "Build something", "repo": "org/repo"},
        )
        assert resp.status_code == 200
        assert llm.last_model == "claude-sonnet-4-6"  # default from LLMConfig

    def test_uses_default_model_when_model_empty_string(
        self, client: TestClient, llm: MockLLM
    ) -> None:
        resp = client.post(
            "/api/v1/tyr/sagas/decompose",
            json={"spec": "Build something", "repo": "org/repo", "model": ""},
        )
        assert resp.status_code == 200
        assert llm.last_model == "claude-sonnet-4-6"

    def test_llm_failure_returns_502(self, client: TestClient, llm: MockLLM) -> None:
        llm.error = DecompositionError("Failed after retries")
        resp = client.post(
            "/api/v1/tyr/sagas/decompose",
            json={"spec": "Build auth", "repo": "org/repo", "model": "m"},
        )
        assert resp.status_code == 502
        assert "decomposition failed" in resp.json()["detail"].lower()

    def test_missing_spec_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/tyr/sagas/decompose",
            json={"repo": "org/repo", "model": "m"},
        )
        assert resp.status_code == 422

    def test_empty_spec_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/tyr/sagas/decompose",
            json={"spec": "", "repo": "org/repo"},
        )
        assert resp.status_code == 422

    def test_missing_repo_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/tyr/sagas/decompose",
            json={"spec": "Build auth"},
        )
        assert resp.status_code == 422

    def test_empty_repo_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/tyr/sagas/decompose",
            json={"spec": "Build auth", "repo": ""},
        )
        assert resp.status_code == 422

    def test_custom_result(self, client: TestClient, llm: MockLLM) -> None:
        llm.result = SagaStructure(
            name="Custom Saga",
            phases=[
                PhaseSpec(
                    name="Setup",
                    raids=[
                        RaidSpec(
                            name="Init DB",
                            description="Set up database schema",
                            acceptance_criteria=["Migrations run", "Tables created"],
                            declared_files=["migrations/001.sql", "src/db.py"],
                            estimate_hours=2.0,
                            confidence=0.95,
                        ),
                        RaidSpec(
                            name="Add API",
                            description="Create REST endpoints",
                            acceptance_criteria=["Endpoints respond"],
                            declared_files=["src/api.py"],
                            estimate_hours=4.0,
                            confidence=0.7,
                        ),
                    ],
                )
            ],
        )
        resp = client.post(
            "/api/v1/tyr/sagas/decompose",
            json={"spec": "DB + API", "repo": "org/repo", "model": "m"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Custom Saga"
        assert len(data["phases"][0]["raids"]) == 2
        assert data["phases"][0]["raids"][0]["confidence"] == 0.95
        assert data["phases"][0]["raids"][1]["estimate_hours"] == 4.0

    def test_create_saga_returns_preview_shape(self, client: TestClient, llm: MockLLM) -> None:
        resp = client.post(
            "/api/v1/tyr/sagas",
            json={"spec": "Build auth system", "repo": "org/repo", "model": "claude-opus-4-6"},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["tracker_id"] == ""
        assert data["tracker_type"] == "preview"
        assert data["name"] == "Test Saga"
        assert data["repos"] == ["org/repo"]
        assert data["feature_branch"] == "feat/test-saga"
        assert data["base_branch"] == "main"
        assert data["confidence"] == 0.0
        assert data["created_at"] == ""
        assert data["phase_summary"] == {"total": 0, "completed": 0}
        assert data["milestone_count"] == 1
        assert data["issue_count"] == 1
        assert llm.last_model == "claude-opus-4-6"

    def test_connection_error_returns_502(self, client: TestClient, llm: MockLLM) -> None:
        llm.error = ConnectionError("Cannot reach Bifröst")
        resp = client.post(
            "/api/v1/tyr/sagas/decompose",
            json={"spec": "spec", "repo": "repo", "model": "m"},
        )
        assert resp.status_code == 502
