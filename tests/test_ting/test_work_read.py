"""Tests for Ting's source-qualified Work read surface."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ting.api.research import resolve_workflow_campaign_repo
from ting.api.sagas import resolve_saga_repo
from ting.api.work import (
    create_work_router,
    resolve_work_execution_repo,
    resolve_work_trackers,
)
from ting.config import AuthConfig, Settings
from ting.domain.models import (
    CampaignStageState,
    Phase,
    PhaseStatus,
    Run,
    RunStatus,
    Saga,
    SagaStatus,
    TrackerIssue,
    TrackerProject,
    WorkflowCampaign,
    WorkflowCampaignStatus,
)
from ting.domain.services.work_read import CoverageStatus, WorkCondition, WorkKind, WorkReadService
from ting.domain.workflow_execution import (
    ChildTemplate,
    ExecutionBudget,
    ExecutionState,
    ExpansionPolicy,
    WorkflowExecution,
)
from ting.ports.saga_repository import SagaRepository
from ting.ports.tracker import (
    TrackerPort,
    TrackerResolution,
    TrackerResolutionFailure,
)
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_execution import WorkflowExecutionRepository

NOW = datetime(2026, 9, 20, 12, tzinfo=UTC)


def _digest(value: str) -> str:
    return "sha256:" + sha256(value.encode()).hexdigest()


def _saga(*, connection_id: str = "linear-a") -> Saga:
    return Saga(
        id=uuid4(),
        tracker_id="project-1",
        tracker_type="linear",
        tracker_connection_id=connection_id,
        slug="platform",
        name="Platform",
        repos=["niuulabs/platform"],
        feature_branch="work/platform",
        status=SagaStatus.ACTIVE,
        confidence=0.8,
        created_at=NOW,
        base_branch="main",
        owner_id="owner-1",
        tenant_id="tenant-1",
        workflow_id=uuid4(),
        workflow_version="1.3.0",
        workflow_snapshot={"version": "1.3.0", "workflow_revision": _digest("workflow")},
        instance_id="volundr-west",
    )


def _campaign(*, surface: str = "ting.research") -> WorkflowCampaign:
    return WorkflowCampaign(
        id=uuid4(),
        slug="market-analysis",
        name="Market analysis",
        owner_id="owner-1",
        tenant_id="tenant-1",
        workflow_id=uuid4(),
        workflow_version="2.0.0",
        workflow_name="Research campaign",
        workflow_snapshot={"workflow_revision": _digest("campaign"), "graph": {}},
        session_id="session-campaign",
        session_name="market-analysis",
        connection_id="volundr-west",
        status=WorkflowCampaignStatus.RUNNING,
        active_stage_id="research",
        stage_state=[CampaignStageState(stage_id="research", label="Research", status="running")],
        metadata={"surface": surface},
        created_at=NOW,
        updated_at=NOW + timedelta(minutes=1),
    )


def _execution(*, state: ExecutionState = ExecutionState.RUNNING) -> WorkflowExecution:
    schema = {
        "type": "object",
        "properties": {"attemptId": {"type": "string"}},
        "required": ["attemptId"],
        "additionalProperties": False,
    }
    return WorkflowExecution(
        id=uuid4(),
        name="Developer delivery",
        prompt="Deliver the selected work",
        owner_id="owner-1",
        tenant_id="tenant-1",
        workflow_id=uuid4(),
        workflow_revision="1.4.0",
        workflow_digest=_digest("execution"),
        parent_session_id="session-parent",
        parent_node_id="expand",
        connection_id="volundr-west",
        policy=ExpansionPolicy(
            coordinator_id="delivery-coordinator",
            templates={
                "task": ChildTemplate(
                    dependency_alias="developer-task",
                    id=uuid4(),
                    revision="1.0.0",
                    digest=_digest("child"),
                )
            },
            input_schema=schema,
            result_schema=schema,
            max_children=10,
            max_attempts=2,
            max_active_children=3,
        ),
        budget=ExecutionBudget(total_units=20),
        deadline=NOW + timedelta(days=1),
        state=state,
        workflow_snapshot={"version": "1.4.0"},
        created_at=NOW,
        updated_at=NOW + timedelta(minutes=2),
    )


def _tracker(
    connection_id: str,
    *,
    projects: list[TrackerProject] | None = None,
    issues: list[TrackerIssue] | None = None,
    runs: list[Run] | None = None,
) -> MagicMock:
    tracker = MagicMock(spec=TrackerPort)
    tracker.connection_id = connection_id
    tracker.provider = "linear"
    tracker.list_projects = AsyncMock(return_value=projects or [])
    tracker.get_project = AsyncMock(return_value=(projects or [])[0] if projects else None)
    tracker.list_issues = AsyncMock(return_value=issues or [])
    tracker.get_run_progress_for_saga = AsyncMock(return_value=runs or [])
    tracker.get_authorized_run_progress_for_saga = AsyncMock(return_value=runs or [])
    tracker.has_unscoped_run_progress_for_saga = AsyncMock(return_value=False)
    return tracker


def _repositories(
    *,
    sagas: list[Saga] | None = None,
    campaigns: list[WorkflowCampaign] | None = None,
) -> tuple[MagicMock, MagicMock]:
    saga_repo = MagicMock(spec=SagaRepository)
    saga_repo.list_sagas = AsyncMock(return_value=sagas or [])
    saga_repo.get_saga = AsyncMock(
        side_effect=lambda saga_id, **_: next(
            (item for item in sagas or [] if item.id == saga_id), None
        )
    )
    saga_repo.get_phases_by_saga = AsyncMock(return_value=[])
    saga_repo.get_runs_by_phase = AsyncMock(return_value=[])
    campaign_repo = MagicMock(spec=WorkflowCampaignRepository)
    campaign_repo.list_campaigns = AsyncMock(return_value=campaigns or [])
    campaign_repo.get_campaign = AsyncMock(
        side_effect=lambda campaign_id: next(
            (item for item in campaigns or [] if item.id == campaign_id), None
        )
    )
    return saga_repo, campaign_repo


def _service(
    *,
    saga_repo: SagaRepository,
    campaign_repo: WorkflowCampaignRepository,
    trackers: list[TrackerPort],
    execution_repo: WorkflowExecutionRepository | None = None,
    failures: tuple[TrackerResolutionFailure, ...] = (),
) -> WorkReadService:
    from niuu.domain.models import Principal

    return WorkReadService(
        principal=Principal(user_id="owner-1", email="", tenant_id="tenant-1", roles=[]),
        saga_repo=saga_repo,
        campaign_repo=campaign_repo,
        trackers=trackers,
        execution_repo=execution_repo,
        tracker_resolution_failures=failures,
    )


@pytest.mark.asyncio
async def test_list_composes_real_sources_and_reports_bounded_execution_cursor() -> None:
    saga = _saga()
    campaign = _campaign()
    execution = _execution()
    project = TrackerProject(
        id=saga.tracker_id,
        name="Platform roadmap",
        description="",
        status="started",
        url="https://linear.example/project/platform",
        milestone_count=1,
        issue_count=2,
    )
    tracker = _tracker("linear-a", projects=[project])
    saga_repo, campaign_repo = _repositories(sagas=[saga], campaigns=[campaign])
    execution_repo = MagicMock(spec=WorkflowExecutionRepository)
    execution_repo.list = AsyncMock(return_value=([execution], "next-page"))

    result = await _service(
        saga_repo=saga_repo,
        campaign_repo=campaign_repo,
        trackers=[tracker],
        execution_repo=execution_repo,
        failures=(
            TrackerResolutionFailure(
                connection_id="tracker-b",
                code="credentialUnavailable",
                message="The configured tracker credential is unavailable.",
            ),
        ),
    ).list_work(execution_limit=25, execution_cursor="cursor-1")

    assert result.projects[0].title == "Platform roadmap"
    assert result.projects[0].source is not None
    assert result.projects[0].source.connection_id == "linear-a"
    assert result.campaigns[0].kind == WorkKind.RESEARCH
    assert result.campaigns[0].campaign_slug == "market-analysis"
    assert result.campaigns[0].current_step == "Research"
    assert result.executions[0].kind == WorkKind.WORKFLOW_EXECUTION
    assert result.execution_next_cursor == "next-page"
    assert any(
        item.source == "workflowExecutions" and item.status == CoverageStatus.PARTIAL
        for item in result.coverage
    )
    assert any(
        item.connection_id == "tracker-b"
        and item.status == CoverageStatus.UNAVAILABLE
        and item.error is not None
        and item.error.code == "credentialUnavailable"
        for item in result.coverage
    )
    execution_repo.list.assert_awaited_once_with(
        owner_id="owner-1", tenant_id="tenant-1", limit=25, cursor="cursor-1"
    )


@pytest.mark.asyncio
async def test_list_tracker_failure_is_sanitized_and_does_not_hide_other_work() -> None:
    campaign = _campaign(surface="ting.specs")
    tracker = _tracker("linear-a")
    tracker.list_projects.side_effect = RuntimeError("secret token leaked upstream")
    saga_repo, campaign_repo = _repositories(campaigns=[campaign])

    result = await _service(
        saga_repo=saga_repo,
        campaign_repo=campaign_repo,
        trackers=[tracker],
    ).list_work(execution_limit=10, execution_cursor="")

    assert result.campaigns[0].kind == WorkKind.SPECIFICATION
    tracker_coverage = next(item for item in result.coverage if item.source == "tracker")
    assert tracker_coverage.status == CoverageStatus.UNAVAILABLE
    assert tracker_coverage.error is not None
    assert tracker_coverage.error.code == "sourceReadFailed"
    assert "secret" not in tracker_coverage.error.message


@pytest.mark.asyncio
async def test_list_keeps_healthy_sections_when_repositories_fail() -> None:
    campaign = _campaign()
    saga_repo, campaign_repo = _repositories(campaigns=[campaign])
    saga_repo.list_sagas.side_effect = RuntimeError("database password")
    execution_repo = MagicMock(spec=WorkflowExecutionRepository)
    execution_repo.list = AsyncMock(side_effect=RuntimeError("database host"))

    result = await _service(
        saga_repo=saga_repo,
        campaign_repo=campaign_repo,
        trackers=[],
        execution_repo=execution_repo,
    ).list_work(execution_limit=10, execution_cursor="")

    assert result.projects == ()
    assert result.campaigns[0].title == "Market analysis"
    assert result.executions == ()
    failed = {
        item.source: item for item in result.coverage if item.status == CoverageStatus.UNAVAILABLE
    }
    assert set(failed) == {"sagas", "workflowExecutions"}
    assert all("database" not in item.error.message for item in failed.values() if item.error)


@pytest.mark.asyncio
async def test_active_campaign_is_unknown_when_lifecycle_subscriber_is_unavailable() -> None:
    campaign = _campaign()
    saga_repo, campaign_repo = _repositories(campaigns=[campaign])
    from niuu.domain.models import Principal

    service = WorkReadService(
        principal=Principal(user_id="owner-1", email="", tenant_id="tenant-1", roles=[]),
        saga_repo=saga_repo,
        campaign_repo=campaign_repo,
        trackers=[],
        execution_repo=None,
        campaign_lifecycle_available=False,
    )

    result = await service.list_work(execution_limit=10, execution_cursor="")

    assert result.campaigns[0].condition == WorkCondition.UNKNOWN
    assert result.campaigns[0].attention is not None
    assert result.campaigns[0].attention.kind == "sourceUnavailable"
    lifecycle = next(item for item in result.coverage if item.source == "campaignLifecycle")
    assert lifecycle.status == CoverageStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_project_detail_uses_exact_source_and_real_run_links() -> None:
    saga = _saga(connection_id="linear-b")
    issue_ready = TrackerIssue(
        id="issue-1",
        identifier="ENG-1",
        title="Ship Work view",
        description="",
        status="In Progress",
        status_type="started",
        url="https://linear.example/issue/ENG-1",
    )
    issue_waiting = TrackerIssue(
        id="issue-2",
        identifier="ENG-2",
        title="Document Work view",
        description="",
        status="Backlog",
        status_type="backlog",
        url="https://linear.example/issue/ENG-2",
    )
    project_a = TrackerProject(
        id="project-1",
        name="Wrong duplicate",
        description="",
        status="started",
        url="https://linear-a.example/project/1",
        milestone_count=0,
        issue_count=0,
    )
    project_b = TrackerProject(
        id="project-1",
        name="Selected project",
        description="",
        status="started",
        url="https://linear-b.example/project/1",
        milestone_count=0,
        issue_count=2,
    )
    run = Run(
        id=uuid4(),
        phase_id=uuid4(),
        tracker_id="issue-1",
        identifier="ENG-1",
        name="Ship Work view",
        description="",
        acceptance_criteria=[],
        declared_files=[],
        estimate_hours=None,
        status=RunStatus.REVIEW,
        confidence=0.9,
        session_id="session-task",
        reviewer_session_id="session-reviewer",
        branch="work/ENG-1",
        chronicle_summary=None,
        pr_url="https://github.example/pr/1",
        pr_id="1",
        retry_count=0,
        created_at=NOW,
        updated_at=NOW + timedelta(minutes=3),
    )
    tracker_a = _tracker("linear-a", projects=[project_a])
    tracker_b = _tracker(
        "linear-b",
        projects=[project_b],
        issues=[issue_ready, issue_waiting],
        runs=[run],
    )
    saga_repo, campaign_repo = _repositories(sagas=[saga])

    detail = await _service(
        saga_repo=saga_repo,
        campaign_repo=campaign_repo,
        trackers=[tracker_a, tracker_b],
    ).get_project(saga.id, ready_issue_ids={"issue-1"})

    assert detail.item.title == "Selected project"
    assert detail.item.source is not None
    assert detail.item.source.connection_id == "linear-b"
    assert [task.source.issue_id for task in detail.tasks] == ["issue-1", "issue-2"]
    assert detail.tasks[0].operational_run is not None
    assert detail.tasks[0].operational_run.session_id == "session-task"
    assert detail.tasks[0].operational_run.reviewer_session_id == "session-reviewer"
    assert detail.tasks[0].dispatch is not None
    assert detail.tasks[0].dispatch.connection_id == "volundr-west"
    assert detail.tasks[0].dispatch.repo == "niuulabs/platform"
    assert detail.tasks[1].operational_run is None
    assert detail.tasks[1].dispatch is None
    tracker_a.get_project.assert_not_awaited()
    tracker_b.get_project.assert_awaited_once_with("project-1")
    tracker_b.get_authorized_run_progress_for_saga.assert_awaited_once_with(
        "project-1", owner_id="owner-1", tenant_id="tenant-1"
    )


@pytest.mark.asyncio
async def test_project_detail_preserves_embedded_run_when_tracker_run_read_fails() -> None:
    saga = _saga()
    phase = Phase(
        id=uuid4(),
        saga_id=saga.id,
        tracker_id="milestone-1",
        number=1,
        name="Delivery",
        status=PhaseStatus.ACTIVE,
        confidence=0.8,
    )
    issue = TrackerIssue(
        id="issue-1",
        identifier="ENG-1",
        title="Ship",
        description="",
        status="Started",
        status_type="started",
    )
    run = Run(
        id=uuid4(),
        phase_id=phase.id,
        tracker_id="issue-1",
        identifier="ENG-1",
        name="Ship",
        description="",
        acceptance_criteria=[],
        declared_files=[],
        estimate_hours=None,
        status=RunStatus.RUNNING,
        confidence=0.8,
        session_id="persisted-session",
        branch="work/ENG-1",
        chronicle_summary=None,
        pr_url=None,
        pr_id=None,
        retry_count=0,
        created_at=NOW,
        updated_at=NOW,
    )
    project = TrackerProject(
        id="project-1",
        name="Platform",
        description="",
        status="started",
        url="",
        milestone_count=1,
        issue_count=1,
    )
    tracker = _tracker("linear-a", projects=[project], issues=[issue])
    tracker.get_authorized_run_progress_for_saga.side_effect = RuntimeError("tracker unavailable")
    saga_repo, campaign_repo = _repositories(sagas=[saga])
    saga_repo.get_phases_by_saga.return_value = [phase]
    saga_repo.get_runs_by_phase.return_value = [run]

    detail = await _service(
        saga_repo=saga_repo,
        campaign_repo=campaign_repo,
        trackers=[tracker],
    ).get_project(saga.id)

    assert detail.tasks[0].operational_run is not None
    assert detail.tasks[0].operational_run.session_id == "persisted-session"
    assert any(
        item.source == "operationalRuns" and item.status == CoverageStatus.PARTIAL
        for item in detail.coverage
    )


@pytest.mark.asyncio
async def test_legacy_unscoped_progress_is_withheld_and_marks_linkage_unavailable() -> None:
    saga = _saga()
    issue = TrackerIssue(
        id="issue-1",
        identifier="ENG-1",
        title="Legacy run",
        description="",
        status="Started",
        status_type="started",
    )
    project = TrackerProject(
        id="project-1",
        name="Platform",
        description="",
        status="started",
        url="",
        milestone_count=0,
        issue_count=1,
    )
    tracker = _tracker("linear-a", projects=[project], issues=[issue])
    tracker.has_unscoped_run_progress_for_saga.return_value = True
    saga_repo, campaign_repo = _repositories(sagas=[saga])

    detail = await _service(
        saga_repo=saga_repo,
        campaign_repo=campaign_repo,
        trackers=[tracker],
    ).get_project(saga.id)

    assert detail.tasks[0].operational_run is None
    operational = next(item for item in detail.coverage if item.source == "operationalRuns")
    assert operational.status == CoverageStatus.UNAVAILABLE
    assert operational.error is not None
    assert operational.error.code == "operationalReadFailed"


def test_api_serializes_camel_case_and_rejects_invalid_execution_cursor() -> None:
    saga = _saga()
    project = TrackerProject(
        id="project-1",
        name="Platform",
        description="",
        status="started",
        url="https://linear.example/project/1",
        milestone_count=0,
        issue_count=0,
    )
    tracker = _tracker("linear-a", projects=[project])
    saga_repo, campaign_repo = _repositories(sagas=[saga], campaigns=[_campaign()])
    execution_repo = MagicMock(spec=WorkflowExecutionRepository)
    execution_repo.list = AsyncMock(side_effect=ValueError("Invalid cursor"))

    app = FastAPI()
    app.state.settings = Settings(auth=AuthConfig(allow_anonymous_dev=False))
    app.state.subscriber = SimpleNamespace(running=True)
    app.include_router(create_work_router())
    app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
    app.dependency_overrides[resolve_workflow_campaign_repo] = lambda: campaign_repo
    app.dependency_overrides[resolve_work_trackers] = lambda: TrackerResolution(adapters=(tracker,))
    app.dependency_overrides[resolve_work_execution_repo] = lambda: execution_repo
    client = TestClient(app)

    response = client.get(
        "/api/v1/ting/work?executionLimit=10&executionCursor=bad",
        headers={"x-auth-user-id": "owner-1", "x-auth-tenant": "tenant-1"},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid cursor"}

    execution_repo.list.side_effect = None
    execution_repo.list.return_value = ([], "")
    response = client.get(
        "/api/v1/ting/work",
        headers={"x-auth-user-id": "owner-1", "x-auth-tenant": "tenant-1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["executionNextCursor"] is None
    assert body["campaigns"][0]["campaignSlug"] == "market-analysis"
    assert body["projects"][0]["rawState"] == {"source": "saga", "status": "active"}
    assert body["projects"][0]["condition"] == WorkCondition.ACTIVE
    assert body["projects"][0]["source"]["connectionId"] == "linear-a"


def test_api_project_detail_exposes_only_queue_authorized_dispatch() -> None:
    saga = _saga()
    issue = TrackerIssue(
        id="issue-1",
        identifier="ENG-1",
        title="Ship Work",
        description="",
        status="Backlog",
        status_type="backlog",
        url="https://linear.example/issue/ENG-1",
    )
    project = TrackerProject(
        id="project-1",
        name="Platform",
        description="",
        status="started",
        url="https://linear.example/project/1",
        milestone_count=0,
        issue_count=1,
    )
    tracker = _tracker("linear-a", projects=[project], issues=[issue])
    saga_repo, campaign_repo = _repositories(sagas=[saga])
    find_ready = AsyncMock(return_value=[SimpleNamespace(issue_id="issue-1")])

    app = FastAPI()
    app.state.settings = Settings(auth=AuthConfig(allow_anonymous_dev=False))
    app.state.subscriber = SimpleNamespace(running=True)
    app.state.dispatch_service = SimpleNamespace(find_ready_issues=find_ready)
    app.include_router(create_work_router())
    app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
    app.dependency_overrides[resolve_workflow_campaign_repo] = lambda: campaign_repo
    app.dependency_overrides[resolve_work_trackers] = lambda: TrackerResolution(adapters=(tracker,))
    app.dependency_overrides[resolve_work_execution_repo] = lambda: None
    client = TestClient(app)

    response = client.get(
        f"/api/v1/ting/work/project/{saga.id}",
        headers={"x-auth-user-id": "owner-1", "x-auth-tenant": "tenant-1"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["item"]["id"] == f"project:{saga.id}"
    assert body["tasks"][0]["dispatch"] == {
        "sagaId": str(saga.id),
        "issueId": "issue-1",
        "repo": "niuulabs/platform",
        "connectionId": "volundr-west",
        "workflowId": str(saga.workflow_id),
    }
    assert "dispatch" in body["tasks"][0]["actions"]
    find_ready.assert_awaited_once()


def test_api_detail_returns_not_found_and_unavailable_without_leaking_sources() -> None:
    saga_repo, campaign_repo = _repositories()
    app = FastAPI()
    app.state.settings = Settings(auth=AuthConfig(allow_anonymous_dev=False))
    app.state.subscriber = SimpleNamespace(running=True)
    app.include_router(create_work_router())
    app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
    app.dependency_overrides[resolve_workflow_campaign_repo] = lambda: campaign_repo
    app.dependency_overrides[resolve_work_trackers] = lambda: TrackerResolution(adapters=())
    app.dependency_overrides[resolve_work_execution_repo] = lambda: None
    client = TestClient(app)
    headers = {"x-auth-user-id": "owner-1", "x-auth-tenant": "tenant-1"}

    missing = client.get(f"/api/v1/ting/work/campaign/{uuid4()}", headers=headers)
    unavailable = client.get(f"/api/v1/ting/work/execution/{uuid4()}", headers=headers)

    assert missing.status_code == 404
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "Bounded workflow executions are not configured"}
