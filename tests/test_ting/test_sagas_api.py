"""Tests for saga REST API endpoints."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from niuu.domain.models import Principal
from ting.api.phases import create_saga_phases_router
from ting.api.sagas import (
    _build_phase_summary,
    _can_use_workflow,
    _find_project,
    _resolve_selected_workflow,
    _sanitize_log,
    create_sagas_router,
    resolve_git,
    resolve_llm,
    resolve_saga_repo,
    resolve_volundr,
)
from ting.api.tracker import resolve_trackers
from ting.config import AuthConfig
from ting.domain.models import (
    Phase,
    PhaseStatus,
    Run,
    RunStatus,
    Saga,
    SagaStatus,
    TrackerIssue,
    TrackerMilestone,
    TrackerProject,
    WorkflowDefinition,
    WorkflowScope,
)
from ting.ports.workflow_repository import WorkflowRepository

from .test_tracker_api import MockSagaRepo, MockTracker


class InMemoryWorkflowRepository(WorkflowRepository):
    def __init__(self, workflows: list[WorkflowDefinition] | None = None) -> None:
        self._workflows = {workflow.id: workflow for workflow in workflows or []}

    async def list_workflows(
        self,
        *,
        owner_id: str,
        scope: WorkflowScope | None = None,
    ) -> list[WorkflowDefinition]:
        return list(self._workflows.values())

    async def get_workflow(self, workflow_id):
        return self._workflows.get(workflow_id)

    async def save_workflow(self, workflow: WorkflowDefinition) -> WorkflowDefinition:
        self._workflows[workflow.id] = workflow
        return workflow

    async def delete_workflow(self, workflow_id) -> bool:
        return self._workflows.pop(workflow_id, None) is not None


def _workflow(*, executable: bool = True) -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name="Review Flow",
        description="",
        version="1.0.0",
        scope=WorkflowScope.USER,
        owner_id="dev-user",
        definition_yaml="name: Review Flow\nstages: []" if executable else None,
        graph={
            "nodes": [
                {"id": "trigger-1", "kind": "trigger", "label": "Start"},
                {
                    "id": "stage-1",
                    "kind": "stage",
                    "label": "Review",
                    "personaIds": ["reviewer"],
                    "stageMembers": [{"personaId": "reviewer", "budget": 40}],
                },
            ],
            "edges": [{"id": "e1", "source": "trigger-1", "target": "stage-1"}],
        },
        created_at=now,
        updated_at=now,
    )


def _dev_settings() -> MagicMock:
    """Create mock settings with anonymous dev enabled for test apps."""
    s = MagicMock()
    s.auth = AuthConfig(allow_anonymous_dev=True)
    s.dispatch.flock.default_workflow_name = "Ting Run Flow"
    return s


class _ProjectLookupFails:
    async def get_project(self, project_id: str):
        raise RuntimeError(f"lookup failed for {project_id}")


class _PhaseSummaryNotImplementedRepo:
    async def get_phases_by_saga(self, saga_id):
        raise NotImplementedError


def _principal(user_id: str = "dev-user") -> Principal:
    return Principal(
        user_id=user_id,
        email=f"{user_id}@example.com",
        tenant_id="tenant-1",
        roles=[],
    )


def test_sanitize_log_escapes_newlines_and_carriage_returns() -> None:
    assert _sanitize_log("hello\nworld\ragain") == "hello\\nworld\\ragain"


def test_can_use_workflow_allows_system_scope_and_owner() -> None:
    workflow = _workflow()
    assert _can_use_workflow(workflow, _principal("dev-user")) is True
    assert _can_use_workflow(workflow, _principal("other-user")) is False

    assert (
        _can_use_workflow(
            replace(workflow, scope=WorkflowScope.SYSTEM, owner_id="someone-else"),
            _principal("other-user"),
        )
        is True
    )


@pytest.mark.asyncio
async def test_dependency_resolvers_raise_503_when_unconfigured() -> None:
    for resolver, detail in (
        (resolve_saga_repo, "Saga repository not configured"),
        (resolve_llm, "LLM adapter not configured"),
        (resolve_git, "Git adapter not configured"),
        (resolve_volundr, "Volundr adapter not configured"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await resolver()
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == detail


@pytest.mark.asyncio
async def test_find_project_skips_failing_tracker_and_handles_all_failures() -> None:
    tracker = MockTracker()
    tracker.projects = [
        TrackerProject(
            id="proj-1",
            name="Alpha",
            description="",
            status="started",
            url="https://linear.app/test/project/alpha-abc123",
            milestone_count=0,
            issue_count=0,
            slug="alpha",
            progress=0.0,
        )
    ]

    found = await _find_project("proj-1", [_ProjectLookupFails(), tracker])
    assert found is not None
    assert found.id == "proj-1"

    missing = await _find_project("missing", [_ProjectLookupFails()])
    assert missing is None


@pytest.mark.asyncio
async def test_build_phase_summary_falls_back_when_repo_does_not_support_phases() -> None:
    summary = await _build_phase_summary(_PhaseSummaryNotImplementedRepo(), uuid4())
    assert summary.total == 0
    assert summary.completed == 0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_tracker() -> MockTracker:
    tracker = MockTracker()
    tracker.projects = [
        TrackerProject(
            id="proj-1",
            name="Alpha",
            description="First project",
            status="started",
            url="https://linear.app/test/project/alpha-abc123",
            milestone_count=2,
            issue_count=3,
            slug="alpha",
            progress=0.5,
        ),
    ]
    tracker.milestones = {
        "proj-1": [
            TrackerMilestone(
                id="ms-1",
                project_id="proj-1",
                name="Phase 1",
                description="First phase",
                sort_order=1,
                progress=1.0,
            ),
            TrackerMilestone(
                id="ms-2",
                project_id="proj-1",
                name="Phase 2",
                description="Second phase",
                sort_order=2,
                progress=0.0,
            ),
        ],
    }
    tracker.issues = {
        "proj-1": [
            TrackerIssue(
                id="i-1",
                identifier="A-1",
                title="Done task",
                description="",
                status="Done",
                status_type="completed",
            ),
            TrackerIssue(
                id="i-2",
                identifier="A-2",
                title="Open task",
                description="",
                status="Todo",
                status_type="unstarted",
                milestone_id="ms-1",
            ),
            TrackerIssue(
                id="i-3",
                identifier="A-3",
                title="In progress",
                description="",
                status="In Progress",
                status_type="started",
                milestone_id="ms-2",
            ),
        ],
    }
    return tracker


@pytest.fixture
def saga_repo() -> MockSagaRepo:
    repo = MockSagaRepo()
    saga = Saga(
        id=uuid4(),
        tracker_id="proj-1",
        tracker_type="mock",
        slug="alpha",
        name="Alpha",
        repos=["org/repo"],
        feature_branch="feat/alpha",
        status=SagaStatus.ACTIVE,
        confidence=0.0,
        created_at=datetime.now(UTC),
        base_branch="dev",
        owner_id="dev-user",
    )
    repo.sagas.append(saga)
    phase = Phase(
        id=uuid4(),
        saga_id=saga.id,
        tracker_id="ms-1",
        number=1,
        name="Phase 1",
        status=PhaseStatus.ACTIVE,
        confidence=0.8,
    )
    repo.phases.append(phase)
    repo.runs.append(
        Run(
            id=uuid4(),
            phase_id=phase.id,
            tracker_id="A-2",
            name="Open task",
            description="",
            acceptance_criteria=["Ship it"],
            declared_files=["src/feature.py"],
            estimate_hours=2.0,
            status=RunStatus.REVIEW,
            confidence=0.7,
            session_id="sess-1",
            branch="feat/alpha",
            chronicle_summary="done",
            pr_url=None,
            pr_id=None,
            retry_count=1,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            reviewer_session_id="reviewer-1",
            review_round=2,
        )
    )
    return repo


@pytest.fixture
def client(mock_tracker: MockTracker, saga_repo: MockSagaRepo) -> TestClient:
    app = FastAPI()
    app.include_router(create_sagas_router())
    app.include_router(create_saga_phases_router())
    app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
    app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
    app.state.settings = _dev_settings()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListSagas:
    def test_returns_sagas_with_tracker_data(self, client: TestClient):
        resp = client.get("/api/v1/ting/sagas")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        saga = data[0]
        assert saga["name"] == "Alpha"
        assert saga["tracker_id"] == "proj-1"
        assert saga["repos"] == ["org/repo"]
        assert saga["milestone_count"] == 2
        assert saga["issue_count"] == 3
        assert saga["status"] == "active"
        assert saga["url"] == "https://linear.app/test/project/alpha-abc123"
        assert saga["base_branch"] == "dev"
        assert saga["confidence"] == 0.0
        assert saga["created_at"]
        assert saga["phase_summary"] == {"total": 1, "completed": 0}

    def test_empty_when_no_sagas(self, mock_tracker: MockTracker):
        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
        app.dependency_overrides[resolve_saga_repo] = lambda: MockSagaRepo()
        app.state.settings = _dev_settings()
        client = TestClient(app)
        resp = client.get("/api/v1/ting/sagas")
        assert resp.status_code == 200
        assert resp.json() == []


class TestGetSaga:
    def test_returns_detail_with_phases(self, client: TestClient, saga_repo: MockSagaRepo):
        saga_id = str(saga_repo.sagas[0].id)
        resp = client.get(f"/api/v1/ting/sagas/{saga_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Alpha"
        assert data["description"] == "First project"
        assert data["base_branch"] == "dev"
        assert data["confidence"] == 0.0
        assert data["created_at"]
        assert data["status"] == "active"
        assert data["phase_summary"] == {"total": 1, "completed": 0}
        assert len(data["phases"]) == 3  # 2 milestones + unassigned
        assert data["phases"][0]["name"] == "Phase 1"
        assert data["phases"][1]["name"] == "Phase 2"
        assert data["phases"][2]["name"] == "Unassigned"

    def test_imported_unassigned_completed_runs_drive_phase_summary(self):
        tracker = MockTracker()
        tracker.projects = [
            TrackerProject(
                id="proj-import",
                name="Imported",
                description="Imported project",
                status="completed",
                url="https://linear.app/test/project/imported",
                milestone_count=0,
                issue_count=2,
                slug="imported",
                progress=1.0,
            )
        ]
        tracker.issues = {
            "proj-import": [
                TrackerIssue(
                    id="i-1",
                    identifier="IMP-1",
                    title="Step 1",
                    description="",
                    status="Done",
                    status_type="completed",
                    milestone_id=None,
                ),
                TrackerIssue(
                    id="i-2",
                    identifier="IMP-2",
                    title="Step 2",
                    description="",
                    status="Done",
                    status_type="completed",
                    milestone_id=None,
                ),
            ]
        }
        repo = MockSagaRepo()
        repo.sagas.append(
            Saga(
                id=uuid4(),
                tracker_id="proj-import",
                tracker_type="mock",
                slug="imported",
                name="Imported",
                repos=["org/repo"],
                feature_branch="feat/imported",
                status=SagaStatus.COMPLETE,
                confidence=0.0,
                created_at=datetime.now(UTC),
                base_branch="dev",
                owner_id="dev-user",
            )
        )

        app = FastAPI()
        app.include_router(create_sagas_router())
        app.include_router(create_saga_phases_router())
        app.dependency_overrides[resolve_trackers] = lambda: [tracker]
        app.dependency_overrides[resolve_saga_repo] = lambda: repo
        app.state.settings = _dev_settings()
        client = TestClient(app)

        resp = client.get(f"/api/v1/ting/sagas/{repo.sagas[0].id}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "complete"
        assert data["phase_summary"] == {"total": 1, "completed": 1}
        assert len(data["phases"]) == 1
        assert data["phases"][0]["name"] == "Unassigned"

    def test_returns_assigned_workflow_fields(
        self, mock_tracker: MockTracker, saga_repo: MockSagaRepo
    ):
        workflow = _workflow()
        saga = saga_repo.sagas[0]
        saga_repo.sagas[0] = Saga(
            id=saga.id,
            tracker_id=saga.tracker_id,
            tracker_type=saga.tracker_type,
            slug=saga.slug,
            name=saga.name,
            repos=saga.repos,
            feature_branch=saga.feature_branch,
            status=saga.status,
            confidence=saga.confidence,
            created_at=saga.created_at,
            base_branch=saga.base_branch,
            owner_id=saga.owner_id,
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            workflow_snapshot={
                "workflow_id": str(workflow.id),
                "name": workflow.name,
                "version": workflow.version,
                "definition_yaml": workflow.definition_yaml,
                "graph": workflow.graph,
                "scope": workflow.scope.value,
            },
        )

        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
        app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
        app.state.settings = _dev_settings()
        client = TestClient(app)

        response = client.get(f"/api/v1/ting/sagas/{saga.id}")

        assert response.status_code == 200
        body = response.json()
        assert body["workflow_id"] == str(workflow.id)
        assert body["workflow"] == workflow.name
        assert body["workflow_version"] == workflow.version


class TestAssignWorkflow:
    def test_assigns_graph_backed_workflow(
        self, mock_tracker: MockTracker, saga_repo: MockSagaRepo
    ):
        workflow = _workflow()
        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
        app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
        app.state.settings = _dev_settings()
        app.state.workflow_repo = InMemoryWorkflowRepository([workflow])
        client = TestClient(app)

        response = client.put(
            f"/api/v1/ting/sagas/{saga_repo.sagas[0].id}/workflow",
            json={"workflow_id": str(workflow.id)},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["workflow_id"] == str(workflow.id)
        assert body["workflow"] == workflow.name
        assert body["workflow_version"] == workflow.version

    def test_assigns_workflow_even_without_compiled_definition(
        self,
        mock_tracker: MockTracker,
        saga_repo: MockSagaRepo,
    ):
        workflow = _workflow(executable=False)
        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
        app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
        app.state.settings = _dev_settings()
        app.state.workflow_repo = InMemoryWorkflowRepository([workflow])
        client = TestClient(app)

        response = client.put(
            f"/api/v1/ting/sagas/{saga_repo.sagas[0].id}/workflow",
            json={"workflow_id": str(workflow.id)},
        )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_resolve_selected_workflow_uses_default_system_workflow_when_missing(
        self,
    ) -> None:
        workflow = WorkflowDefinition(
            id=uuid4(),
            name="Ting Run Flow",
            description="",
            version="1.0.0",
            scope=WorkflowScope.SYSTEM,
            owner_id=None,
            definition_yaml="name: Ting Run Flow",
            graph={
                "nodes": [
                    {
                        "id": "stage-1",
                        "kind": "stage",
                        "label": "Run",
                        "stageMembers": [
                            {"personaId": "coordinator", "budget": 40},
                            {"personaId": "coder", "budget": 40},
                            {"personaId": "reviewer", "budget": 25},
                        ],
                    }
                ],
                "edges": [],
            },
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        app = FastAPI()
        app.state.settings = _dev_settings()
        app.state.workflow_repo = InMemoryWorkflowRepository([workflow])

        request = Request({"type": "http", "app": app, "headers": []})
        workflow_id, workflow_version, workflow_snapshot = await _resolve_selected_workflow(
            request=request,
            principal=_principal(),
            workflow_id_value=None,
            use_default_when_missing=True,
        )

        assert workflow_id == workflow.id
        assert workflow_version == workflow.version
        assert workflow_snapshot is not None
        assert workflow_snapshot["personas"] == [
            {"name": "coordinator", "iteration_budget": 40},
            {"name": "coder", "iteration_budget": 40},
            {"name": "reviewer", "iteration_budget": 25},
        ]

    def test_rejects_invalid_saga_id_for_workflow_assignment(
        self,
        mock_tracker: MockTracker,
        saga_repo: MockSagaRepo,
    ) -> None:
        workflow = _workflow()
        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
        app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
        app.state.settings = _dev_settings()
        app.state.workflow_repo = InMemoryWorkflowRepository([workflow])
        client = TestClient(app)

        response = client.put("/api/v1/ting/sagas/not-a-uuid/workflow", json={"workflow_id": None})

        assert response.status_code == 404
        assert response.json()["detail"] == "Saga not found: not-a-uuid"

    def test_returns_503_when_workflow_repo_is_missing(
        self,
        mock_tracker: MockTracker,
        saga_repo: MockSagaRepo,
    ) -> None:
        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
        app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
        app.state.settings = _dev_settings()
        client = TestClient(app)

        response = client.put(
            f"/api/v1/ting/sagas/{saga_repo.sagas[0].id}/workflow",
            json={"workflow_id": str(uuid4())},
        )

        assert response.status_code == 503
        assert response.json()["detail"] == "Workflow repository not configured"

    def test_rejects_invalid_workflow_id(
        self,
        mock_tracker: MockTracker,
        saga_repo: MockSagaRepo,
    ) -> None:
        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
        app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
        app.state.settings = _dev_settings()
        app.state.workflow_repo = InMemoryWorkflowRepository()
        client = TestClient(app)

        response = client.put(
            f"/api/v1/ting/sagas/{saga_repo.sagas[0].id}/workflow",
            json={"workflow_id": "not-a-uuid"},
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "Invalid workflow_id: 'not-a-uuid'"

    def test_returns_404_when_workflow_assignment_target_saga_is_missing(
        self,
        mock_tracker: MockTracker,
    ) -> None:
        workflow = _workflow()
        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
        app.dependency_overrides[resolve_saga_repo] = lambda: MockSagaRepo()
        app.state.settings = _dev_settings()
        app.state.workflow_repo = InMemoryWorkflowRepository([workflow])
        client = TestClient(app)

        missing_id = uuid4()
        response = client.put(
            f"/api/v1/ting/sagas/{missing_id}/workflow",
            json={"workflow_id": str(workflow.id)},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == f"Saga not found: {missing_id}"

    def test_phases_contain_runs(self, client: TestClient, saga_repo: MockSagaRepo):
        saga_id = str(saga_repo.sagas[0].id)
        resp = client.get(f"/api/v1/ting/sagas/{saga_id}")
        data = resp.json()
        # ms-1 has i-2
        assert len(data["phases"][0]["runs"]) == 1
        assert data["phases"][0]["runs"][0]["identifier"] == "A-2"
        # ms-2 has i-3
        assert len(data["phases"][1]["runs"]) == 1
        # unassigned has i-1
        assert len(data["phases"][2]["runs"]) == 1

    def test_not_found(self, client: TestClient):
        resp = client.get(f"/api/v1/ting/sagas/{uuid4()}")
        assert resp.status_code == 404

    def test_returns_persisted_phase_wire_shape(self, client: TestClient, saga_repo: MockSagaRepo):
        saga_id = str(saga_repo.sagas[0].id)

        resp = client.get(f"/api/v1/ting/sagas/{saga_id}/phases")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["tracker_id"] == "ms-1"
        assert data[0]["status"] == "active"
        assert data[0]["runs"][0]["tracker_id"] == "A-2"
        assert data[0]["runs"][0]["reviewer_session_id"] == "reviewer-1"
        assert data[0]["runs"][0]["review_round"] == 2

    def test_synthesizes_tracker_backed_phases_when_repo_has_none(
        self,
        client: TestClient,
        saga_repo: MockSagaRepo,
    ):
        saga_repo.phases.clear()
        saga_repo.runs.clear()
        saga_id = str(saga_repo.sagas[0].id)

        resp = client.get(f"/api/v1/ting/sagas/{saga_id}/phases")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        assert data[0]["name"] == "Phase 1"
        assert data[0]["runs"][0]["name"] == "R1"
        assert data[1]["name"] == "Phase 2"
        assert data[2]["name"] == "Unassigned"


class TestGetSagaErrors:
    def test_tracker_unavailable_returns_degraded_response(self, saga_repo: MockSagaRepo):
        """Returns 200 with empty tracker data when tracker is unavailable."""

        class FailingTracker(MockTracker):
            async def get_project(self, project_id: str) -> TrackerProject:
                raise ConnectionError("Tracker down")

            async def get_project_full(self, project_id: str):
                raise ConnectionError("Tracker down")

        app = FastAPI()
        app.include_router(create_sagas_router())
        failing = FailingTracker()
        app.dependency_overrides[resolve_trackers] = lambda: [failing]
        app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
        app.state.settings = _dev_settings()
        client = TestClient(app)

        saga_id = str(saga_repo.sagas[0].id)
        resp = client.get(f"/api/v1/ting/sagas/{saga_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["phases"] == []

    def test_list_with_tracker_error(self, saga_repo: MockSagaRepo):
        """List sagas gracefully handles tracker errors."""

        class FailingTracker(MockTracker):
            async def list_projects(self) -> list[TrackerProject]:
                raise ConnectionError("Tracker down")

        app = FastAPI()
        app.include_router(create_sagas_router())
        failing = FailingTracker()
        app.dependency_overrides[resolve_trackers] = lambda: [failing]
        app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
        app.state.settings = _dev_settings()
        client = TestClient(app)

        resp = client.get("/api/v1/ting/sagas")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        # Falls back to DB name
        assert data[0]["name"] == "Alpha"


class TestSpawnPlanSession:
    def test_plan_config_returns_finalize_prompt(self, mock_tracker: MockTracker) -> None:
        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
        app.dependency_overrides[resolve_saga_repo] = lambda: MockSagaRepo()
        app.state.settings = _dev_settings()
        app.state.settings.planner.finalize_prompt = "Finish the structure"
        client = TestClient(app)

        response = client.get("/api/v1/ting/sagas/plan/config")

        assert response.status_code == 200
        assert response.json() == {"finalize_prompt": "Finish the structure"}

    def test_defaults_base_branch_to_main(self, mock_tracker: MockTracker) -> None:
        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
        app.dependency_overrides[resolve_saga_repo] = lambda: MockSagaRepo()
        mock_volundr = AsyncMock()
        mock_volundr.list_integration_ids.return_value = []
        mock_volundr.spawn_session.return_value = MagicMock(
            id="plan-1",
            chat_endpoint="/api/v1/forge/sessions/plan-1/messages",
        )
        app.dependency_overrides[resolve_volundr] = lambda: mock_volundr
        settings = _dev_settings()
        settings.dispatch.default_model = "claude-opus"
        settings.dispatch.default_system_prompt = "dispatch-system"
        settings.planner.planner_system_prompt = ""
        app.state.settings = settings

        client = TestClient(app)
        resp = client.post(
            "/api/v1/ting/sagas/plan",
            json={"spec": "Ship the dashboard", "repo": "niuulabs/volundr"},
        )

        assert resp.status_code == 201
        assert resp.json() == {
            "session_id": "plan-1",
            "chat_endpoint": "/api/v1/forge/sessions/plan-1/messages",
        }
        spawn_request = mock_volundr.spawn_session.await_args.args[0]
        assert spawn_request.base_branch == "main"
        assert spawn_request.repo == "niuulabs/volundr"


class TestDeleteSaga:
    def test_delete_existing(self, client: TestClient, saga_repo: MockSagaRepo):
        saga_id = str(saga_repo.sagas[0].id)
        resp = client.delete(f"/api/v1/ting/sagas/{saga_id}")
        assert resp.status_code == 204
        assert len(saga_repo.sagas) == 0

    def test_delete_not_found(self, client: TestClient):
        resp = client.delete(f"/api/v1/ting/sagas/{uuid4()}")
        assert resp.status_code == 404


class _DispatchRecorder:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls: list[tuple[str, str]] = []

    async def try_auto_continue(self, owner_id: str, saga_tracker_id: str) -> None:
        self.calls.append((owner_id, saga_tracker_id))
        if self.should_fail:
            raise RuntimeError("dispatch unavailable")


class TestCommitSaga:
    def test_kicks_off_initial_dispatch_when_service_is_available(
        self,
        mock_tracker: MockTracker,
    ) -> None:
        repo = MockSagaRepo()
        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
        app.dependency_overrides[resolve_saga_repo] = lambda: repo
        app.dependency_overrides[resolve_git] = lambda: AsyncMock()
        app.state.settings = _dev_settings()
        recorder = _DispatchRecorder()
        app.state.dispatch_service = recorder
        client = TestClient(app)

        response = client.post(
            "/api/v1/ting/sagas/commit",
            json={
                "name": "Proof Saga",
                "slug": "proof-saga",
                "description": "Test kickoff",
                "repos": ["https://github.com/niuulabs/volundr.git"],
                "base_branch": "dev",
                "phases": [
                    {
                        "name": "Phase 1",
                        "runs": [
                            {
                                "name": "Create proof file",
                                "description": "Create a proof file",
                                "acceptance_criteria": ["file exists"],
                                "declared_files": ["proof.txt"],
                                "estimate_hours": 1.0,
                            }
                        ],
                    }
                ],
            },
        )

        assert response.status_code == 201
        assert recorder.calls == [("dev-user", "saga-created")]

    def test_returns_created_saga_even_when_initial_dispatch_fails(
        self,
        mock_tracker: MockTracker,
    ) -> None:
        repo = MockSagaRepo()
        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
        app.dependency_overrides[resolve_saga_repo] = lambda: repo
        app.dependency_overrides[resolve_git] = lambda: AsyncMock()
        app.state.settings = _dev_settings()
        app.state.dispatch_service = _DispatchRecorder(should_fail=True)
        client = TestClient(app)

        response = client.post(
            "/api/v1/ting/sagas/commit",
            json={
                "name": "Proof Saga",
                "slug": "proof-saga-warning",
                "description": "Test kickoff warning",
                "repos": ["https://github.com/niuulabs/volundr.git"],
                "base_branch": "dev",
                "phases": [
                    {
                        "name": "Phase 1",
                        "runs": [
                            {
                                "name": "Create proof file",
                                "description": "Create a proof file",
                                "acceptance_criteria": ["file exists"],
                                "declared_files": ["proof.txt"],
                                "estimate_hours": 1.0,
                            }
                        ],
                    }
                ],
            },
        )

        assert response.status_code == 201
        assert "Failed to kick off initial dispatch" in response.json()["warnings"][0]


class TestExtractStructure:
    """Tests for the POST /sagas/extract-structure endpoint."""

    @pytest.fixture
    def client(self) -> TestClient:
        app = FastAPI()
        app.state.settings = _dev_settings()
        app.include_router(create_sagas_router())
        return TestClient(app)

    def test_extracts_valid_structure_from_json_block(self, client: TestClient):
        text = (
            "Here is the plan:\n"
            "```json\n"
            '{"name": "Auth Refactor", "phases": [{"name": "Phase 1", "runs": [{'
            '"name": "Setup", "description": "Set up auth", '
            '"acceptance_criteria": ["AC1"], "declared_files": ["src/auth.py"], '
            '"estimate_hours": 4, "confidence": 0.8}]}]}\n'
            "```"
        )
        resp = client.post("/api/v1/ting/sagas/extract-structure", json={"text": text})
        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] is True
        assert data["structure"]["name"] == "Auth Refactor"
        assert len(data["structure"]["phases"]) == 1
        assert data["structure"]["phases"][0]["runs"][0]["name"] == "Setup"

    def test_returns_not_found_for_plain_text(self, client: TestClient):
        resp = client.post(
            "/api/v1/ting/sagas/extract-structure",
            json={"text": "Just a regular message with no JSON."},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] is False
        assert data["structure"] is None

    def test_returns_not_found_for_invalid_json(self, client: TestClient):
        text = "```json\n{not valid json}\n```"
        resp = client.post("/api/v1/ting/sagas/extract-structure", json={"text": text})
        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] is False

    def test_returns_not_found_for_json_missing_required_fields(self, client: TestClient):
        text = '```json\n{"key": "value"}\n```'
        resp = client.post("/api/v1/ting/sagas/extract-structure", json={"text": text})
        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] is False

    def test_rejects_empty_text(self, client: TestClient):
        resp = client.post("/api/v1/ting/sagas/extract-structure", json={"text": ""})
        assert resp.status_code == 422


class TestUpdateSaga:
    def test_updates_status_returns_saga(self, client: TestClient, saga_repo: MockSagaRepo):
        saga_id = str(saga_repo.sagas[0].id)
        resp = client.patch(f"/api/v1/ting/sagas/{saga_id}", json={"status": "COMPLETE"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == saga_id
        assert data["slug"] == "alpha"

    def test_lowercase_status_accepted(self, client: TestClient, saga_repo: MockSagaRepo):
        saga_id = str(saga_repo.sagas[0].id)
        resp = client.patch(f"/api/v1/ting/sagas/{saga_id}", json={"status": "complete"})
        assert resp.status_code == 200

    def test_invalid_status_returns_422(self, client: TestClient, saga_repo: MockSagaRepo):
        saga_id = str(saga_repo.sagas[0].id)
        resp = client.patch(f"/api/v1/ting/sagas/{saga_id}", json={"status": "INVALID_STATUS"})
        assert resp.status_code == 422

    def test_not_found_returns_404(self, client: TestClient):
        resp = client.patch(f"/api/v1/ting/sagas/{uuid4()}", json={"status": "COMPLETE"})
        assert resp.status_code == 404
