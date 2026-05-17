"""Tests for tracker REST API endpoints.

Tests the tracker browsing endpoints by overriding the
_resolve_tracker_adapters FastAPI dependency with a mock TrackerPort.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ting.api.tracker import (
    create_canonical_tracker_router,
    create_tracker_router,
    resolve_trackers,
)
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
from ting.ports.saga_repository import SagaRepository
from ting.ports.tracker import TrackerPort
from ting.ports.workflow_repository import WorkflowRepository

# ---------------------------------------------------------------------------
# Mock TrackerPort implementation
# ---------------------------------------------------------------------------


class MockTracker(TrackerPort):
    """In-memory mock tracker for API tests."""

    def __init__(self) -> None:
        self.projects: list[TrackerProject] = []
        self.milestones: dict[str, list[TrackerMilestone]] = {}
        self.issues: dict[str, list[TrackerIssue]] = {}

    async def create_saga(self, saga: Saga, *, description: str = "") -> str:
        return "saga-created"

    async def create_phase(self, phase: Phase, *, project_id: str = "") -> str:
        return "phase-created"

    async def create_run(self, run: Run, *, project_id: str = "", milestone_id: str = "") -> str:
        return "run-created"

    async def update_run_state(self, run_id: str, state: RunStatus) -> None:
        pass

    async def close_run(self, run_id: str) -> None:
        pass

    async def get_saga(self, saga_id: str) -> Saga:
        now = datetime.now(UTC)
        return Saga(
            id=uuid4(),
            tracker_id=saga_id,
            tracker_type="mock",
            slug="test",
            name="Test",
            repos=[],
            feature_branch="feat/test",
            status=SagaStatus.ACTIVE,
            confidence=0.0,
            created_at=now,
            base_branch="dev",
        )

    async def get_phase(self, tracker_id: str) -> Phase:
        return Phase(
            id=uuid4(),
            saga_id=UUID(int=0),
            tracker_id=tracker_id,
            number=1,
            name="P1",
            status=PhaseStatus.PENDING,
            confidence=0.0,
        )

    async def get_run(self, tracker_id: str) -> Run:
        now = datetime.now(UTC)
        return Run(
            id=uuid4(),
            phase_id=UUID(int=0),
            tracker_id=tracker_id,
            name="R1",
            description="",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=None,
            status=RunStatus.PENDING,
            confidence=0.0,
            session_id=None,
            branch=None,
            chronicle_summary=None,
            pr_url=None,
            pr_id=None,
            retry_count=0,
            created_at=now,
            updated_at=now,
        )

    async def list_pending_runs(self, phase_id: str) -> list[Run]:
        return []

    async def list_projects(self) -> list[TrackerProject]:
        return self.projects

    async def get_project(self, project_id: str) -> TrackerProject:
        for p in self.projects:
            if p.id == project_id:
                return p
        raise ValueError(f"Project not found: {project_id}")

    async def list_milestones(self, project_id: str) -> list[TrackerMilestone]:
        return self.milestones.get(project_id, [])

    async def list_issues(
        self,
        project_id: str,
        milestone_id: str | None = None,
    ) -> list[TrackerIssue]:
        issues = self.issues.get(project_id, [])
        if milestone_id:
            issues = [i for i in issues if i.milestone_id == milestone_id]
        return issues

    async def get_project_full(
        self, project_id: str
    ) -> tuple[TrackerProject, list[TrackerMilestone], list[TrackerIssue]]:
        project = await self.get_project(project_id)
        milestones = await self.list_milestones(project_id)
        issues = await self.list_issues(project_id)
        return project, milestones, issues

    async def get_blocked_identifiers(self, project_id: str) -> set[str]:
        return getattr(self, "_blocked", set())

    async def get_run_progress_for_saga(self, saga_tracker_id: str) -> list[Run]:
        return []

    async def update_run_progress(self, tracker_id: str, **kwargs: object) -> Run:  # noqa: ANN003
        now = datetime.now(UTC)
        return Run(
            id=uuid4(),
            phase_id=UUID(int=0),
            tracker_id=tracker_id,
            name="R1",
            description="",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=None,
            status=RunStatus.PENDING,
            confidence=0.0,
            session_id=None,
            branch=None,
            chronicle_summary=None,
            pr_url=None,
            pr_id=None,
            retry_count=0,
            created_at=now,
            updated_at=now,
        )

    async def get_run_by_session(self, session_id: str) -> Run | None:
        return None

    async def list_runs_by_status(self, status: RunStatus) -> list[Run]:
        return []

    async def get_run_by_id(self, run_id: UUID) -> Run | None:
        return None

    async def add_confidence_event(self, tracker_id: str, event: object) -> None:  # noqa: ANN001
        pass

    async def get_confidence_events(self, tracker_id: str) -> list:
        return []

    async def all_runs_merged(self, phase_tracker_id: str) -> bool:
        return False

    async def list_phases_for_saga(self, saga_tracker_id: str) -> list[Phase]:
        return []

    async def update_phase_status(self, phase_tracker_id: str, status: PhaseStatus) -> Phase | None:
        return None

    async def get_saga_for_run(self, tracker_id: str) -> Saga | None:
        return None

    async def get_phase_for_run(self, tracker_id: str) -> Phase | None:
        return None

    async def get_owner_for_run(self, tracker_id: str) -> str | None:
        return None

    async def save_session_message(self, message: object) -> None:  # noqa: ANN001
        pass

    async def get_session_messages(self, tracker_id: str) -> list:
        return []


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
            url="https://linear.app/proj-1",
            milestone_count=2,
            issue_count=5,
        ),
        TrackerProject(
            id="proj-2",
            name="Beta",
            description="Second project",
            status="planned",
            url="https://linear.app/proj-2",
            milestone_count=0,
            issue_count=0,
        ),
        TrackerProject(
            id="proj-3",
            name="Gamma",
            description="Completed project",
            status="completed",
            url="https://linear.app/proj-3",
            milestone_count=1,
            issue_count=2,
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
                progress=0.5,
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
                identifier="ALPHA-1",
                title="Setup",
                description="Initial setup",
                status="Todo",
                assignee="Dev",
                labels=["setup"],
                priority=1,
                url="https://linear.app/i-1",
                milestone_id="ms-1",
            ),
            TrackerIssue(
                id="i-2",
                identifier="ALPHA-2",
                title="Build",
                description="Build things",
                status="In Progress",
                assignee=None,
                labels=[],
                priority=2,
                url="https://linear.app/i-2",
                milestone_id="ms-2",
            ),
        ],
    }
    return tracker


class MockSagaRepo(SagaRepository):
    """In-memory saga repository for tests."""

    def __init__(self) -> None:
        self.sagas: list[Saga] = []
        self.phases: list[Phase] = []
        self.runs: list[Run] = []

    @contextlib.asynccontextmanager
    async def begin(self):  # noqa: ANN201
        yield None

    async def save_saga(self, saga: Saga, *, conn=None) -> None:  # noqa: ANN001
        for index, existing in enumerate(self.sagas):
            if existing.id == saga.id:
                self.sagas[index] = saga
                return
        self.sagas.append(saga)

    async def save_phase(self, phase: Phase, *, conn=None) -> None:  # noqa: ANN001
        self.phases.append(phase)

    async def save_run(self, run: Run, *, conn=None) -> None:  # noqa: ANN001
        self.runs.append(run)

    async def get_saga_by_slug(self, slug: str) -> Saga | None:
        return next((s for s in self.sagas if s.slug == slug), None)

    async def list_sagas(self, *, owner_id: str | None = None) -> list[Saga]:
        return list(self.sagas)

    async def get_saga(self, saga_id, *, owner_id: str | None = None) -> Saga | None:
        return next((s for s in self.sagas if s.id == saga_id), None)

    async def count_by_status(self) -> dict[str, int]:
        from ting.domain.models import RunStatus

        counts = {s.value: 0 for s in RunStatus}
        for run in self.runs:
            counts[run.status.value] += 1
        return counts

    async def delete_saga(self, saga_id, *, owner_id=None) -> bool:
        before = len(self.sagas)
        self.sagas = [
            s
            for s in self.sagas
            if not (s.id == saga_id and (owner_id is None or s.owner_id == owner_id))
        ]
        return len(self.sagas) < before

    async def update_saga_status(self, saga_id: UUID, status: SagaStatus) -> None:
        pass

    async def update_saga_workflow(
        self,
        saga_id: UUID,
        *,
        workflow_id: UUID | None,
        workflow_version: str | None,
        workflow_snapshot: dict | None,
        owner_id: str | None = None,
    ) -> None:
        updated: list[Saga] = []
        for saga in self.sagas:
            if saga.id != saga_id:
                updated.append(saga)
                continue
            if owner_id is not None and saga.owner_id != owner_id:
                updated.append(saga)
                continue
            updated.append(
                Saga(
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
                    workflow_id=workflow_id,
                    workflow_version=workflow_version,
                    workflow_snapshot=workflow_snapshot,
                )
            )
        self.sagas = updated

    async def get_phases_by_saga(self, saga_id: UUID) -> list[Phase]:
        return [phase for phase in self.phases if phase.saga_id == saga_id]

    async def get_runs_by_phase(self, phase_id: UUID) -> list[Run]:
        return [run for run in self.runs if run.phase_id == phase_id]


class InMemoryWorkflowRepository(WorkflowRepository):
    def __init__(self, workflows: list[WorkflowDefinition] | None = None) -> None:
        self._workflows = {workflow.id: workflow for workflow in workflows or []}

    async def list_workflows(
        self,
        *,
        owner_id: str,
        scope: WorkflowScope | None = None,
    ) -> list[WorkflowDefinition]:
        workflows = list(self._workflows.values())
        if scope is None:
            return workflows
        return [workflow for workflow in workflows if workflow.scope == scope]

    async def get_workflow(self, workflow_id):
        return self._workflows.get(workflow_id)

    async def save_workflow(self, workflow: WorkflowDefinition) -> WorkflowDefinition:
        self._workflows[workflow.id] = workflow
        return workflow

    async def delete_workflow(self, workflow_id) -> bool:
        return self._workflows.pop(workflow_id, None) is not None


class _DispatchRecorder:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls: list[tuple[str, str]] = []

    async def try_auto_continue(self, owner_id: str, saga_tracker_id: str) -> None:
        self.calls.append((owner_id, saga_tracker_id))
        if self.should_fail:
            raise RuntimeError("dispatch unavailable")


def _workflow() -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name="Security Review Flow",
        description="Saved workflow for import tests",
        version="1.2.3",
        scope=WorkflowScope.USER,
        owner_id="dev-user",
        definition_yaml=None,
        graph={"nodes": [], "edges": []},
        created_at=now,
        updated_at=now,
    )


def _build_test_client(
    mock_tracker: MockTracker,
    *,
    workflow_repo: WorkflowRepository | None = None,
    dispatch_service: object | None = None,
) -> TestClient:
    app = FastAPI()
    app.state.legacy_route_hits = {}
    app.include_router(create_canonical_tracker_router())
    app.include_router(create_tracker_router())
    app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
    app.state.saga_repo = MockSagaRepo()
    mock_settings = MagicMock()
    mock_settings.auth = AuthConfig(allow_anonymous_dev=True)
    app.state.settings = mock_settings
    if workflow_repo is not None:
        app.state.workflow_repo = workflow_repo
    if dispatch_service is not None:
        app.state.dispatch_service = dispatch_service
    return TestClient(app)


@pytest.fixture
def client(mock_tracker: MockTracker) -> TestClient:
    return _build_test_client(mock_tracker)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListProjects:
    def test_filters_terminal_projects(self, client: TestClient):
        resp = client.get("/api/v1/ting/tracker/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["id"] == "proj-1"
        assert data[1]["id"] == "proj-2"
        assert {project["id"] for project in data} == {"proj-1", "proj-2"}

    def test_canonical_projects_match_legacy(self, client: TestClient):
        legacy = client.get("/api/v1/ting/tracker/projects")
        canonical = client.get("/api/v1/tracker/projects")
        assert legacy.status_code == 200
        assert canonical.status_code == 200
        assert canonical.json() == legacy.json()
        assert legacy.headers["X-Niuu-Canonical-Route"] == "/api/v1/tracker/projects"


class TestGetProject:
    def test_found(self, client: TestClient):
        resp = client.get("/api/v1/ting/tracker/projects/proj-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "proj-1"
        assert data["name"] == "Alpha"

    def test_not_found(self, client: TestClient):
        resp = client.get("/api/v1/ting/tracker/projects/nonexistent")
        assert resp.status_code == 404


class TestListMilestones:
    def test_returns_milestones(self, client: TestClient):
        resp = client.get("/api/v1/ting/tracker/projects/proj-1/milestones")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["name"] == "Phase 1"

    def test_empty_milestones(self, client: TestClient):
        resp = client.get("/api/v1/ting/tracker/projects/proj-2/milestones")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_canonical_milestones_match_legacy(self, client: TestClient):
        legacy = client.get("/api/v1/ting/tracker/projects/proj-1/milestones")
        canonical = client.get("/api/v1/tracker/projects/proj-1/milestones")
        assert legacy.status_code == 200
        assert canonical.status_code == 200
        assert canonical.json() == legacy.json()


class TestListIssues:
    def test_all_issues(self, client: TestClient):
        resp = client.get("/api/v1/ting/tracker/projects/proj-1/issues")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_filtered_by_milestone(self, client: TestClient):
        resp = client.get("/api/v1/ting/tracker/projects/proj-1/issues?milestone_id=ms-1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["identifier"] == "ALPHA-1"

    def test_no_matching_milestone(self, client: TestClient):
        resp = client.get("/api/v1/ting/tracker/projects/proj-1/issues?milestone_id=nonexistent")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_canonical_project_issues_match_legacy(self, client: TestClient):
        legacy = client.get("/api/v1/ting/tracker/projects/proj-1/issues?milestone_id=ms-1")
        canonical = client.get("/api/v1/tracker/projects/proj-1/issues?milestone_id=ms-1")
        assert legacy.status_code == 200
        assert canonical.status_code == 200
        assert canonical.json() == legacy.json()


class TestImportProject:
    def test_success(self, client: TestClient):
        resp = client.post(
            "/api/v1/ting/tracker/import",
            json={
                "project_id": "proj-1",
                "repos": ["org/repo"],
                "base_branch": "dev",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["tracker_id"] == "proj-1"
        assert data["name"] == "Alpha"
        assert data["repos"] == ["org/repo"]
        assert data["feature_branch"] == "feat/alpha"
        assert data["status"] == "ACTIVE"
        assert data["phase_count"] == 2
        assert data["run_count"] == 5

    def test_project_not_found(self, client: TestClient):
        resp = client.post(
            "/api/v1/ting/tracker/import",
            json={
                "project_id": "nonexistent",
                "repos": ["org/repo"],
                "base_branch": "dev",
            },
        )
        assert resp.status_code == 404

    def test_duplicate_slug_returns_409(self, client: TestClient):
        client.app.state.saga_repo.sagas.append(
            Saga(
                id=uuid4(),
                tracker_id="existing",
                tracker_type="mock",
                slug="alpha",
                name="Existing",
                repos=["org/repo"],
                feature_branch="feat/alpha",
                status=SagaStatus.ACTIVE,
                confidence=0.0,
                created_at=datetime.now(UTC),
                base_branch="dev",
                owner_id="dev-user",
            )
        )

        resp = client.post(
            "/api/v1/ting/tracker/import",
            json={
                "project_id": "proj-1",
                "repos": ["org/repo"],
                "base_branch": "dev",
            },
        )
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    def test_reimport_same_project_updates_existing_saga(self, client: TestClient):
        existing_id = uuid4()
        original_created_at = datetime.now(UTC)
        client.app.state.saga_repo.sagas.append(
            Saga(
                id=existing_id,
                tracker_id="proj-1",
                tracker_type="mock",
                slug="alpha",
                name="Alpha",
                repos=["org/old-repo"],
                feature_branch="feat/alpha",
                status=SagaStatus.ACTIVE,
                confidence=0.42,
                created_at=original_created_at,
                base_branch="main",
                owner_id="dev-user",
            )
        )

        resp = client.post(
            "/api/v1/ting/tracker/import",
            json={
                "project_id": "proj-1",
                "repos": ["org/new-repo"],
                "base_branch": "dev",
            },
        )

        assert resp.status_code == 200
        assert len(client.app.state.saga_repo.sagas) == 1
        saved = client.app.state.saga_repo.sagas[0]
        assert saved.id == existing_id
        assert saved.created_at == original_created_at
        assert saved.repos == ["org/new-repo"]
        assert saved.base_branch == "dev"
        assert saved.confidence == 0.42

    def test_canonical_import_matches_legacy_shape(self, mock_tracker: MockTracker):
        legacy_client = _build_test_client(mock_tracker)
        canonical_client = _build_test_client(mock_tracker)

        legacy = legacy_client.post(
            "/api/v1/ting/tracker/import",
            json={
                "project_id": "proj-1",
                "repos": ["org/repo"],
                "base_branch": "dev",
            },
        )
        canonical = canonical_client.post(
            "/api/v1/tracker/import",
            json={
                "project_id": "proj-1",
                "repos": ["org/repo"],
                "base_branch": "dev",
            },
        )
        assert legacy.status_code == 200
        assert canonical.status_code == 200
        assert canonical.json()["tracker_id"] == legacy.json()["tracker_id"]
        assert canonical.json()["name"] == legacy.json()["name"]

    def test_assigns_saved_workflow_without_starting_dispatch(self, mock_tracker: MockTracker):
        workflow = _workflow()
        dispatch = _DispatchRecorder()
        client = _build_test_client(
            mock_tracker,
            workflow_repo=InMemoryWorkflowRepository([workflow]),
            dispatch_service=dispatch,
        )

        response = client.post(
            "/api/v1/ting/tracker/import",
            json={
                "project_id": "proj-1",
                "repos": ["org/repo"],
                "base_branch": "dev",
                "workflow_id": str(workflow.id),
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["workflow_id"] == str(workflow.id)
        assert body["workflow"] == "Security Review Flow"
        assert body["workflow_version"] == "1.2.3"
        assert body["warnings"] == []
        assert dispatch.calls == []

        saved = client.app.state.saga_repo.sagas[-1]
        assert saved.workflow_id == workflow.id
        assert saved.workflow_version == "1.2.3"
        assert saved.workflow_snapshot is not None
        assert saved.workflow_snapshot["name"] == "Security Review Flow"

    def test_start_immediately_requires_workflow(self, client: TestClient):
        response = client.post(
            "/api/v1/ting/tracker/import",
            json={
                "project_id": "proj-1",
                "repos": ["org/repo"],
                "base_branch": "dev",
                "start_immediately": True,
            },
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "start_immediately requires workflow_id"

    def test_start_immediately_requires_dispatch_service(self, mock_tracker: MockTracker):
        workflow = _workflow()
        client = _build_test_client(
            mock_tracker,
            workflow_repo=InMemoryWorkflowRepository([workflow]),
        )

        response = client.post(
            "/api/v1/ting/tracker/import",
            json={
                "project_id": "proj-1",
                "repos": ["org/repo"],
                "base_branch": "dev",
                "workflow_id": str(workflow.id),
                "start_immediately": True,
            },
        )

        assert response.status_code == 503
        assert response.json()["detail"] == "Dispatch service not configured"

    def test_start_immediately_kicks_existing_dispatcher(self, mock_tracker: MockTracker):
        workflow = _workflow()
        dispatch = _DispatchRecorder()
        client = _build_test_client(
            mock_tracker,
            workflow_repo=InMemoryWorkflowRepository([workflow]),
            dispatch_service=dispatch,
        )

        response = client.post(
            "/api/v1/ting/tracker/import",
            json={
                "project_id": "proj-1",
                "repos": ["org/repo"],
                "base_branch": "dev",
                "workflow_id": str(workflow.id),
                "start_immediately": True,
            },
        )

        assert response.status_code == 200
        assert dispatch.calls == [("dev-user", "proj-1")]
        assert response.json()["warnings"] == []

    def test_start_immediately_returns_warning_when_dispatch_fails(
        self,
        mock_tracker: MockTracker,
    ):
        workflow = _workflow()
        client = _build_test_client(
            mock_tracker,
            workflow_repo=InMemoryWorkflowRepository([workflow]),
            dispatch_service=_DispatchRecorder(should_fail=True),
        )

        response = client.post(
            "/api/v1/ting/tracker/import",
            json={
                "project_id": "proj-1",
                "repos": ["org/repo"],
                "base_branch": "dev",
                "workflow_id": str(workflow.id),
                "start_immediately": True,
            },
        )

        assert response.status_code == 200
        assert "Failed to kick off initial dispatch" in response.json()["warnings"][0]
