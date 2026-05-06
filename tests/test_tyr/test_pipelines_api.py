"""Tests for POST /api/v1/tyr/pipelines endpoint."""

from __future__ import annotations

import textwrap
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_tyr.stubs import InMemorySagaRepository, StubVolundrFactory, StubVolundrPort
from tyr.adapters.memory_event_bus import InMemoryEventBus
from tyr.api.pipelines import (
    create_pipelines_router,
    resolve_pipeline_executor,
)
from tyr.config import AuthConfig
from tyr.domain.flock_flow import FlockFlowConfig, FlockPersonaOverride
from tyr.domain.pipeline_executor import TemplateAwarePipelineExecutor
from tyr.ports.flock_flow import FlockFlowProvider

_VALID_YAML = textwrap.dedent(
    """
    name: "Test pipeline"
    feature_branch: "feat/test"
    base_branch: "main"
    repos:
      - "acme/app"
    stages:
      - name: review
        sequential:
          - name: "Review code"
            persona: reviewer
            prompt: "Review the code"
    """
)

_FLOW_YAML = textwrap.dedent(
    """
    name: "Flock pipeline"
    feature_branch: "feat/test"
    base_branch: "main"
    repos:
      - "acme/app"
    flock_flow: code-review-flow
    stages:
      - name: review
        sequential:
          - name: "Review code"
            persona: reviewer
            prompt: "Review the code"
    """
)

_OWNER = "test-owner"


def _make_executor(
    flow_provider: FlockFlowProvider | None = None,
    volundr: StubVolundrPort | None = None,
) -> TemplateAwarePipelineExecutor:
    repo = InMemorySagaRepository()
    bus = InMemoryEventBus()
    factory = StubVolundrFactory(volundr or StubVolundrPort())
    return TemplateAwarePipelineExecutor(
        saga_repo=repo,
        volundr_factory=factory,
        event_bus=bus,
        owner_id=_OWNER,
        flow_provider=flow_provider,
    )


def _make_flow_provider() -> FlockFlowProvider:
    class _FlowProvider(FlockFlowProvider):
        def __init__(self) -> None:
            self._flow = FlockFlowConfig(
                name="code-review-flow",
                personas=[
                    FlockPersonaOverride(name="reviewer"),
                    FlockPersonaOverride(name="security-auditor"),
                ],
            )

        def get(self, name: str) -> FlockFlowConfig | None:
            if name != self._flow.name:
                return None
            return self._flow

        def list(self) -> list[FlockFlowConfig]:
            return [self._flow]

        def save(self, flow: FlockFlowConfig) -> None:
            self._flow = flow

        def delete(self, name: str) -> bool:
            if name != self._flow.name:
                return False
            self._flow = FlockFlowConfig(name="")
            return True

    return _FlowProvider()


def _make_empty_flow_provider() -> FlockFlowProvider:
    class _EmptyFlowProvider(FlockFlowProvider):
        def get(self, name: str) -> FlockFlowConfig | None:
            return None

        def list(self) -> list[FlockFlowConfig]:
            return []

        def save(self, flow: FlockFlowConfig) -> None:
            return None

        def delete(self, name: str) -> bool:
            return False

    return _EmptyFlowProvider()


