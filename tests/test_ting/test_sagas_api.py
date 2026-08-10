"""Tests for saga REST API endpoints."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

import ting.api.sagas as sagas_api
from niuu.domain.models import (
    InstanceKind,
    InstanceVisibility,
    Principal,
    RegisteredInstance,
)
from ting.api.dispatch import resolve_volundr_factory
from ting.api.phases import create_saga_phases_router
from ting.api.research import resolve_workflow_campaign_repo
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
from ting.api.workflows import resolve_workflow_repo
from ting.config import AuthConfig, Settings
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
    WorkflowCampaign,
    WorkflowCampaignStatus,
    WorkflowDefinition,
    WorkflowScope,
)
from ting.ports.volundr import VolundrSession
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
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


class InMemoryWorkflowCampaignRepository(WorkflowCampaignRepository):
    def __init__(self, campaigns: list[WorkflowCampaign] | None = None) -> None:
        self._campaigns = {campaign.id: campaign for campaign in campaigns or []}

    async def list_campaigns(self, *, owner_id: str) -> list[WorkflowCampaign]:
        return [campaign for campaign in self._campaigns.values() if campaign.owner_id == owner_id]

    async def list_active_campaigns(self) -> list[WorkflowCampaign]:
        return [
            campaign
            for campaign in self._campaigns.values()
            if campaign.status
            in {
                WorkflowCampaignStatus.PENDING,
                WorkflowCampaignStatus.RUNNING,
                WorkflowCampaignStatus.BLOCKED,
            }
        ]

    async def get_campaign(self, campaign_id) -> WorkflowCampaign | None:
        return self._campaigns.get(campaign_id)

    async def get_campaign_by_slug(
        self,
        slug: str,
        *,
        owner_id: str | None = None,
    ) -> WorkflowCampaign | None:
        for campaign in self._campaigns.values():
            if campaign.slug != slug:
                continue
            if owner_id is not None and campaign.owner_id != owner_id:
                continue
            return campaign
        return None

    async def save_campaign(self, campaign: WorkflowCampaign) -> WorkflowCampaign:
        self._campaigns[campaign.id] = campaign
        return campaign

    async def delete_campaign(self, campaign_id) -> bool:
        return self._campaigns.pop(campaign_id, None) is not None


def _workflow(*, executable: bool = True) -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name="Review Flow",
        description="",
        version="1.0.0",
        scope=WorkflowScope.USER,
        owner_id="dev-user",
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


def _planning_workflow() -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name="Saga Planning",
        description="Planning workflow",
        version="1.0.0",
        scope=WorkflowScope.SYSTEM,
        owner_id=None,
        graph={
            "tags": ["planning", "saga"],
            "nodes": [
                {"id": "plan-request", "kind": "trigger", "dispatchEvent": "plan.requested"},
                {
                    "id": "plan-clarify",
                    "kind": "stage",
                    "label": "Clarify brief",
                    "stageMembers": [
                        {
                            "personaId": "specification-framer",
                            "budget": 12,
                            "model": "gpt-5.5",
                            "consumesEventTypes": ["plan.requested"],
                        }
                    ],
                },
                {
                    "id": "plan-gate",
                    "kind": "gate",
                    "mode": "human_approval",
                    "approvalEvent": "plan.brief.approved",
                    "changesRequestedEvent": "plan.brief.changes_requested",
                },
            ],
            "edges": [
                {"id": "e1", "source": "plan-request", "target": "plan-clarify"},
                {"id": "e2", "source": "plan-clarify", "target": "plan-gate"},
            ],
        },
        created_at=now,
        updated_at=now,
    )


def _dev_settings() -> MagicMock:
    """Create mock settings with anonymous dev enabled for test apps."""
    s = MagicMock()
    s.auth = AuthConfig(allow_anonymous_dev=True)
    s.dispatch.flock.default_workflow_name = "Ting Run Flow + Security + Memory Curation"
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
        app.dependency_overrides[resolve_saga_repo] = MockSagaRepo
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

    def test_assigns_graph_backed_workflow_without_compiled_artifacts(
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
            name="Ting Run Flow + Security + Memory Curation",
            description="",
            version="1.0.0",
            scope=WorkflowScope.SYSTEM,
            owner_id=None,
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
        app.dependency_overrides[resolve_saga_repo] = MockSagaRepo
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

    def test_hydrates_persisted_run_tracker_link(self, saga_repo: MockSagaRepo):
        class LinkTracker(MockTracker):
            async def get_run(self, tracker_id: str) -> Run:
                run = await super().get_run(tracker_id)
                return replace(
                    run,
                    identifier="A-2",
                    url="https://linear.app/test/issue/A-2/open-task",
                )

        app = FastAPI()
        app.include_router(create_saga_phases_router())
        app.dependency_overrides[resolve_trackers] = lambda: [LinkTracker()]
        app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
        app.state.settings = _dev_settings()
        client = TestClient(app)
        saga_id = str(saga_repo.sagas[0].id)

        resp = client.get(f"/api/v1/ting/sagas/{saga_id}/phases")

        assert resp.status_code == 200
        run = resp.json()[0]["runs"][0]
        assert run["identifier"] == "A-2"
        assert run["url"] == "https://linear.app/test/issue/A-2/open-task"

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


class TestAssignRepos:
    def test_assigns_multiple_repos_with_branches(
        self,
        client: TestClient,
        saga_repo: MockSagaRepo,
    ) -> None:
        saga_id = saga_repo.sagas[0].id

        response = client.put(
            f"/api/v1/ting/sagas/{saga_id}/repos",
            json={
                "repo_refs": [
                    {"repo": "niuulabs/volundr", "branch": "dev"},
                    {"repo": "niuulabs/infrastructure", "branch": "main"},
                ],
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["repos"] == ["niuulabs/volundr", "niuulabs/infrastructure"]
        assert body["repo_refs"] == [
            {"repo": "niuulabs/volundr", "branch": "dev"},
            {"repo": "niuulabs/infrastructure", "branch": "main"},
        ]
        assert saga_repo.sagas[0].repo_branches == {
            "niuulabs/volundr": "dev",
            "niuulabs/infrastructure": "main",
        }

    def test_rejects_empty_repo_assignment(
        self,
        client: TestClient,
        saga_repo: MockSagaRepo,
    ) -> None:
        response = client.put(
            f"/api/v1/ting/sagas/{saga_repo.sagas[0].id}/repos",
            json={"repo_refs": []},
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "At least one repository is required"


class TestSpawnPlanSession:
    def test_plan_config_returns_finalize_prompt(self, mock_tracker: MockTracker) -> None:
        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
        app.dependency_overrides[resolve_saga_repo] = MockSagaRepo
        app.state.settings = _dev_settings()
        app.state.settings.planner.finalize_prompt = "Finish the structure"
        client = TestClient(app)

        response = client.get("/api/v1/ting/sagas/plan/config")

        assert response.status_code == 200
        assert response.json() == {"finalize_prompt": "Finish the structure"}

    def test_plan_request_allows_missing_repo(self) -> None:
        body = sagas_api.PlanRequest.model_validate({"spec": "Plan SDCP operator"})

        assert body.repo == ""
        assert body.base_branch == "main"

    def test_lists_every_plan_session_including_finished(self, mock_tracker: MockTracker) -> None:
        """A finished plan stays listed.

        Filtering to PENDING/RUNNING/BLOCKED removed a plan from the surface the
        moment it completed: its campaign, approved plan and slug all still
        existed, but nothing linked to them, so it was reachable only by URL.
        """
        workflow = _planning_workflow()
        now = datetime.now(UTC)
        active_plan = WorkflowCampaign(
            id=uuid4(),
            slug="plan-sdcp-operator",
            name="Plan SDCP operator",
            owner_id="dev-user",
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            workflow_name=workflow.name,
            workflow_snapshot={"graph": workflow.graph},
            session_id="plan-1",
            session_name="plan-sdcp-operator",
            status=WorkflowCampaignStatus.RUNNING,
            active_stage_id="plan-clarify",
            stage_state=[],
            metadata={"surface": "ting.plan", "spec": "Plan SDCP operator", "repo": ""},
            created_at=now,
            updated_at=now,
        )
        completed_plan = replace(
            active_plan,
            id=uuid4(),
            slug="done",
            status=WorkflowCampaignStatus.COMPLETED,
        )
        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
        app.dependency_overrides[resolve_saga_repo] = MockSagaRepo
        app.dependency_overrides[resolve_workflow_campaign_repo] = lambda: (
            InMemoryWorkflowCampaignRepository([active_plan, completed_plan])
        )
        app.state.settings = Settings(auth=AuthConfig(allow_anonymous_dev=True))
        client = TestClient(app)

        response = client.get("/api/v1/ting/sagas/plan")

        assert response.status_code == 200
        body = response.json()
        assert sorted(item["campaign_slug"] for item in body) == ["done", "plan-sdcp-operator"]
        # Status travels per item so the caller can split running from history.
        by_slug = {item["campaign_slug"]: item["status"] for item in body}
        assert by_slug["plan-sdcp-operator"] == "running"
        assert by_slug["done"] == "completed"
        body = [item for item in body if item["campaign_slug"] == "plan-sdcp-operator"]
        assert body[0]["name"] == "Plan SDCP operator"
        assert body[0]["prompt"] == "Plan SDCP operator"
        assert body[0]["repo"] == ""

    def test_cancels_active_plan_session(self, mock_tracker: MockTracker) -> None:
        workflow = _planning_workflow()
        now = datetime.now(UTC)
        active_plan = WorkflowCampaign(
            id=uuid4(),
            slug="plan-sdcp-operator",
            name="Plan SDCP operator",
            owner_id="dev-user",
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            workflow_name=workflow.name,
            workflow_snapshot={"graph": workflow.graph},
            session_id="plan-1",
            session_name="plan-sdcp-operator",
            status=WorkflowCampaignStatus.RUNNING,
            active_stage_id="plan-clarify",
            stage_state=[],
            metadata={"surface": "ting.plan", "spec": "Plan SDCP operator", "repo": ""},
            created_at=now,
            updated_at=now,
        )
        campaign_repo = InMemoryWorkflowCampaignRepository([active_plan])
        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
        app.dependency_overrides[resolve_saga_repo] = MockSagaRepo
        app.dependency_overrides[resolve_workflow_campaign_repo] = lambda: campaign_repo
        app.state.settings = Settings(auth=AuthConfig(allow_anonymous_dev=True))
        client = TestClient(app)

        response = client.delete("/api/v1/ting/sagas/plan/plan-sdcp-operator")

        assert response.status_code == 204
        stored = campaign_repo._campaigns[active_plan.id]
        assert stored.status == WorkflowCampaignStatus.FAILED
        assert stored.active_stage_id is None
        assert stored.metadata["cancelled_by"] == "ting.plan"
        assert stored.completed_at is not None

        # Cancelling ends the plan; it does not erase it. The row stays visible
        # as history, carrying the status that says how it ended.
        list_response = client.get("/api/v1/ting/sagas/plan")
        assert list_response.status_code == 200
        listed = list_response.json()
        assert [item["campaign_slug"] for item in listed] == ["plan-sdcp-operator"]
        assert listed[0]["status"] == "failed"

    def test_defaults_base_branch_to_main(
        self, mock_tracker: MockTracker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sagas_api, "_PLAN_FEEDBACK_POLL_SECONDS", 0)
        monkeypatch.setattr(sagas_api, "_PLAN_GATE_POLL_SECONDS", 0)
        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
        app.dependency_overrides[resolve_saga_repo] = MockSagaRepo
        workflow = _planning_workflow()
        workflow_repo = InMemoryWorkflowRepository([workflow])
        app.dependency_overrides[resolve_workflow_repo] = lambda: workflow_repo
        campaign_repo = InMemoryWorkflowCampaignRepository()
        app.dependency_overrides[resolve_workflow_campaign_repo] = lambda: campaign_repo

        adapter = MagicMock()
        adapter.name = "local"
        adapter.target_id = "local"
        adapter.spawn_session = AsyncMock(
            return_value=VolundrSession(
                id="plan-1",
                name="plan-1",
                status="starting",
                tracker_issue_id="workflow:ship-the-dashboard",
                chat_endpoint="/api/v1/forge/sessions/plan-1/messages",
            )
        )
        adapter.get_last_assistant_message = AsyncMock(
            return_value="""```json
{
  "name": "Dashboard Saga",
  "risks": [
    {"kind": "blast", "message": "Touches dashboard routing."}
  ],
  "phases": [
    {
      "name": "Plan",
      "runs": [
        {
          "name": "Draft dashboard plan",
          "description": "Create the implementation plan.",
          "acceptance_criteria": ["Plan is reviewable"],
          "declared_files": ["docs/plan.md"],
          "estimate_hours": 2,
          "confidence": 0.8
        }
      ]
    }
  ]
}
```"""
        )
        adapter.get_conversation = AsyncMock(return_value={"turns": []})
        adapter.send_message = AsyncMock()
        adapter.get_workflow_gates = AsyncMock(
            return_value=[
                {
                    "id": "plan-brief-gate:plan-1:1",
                    "node_id": "plan-brief-gate",
                    "status": "pending",
                }
            ]
        )
        adapter.resolve_workflow_gate = AsyncMock(return_value={"status": "resolved"})
        volundr_factory = AsyncMock()
        volundr_factory.for_principal.return_value = [adapter]
        volundr_factory.primary_for_principal.return_value = adapter
        app.dependency_overrides[resolve_volundr_factory] = lambda: volundr_factory

        settings = Settings(auth=AuthConfig(allow_anonymous_dev=True))
        settings.dispatch.default_model = "claude-opus"
        settings.planner.planner_system_prompt = ""
        app.state.settings = settings

        client = TestClient(app)
        resp = client.post(
            "/api/v1/ting/sagas/plan",
            json={
                "spec": "Ship the dashboard",
                "repo": "niuulabs/volundr",
                "workflowId": str(workflow.id),
                "connectionId": "local",
            },
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["session_id"] == "plan-1"
        assert body["chat_endpoint"] == "/api/v1/forge/sessions/plan-1/messages"
        assert body["campaign_slug"] == "plan-ship-the-dashboard"
        assert body["workflow_name"] == "Saga Planning"
        assert body["status"] == "pending"
        assert body["active_stage_id"] == "plan-clarify"
        assert body["stage_state"][0]["label"] == "Clarify brief"
        assert body["questions"][0]["id"] == "planning-feedback"
        assert "constraints" in body["questions"][0]["question"]
        spawn_request = adapter.spawn_session.await_args.args[0]
        assert spawn_request.repo == "niuulabs/volundr"
        assert spawn_request.branch == "main"
        assert spawn_request.base_branch == ""
        assert spawn_request.workload_type == "ravn_flock"
        assert spawn_request.profile is None
        assert spawn_request.workload_config["workflow"]["name"] == "Saga Planning"
        assert spawn_request.workload_config["workflow"]["graph"]["tags"] == ["planning", "saga"]
        assert spawn_request.workload_config["provenance"] == {
            "surface": "ting.plan",
            "repo": "niuulabs/volundr",
            "base_branch": "main",
            "connection_id": "local",
        }
        campaign = next(iter(campaign_repo._campaigns.values()), None)
        assert campaign is not None
        assert campaign.workflow_name == "Saga Planning"
        assert campaign.session_id == "plan-1"
        assert campaign.metadata["repo"] == "niuulabs/volundr"
        assert campaign.metadata["base_branch"] == "main"
        assert campaign.metadata["connection_id"] == "local"
        assert campaign.stage_state[0].status == "active"

        status_resp = client.get("/api/v1/ting/sagas/plan/plan-ship-the-dashboard")
        assert status_resp.status_code == 200
        status_body = status_resp.json()
        assert status_body["session_id"] == "plan-1"
        assert status_body["active_stage_id"] == "plan-brief-gate"
        assert status_body["questions"][0]["id"] == "planning-feedback"
        assert status_body["questions"][0]["hint"] == (
            "Keep this focused; the answer is sent to the active workflow run before drafting."
        )

        adapter.get_workflow_gates.return_value = [
            {
                "id": "plan-review-gate:plan-1:2",
                "node_id": "plan-review-gate",
                "status": "pending",
                "summary": "Review the bounded one-phase draft before publishing.",
            }
        ]
        review_status_resp = client.get("/api/v1/ting/sagas/plan/plan-ship-the-dashboard")
        assert review_status_resp.status_code == 200
        review_status_body = review_status_resp.json()
        assert review_status_body["active_stage_id"] == "plan-review-gate"
        assert review_status_body["questions"][0]["id"] == "draft-feedback"
        assert "draft plan review" in review_status_body["questions"][0]["question"]
        assert review_status_body["questions"][0]["hint"] == (
            "Review the bounded one-phase draft before publishing."
        )

        adapter.get_workflow_gates.return_value = []
        campaign_repo._campaigns[campaign.id] = replace(
            campaign,
            active_stage_id="plan-clarify",
            metadata={
                **campaign.metadata,
                "pending_workflow_gates": [
                    {
                        "id": "plan-review-gate:plan-1:2",
                        "node_id": "plan-review-gate",
                        "status": "pending",
                        "summary": "Persisted review gate from activity metadata.",
                    }
                ],
            },
        )
        stored_status_resp = client.get("/api/v1/ting/sagas/plan/plan-ship-the-dashboard")
        assert stored_status_resp.status_code == 200
        stored_status_body = stored_status_resp.json()
        assert stored_status_body["active_stage_id"] == "plan-review-gate"
        assert stored_status_body["questions"][0]["id"] == "draft-feedback"
        assert stored_status_body["questions"][0]["hint"] == (
            "Persisted review gate from activity metadata."
        )

        draft_resp = client.get("/api/v1/ting/sagas/plan/plan-ship-the-dashboard/draft")
        assert draft_resp.status_code == 200
        draft = draft_resp.json()
        assert draft["found"] is True
        assert draft["structure"]["phases"][0]["runs"][0]["declared_files"] == ["docs/plan.md"]
        assert draft["structure"]["risks"] == [
            {"kind": "blast", "message": "Touches dashboard routing."}
        ]
        adapter.get_last_assistant_message.assert_awaited_once()
        assert adapter.get_last_assistant_message.await_args.args == ("plan-1",)
        assert adapter.get_last_assistant_message.await_args.kwargs["auth_token"] is None

        adapter.get_last_assistant_message.reset_mock()
        outcome_structure = json.dumps(
            {
                "name": "Workflow Outcome Draft",
                "phases": [
                    {
                        "name": "Docs",
                        "runs": [
                            {
                                "name": "Document workflow outcome",
                                "description": "Record the reviewable draft path.",
                                "acceptance_criteria": ["Draft renders before approval"],
                                "declared_files": ["docs/workflow.md"],
                            }
                        ],
                    }
                ],
            }
        )
        adapter.get_conversation.return_value = {
            "turns": [
                {
                    "role": "assistant",
                    "content": f"""---outcome---
