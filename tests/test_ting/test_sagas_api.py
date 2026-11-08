"""Tests for saga REST API endpoints."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from identity.adapters.authorization import AllowAllAuthorizationAdapter
from niuu.domain.models import (
    InstanceKind,
    InstanceVisibility,
    Principal,
    RegisteredInstance,
)
from ting.api import sagas as sagas_api
from ting.api.dispatch import resolve_volundr_factory
from ting.api.phases import create_saga_phases_router
from ting.api.research import resolve_workflow_campaign_repo
from ting.api.sagas import (
    _build_phase_summary,
    _can_use_workflow,
    _find_project,
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
from ting.domain.workflow_steps import (
    EVENT_GATE_RESOLVED,
    EVENT_RUN_LAUNCHED,
    GateDecision,
    GateRefusedError,
    RunEvent,
)
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_repository import WorkflowRepository
from ting.system_workflows import load_system_workflows

from .fake_workflow_runs import FakeWorkflowRuns
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

    async def list_workflow_versions(self, workflow_id):
        return []

    async def get_workflow_version(self, workflow_id, *, version=None, document_revision=None):
        workflow = await self.get_workflow(workflow_id)
        return workflow if workflow is not None and workflow.version == version else None

    async def save_workflow_version(self, workflow, **kwargs):
        raise NotImplementedError

    async def delete_workflow(self, workflow_id) -> bool:
        return self._workflows.pop(workflow_id, None) is not None

    async def has_recorded_version_history(self, workflow_id) -> bool:
        return True

    async def adopt_legacy_bundled(self, seed):
        return await self.save_workflow(seed)

    async def reclassify_orphaned_bundled_as_authored(self, workflow_id):
        return await self.get_workflow(workflow_id)


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


def _plan_campaign() -> WorkflowCampaign:
    workflow = _planning_workflow()
    now = datetime.now(UTC)
    return WorkflowCampaign(
        id=uuid4(),
        slug="plan-sdcp-operator",
        name="Plan SDCP operator",
        owner_id="dev-user",
        workflow_id=workflow.id,
        workflow_version=workflow.version,
        workflow_name=workflow.name,
        workflow_snapshot={"graph": workflow.graph},
        session_id="",
        session_name="",
        status=WorkflowCampaignStatus.RUNNING,
        active_stage_id="plan-clarify",
        stage_state=[],
        metadata={"surface": "ting.plan", "spec": "Plan SDCP operator", "repo": ""},
        created_at=now,
        updated_at=now,
    )


def _run_event(
    campaign: WorkflowCampaign,
    seq: int,
    *,
    event_type: str = EVENT_RUN_LAUNCHED,
    payload: dict | None = None,
) -> RunEvent:
    return RunEvent(
        run_id=campaign.id,
        seq=seq,
        event_type=event_type,
        generation=0,
        created_at=datetime.now(UTC),
        payload=payload or {},
    )


def _gate(node_id: str, *, summary: str = "") -> dict:
    return {"id": node_id, "node_id": node_id, "status": "pending", "summary": summary}


def _plan_client(
    mock_tracker: MockTracker,
    campaign_repo: InMemoryWorkflowCampaignRepository,
    runs: FakeWorkflowRuns,
    *,
    workflow_repo: InMemoryWorkflowRepository | None = None,
    volundr_factory: object | None = None,
) -> TestClient:
    app = FastAPI()
    app.state.authorization = AllowAllAuthorizationAdapter()
    app.include_router(create_sagas_router())
    app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
    app.dependency_overrides[resolve_saga_repo] = MockSagaRepo
    app.dependency_overrides[resolve_workflow_campaign_repo] = lambda: campaign_repo
    if workflow_repo is not None:
        app.dependency_overrides[resolve_workflow_repo] = lambda: workflow_repo
    if volundr_factory is not None:
        app.dependency_overrides[resolve_volundr_factory] = lambda: volundr_factory
    app.state.settings = Settings(auth=AuthConfig(allow_anonymous_dev=True))
    app.state.workflow_runs = runs
    return TestClient(app)


def _dev_settings() -> MagicMock:
    """Create mock settings with anonymous dev enabled for test apps."""
    s = MagicMock()
    s.auth = AuthConfig(allow_anonymous_dev=True)
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
    app.state.authorization = AllowAllAuthorizationAdapter()
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
        assert saga["created_at"]
        assert saga["phase_summary"] == {"total": 1, "completed": 0}

    def test_empty_when_no_sagas(self, mock_tracker: MockTracker):
        app = FastAPI()
        app.state.authorization = AllowAllAuthorizationAdapter()
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
                created_at=datetime.now(UTC),
                base_branch="dev",
                owner_id="dev-user",
            )
        )

        app = FastAPI()
        app.state.authorization = AllowAllAuthorizationAdapter()
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
        app.state.authorization = AllowAllAuthorizationAdapter()
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


class TestBoardWorkflows:
    @staticmethod
    def _client(
        mock_tracker: MockTracker,
        saga_repo: MockSagaRepo,
        workflows: list[WorkflowDefinition],
        campaigns: list[WorkflowCampaign] | None = None,
    ) -> TestClient:
        app = FastAPI()
        app.state.authorization = AllowAllAuthorizationAdapter()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
        app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
        campaign_repo = MagicMock()
        campaign_repo.list_campaigns = AsyncMock(return_value=campaigns or [])
        campaign_repo.list_ticket_campaigns = partial(
            WorkflowCampaignRepository.list_ticket_campaigns, campaign_repo
        )
        app.dependency_overrides[resolve_workflow_campaign_repo] = lambda: campaign_repo
        app.state.settings = _dev_settings()
        app.state.workflow_repo = InMemoryWorkflowRepository(workflows)
        return TestClient(app)

    @staticmethod
    def _bundled(*names: str) -> list[WorkflowDefinition]:
        by_name = {workflow.name: workflow for workflow in load_system_workflows()}
        return [by_name[name] for name in names]

    def test_a_project_without_a_repository_refuses_a_workflow_that_needs_one(
        self, mock_tracker: MockTracker, saga_repo: MockSagaRepo
    ):
        [delivery] = self._bundled("Reviewed Delivery Flow")
        saga_repo.sagas[0] = replace(saga_repo.sagas[0], repos=[], repo_branches={})
        client = self._client(mock_tracker, saga_repo, [delivery])

        response = client.put(
            f"/api/v1/ting/sagas/{saga_repo.sagas[0].id}/board-workflows",
            json={"workflow_ids": [str(delivery.id)]},
        )

        assert response.status_code == 422
        assert "needs a repository" in response.json()["detail"]

    def test_a_project_without_a_repository_takes_a_workflow_that_needs_none(
        self, mock_tracker: MockTracker, saga_repo: MockSagaRepo
    ):
        [work] = self._bundled("Ticket Work Flow")
        saga_repo.sagas[0] = replace(saga_repo.sagas[0], repos=[], repo_branches={})
        client = self._client(mock_tracker, saga_repo, [work])

        response = client.put(
            f"/api/v1/ting/sagas/{saga_repo.sagas[0].id}/board-workflows",
            json={"workflow_ids": [str(work.id)]},
        )

        assert response.status_code == 200
        assert saga_repo.sagas[0].board_workflows[0].workflow_id == work.id

    def test_assigning_a_workflow_that_needs_a_repository_is_refused_without_one(
        self, mock_tracker: MockTracker, saga_repo: MockSagaRepo
    ):
        [delivery] = self._bundled("Reviewed Delivery Flow")
        saga_repo.sagas[0] = replace(saga_repo.sagas[0], repos=[], repo_branches={})
        client = self._client(mock_tracker, saga_repo, [delivery])

        response = client.put(
            f"/api/v1/ting/sagas/{saga_repo.sagas[0].id}/workflow",
            json={"workflow_id": str(delivery.id)},
        )

        assert response.status_code == 422
        assert "needs a repository" in response.json()["detail"]

    def test_orders_and_pins_the_workflows(
        self, mock_tracker: MockTracker, saga_repo: MockSagaRepo
    ):
        planning, delivery = self._bundled("Ticket Planning Flow", "Reviewed Delivery Flow")
        client = self._client(mock_tracker, saga_repo, [planning, delivery])
        saga = saga_repo.sagas[0]

        response = client.put(
            f"/api/v1/ting/sagas/{saga.id}/board-workflows",
            json={"workflow_ids": [str(planning.id), str(delivery.id)]},
        )

        assert response.status_code == 200
        assert [item["id"] for item in response.json()["board_workflows"]] == [
            str(planning.id),
            str(delivery.id),
        ]
        pins = saga_repo.sagas[0].board_workflows
        assert [pin.workflow_id for pin in pins] == [planning.id, delivery.id]
        assert pins[0].workflow_snapshot["name"] == "Ticket Planning Flow"
        detail = client.get(f"/api/v1/ting/sagas/{saga.id}").json()
        assert detail["board_workflows"][0] == {
            "id": str(planning.id),
            "name": "Ticket Planning Flow",
        }

    def test_reordering_keeps_each_workflows_pin(
        self, mock_tracker: MockTracker, saga_repo: MockSagaRepo
    ):
        planning, delivery = self._bundled("Ticket Planning Flow", "Reviewed Delivery Flow")
        client = self._client(mock_tracker, saga_repo, [planning, delivery])
        saga = saga_repo.sagas[0]
        url = f"/api/v1/ting/sagas/{saga.id}/board-workflows"
        client.put(url, json={"workflow_ids": [str(planning.id), str(delivery.id)]})
        pinned = saga_repo.sagas[0].board_workflows[0]

        client.app.state.workflow_repo = InMemoryWorkflowRepository([])
        response = client.put(url, json={"workflow_ids": [str(delivery.id), str(planning.id)]})

        assert response.status_code == 200
        assert saga_repo.sagas[0].board_workflows[1] == pinned

    def test_a_workflow_without_columns_is_refused(
        self, mock_tracker: MockTracker, saga_repo: MockSagaRepo
    ):
        [planning] = self._bundled("Saga Planning")
        client = self._client(mock_tracker, saga_repo, [planning])

        response = client.put(
            f"/api/v1/ting/sagas/{saga_repo.sagas[0].id}/board-workflows",
            json={"workflow_ids": [str(planning.id)]},
        )

        assert response.status_code == 422
        assert "declares no board columns" in response.json()["detail"]

    def test_a_private_workflow_is_refused(
        self, mock_tracker: MockTracker, saga_repo: MockSagaRepo
    ):
        [delivery] = self._bundled("Reviewed Delivery Flow")
        private = replace(
            delivery,
            scope=WorkflowScope.USER,
            owner_id="dev-user",
            read_only=False,
        )
        client = self._client(mock_tracker, saga_repo, [private])

        response = client.put(
            f"/api/v1/ting/sagas/{saga_repo.sagas[0].id}/board-workflows",
            json={"workflow_ids": [str(private.id)]},
        )

        assert response.status_code == 422
        assert "only start system workflows" in response.json()["detail"]

    def test_a_workflow_listed_twice_is_refused(
        self, mock_tracker: MockTracker, saga_repo: MockSagaRepo
    ):
        [delivery] = self._bundled("Reviewed Delivery Flow")
        client = self._client(mock_tracker, saga_repo, [delivery])

        response = client.put(
            f"/api/v1/ting/sagas/{saga_repo.sagas[0].id}/board-workflows",
            json={"workflow_ids": [str(delivery.id), str(delivery.id)]},
        )

        assert response.status_code == 422

    def test_a_board_needing_a_widget_not_installed_is_refused(
        self, mock_tracker: MockTracker, saga_repo: MockSagaRepo
    ):
        planning = self._bundled("Ticket Planning Flow")[0]
        graph = deepcopy(planning.graph)
        column = next(item for item in graph["board"]["columns"] if item.get("gate"))
        column["panels"] = [{"widget": "acme-print/print-quality", "title": "Quality"}]
        client = self._client(mock_tracker, saga_repo, [replace(planning, graph=graph)])

        response = client.put(
            f"/api/v1/ting/sagas/{saga_repo.sagas[0].id}/board-workflows",
            json={"workflow_ids": [str(planning.id)]},
        )

        assert response.status_code == 422
        assert "acme-print/print-quality" in response.json()["detail"]

    def test_a_workflow_with_a_run_stays_on_the_board(
        self, mock_tracker: MockTracker, saga_repo: MockSagaRepo
    ):
        planning, delivery = self._bundled("Ticket Planning Flow", "Reviewed Delivery Flow")
        saga = saga_repo.sagas[0]
        running = SimpleNamespace(
            workflow_id=planning.id,
            workflow_name=planning.name,
            status=WorkflowCampaignStatus.RUNNING,
            metadata={"ticket": {"sagaId": str(saga.id), "issueId": "issue-1"}},
            created_at=datetime.now(UTC),
        )
        client = self._client(mock_tracker, saga_repo, [planning, delivery], [running])

        response = client.put(
            f"/api/v1/ting/sagas/{saga.id}/board-workflows",
            json={"workflow_ids": [str(delivery.id)]},
        )

        assert response.status_code == 409
        assert "Ticket Planning Flow" in response.json()["detail"]
        assert saga_repo.sagas[0].board_workflows == ()

    def test_an_unknown_project_is_not_found(
        self, mock_tracker: MockTracker, saga_repo: MockSagaRepo
    ):
        client = self._client(mock_tracker, saga_repo, [])

        response = client.put(
            f"/api/v1/ting/sagas/{uuid4()}/board-workflows",
            json={"workflow_ids": []},
        )

        assert response.status_code == 404


class TestAssignWorkflow:
    def test_assigns_graph_backed_workflow(
        self, mock_tracker: MockTracker, saga_repo: MockSagaRepo
    ):
        workflow = _workflow()
        app = FastAPI()
        app.state.authorization = AllowAllAuthorizationAdapter()
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
        app.state.authorization = AllowAllAuthorizationAdapter()
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

    def test_rejects_invalid_saga_id_for_workflow_assignment(
        self,
        mock_tracker: MockTracker,
        saga_repo: MockSagaRepo,
    ) -> None:
        workflow = _workflow()
        app = FastAPI()
        app.state.authorization = AllowAllAuthorizationAdapter()
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
        app.state.authorization = AllowAllAuthorizationAdapter()
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
        app.state.authorization = AllowAllAuthorizationAdapter()
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
        app.state.authorization = AllowAllAuthorizationAdapter()
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
        app.state.authorization = AllowAllAuthorizationAdapter()
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
        app.state.authorization = AllowAllAuthorizationAdapter()
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
        app.state.authorization = AllowAllAuthorizationAdapter()
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
        saga_repo.sagas[0] = replace(
            saga_repo.sagas[0],
            workflow_id=uuid4(),
            workflow_snapshot={"name": "Delivery", "graph": {}},
        )

        response = client.put(
            f"/api/v1/ting/sagas/{saga_repo.sagas[0].id}/repos",
            json={"repo_refs": []},
        )

        assert response.status_code == 422
        assert "Delivery needs a repository" in response.json()["detail"]
        assert saga_repo.sagas[0].repos

    def test_clears_the_repositories_of_a_project_that_needs_none(
        self,
        client: TestClient,
        saga_repo: MockSagaRepo,
    ) -> None:
        response = client.put(
            f"/api/v1/ting/sagas/{saga_repo.sagas[0].id}/repos",
            json={"repo_refs": []},
        )

        assert response.status_code == 200
        assert response.json()["repos"] == []
        assert saga_repo.sagas[0].repos == []


class TestSpawnPlanSession:
    def test_plan_config_returns_finalize_prompt(self, mock_tracker: MockTracker) -> None:
        app = FastAPI()
        app.state.authorization = AllowAllAuthorizationAdapter()
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
        app.state.authorization = AllowAllAuthorizationAdapter()
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
        campaign = _plan_campaign()
        campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
        runs = FakeWorkflowRuns(events_by_run={campaign.id: [_run_event(campaign, 1)]})
        client = _plan_client(mock_tracker, campaign_repo, runs)

        response = client.delete("/api/v1/ting/sagas/plan/plan-sdcp-operator")

        assert response.status_code == 204
        [(canceled, reason)] = runs.canceled
        assert canceled.id == campaign.id
        assert "canceled in Ting Plan" in reason
        assert campaign_repo._campaigns[campaign.id].metadata["cancelled_by"] == "ting.plan"

    def test_cancelling_a_plan_that_never_started_fails_it(self, mock_tracker: MockTracker) -> None:
        campaign = _plan_campaign()
        campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
        runs = FakeWorkflowRuns()
        client = _plan_client(mock_tracker, campaign_repo, runs)

        response = client.delete("/api/v1/ting/sagas/plan/plan-sdcp-operator")

        assert response.status_code == 204
        assert runs.canceled == []
        stored = campaign_repo._campaigns[campaign.id]
        assert stored.status == WorkflowCampaignStatus.FAILED
        assert stored.metadata["failure_error"] == "canceled before it started"

    def test_cancelling_a_finished_plan_changes_nothing(self, mock_tracker: MockTracker) -> None:
        campaign = _plan_campaign()
        campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
        runs = FakeWorkflowRuns(
            events_by_run={
                campaign.id: [
                    _run_event(campaign, 1),
                    _run_event(campaign, 2, event_type="engine.run.completed"),
                ]
            }
        )
        client = _plan_client(mock_tracker, campaign_repo, runs)

        response = client.delete("/api/v1/ting/sagas/plan/plan-sdcp-operator")

        assert response.status_code == 204
        assert runs.canceled == []
        assert campaign_repo._campaigns[campaign.id] == campaign

    def test_plan_launch_starts_a_run_on_the_step_engine(self, mock_tracker: MockTracker) -> None:
        workflow = _planning_workflow()
        campaign_repo = InMemoryWorkflowCampaignRepository()
        runs = FakeWorkflowRuns()
        adapter = MagicMock()
        adapter.name = "local"
        adapter.target_id = "local"
        adapter.spawn_session = AsyncMock()
        volundr_factory = AsyncMock()
        volundr_factory.for_principal.return_value = [adapter]
        client = _plan_client(
            mock_tracker,
            campaign_repo,
            runs,
            workflow_repo=InMemoryWorkflowRepository([workflow]),
            volundr_factory=volundr_factory,
        )
        client.app.state.settings.dispatch.default_model = "claude-opus"
        client.app.state.settings.planner.planner_system_prompt = ""

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
        assert body["session_id"] == ""
        assert body["chat_endpoint"] is None
        assert body["campaign_slug"] == "plan-ship-the-dashboard"
        assert body["workflow_name"] == "Saga Planning"
        assert body["status"] == "pending"
        assert body["active_stage_id"] == "plan-clarify"
        assert body["stage_state"][0]["label"] == "Clarify brief"
        assert body["questions"][0]["id"] == "planning-feedback"
        adapter.spawn_session.assert_not_awaited()
        [(campaign, payload)] = runs.launched
        assert campaign.workflow_name == "Saga Planning"
        assert campaign.session_id == ""
        assert campaign.connection_id == "local"
        assert campaign.metadata["repo"] == "niuulabs/volundr"
        assert campaign.metadata["base_branch"] == "main"
        assert campaign.metadata["connection_id"] == "local"
        assert campaign.metadata["model"] == "claude-opus"
        assert campaign.stage_state[0].status == "active"
        assert payload["repo"] == "niuulabs/volundr"
        assert payload["branch"] == "main"
        assert "Ship the dashboard" in payload["prompt"]
        assert campaign_repo._campaigns[campaign.id].slug == "plan-ship-the-dashboard"

    def test_plan_launch_that_cannot_start_is_a_bad_gateway_and_fails_its_campaign(
        self, mock_tracker: MockTracker
    ) -> None:
        workflow = _planning_workflow()
        campaign_repo = InMemoryWorkflowCampaignRepository()
        runs = FakeWorkflowRuns(launch_error=RuntimeError("no capacity"))
        adapter = MagicMock()
        adapter.name = "local"
        adapter.target_id = "local"
        volundr_factory = AsyncMock()
        volundr_factory.for_principal.return_value = [adapter]
        client = _plan_client(
            mock_tracker,
            campaign_repo,
            runs,
            workflow_repo=InMemoryWorkflowRepository([workflow]),
            volundr_factory=volundr_factory,
        )

        resp = client.post(
            "/api/v1/ting/sagas/plan",
            json={"spec": "Ship the dashboard", "workflowId": str(workflow.id)},
        )

        assert resp.status_code == 502
        assert "no capacity" in resp.json()["detail"]
        [failed] = campaign_repo._campaigns.values()
        assert failed.status == WorkflowCampaignStatus.FAILED
        assert failed.metadata["failure_error"] == "no capacity"

    def test_plan_status_reads_the_open_gate_from_the_ledger(
        self, mock_tracker: MockTracker
    ) -> None:
        campaign = _plan_campaign()
        runs = FakeWorkflowRuns(
            gates_by_run={campaign.id: [_gate("plan-brief-gate", summary="Framed brief.")]}
        )
        client = _plan_client(mock_tracker, InMemoryWorkflowCampaignRepository([campaign]), runs)

        body = client.get("/api/v1/ting/sagas/plan/plan-sdcp-operator").json()

        assert body["session_id"] == ""
        assert body["active_stage_id"] == "plan-brief-gate"
        assert body["questions"][0]["id"] == "planning-feedback"
        assert body["questions"][0]["hint"] == "Framed brief."

        runs.gates_by_run[campaign.id] = [
            _gate("plan-review-gate", summary="Review the bounded one-phase draft.")
        ]
        review = client.get("/api/v1/ting/sagas/plan/plan-sdcp-operator").json()
        assert review["active_stage_id"] == "plan-review-gate"
        assert review["questions"][0]["id"] == "draft-feedback"
        assert review["questions"][0]["hint"] == "Review the bounded one-phase draft."

    def test_plan_draft_is_read_from_the_step_outcomes_of_the_run(
        self, mock_tracker: MockTracker
    ) -> None:
        campaign = _plan_campaign()
        structure = {
            "name": "Dashboard Saga",
            "risks": [{"kind": "blast", "message": "Touches dashboard routing."}],
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
                            "confidence": 0.8,
                        }
                    ],
                }
            ],
        }
        runs = FakeWorkflowRuns(
            events_by_run={
                campaign.id: [
                    _run_event(campaign, 1),
                    _run_event(
                        campaign,
                        2,
                        event_type="plan.breakdown.drafted",
                        payload={
                            "node_id": "plan-breakdown",
                            "verdict": "drafted",
                            "outcome": {"structure": structure},
                        },
                    ),
                    _run_event(
                        campaign,
                        3,
                        event_type="plan.breakdown.ready_for_gate",
                        payload={
                            "node_id": "plan-review",
                            "verdict": "ready_for_gate",
                            "outcome": {"summary": "Bounded and ready for human review."},
                        },
                    ),
                ]
            }
        )
        client = _plan_client(mock_tracker, InMemoryWorkflowCampaignRepository([campaign]), runs)

        draft = client.get("/api/v1/ting/sagas/plan/plan-sdcp-operator/draft").json()

        assert draft["found"] is True
        assert draft["structure"]["name"] == "Dashboard Saga"
        assert draft["structure"]["phases"][0]["runs"][0]["declared_files"] == ["docs/plan.md"]
        assert draft["structure"]["risks"] == [
            {"kind": "blast", "message": "Touches dashboard routing."}
        ]

    def test_plan_draft_in_a_note_is_found_and_a_change_request_makes_it_stale(
        self, mock_tracker: MockTracker
    ) -> None:
        campaign = _plan_campaign()
        structure = json.dumps(
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
        drafted = _run_event(
            campaign,
            2,
            event_type="plan.breakdown.drafted",
            payload={
                "node_id": "plan-breakdown",
                "verdict": "drafted",
                "note": f"```json\n{structure}\n```",
                "outcome": {},
            },
        )
        runs = FakeWorkflowRuns(events_by_run={campaign.id: [_run_event(campaign, 1), drafted]})
        client = _plan_client(mock_tracker, InMemoryWorkflowCampaignRepository([campaign]), runs)

        found = client.get("/api/v1/ting/sagas/plan/plan-sdcp-operator/draft").json()
        assert found["structure"]["name"] == "Workflow Outcome Draft"

        runs.events_by_run[campaign.id].append(
            _run_event(
                campaign,
                3,
                event_type=EVENT_GATE_RESOLVED,
                payload={
                    "node_id": "plan-review-gate",
                    "verdict": GateDecision.REQUEST_CHANGES.value,
                },
            )
        )
        stale = client.get("/api/v1/ting/sagas/plan/plan-sdcp-operator/draft").json()
        assert stale == {"found": False, "structure": None}

    def test_feedback_answers_the_open_brief_gate_with_the_note(
        self, mock_tracker: MockTracker
    ) -> None:
        campaign = _plan_campaign()
        campaign_repo = InMemoryWorkflowCampaignRepository([campaign])
        runs = FakeWorkflowRuns(gates_by_run={campaign.id: [_gate("plan-brief-gate")]})
        client = _plan_client(mock_tracker, campaign_repo, runs)

        resp = client.post(
            "/api/v1/ting/sagas/plan/plan-sdcp-operator/feedback",
            json={"content": "Keep the first pass to one saga."},
        )

        assert resp.status_code == 200
        assert resp.json() == {"status": "sent", "session_id": ""}
        [decision] = runs.decisions
        assert decision.node_id == "plan-brief-gate"
        assert decision.decision is GateDecision.APPROVE
        assert decision.note == "Keep the first pass to one saga."
        assert decision.by.id == "dev-user"
        stored = campaign_repo._campaigns[campaign.id]
        assert stored.metadata["planning_feedback_notes"] == "Keep the first pass to one saga."

    def test_feedback_requests_changes_at_the_review_gate(self, mock_tracker: MockTracker) -> None:
        campaign = _plan_campaign()
        runs = FakeWorkflowRuns(
            gates_by_run={
                campaign.id: [_gate("plan-brief-gate"), _gate("plan-review-gate")],
            }
        )
        client = _plan_client(mock_tracker, InMemoryWorkflowCampaignRepository([campaign]), runs)

        resp = client.post(
            "/api/v1/ting/sagas/plan/plan-sdcp-operator/feedback",
            json={"content": "Keep this as one phase.", "decision": "changes_requested"},
        )

        assert resp.status_code == 200
        [decision] = runs.decisions
        assert decision.node_id == "plan-review-gate"
        assert decision.decision is GateDecision.REQUEST_CHANGES
        assert decision.note == "Keep this as one phase."

    def test_feedback_with_no_open_gate_is_a_conflict(self, mock_tracker: MockTracker) -> None:
        campaign = _plan_campaign()
        runs = FakeWorkflowRuns()
        client = _plan_client(mock_tracker, InMemoryWorkflowCampaignRepository([campaign]), runs)

        resp = client.post(
            "/api/v1/ting/sagas/plan/plan-sdcp-operator/feedback",
            json={"content": "Looks fine.", "decision": "approve"},
        )

        assert resp.status_code == 409
        assert "No planning gate" in resp.json()["detail"]
        assert runs.decisions == []

    def test_repeated_final_approval_with_no_open_gate_changes_nothing(
        self, mock_tracker: MockTracker
    ) -> None:
        campaign = _plan_campaign()
        runs = FakeWorkflowRuns()
        client = _plan_client(mock_tracker, InMemoryWorkflowCampaignRepository([campaign]), runs)

        resp = client.post(
            "/api/v1/ting/sagas/plan/plan-sdcp-operator/feedback",
            json={"content": "Approved in Ting Plan.", "decision": "approve"},
        )

        assert resp.status_code == 200
        assert resp.json() == {"status": "sent", "session_id": ""}
        assert runs.decisions == []

    def test_feedback_rejects_an_unknown_decision(self, mock_tracker: MockTracker) -> None:
        campaign = _plan_campaign()
        client = _plan_client(
            mock_tracker, InMemoryWorkflowCampaignRepository([campaign]), FakeWorkflowRuns()
        )

        resp = client.post(
            "/api/v1/ting/sagas/plan/plan-sdcp-operator/feedback",
            json={"content": "Hmm.", "decision": "shrug"},
        )

        assert resp.status_code == 422

    def test_feedback_the_engine_refuses_is_a_conflict(self, mock_tracker: MockTracker) -> None:
        campaign = _plan_campaign()
        runs = FakeWorkflowRuns(
            gates_by_run={campaign.id: [_gate("plan-brief-gate")]},
            gate_error=GateRefusedError("gate waits for a person"),
        )
        client = _plan_client(mock_tracker, InMemoryWorkflowCampaignRepository([campaign]), runs)

        resp = client.post(
            "/api/v1/ting/sagas/plan/plan-sdcp-operator/feedback",
            json={"content": "Go on."},
        )

        assert resp.status_code == 409
        assert "waits for a person" in resp.json()["detail"]


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


def _no_plan_campaigns() -> MagicMock:
    """A campaign repository for commits that come from no plan.

    A FastAPI dependency, so it must take no arguments: MagicMock itself reads as
    one that wants `args` and `kwargs` query parameters.
    """
    return MagicMock()


class TestCommitSaga:
    def test_kicks_off_initial_dispatch_when_service_is_available(
        self,
        mock_tracker: MockTracker,
    ) -> None:
        repo = MockSagaRepo()
        app = FastAPI()
        app.state.authorization = AllowAllAuthorizationAdapter()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
        app.dependency_overrides[resolve_saga_repo] = lambda: repo
        app.dependency_overrides[resolve_workflow_campaign_repo] = _no_plan_campaigns
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
        app.state.authorization = AllowAllAuthorizationAdapter()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
        app.dependency_overrides[resolve_saga_repo] = lambda: repo
        app.dependency_overrides[resolve_workflow_campaign_repo] = _no_plan_campaigns
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
                created_at=datetime.now(UTC),
                base_branch="dev",
                owner_id="dev-user",
            )
        )
        app = FastAPI()
        app.state.authorization = AllowAllAuthorizationAdapter()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
        app.dependency_overrides[resolve_saga_repo] = lambda: repo
        app.dependency_overrides[resolve_workflow_campaign_repo] = _no_plan_campaigns
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
        app.state.authorization = AllowAllAuthorizationAdapter()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_saga_repo] = MockSagaRepo
        app.dependency_overrides[resolve_workflow_campaign_repo] = _no_plan_campaigns
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
        app.state.authorization = AllowAllAuthorizationAdapter()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [FailingTracker()]
        app.dependency_overrides[resolve_saga_repo] = MockSagaRepo
        app.dependency_overrides[resolve_workflow_campaign_repo] = _no_plan_campaigns
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
        app.state.authorization = AllowAllAuthorizationAdapter()
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
        app.state.authorization = AllowAllAuthorizationAdapter()
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
        app.state.authorization = AllowAllAuthorizationAdapter()
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
        app.state.authorization = AllowAllAuthorizationAdapter()
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