def _make_app(executor: TemplateAwarePipelineExecutor) -> FastAPI:
    app = FastAPI()
    settings = MagicMock()
    settings.auth = AuthConfig(allow_anonymous_dev=True)
    app.state.settings = settings

    app.include_router(create_pipelines_router())
    app.dependency_overrides[resolve_pipeline_executor] = lambda: executor
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreatePipeline:
    def test_create_pipeline_returns_201(self):
        executor = _make_executor()
        app = _make_app(executor)
        client = TestClient(app)

        resp = client.post(
            "/api/v1/tyr/pipelines",
            json={"definition": _VALID_YAML, "context": {}, "auto_start": False},
        )
        assert resp.status_code == 201

    def test_create_pipeline_response_shape(self):
        executor = _make_executor()
        app = _make_app(executor)
        client = TestClient(app)

        resp = client.post(
            "/api/v1/tyr/pipelines",
            json={"definition": _VALID_YAML, "context": {}, "auto_start": False},
        )
        data = resp.json()
        assert "saga_id" in data
        assert "slug" in data
        assert "name" in data
        assert data["phase_count"] == 1
        assert data["auto_started"] is False

    def test_create_pipeline_auto_start_true(self):
        executor = _make_executor()
        app = _make_app(executor)
        client = TestClient(app)

        resp = client.post(
            "/api/v1/tyr/pipelines",
            json={"definition": _VALID_YAML, "auto_start": True},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["auto_started"] is True

    def test_invalid_yaml_returns_422(self):
        executor = _make_executor()
        app = _make_app(executor)
        client = TestClient(app)

        resp = client.post(
            "/api/v1/tyr/pipelines",
            json={"definition": "name: ''\nstages: []"},
        )
        assert resp.status_code == 422

    def test_executor_exception_returns_502(self):
        executor = MagicMock()
        executor.create_from_yaml = AsyncMock(side_effect=RuntimeError("boom"))
        executor._saga_repo = InMemorySagaRepository()

        app = FastAPI()
        settings = MagicMock()
        settings.auth = AuthConfig(allow_anonymous_dev=True)
        app.state.settings = settings
        app.include_router(create_pipelines_router())
        app.dependency_overrides[resolve_pipeline_executor] = lambda: executor

        client = TestClient(app)
        resp = client.post(
            "/api/v1/tyr/pipelines",
            json={"definition": _VALID_YAML},
        )
        assert resp.status_code == 502

    def test_unconfigured_executor_returns_503(self):
        """When executor dependency is not overridden, returns 503."""
        app = FastAPI()
        settings = MagicMock()
        settings.auth = AuthConfig(allow_anonymous_dev=True)
        app.state.settings = settings
        app.include_router(create_pipelines_router())
        # No dependency override — uses the stub that raises 503

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/v1/tyr/pipelines",
            json={"definition": _VALID_YAML},
        )
        assert resp.status_code == 503

    def test_context_substituted_in_pipeline_name(self):
        yaml_with_ctx = textwrap.dedent(
            """
            name: "Deploy {event.repo}"
            feature_branch: "main"
            base_branch: "main"
            repos:
              - "{event.repo}"
            stages:
              - name: smoke
                sequential:
                  - name: "Smoke test"
                    persona: qa-agent
                    prompt: "Test {event.repo}"
            """
        )
        executor = _make_executor()
        app = _make_app(executor)
        client = TestClient(app)

        resp = client.post(
            "/api/v1/tyr/pipelines",
            json={
                "definition": yaml_with_ctx,
                "context": {"repo": "acme/app"},
                "auto_start": False,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "acme/app" in data["name"]

    def test_missing_named_flock_flow_returns_422(self):
        executor = _make_executor(flow_provider=_make_empty_flow_provider())
        app = _make_app(executor)
        client = TestClient(app)

        resp = client.post(
            "/api/v1/tyr/pipelines",
            json={"definition": _FLOW_YAML, "context": {}, "auto_start": False},
        )
        assert resp.status_code == 422
        assert "flock_flow" in resp.json()["detail"]

    def test_named_flock_flow_dispatches_ravn_flock(self):
        volundr = StubVolundrPort()
        executor = _make_executor(
            flow_provider=_make_flow_provider(),
            volundr=volundr,
        )
        app = _make_app(executor)
        client = TestClient(app)

        resp = client.post(
            "/api/v1/tyr/pipelines",
            json={"definition": _FLOW_YAML, "context": {}, "auto_start": True},
        )
        assert resp.status_code == 201
        assert len(volundr.spawned) == 1
        assert volundr.spawned[0].workload_type == "ravn_flock"