verdict: drafted
summary: Drafted one docs-only phase.
structure: '{outcome_structure}'
---end---""",
                },
                {
                    "role": "assistant",
                    "content": """---outcome---
verdict: ready_for_gate
summary: Plan is bounded and ready for human review.
---end---""",
                },
            ],
        }
        outcome_draft_resp = client.get("/api/v1/ting/sagas/plan/plan-ship-the-dashboard/draft")
        assert outcome_draft_resp.status_code == 200
        outcome_draft = outcome_draft_resp.json()
        assert outcome_draft["found"] is True
        assert outcome_draft["structure"]["name"] == "Workflow Outcome Draft"
        assert outcome_draft["structure"]["phases"][0]["runs"][0]["declared_files"] == [
            "docs/workflow.md"
        ]
        adapter.get_last_assistant_message.assert_not_awaited()

        adapter.get_conversation.return_value = {"turns": []}
        adapter.get_last_assistant_message.reset_mock()
        adapter.get_last_assistant_message.side_effect = httpx.HTTPStatusError(
            "session gateway not ready",
            request=httpx.Request("GET", "https://volundr.example/sessions/plan-1/conversation"),
            response=httpx.Response(502),
        )
        transient_draft_resp = client.get("/api/v1/ting/sagas/plan/plan-ship-the-dashboard/draft")
        assert transient_draft_resp.status_code == 200
        assert transient_draft_resp.json() == {"found": False, "structure": None}
        adapter.get_last_assistant_message.side_effect = None

        adapter.get_workflow_gates.return_value = [
            {
                "id": "plan-brief-gate:plan-1:1",
                "node_id": "plan-brief-gate",
                "status": "pending",
            }
        ]
        feedback_resp = client.post(
            "/api/v1/ting/sagas/plan/plan-ship-the-dashboard/feedback",
            json={"content": "Keep the first pass to one saga."},
        )
        assert feedback_resp.status_code == 200
        assert feedback_resp.json() == {"status": "sent", "session_id": "plan-1"}
        adapter.send_message.assert_awaited_once()
        assert adapter.send_message.await_args.args[:2] == (
            "plan-1",
            "Keep the first pass to one saga.",
        )
        adapter.resolve_workflow_gate.assert_awaited_once_with(
            "plan-1",
            "plan-brief-gate:plan-1:1",
            "APPROVE",
            notes="Keep the first pass to one saga.",
            source="ting.plan",
            auth_token=None,
            principal=adapter.send_message.await_args.kwargs["principal"],
        )

        adapter.send_message.reset_mock()
        adapter.resolve_workflow_gate.reset_mock()
        adapter.get_workflow_gates.reset_mock()
        adapter.send_message.side_effect = [
            httpx.HTTPStatusError(
                "session gateway not ready",
                request=httpx.Request("POST", "https://volundr.example/sessions/plan-1/messages"),
                response=httpx.Response(503),
            ),
            None,
        ]
        adapter.get_workflow_gates.side_effect = [
            httpx.HTTPStatusError(
                "session gate endpoint not ready",
                request=httpx.Request(
                    "GET", "https://volundr.example/sessions/plan-1/workflow/gates"
                ),
                response=httpx.Response(502),
            ),
            [
                {
                    "id": "plan-brief-gate:plan-1:retry",
                    "node_id": "plan-brief-gate",
                    "status": "pending",
                }
            ],
        ]
        transient_feedback_resp = client.post(
            "/api/v1/ting/sagas/plan/plan-ship-the-dashboard/feedback",
            json={"content": "Retry once the session gateway is ready."},
        )
        assert transient_feedback_resp.status_code == 200
        assert adapter.send_message.await_count == 2
        assert adapter.get_workflow_gates.await_count == 2
        adapter.resolve_workflow_gate.assert_awaited_once_with(
            "plan-1",
            "plan-brief-gate:plan-1:retry",
            "APPROVE",
            notes="Retry once the session gateway is ready.",
            source="ting.plan",
            auth_token=None,
            principal=adapter.send_message.await_args.kwargs["principal"],
        )
        adapter.send_message.side_effect = None
        adapter.get_workflow_gates.side_effect = None

        adapter.send_message.reset_mock()
        adapter.resolve_workflow_gate.reset_mock()
        adapter.get_workflow_gates.return_value = [
            {
                "id": "plan-review-gate:plan-1:2",
                "node_id": "plan-review-gate",
                "status": "pending",
            }
        ]
        review_feedback_resp = client.post(
            "/api/v1/ting/sagas/plan/plan-ship-the-dashboard/feedback",
            json={"content": "Keep this as one phase.", "decision": "changes_requested"},
        )
        assert review_feedback_resp.status_code == 200
        adapter.resolve_workflow_gate.assert_awaited_once_with(
            "plan-1",
            "plan-review-gate:plan-1:2",
            "CHANGES_REQUESTED",
            notes="Keep this as one phase.",
            source="ting.plan",
            auth_token=None,
            principal=adapter.send_message.await_args.kwargs["principal"],
        )
        adapter.get_last_assistant_message.reset_mock()
        adapter.get_workflow_gates.return_value = []
        stale_draft_resp = client.get("/api/v1/ting/sagas/plan/plan-ship-the-dashboard/draft")
        assert stale_draft_resp.status_code == 200
        assert stale_draft_resp.json() == {"found": False, "structure": None}
        adapter.get_last_assistant_message.assert_not_awaited()

        adapter.send_message.reset_mock()
        adapter.resolve_workflow_gate.reset_mock()
        adapter.get_workflow_gates.return_value = [
            {
                "id": "plan-review-gate:plan-1:3",
                "node_id": "plan-review-gate",
                "status": "pending",
            }
        ]
        review_approve_resp = client.post(
            "/api/v1/ting/sagas/plan/plan-ship-the-dashboard/feedback",
            json={"content": "Approved; show this in Ting.", "decision": "approve"},
        )
        assert review_approve_resp.status_code == 200
        adapter.resolve_workflow_gate.assert_awaited_once_with(
            "plan-1",
            "plan-review-gate:plan-1:3",
            "APPROVE",
            notes="Approved; show this in Ting.",
            source="ting.plan",
            auth_token=None,
            principal=adapter.send_message.await_args.kwargs["principal"],
        )

        adapter.send_message.reset_mock()
        adapter.resolve_workflow_gate.reset_mock()
        adapter.get_workflow_gates.reset_mock()
        adapter.send_message.side_effect = httpx.HTTPStatusError(
            "session gateway already closed",
            request=httpx.Request("POST", "https://volundr.example/sessions/plan-1/messages"),
            response=httpx.Response(502),
        )
        stale_final_approve_resp = client.post(
            "/api/v1/ting/sagas/plan/plan-ship-the-dashboard/feedback",
            json={"content": "Approved in Ting Plan.", "decision": "approve"},
        )
        assert stale_final_approve_resp.status_code == 200
        assert stale_final_approve_resp.json() == {"status": "sent", "session_id": "plan-1"}
        adapter.resolve_workflow_gate.assert_not_awaited()


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
        app.dependency_overrides[resolve_git] = AsyncMock
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
        app.dependency_overrides[resolve_git] = AsyncMock
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

    def test_rejects_duplicate_slug(self, mock_tracker: MockTracker) -> None:
        repo = MockSagaRepo()
        repo.sagas.append(
            Saga(
                id=uuid4(),
                tracker_id="existing",
                tracker_type="mock",
                slug="proof-saga",
                name="Existing",
                repos=["org/repo"],
                feature_branch="feat/proof-saga",
                status=SagaStatus.ACTIVE,
                confidence=0.0,
                created_at=datetime.now(UTC),
                base_branch="dev",
                owner_id="dev-user",
            )
        )
        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
        app.dependency_overrides[resolve_saga_repo] = lambda: repo
        app.dependency_overrides[resolve_git] = AsyncMock
        app.state.settings = _dev_settings()
        client = TestClient(app)

        response = client.post(
            "/api/v1/ting/sagas/commit",
            json={
                "name": "Proof Saga",
                "slug": "proof-saga",
                "repos": ["org/repo"],
                "base_branch": "dev",
                "phases": [{"name": "Phase 1", "runs": [{"name": "Run 1"}]}],
            },
        )

        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]

    def test_rejects_empty_phases_and_missing_tracker(self) -> None:
        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_saga_repo] = MockSagaRepo
        app.dependency_overrides[resolve_git] = AsyncMock
        app.state.settings = _dev_settings()
        client = TestClient(app)
        app.dependency_overrides[resolve_trackers] = lambda: [MockTracker()]

        no_phases = client.post(
            "/api/v1/ting/sagas/commit",
            json={
                "name": "Proof Saga",
                "slug": "proof-saga",
                "repos": ["org/repo"],
                "base_branch": "dev",
                "phases": [],
            },
        )
        app.dependency_overrides[resolve_trackers] = lambda: []
        no_tracker = client.post(
            "/api/v1/ting/sagas/commit",
            json={
                "name": "Proof Saga",
                "slug": "proof-saga-2",
                "repos": ["org/repo"],
                "base_branch": "dev",
                "phases": [{"name": "Phase 1", "runs": [{"name": "Run 1"}]}],
            },
        )

        assert no_phases.status_code == 422
        assert no_phases.json()["detail"] == "At least one phase is required"
        assert no_tracker.status_code == 503
        assert no_tracker.json()["detail"] == "No tracker configured"

    def test_returns_502_when_tracker_create_saga_fails(self, mock_tracker: MockTracker) -> None:
        class FailingTracker(MockTracker):
            async def create_saga(self, saga: Saga, *, description: str = "") -> str:
                raise RuntimeError("tracker unavailable")

        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [FailingTracker()]
        app.dependency_overrides[resolve_saga_repo] = MockSagaRepo
        app.dependency_overrides[resolve_git] = AsyncMock
        app.state.settings = _dev_settings()
        client = TestClient(app)

        response = client.post(
            "/api/v1/ting/sagas/commit",
            json={
                "name": "Proof Saga",
                "slug": "proof-saga-fail",
                "repos": ["org/repo"],
                "base_branch": "dev",
                "phases": [{"name": "Phase 1", "runs": [{"name": "Run 1"}]}],
            },
        )

        assert response.status_code == 502
        assert "Failed to create project in tracker" in response.json()["detail"]


class TestAssignTarget:
    class _StubInstanceRegistry:
        def __init__(self, instance: object | None) -> None:
            self.instance = instance

        async def get_volundr_target(self, principal: Principal, instance_id: str) -> object | None:
            return self.instance

    def test_assigns_visible_target_and_returns_instance_name(
        self,
        mock_tracker: MockTracker,
        saga_repo: MockSagaRepo,
    ) -> None:
        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
        app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
        app.state.settings = _dev_settings()
        app.state.instance_registry = self._StubInstanceRegistry(
            RegisteredInstance(
                id="volundr-1",
                kind=InstanceKind.VOLUNDR,
                slug="volundr-1",
                name="Volundr One",
                base_url="http://volundr:8000",
                visibility=InstanceVisibility.SYSTEM,
                owner_id=None,
                tenant_id=None,
                enabled=True,
                is_default=False,
                config={},
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        client = TestClient(app)
        saga_id = str(saga_repo.sagas[0].id)

        response = client.put(
            f"/api/v1/ting/sagas/{saga_id}/target",
            json={"instance_id": "volundr-1"},
        )

        assert response.status_code == 200
        assert response.json()["instance_id"] == "volundr-1"
        assert response.json()["instance_name"] == "Volundr One"

    def test_assigns_tag_target_without_instance_registry(
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
        saga_id = str(saga_repo.sagas[0].id)

        response = client.put(
            f"/api/v1/ting/sagas/{saga_id}/target",
            json={"target_tags": ["gpu", " valhalla ", ""], "target_match": "any"},
        )

        assert response.status_code == 200
        assert response.json()["instance_id"] is None
        assert response.json()["target_tags"] == ["gpu", "valhalla"]
        assert response.json()["target_match"] == "any"

    def test_assign_target_requires_registry_and_valid_instance(
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
        saga_id = str(saga_repo.sagas[0].id)

        missing_registry = client.put(
            f"/api/v1/ting/sagas/{saga_id}/target",
            json={"instance_id": "volundr-1"},
        )

        app.state.instance_registry = self._StubInstanceRegistry(None)
        missing_target = client.put(
            f"/api/v1/ting/sagas/{saga_id}/target",
            json={"instance_id": "volundr-1"},
        )

        assert missing_registry.status_code == 503
        assert missing_registry.json()["detail"] == "Instance registry not configured"
        assert missing_target.status_code == 404
        assert missing_target.json()["detail"] == "Target not found: volundr-1"


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
