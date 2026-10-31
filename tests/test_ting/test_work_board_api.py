"""Tests for the project board on the Work surface and its gate decision."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ting.api.research import resolve_workflow_campaign_repo
from ting.api.sagas import resolve_saga_repo
from ting.api.work import create_work_router, resolve_work_trackers
from ting.config import AuthConfig, Settings
from ting.domain.models import (
    Run,
    RunStatus,
    Saga,
    SagaBoardWorkflow,
    SagaStatus,
    TrackerIssue,
    TrackerProject,
    WorkflowCampaign,
    WorkflowCampaignStatus,
)
from ting.domain.ticket_coordinator import TicketCoordinator, TicketCoordinatorStatus
from ting.domain.workflow_board import SESSION_GONE_REASON
from ting.ports.saga_repository import SagaRepository
from ting.ports.ticket_coordinator_repository import TicketCoordinatorRepository
from ting.ports.tracker import TrackerPort, TrackerResolution
from ting.ports.volundr import PublicSessionLogEntry, PublicSessionLogPage
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.system_workflows import load_system_workflows

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
HEADERS = {"x-auth-user-id": "owner-1", "x-auth-tenant": "tenant-1"}


def _snapshot() -> dict[str, Any]:
    workflow = next(
        item for item in load_system_workflows() if item.name == "Reviewed Delivery Flow"
    )
    return {"name": workflow.name, "version": workflow.version, "graph": workflow.graph}


def _saga(snapshot: dict[str, Any] | None) -> Saga:
    return Saga(
        id=uuid4(),
        tracker_id="project-1",
        tracker_type="linear",
        tracker_connection_id="linear-a",
        slug="platform",
        name="Platform",
        repos=["niuulabs/platform"],
        feature_branch="work/platform",
        status=SagaStatus.ACTIVE,
        created_at=NOW,
        base_branch="main",
        owner_id="owner-1",
        tenant_id="tenant-1",
        workflow_id=uuid4(),
        workflow_version="1.0.0",
        workflow_snapshot=snapshot,
    )


def _issue(issue_id: str = "issue-1", status_type: str = "started") -> TrackerIssue:
    return TrackerIssue(
        id=issue_id,
        identifier=issue_id.upper(),
        title="Ship the board",
        description="",
        status="In Progress",
        status_type=status_type,
        url=f"https://linear.example/{issue_id}",
    )


def _run(issue_id: str = "issue-1", session_id: str | None = "session-1") -> Run:
    return Run(
        id=uuid4(),
        phase_id=uuid4(),
        tracker_id=issue_id,
        name="Ship the board",
        description="",
        acceptance_criteria=[],
        declared_files=[],
        estimate_hours=None,
        status=RunStatus.RUNNING,
        session_id=session_id,
        branch="feat/board",
        chronicle_summary=None,
        pr_url=None,
        pr_id=None,
        retry_count=0,
        created_at=NOW,
        updated_at=NOW,
    )


def _pending_gate() -> dict[str, Any]:
    return {
        "id": "gate-1",
        "activation_id": "activation-1",
        "node_id": "delivery-code-gate",
        "label": "Code review",
        "condition": "Is this ready to merge?",
        "instructions": "Read the pull request.",
        "summary": "",
        "status": "pending",
        "requested_at": "2026-10-30T12:00:00+00:00",
        "attempt": 1,
    }


class _Coordinators(TicketCoordinatorRepository):
    def __init__(self, *existing: TicketCoordinator) -> None:
        self.rows = {(item.saga_id, item.issue_id): item for item in existing}

    async def get(self, saga_id, issue_id):
        return self.rows.get((saga_id, issue_id))

    async def get_by_session(self, session_id):
        return next((item for item in self.rows.values() if item.session_id == session_id), None)

    async def list_active_owner_ids(self):
        return sorted({item.owner_id for item in self.rows.values()})

    async def list_for_saga(self, saga_id, *, owner_id):
        return [item for item in self.rows.values() if item.saga_id == saga_id]

    async def claim_advance(
        self, saga_id, issue_id, *, finished_campaign_id, workflow_id, requested_at
    ):
        current = self.rows.get((saga_id, issue_id))
        if current is None or current.advanced_from == finished_campaign_id:
            return None
        claimed = replace(
            current,
            advanced_from=finished_campaign_id,
            requested_workflow_id=workflow_id,
            requested_at=requested_at,
            start_error=None,
        )
        self.rows[(saga_id, issue_id)] = claimed
        return claimed

    async def save(self, coordinator):
        self.rows[(coordinator.saga_id, coordinator.issue_id)] = coordinator
        return coordinator

    async def close(self, saga_id, issue_id):
        current = self.rows[(saga_id, issue_id)]
        self.rows[(saga_id, issue_id)] = replace(
            current, status=TicketCoordinatorStatus.CLOSED, closed_at=NOW
        )


def _coordinator(saga: Saga, issue_id: str = "issue-1") -> TicketCoordinator:
    return TicketCoordinator(
        saga_id=saga.id,
        issue_id=issue_id,
        owner_id="owner-1",
        tenant_id="tenant-1",
        session_id="coordinator-session",
        connection_id=None,
        status=TicketCoordinatorStatus.ACTIVE,
        created_at=NOW,
    )


def _campaign(saga: Saga, *, status: WorkflowCampaignStatus) -> WorkflowCampaign:
    return WorkflowCampaign(
        id=uuid4(),
        slug="a2a-run",
        name="Reviewed Delivery Flow",
        owner_id="owner-1",
        tenant_id="tenant-1",
        workflow_id=saga.workflow_id,
        workflow_version="1.0.0",
        workflow_name="Reviewed Delivery Flow",
        workflow_snapshot=_snapshot(),
        session_id="run-session",
        session_name="a2a-run",
        connection_id=None,
        status=status,
        active_stage_id=None,
        stage_state=[],
        metadata={
            "ticket": {"sagaId": str(saga.id), "issueId": "issue-1", "identifier": "ISSUE-1"}
        },
        created_at=NOW,
        updated_at=NOW,
    )


def _client(
    saga: Saga,
    *,
    issues: list[TrackerIssue],
    runs: list[Run],
    adapter: MagicMock | None,
    campaigns: list[WorkflowCampaign] | None = None,
    coordinators: _Coordinators | None = None,
    forges: list[MagicMock] | None = None,
    unresolved: int = 0,
) -> TestClient:
    project = TrackerProject(
        id="project-1",
        name="Platform",
        description="",
        status="started",
        url="https://linear.example/project/1",
        milestone_count=0,
        issue_count=len(issues),
    )
    tracker = MagicMock(spec=TrackerPort)
    tracker.connection_id = "linear-a"
    tracker.provider = "linear"
    tracker.get_project = AsyncMock(return_value=project)
    tracker.list_projects = AsyncMock(return_value=[project])
    tracker.list_issues = AsyncMock(return_value=issues)
    tracker.get_authorized_run_progress_for_saga = AsyncMock(return_value=runs)
    tracker.has_unscoped_run_progress_for_saga = AsyncMock(return_value=False)
    saga_repo = MagicMock(spec=SagaRepository)
    saga_repo.get_saga = AsyncMock(return_value=saga)
    saga_repo.get_phases_by_saga = AsyncMock(return_value=[])

    app = FastAPI()
    app.state.settings = Settings(auth=AuthConfig(allow_anonymous_dev=False))
    app.state.subscriber = SimpleNamespace(running=True)
    if adapter is not None:
        app.state.volundr_factory = SimpleNamespace(
            primary_for_principal=AsyncMock(return_value=adapter),
            for_principal_with_unresolved=AsyncMock(
                return_value=([adapter] if forges is None else forges, unresolved)
            ),
        )
    app.include_router(create_work_router())
    app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
    campaign_repo = MagicMock(spec=WorkflowCampaignRepository)
    campaign_repo.list_campaigns = AsyncMock(return_value=campaigns or [])
    campaign_repo.list_ticket_campaigns = partial(
        WorkflowCampaignRepository.list_ticket_campaigns, campaign_repo
    )
    campaign_repo.list_campaign_summaries = partial(
        WorkflowCampaignRepository.list_campaign_summaries, campaign_repo
    )
    app.state.ticket_coordinator_repo = coordinators or _Coordinators()
    app.dependency_overrides[resolve_workflow_campaign_repo] = lambda: campaign_repo
    app.dependency_overrides[resolve_work_trackers] = lambda: TrackerResolution(adapters=(tracker,))
    return TestClient(app)


def _adapter(
    gates: list[dict[str, Any]] | Exception,
    *,
    activity_state: str = "active",
    name: str = "forge-a",
) -> MagicMock:
    adapter = MagicMock()
    adapter.name = name
    adapter.target_id = name
    adapter.get_session = AsyncMock(
        return_value=SimpleNamespace(
            activity_state=activity_state,
            chat_endpoint="ws://forge/s/session/session",
            status="running",
        )
    )
    adapter.spawn_session = AsyncMock(return_value=SimpleNamespace(id="coordinator-session"))
    adapter.send_directed_room_message = AsyncMock()
    adapter.start_session = AsyncMock()
    adapter.stop_session = AsyncMock()
    adapter.get_workflow_gates = AsyncMock(
        side_effect=gates if isinstance(gates, Exception) else None,
        return_value=gates,
    )
    adapter.resolve_workflow_gate = AsyncMock(return_value={"status": "resolved"})
    return adapter


def _coverage(body: dict[str, Any], source: str) -> dict[str, Any]:
    return next(item for item in body["coverage"] if item["source"] == source)


def test_a_project_without_a_workflow_board_keeps_the_tracker_view() -> None:
    saga = _saga({"version": "1.0.0"})
    client = _client(saga, issues=[_issue()], runs=[_run()], adapter=_adapter([]))

    body = client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()

    assert body["board"] is None
    assert body["tasks"][0]["board"] is None
    assert not any(item["source"] == "workflowGates" for item in body["coverage"])


def test_project_detail_returns_the_columns_and_the_waiting_gate() -> None:
    saga = _saga(_snapshot())
    adapter = _adapter([_pending_gate()])
    client = _client(
        saga,
        issues=[_issue(), _issue("issue-2", "backlog")],
        runs=[_run()],
        adapter=adapter,
    )

    body = client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()

    assert {column["workflowName"] for column in body["board"]["columns"][1:-1]} == {
        "Reviewed Delivery Flow"
    }
    assert [column["id"] for column in body["board"]["columns"]] == [
        "backlog",
        "building",
        "code-review",
        "merging",
        "done",
    ]
    review = body["board"]["columns"][2]
    assert (review["kind"], review["review"]) == ("gate", "code")
    assert (review["approveColumnId"], review["changesColumnId"]) == ("merging", "building")
    waiting, unstarted = body["tasks"]
    assert waiting["board"]["columnId"] == "code-review"
    assert waiting["board"]["state"] == "needsDecision"
    assert waiting["board"]["gate"]["activationId"] == "activation-1"
    assert unstarted["board"] == {
        "columnId": "backlog",
        "state": "notStarted",
        "gate": None,
        "reason": None,
        "lastDecision": None,
        "passedWorkflowIds": [],
    }
    assert _coverage(body, "workflowGates")["status"] == "complete"


def test_a_session_whose_agent_died_is_blocked_not_running() -> None:
    saga = _saga(_snapshot())
    client = _client(
        saga,
        issues=[_issue()],
        runs=[_run()],
        adapter=_adapter([], activity_state="error"),
    )

    body = client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()

    placement = body["tasks"][0]["board"]
    assert (placement["columnId"], placement["state"]) == ("building", "blocked")
    assert "Open the session" in placement["reason"]


def test_a_session_still_booting_is_not_an_unreadable_one() -> None:
    saga = _saga(_snapshot())
    adapter = _adapter(RuntimeError("the broker is not listening yet"))
    adapter.get_session.return_value = SimpleNamespace(
        activity_state=None, chat_endpoint=None, status="provisioning"
    )
    client = _client(saga, issues=[_issue()], runs=[_run()], adapter=adapter)

    body = client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()

    assert body["tasks"][0]["board"]["columnId"] == "building"
    assert body["tasks"][0]["board"]["state"] == "running"
    adapter.get_workflow_gates.assert_not_awaited()


def test_an_unreadable_session_is_reported_and_never_guessed() -> None:
    saga = _saga(_snapshot())
    client = _client(
        saga,
        issues=[_issue()],
        runs=[_run()],
        adapter=_adapter(RuntimeError("pod gone")),
    )

    body = client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()

    assert body["tasks"][0]["board"] == {
        "columnId": None,
        "state": "unknown",
        "gate": None,
        "reason": None,
        "lastDecision": None,
        "passedWorkflowIds": [],
    }
    coverage = _coverage(body, "workflowGates")
    assert coverage["status"] == "unavailable"
    assert coverage["error"]["code"] == "sourceReadFailed"


def test_no_forge_connection_is_reported_for_running_tickets() -> None:
    saga = _saga(_snapshot())
    client = _client(saga, issues=[_issue()], runs=[_run()], adapter=None)

    body = client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()

    assert body["tasks"][0]["board"]["state"] == "unknown"
    assert _coverage(body, "workflowGates")["error"]["code"] == "notConfigured"


def _start(
    client: TestClient,
    saga: Saga,
    issue_id: str = "issue-1",
    *,
    column_id: str | None = None,
) -> httpx.Response:
    return client.post(
        f"/api/v1/ting/work/project/{saga.id}/tasks/{issue_id}/start",
        headers={**HEADERS, "authorization": "Bearer token"},
        json={"columnId": column_id},
    )


def test_starting_a_ticket_creates_its_coordinator_and_links_its_runs() -> None:
    saga = _saga(_snapshot())
    adapter = _adapter([])
    coordinators = _Coordinators()
    client = _client(
        saga,
        issues=[_issue(status_type="unstarted")],
        runs=[],
        adapter=adapter,
        coordinators=coordinators,
    )

    response = _start(client, saga)

    assert response.status_code == 200
    assert response.json() == {"status": "coordinating", "sessionId": "coordinator-session"}
    request = adapter.spawn_session.await_args.args[0]
    assert request.workload_type == "ravn_flock"
    persona = request.workload_config["personas"][0]
    assert persona["name"] == "ticket-coordinator"
    assert "ISSUE-1: Ship the board" in persona["system_prompt_extra"]
    assert "Reviewed Delivery Flow" in request.initial_prompt
    assert saga.workflow_id.hex not in request.name
    metadata = request.workload_config["ravn_config"]["gateway"]["platform"]["a2a_default_metadata"]
    assert metadata["ticket"]["issueId"] == "issue-1"
    assert metadata["ticket"]["sagaId"] == str(saga.id)
    assert metadata["repo"] == "niuulabs/platform"
    assert coordinators.rows[(saga.id, "issue-1")].session_id == "coordinator-session"


def test_a_started_ticket_shows_as_starting_with_its_coordinator() -> None:
    saga = _saga(_snapshot())
    client = _client(
        saga,
        issues=[_issue(status_type="unstarted")],
        runs=[],
        adapter=_adapter([]),
        coordinators=_Coordinators(_coordinator(saga)),
    )

    task = client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()["tasks"][0]

    assert task["board"]["state"] == "starting"
    assert task["board"]["columnId"] == "building"
    assert task["coordinator"] == {
        "sessionId": "coordinator-session",
        "status": "active",
        "connectionId": None,
        "chatEndpoint": "ws://forge/s/session/session",
        "startError": None,
    }


def test_a_ticket_left_on_starting_can_be_asked_again() -> None:
    saga = _saga(_snapshot())
    adapter = _adapter([])
    client = _client(
        saga,
        issues=[_issue(status_type="unstarted")],
        runs=[],
        adapter=adapter,
        coordinators=_Coordinators(_coordinator(saga)),
    )

    response = _start(client, saga)

    assert response.status_code == 200
    adapter.spawn_session.assert_not_awaited()
    adapter.send_directed_room_message.assert_awaited_once()


def test_a_deleted_coordinator_session_reads_blocked_and_can_be_replaced() -> None:
    saga = _saga(_snapshot())
    adapter = _adapter([])
    adapter.get_session.return_value = None
    client = _client(
        saga,
        issues=[_issue(status_type="unstarted")],
        runs=[],
        adapter=adapter,
        coordinators=_Coordinators(_coordinator(saga)),
    )

    board = _board_task(client, saga)["board"]
    response = _start(client, saga)

    assert board["state"] == "blocked"
    assert board["reason"] == SESSION_GONE_REASON
    assert response.status_code == 200
    adapter.spawn_session.assert_awaited_once()
    adapter.send_directed_room_message.assert_not_awaited()


def test_a_coordinator_that_died_before_any_run_can_be_replaced() -> None:
    saga = _saga(_snapshot())
    adapter = _adapter([], activity_state="error")
    client = _client(
        saga,
        issues=[_issue(status_type="unstarted")],
        runs=[],
        adapter=adapter,
        coordinators=_Coordinators(_coordinator(saga)),
    )

    response = _start(client, saga)

    assert response.status_code == 200
    adapter.stop_session.assert_awaited_once()
    adapter.spawn_session.assert_awaited_once()
    adapter.send_directed_room_message.assert_not_awaited()


def test_a_ticket_with_a_run_in_progress_cannot_start_again() -> None:
    saga = _planned_saga()
    adapter = _adapter([])
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=adapter,
        campaigns=[_run_of(saga, DELIVERY, WorkflowCampaignStatus.RUNNING)],
        coordinators=_Coordinators(_coordinator(saga)),
    )

    response = _start(client, saga)

    assert response.status_code == 409
    assert "already has a run in progress" in response.json()["detail"]
    adapter.send_directed_room_message.assert_not_awaited()


def test_a_project_without_a_workflow_cannot_start_a_ticket() -> None:
    saga = replace(_saga(None), workflow_id=None)
    client = _client(saga, issues=[_issue()], runs=[], adapter=_adapter([]))

    assert _start(client, saga).status_code == 409


def test_a_coordinator_that_cannot_start_stays_loud() -> None:
    saga = _saga(_snapshot())
    adapter = _adapter([])
    adapter.spawn_session.side_effect = httpx.ConnectError("forge down")
    client = _client(saga, issues=[_issue(status_type="unstarted")], runs=[], adapter=adapter)

    response = _start(client, saga)

    assert response.status_code == 502
    assert "could not be started" in response.json()["detail"]


def test_a_coordinator_run_places_the_ticket_by_its_own_session() -> None:
    saga = _saga(_snapshot())
    adapter = _adapter([_pending_gate()])
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=adapter,
        campaigns=[_campaign(saga, status=WorkflowCampaignStatus.BLOCKED)],
        coordinators=_Coordinators(_coordinator(saga)),
    )

    task = client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()["tasks"][0]

    assert task["board"]["columnId"] == "code-review"
    assert task["workflowRun"]["taskId"] == "a2a-run"
    assert task["workflowRun"]["sessionId"] == "run-session"

    assert _decide(client, saga).status_code == 200
    assert adapter.resolve_workflow_gate.await_args.args[0] == "run-session"


def test_a_closed_ticket_closes_its_coordinator() -> None:
    saga = _saga(_snapshot())
    adapter = _adapter([])
    coordinators = _Coordinators(_coordinator(saga))
    client = _client(
        saga,
        issues=[_issue(status_type="completed")],
        runs=[],
        adapter=adapter,
        coordinators=coordinators,
    )

    client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS)

    adapter.stop_session.assert_awaited_once()
    assert coordinators.rows[(saga.id, "issue-1")].status == TicketCoordinatorStatus.CLOSED


def test_a_coordinator_that_would_not_close_is_reported() -> None:
    saga = _saga(_snapshot())
    adapter = _adapter([])
    adapter.stop_session.side_effect = httpx.ConnectError("forge down")
    client = _client(
        saga,
        issues=[_issue(status_type="completed")],
        runs=[],
        adapter=adapter,
        coordinators=_Coordinators(_coordinator(saga)),
    )

    body = client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()

    assert _coverage(body, "ticketCoordinators")["error"]["code"] == "closeFailed"


def _decide(client: TestClient, saga: Saga, **overrides: Any) -> httpx.Response:
    body = {
        "gateId": "gate-1",
        "activationId": "activation-1",
        "decision": "approve",
        **overrides,
    }
    return client.post(
        f"/api/v1/ting/work/project/{saga.id}/tasks/issue-1/gate-decision",
        headers={**HEADERS, "authorization": "Bearer token"},
        json=body,
    )


def test_approving_forwards_the_decision_to_the_session_that_owns_the_gate() -> None:
    saga = _saga(_snapshot())
    adapter = _adapter([_pending_gate()])
    client = _client(saga, issues=[_issue()], runs=[_run()], adapter=adapter)

    response = _decide(client, saga)

    assert response.status_code == 200
    assert response.json() == {"status": "resolved", "sessionId": "session-1", "gateId": "gate-1"}
    args, kwargs = adapter.resolve_workflow_gate.await_args
    assert args == ("session-1", "gate-1", "APPROVE")
    assert kwargs["notes"] == ""
    assert kwargs["source"] == "ting.board"
    assert kwargs["auth_token"] == "token"


def test_requesting_changes_needs_a_comment() -> None:
    saga = _saga(_snapshot())
    adapter = _adapter([_pending_gate()])
    client = _client(saga, issues=[_issue()], runs=[_run()], adapter=adapter)

    response = _decide(client, saga, decision="request_changes", notes="   ")

    assert response.status_code == 422
    adapter.resolve_workflow_gate.assert_not_awaited()

    response = _decide(client, saga, decision="request_changes", notes="Handle the empty case.")

    assert response.status_code == 200
    args, kwargs = adapter.resolve_workflow_gate.await_args
    assert args == ("session-1", "gate-1", "CHANGES_REQUESTED")
    assert kwargs["notes"] == "Handle the empty case."


@pytest.mark.parametrize(
    "overrides",
    [{"activationId": "activation-0"}, {"gateId": "gate-0"}],
)
def test_a_decision_on_a_gate_that_changed_is_refused(overrides: dict[str, str]) -> None:
    saga = _saga(_snapshot())
    adapter = _adapter([_pending_gate()])
    client = _client(saga, issues=[_issue()], runs=[_run()], adapter=adapter)

    response = _decide(client, saga, **overrides)

    assert response.status_code == 409
    assert "Review it again" in response.json()["detail"]
    adapter.resolve_workflow_gate.assert_not_awaited()


def test_a_ticket_with_no_waiting_gate_cannot_be_decided() -> None:
    saga = _saga(_snapshot())
    adapter = _adapter([])
    client = _client(saga, issues=[_issue()], runs=[_run()], adapter=adapter)

    assert _decide(client, saga).status_code == 409


def test_an_unknown_ticket_is_not_found() -> None:
    saga = _saga(_snapshot())
    client = _client(saga, issues=[_issue("issue-2")], runs=[], adapter=_adapter([]))

    assert _decide(client, saga).status_code == 404


def test_a_decision_without_a_forge_connection_is_unavailable() -> None:
    saga = _saga(_snapshot())
    client = _client(saga, issues=[_issue()], runs=[_run()], adapter=None)

    assert _decide(client, saga).status_code == 503


@pytest.mark.parametrize(("upstream", "expected"), [(403, 403), (409, 409), (500, 502)])
def test_a_refused_decision_stays_loud(upstream: int, expected: int) -> None:
    saga = _saga(_snapshot())
    adapter = _adapter([_pending_gate()])
    request = httpx.Request("POST", "http://forge.test/resolve")
    adapter.resolve_workflow_gate.side_effect = httpx.HTTPStatusError(
        "refused",
        request=request,
        response=httpx.Response(upstream, request=request),
    )
    client = _client(saga, issues=[_issue()], runs=[_run()], adapter=adapter)

    assert _decide(client, saga).status_code == expected


PLANNING = "Ticket Planning Flow"
DELIVERY = "Reviewed Delivery Flow"


def _bundled(name: str) -> Any:
    return next(item for item in load_system_workflows() if item.name == name)


def _pin(name: str) -> SagaBoardWorkflow:
    workflow = _bundled(name)
    return SagaBoardWorkflow(
        workflow_id=workflow.id,
        workflow_version=workflow.version,
        workflow_snapshot={"name": name, "version": workflow.version, "graph": workflow.graph},
    )


def _planned_saga() -> Saga:
    return replace(_saga(None), board_workflows=(_pin(PLANNING), _pin(DELIVERY)))


def _run_of(
    saga: Saga,
    name: str,
    status: WorkflowCampaignStatus,
    *,
    created_at: datetime = NOW,
) -> WorkflowCampaign:
    pin = _pin(name)
    return replace(
        _campaign(saga, status=status),
        workflow_id=pin.workflow_id,
        workflow_name=name,
        workflow_snapshot=pin.workflow_snapshot,
        created_at=created_at,
    )


def _board_task(client: TestClient, saga: Saga) -> dict[str, Any]:
    return client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()["tasks"][0]


def _plan_gate() -> dict[str, Any]:
    return {**_pending_gate(), "node_id": "plan-review-gate", "label": "Plan review"}


def test_a_board_runs_its_workflows_in_order_between_the_tracker_columns() -> None:
    saga = _planned_saga()
    client = _client(saga, issues=[_issue()], runs=[], adapter=_adapter([]))

    columns = client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()["board"][
        "columns"
    ]

    assert [column["id"] for column in columns] == [
        "backlog",
        "planning",
        "plan-review",
        "plan-record",
        "building",
        "code-review",
        "merging",
        "done",
    ]
    assert [column["workflowName"] for column in columns] == [
        None,
        PLANNING,
        PLANNING,
        PLANNING,
        DELIVERY,
        DELIVERY,
        DELIVERY,
        None,
    ]
    assert columns[1]["workflowId"] == str(_bundled(PLANNING).id)
    assert (columns[2]["approveColumnId"], columns[2]["changesColumnId"]) == (
        "plan-record",
        "planning",
    )


def test_a_project_without_a_list_still_shows_its_one_workflow() -> None:
    saga = _saga(_snapshot())
    client = _client(saga, issues=[_issue()], runs=[], adapter=_adapter([]))

    columns = client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()["board"][
        "columns"
    ]

    assert [column["id"] for column in columns] == [
        "backlog",
        "building",
        "code-review",
        "merging",
        "done",
    ]


def test_a_run_places_its_ticket_through_its_own_workflow() -> None:
    saga = _planned_saga()
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=_adapter([_plan_gate()]),
        campaigns=[_run_of(saga, PLANNING, WorkflowCampaignStatus.BLOCKED)],
    )

    board = _board_task(client, saga)["board"]

    assert board["columnId"] == "plan-review"
    assert board["state"] == "needsDecision"


def test_a_finished_workflow_leaves_the_ticket_ready_at_the_next_one() -> None:
    saga = _planned_saga()
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=_adapter([]),
        campaigns=[_run_of(saga, PLANNING, WorkflowCampaignStatus.COMPLETED)],
        coordinators=_Coordinators(_coordinator(saga)),
    )

    board = _board_task(client, saga)["board"]

    assert board["columnId"] == "building"
    assert board["state"] == "ready"
    assert board["passedWorkflowIds"] == [str(_bundled(PLANNING).id)]


def test_finishing_the_last_workflow_is_done() -> None:
    saga = _planned_saga()
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=_adapter([]),
        campaigns=[
            _run_of(saga, PLANNING, WorkflowCampaignStatus.COMPLETED),
            _run_of(
                saga,
                DELIVERY,
                WorkflowCampaignStatus.COMPLETED,
                created_at=NOW + timedelta(hours=1),
            ),
        ],
    )

    board = _board_task(client, saga)["board"]

    assert (board["columnId"], board["state"]) == ("done", "done")
    assert len(board["passedWorkflowIds"]) == 2


def test_a_dispatched_run_newer_than_a_workflow_run_decides_the_column() -> None:
    delivery = _pin(DELIVERY)
    saga = replace(
        _planned_saga(),
        workflow_id=delivery.workflow_id,
        workflow_snapshot=delivery.workflow_snapshot,
    )
    client = _client(
        saga,
        issues=[_issue()],
        runs=[replace(_run(), created_at=NOW + timedelta(hours=1))],
        adapter=_adapter([]),
        campaigns=[_run_of(saga, PLANNING, WorkflowCampaignStatus.COMPLETED)],
    )

    board = _board_task(client, saga)["board"]

    assert (board["columnId"], board["state"]) == ("building", "running")


def test_a_closed_ticket_is_done_while_workflows_are_left() -> None:
    saga = _planned_saga()
    client = _client(
        saga,
        issues=[_issue(status_type="completed")],
        runs=[],
        adapter=_adapter([]),
        campaigns=[_run_of(saga, PLANNING, WorkflowCampaignStatus.COMPLETED)],
    )

    assert _board_task(client, saga)["board"]["columnId"] == "done"


def test_a_pinned_run_with_columns_the_board_lost_is_not_guessed() -> None:
    saga = _planned_saga()
    run = _run_of(saga, PLANNING, WorkflowCampaignStatus.RUNNING)
    graph = deepcopy(run.workflow_snapshot["graph"])
    graph["board"]["columns"][0]["id"] = "drafting"
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=_adapter([]),
        campaigns=[replace(run, workflow_snapshot={"graph": graph})],
    )

    board = _board_task(client, saga)["board"]

    assert board["columnId"] is None
    assert board["state"] == "unknown"
    assert "does not match" in board["reason"]


def test_a_run_of_a_workflow_off_the_board_is_not_guessed() -> None:
    saga = replace(_planned_saga(), board_workflows=(_pin(PLANNING),))
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=_adapter([]),
        campaigns=[_run_of(saga, DELIVERY, WorkflowCampaignStatus.RUNNING)],
    )

    assert _board_task(client, saga)["board"]["state"] == "unknown"


def test_the_first_start_enters_the_dropped_workflow_through_a_new_coordinator() -> None:
    saga = _planned_saga()
    adapter = _adapter([])
    coordinators = _Coordinators()
    client = _client(
        saga,
        issues=[_issue(status_type="unstarted")],
        runs=[],
        adapter=adapter,
        coordinators=coordinators,
    )

    response = _start(client, saga, column_id="planning")

    assert response.status_code == 200
    assert PLANNING in adapter.spawn_session.await_args.args[0].initial_prompt
    saved = coordinators.rows[(saga.id, "issue-1")]
    assert saved.requested_workflow_id == str(_bundled(PLANNING).id)


def test_a_start_without_a_column_enters_the_first_workflow() -> None:
    saga = _planned_saga()
    adapter = _adapter([])
    client = _client(saga, issues=[_issue(status_type="unstarted")], runs=[], adapter=adapter)

    assert _start(client, saga).status_code == 200
    assert PLANNING in adapter.spawn_session.await_args.args[0].initial_prompt


def test_the_next_workflow_goes_to_the_same_coordinator() -> None:
    saga = _planned_saga()
    adapter = _adapter([])
    coordinators = _Coordinators(_coordinator(saga))
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=adapter,
        campaigns=[_run_of(saga, PLANNING, WorkflowCampaignStatus.COMPLETED)],
        coordinators=coordinators,
    )

    response = _start(client, saga)

    assert response.status_code == 200
    adapter.spawn_session.assert_not_awaited()
    brief = adapter.send_directed_room_message.await_args.args[2]
    assert DELIVERY in brief
    assert str(_bundled(DELIVERY).id) in brief

    # The ticket reads as starting there, not as ready, until the run appears.
    coordinators.rows[(saga.id, "issue-1")] = replace(
        coordinators.rows[(saga.id, "issue-1")],
        requested_at=NOW + timedelta(minutes=5),
    )
    board = _board_task(client, saga)["board"]
    assert (board["columnId"], board["state"]) == ("building", "starting")


def test_a_run_that_appears_replaces_the_request() -> None:
    saga = _planned_saga()
    coordinator = replace(
        _coordinator(saga),
        requested_workflow_id=str(_bundled(DELIVERY).id),
        requested_at=NOW,
    )
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=_adapter([]),
        campaigns=[
            _run_of(
                saga,
                DELIVERY,
                WorkflowCampaignStatus.RUNNING,
                created_at=NOW + timedelta(seconds=30),
            )
        ],
        coordinators=_Coordinators(coordinator),
    )

    board = _board_task(client, saga)["board"]

    assert (board["columnId"], board["state"]) == ("building", "running")


@pytest.mark.parametrize(
    ("column_id", "detail"),
    [
        ("merging", f"{DELIVERY} starts at Building"),
        ("plan-review", f"{PLANNING} starts at Planning"),
        ("done", "Choose a column that starts a workflow"),
        ("nowhere", "Choose a column that starts a workflow"),
    ],
)
def test_a_ticket_enters_a_workflow_only_at_its_first_column(column_id, detail) -> None:
    saga = _planned_saga()
    adapter = _adapter([])
    client = _client(saga, issues=[_issue(status_type="unstarted")], runs=[], adapter=adapter)

    response = _start(client, saga, column_id=column_id)

    assert response.status_code == 409
    assert response.json()["detail"] == detail
    adapter.spawn_session.assert_not_awaited()


def test_a_ticket_with_an_active_run_cannot_enter_another_workflow() -> None:
    saga = _planned_saga()
    adapter = _adapter([])
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=adapter,
        campaigns=[_run_of(saga, PLANNING, WorkflowCampaignStatus.RUNNING)],
        coordinators=_Coordinators(_coordinator(saga)),
    )

    response = _start(client, saga, column_id="building")

    assert response.status_code == 409
    assert "run in progress" in response.json()["detail"]
    adapter.send_directed_room_message.assert_not_awaited()


def test_a_failed_run_can_start_again_at_a_first_column() -> None:
    saga = _planned_saga()
    adapter = _adapter([])
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=adapter,
        campaigns=[_run_of(saga, PLANNING, WorkflowCampaignStatus.FAILED)],
        coordinators=_Coordinators(_coordinator(saga)),
    )

    assert _start(client, saga, column_id="planning").status_code == 200
    assert PLANNING in adapter.send_directed_room_message.await_args.args[2]


def test_a_done_ticket_cannot_start() -> None:
    saga = _planned_saga()
    client = _client(saga, issues=[_issue(status_type="completed")], runs=[], adapter=_adapter([]))

    assert _start(client, saga, column_id="planning").status_code == 409


def test_a_ticket_whose_place_cannot_be_read_cannot_start() -> None:
    saga = _planned_saga()
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=_adapter(RuntimeError("no broker")),
        campaigns=[_run_of(saga, PLANNING, WorkflowCampaignStatus.RUNNING)],
    )

    response = _start(client, saga, column_id="planning")

    assert response.status_code == 409
    assert "cannot read" in response.json()["detail"]


def test_a_coordinator_that_died_before_its_run_appeared_blocks_the_ticket() -> None:
    saga = _planned_saga()
    coordinator = replace(
        _coordinator(saga),
        requested_workflow_id=str(_bundled(DELIVERY).id),
        requested_at=NOW + timedelta(hours=1),
    )
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=_adapter([], activity_state="error"),
        campaigns=[_run_of(saga, PLANNING, WorkflowCampaignStatus.COMPLETED)],
        coordinators=_Coordinators(coordinator),
    )

    board = _board_task(client, saga)["board"]

    assert (board["columnId"], board["state"]) == ("building", "blocked")


def test_a_closed_coordinator_leaves_an_unstarted_ticket_in_the_backlog() -> None:
    saga = _planned_saga()
    closed = replace(_coordinator(saga), status=TicketCoordinatorStatus.CLOSED)
    client = _client(
        saga,
        issues=[_issue(status_type="unstarted")],
        runs=[],
        adapter=_adapter([]),
        coordinators=_Coordinators(closed),
    )

    board = _board_task(client, saga)["board"]

    assert (board["columnId"], board["state"]) == ("backlog", "notStarted")


def test_a_dispatched_run_reads_the_workflow_assigned_to_the_project() -> None:
    delivery = _bundled(DELIVERY)
    saga = replace(
        _planned_saga(),
        workflow_id=delivery.id,
        workflow_snapshot=_pin(DELIVERY).workflow_snapshot,
    )
    client = _client(saga, issues=[_issue()], runs=[_run()], adapter=_adapter([_pending_gate()]))

    board = _board_task(client, saga)["board"]

    assert (board["columnId"], board["state"]) == ("code-review", "needsDecision")


def _on_no_forge(name: str) -> MagicMock:
    forge = _adapter([], name=name)
    forge.get_session.return_value = None
    return forge


def test_a_run_on_another_forge_reads_its_real_state() -> None:
    saga = _saga(_snapshot())
    primary = _on_no_forge("forge-a")
    other = _adapter([_pending_gate()], name="forge-b")
    client = _client(
        saga, issues=[_issue()], runs=[_run()], adapter=primary, forges=[primary, other]
    )

    board = _board_task(client, saga)["board"]

    assert (board["columnId"], board["state"]) == ("code-review", "needsDecision")
    assert board["reason"] != SESSION_GONE_REASON
    primary.get_workflow_gates.assert_not_awaited()
    assert _decide(client, saga).status_code == 200
    other.resolve_workflow_gate.assert_awaited_once()
    primary.resolve_workflow_gate.assert_not_awaited()


def test_a_session_on_no_forge_reads_as_gone() -> None:
    saga = _saga(_snapshot())
    first, second = _on_no_forge("forge-a"), _on_no_forge("forge-b")
    client = _client(saga, issues=[_issue()], runs=[_run()], adapter=first, forges=[first, second])

    board = _board_task(client, saga)["board"]

    assert board["state"] == "blocked"
    assert board["reason"] == SESSION_GONE_REASON


def test_a_forge_that_could_not_be_asked_never_makes_a_session_gone() -> None:
    saga = _saga(_snapshot())
    answering = _on_no_forge("forge-a")
    down = _adapter([], name="forge-b")
    down.get_session.side_effect = httpx.ConnectError("forge-b is down")
    client = _client(
        saga, issues=[_issue()], runs=[_run()], adapter=answering, forges=[answering, down]
    )

    body = client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()

    assert body["tasks"][0]["board"]["state"] == "unknown"
    assert _coverage(body, "workflowGates")["error"]["code"] == "sourceReadFailed"


def test_a_forge_left_out_for_its_credential_never_makes_a_session_gone() -> None:
    saga = _saga(_snapshot())
    answering = _on_no_forge("forge-a")
    client = _client(
        saga, issues=[_issue()], runs=[_run()], adapter=answering, forges=[answering], unresolved=1
    )

    body = client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()

    assert body["tasks"][0]["board"]["state"] == "unknown"
    assert _coverage(body, "workflowGates")["error"]["code"] == "sourceReadFailed"


def test_no_registered_forge_makes_a_session_unreadable_not_gone() -> None:
    saga = _saga(_snapshot())
    client = _client(saga, issues=[_issue()], runs=[_run()], adapter=_adapter([]), forges=[])

    body = client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()

    assert body["tasks"][0]["board"]["state"] == "unknown"
    assert _coverage(body, "workflowGates")["status"] == "unavailable"


def test_a_coordinator_is_read_from_its_own_forge_without_asking_the_others() -> None:
    saga = _saga(_snapshot())
    other = _on_no_forge("forge-a")
    own = _adapter([], name="forge-b")
    client = _client(
        saga,
        issues=[_issue(status_type="unstarted")],
        runs=[],
        adapter=other,
        forges=[other, own],
        coordinators=_Coordinators(replace(_coordinator(saga), connection_id="forge-b")),
    )

    board = _board_task(client, saga)["board"]

    assert board["reason"] != SESSION_GONE_REASON
    other.get_session.assert_not_awaited()
    own.get_session.assert_awaited_once()


def test_a_campaign_is_read_from_the_forge_it_launched_on() -> None:
    saga = _saga(_snapshot())
    other = _on_no_forge("forge-a")
    own = _adapter([_pending_gate()], name="forge-b")
    campaign = replace(
        _campaign(saga, status=WorkflowCampaignStatus.RUNNING), connection_id="forge-b"
    )
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=other,
        forges=[other, own],
        campaigns=[campaign],
        coordinators=_Coordinators(replace(_coordinator(saga), connection_id="forge-b")),
    )

    board = _board_task(client, saga)["board"]

    assert (board["columnId"], board["state"]) == ("code-review", "needsDecision")
    other.get_session.assert_not_awaited()
    assert own.get_session.await_count == 2
    assert _decide(client, saga).status_code == 200
    own.resolve_workflow_gate.assert_awaited_once()


def test_a_connection_that_no_longer_resolves_is_unreadable_never_gone() -> None:
    saga = _saga(_snapshot())
    other = _on_no_forge("forge-a")
    client = _client(
        saga,
        issues=[_issue(status_type="unstarted")],
        runs=[],
        adapter=other,
        forges=[other],
        coordinators=_Coordinators(replace(_coordinator(saga), connection_id="forge-b")),
    )

    body = client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()

    assert body["tasks"][0]["board"]["reason"] != SESSION_GONE_REASON
    assert _coverage(body, "workflowGates")["error"]["code"] == "sourceReadFailed"
    other.get_session.assert_not_awaited()


def _reviews(client: TestClient, saga: Saga, issue_id: str = "issue-1") -> httpx.Response:
    return client.get(
        f"/api/v1/ting/work/project/{saga.id}/tasks/{issue_id}/reviews",
        headers={**HEADERS, "authorization": "Bearer token"},
    )


def _log(*frames: tuple[str, dict[str, Any]]) -> PublicSessionLogPage:
    return PublicSessionLogPage(
        entries=tuple(
            PublicSessionLogEntry(
                session_id="run-session", seq=seq, kind=kind, payload=payload, ts=NOW
            )
            for seq, (kind, payload) in enumerate(frames, start=1)
        ),
        scanned_through=len(frames),
        has_more=False,
    )


def test_a_ticket_review_is_read_back_from_its_runs_log() -> None:
    saga = _planned_saga()
    adapter = _adapter([])
    adapter.get_public_session_log_page = AsyncMock(
        return_value=_log(
            ("room_outcome", {"fields": {"plan": "Trace every tick"}}),
            ("workflow_gate", {"gate": _plan_gate()}),
        )
    )
    run = _run_of(saga, PLANNING, WorkflowCampaignStatus.BLOCKED)
    client = _client(saga, issues=[_issue()], runs=[], adapter=adapter, campaigns=[run])

    response = _reviews(client, saga)

    assert response.status_code == 200
    body = response.json()
    assert body["unreadableRuns"] == []
    (review,) = body["reviews"]
    assert review["columnId"] == "plan-review"
    assert review["taskId"] == "a2a-run"
    assert review["panels"] == [{"widget": "document", "title": "Plan", "field": "plan"}]
    assert review["revisions"][0]["fields"] == {"plan": "Trace every tick"}
    assert review["revisions"][0]["status"] == "pending"
    assert review["chatEndpoint"] == "ws://forge/s/session/session"


def test_another_tickets_runs_are_not_this_tickets_review() -> None:
    saga = _planned_saga()
    adapter = _adapter([])
    adapter.get_public_session_log_page = AsyncMock(return_value=_log())
    run = _run_of(saga, PLANNING, WorkflowCampaignStatus.BLOCKED)
    client = _client(saga, issues=[_issue()], runs=[], adapter=adapter, campaigns=[run])

    response = _reviews(client, saga, issue_id="issue-2")

    assert response.json() == {"reviews": [], "unreadableRuns": []}
    adapter.get_public_session_log_page.assert_not_awaited()


def test_a_run_whose_session_is_gone_is_named_not_dropped() -> None:
    saga = _planned_saga()
    adapter = _adapter([])
    adapter.get_session = AsyncMock(return_value=None)
    run = _run_of(saga, PLANNING, WorkflowCampaignStatus.COMPLETED)
    client = _client(saga, issues=[_issue()], runs=[], adapter=adapter, campaigns=[run])

    assert _reviews(client, saga).json() == {
        "reviews": [],
        "unreadableRuns": ["Ticket Planning Flow"],
    }


def test_a_forge_that_cannot_be_asked_stays_loud() -> None:
    saga = _planned_saga()
    adapter = _adapter([])
    adapter.get_session = AsyncMock(side_effect=httpx.ConnectError("down"))
    run = _run_of(saga, PLANNING, WorkflowCampaignStatus.BLOCKED)
    client = _client(saga, issues=[_issue()], runs=[], adapter=adapter, campaigns=[run])

    assert _reviews(client, saga).status_code == 503


def test_reviews_need_a_forge() -> None:
    saga = _planned_saga()
    client = _client(saga, issues=[_issue()], runs=[], adapter=None)

    assert _reviews(client, saga).status_code == 503


def test_board_columns_carry_their_panels() -> None:
    saga = _planned_saga()
    client = _client(saga, issues=[_issue()], runs=[], adapter=_adapter([]))

    board = client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()["board"]

    column = next(item for item in board["columns"] if item["id"] == "plan-review")
    assert column["panels"] == [{"widget": "document", "title": "Plan", "field": "plan"}]
