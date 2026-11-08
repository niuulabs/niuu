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

from niuu.review_widgets import ReviewWidget
from ting.api.research import resolve_workflow_campaign_repo
from ting.api.sagas import resolve_saga_repo
from ting.api.work import create_work_router, resolve_work_trackers
from ting.config import AuthConfig, Settings
from ting.domain.exceptions import WorkflowDocumentError
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
from ting.domain.workflow_steps import (
    ActorKind,
    GateActor,
    GateDecision,
    GateRefusedError,
    RunEvent,
    RunState,
    RunStateError,
    StepInput,
    StepKind,
    StepState,
    WaitingOn,
    WaitingOnKind,
    WorkflowStep,
)
from ting.ports.saga_repository import SagaRepository
from ting.ports.ticket_coordinator_repository import TicketCoordinatorRepository
from ting.ports.tracker import TrackerPort, TrackerResolution
from ting.ports.volundr import ForgeProjectRef
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
        session_id="",
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
        session_id="",
        session_name="",
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


class FakeRuns:
    """The step engine as the board sees it: each run's gates, and what was decided."""

    def __init__(self, gates: list[dict[str, Any]] | Exception = ()) -> None:
        self._gates = gates
        self.resolve_gate = AsyncMock()
        self.launch = AsyncMock()
        self.ledger = SimpleNamespace(events=(), steps=())

    async def gates(self, campaign):
        if isinstance(self._gates, Exception):
            raise self._gates
        return list(self._gates)

    async def pending_gates(self, campaign):
        return [gate for gate in await self.gates(campaign) if gate["status"] == "pending"]

    async def state(self, run_id):
        return self.ledger


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
    runs_engine: FakeRuns | None = None,
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
    # The gates of an engine run come from the adapter's list, as they did from its session.
    app.state.workflow_runs = runs_engine or FakeRuns(
        getattr(adapter, "gates", ()) if adapter is not None else ()
    )
    app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
    campaign_repo = MagicMock(spec=WorkflowCampaignRepository)
    campaign_repo.list_campaigns = AsyncMock(return_value=campaigns or [])
    campaign_repo.list_ticket_campaigns = partial(
        WorkflowCampaignRepository.list_ticket_campaigns, campaign_repo
    )
    campaign_repo.list_campaign_summaries = partial(
        WorkflowCampaignRepository.list_campaign_summaries, campaign_repo
    )
    campaign_repo.save_ticket_campaign = AsyncMock(return_value=None)
    campaign_repo.save_campaign = AsyncMock(side_effect=lambda item: item)
    campaign_repo.get_campaign_by_slug = AsyncMock(
        side_effect=lambda slug, owner_id: next(
            (item for item in campaigns or [] if item.slug == slug and item.owner_id == owner_id),
            None,
        )
    )
    app.state.campaign_repo = campaign_repo
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
    adapter.ensure_project = AsyncMock(
        side_effect=lambda project_id, name, **_: ForgeProjectRef(
            id=project_id, name=name, instance_id=name
        )
    )
    adapter.send_directed_room_message = AsyncMock()
    adapter.start_session = AsyncMock()
    adapter.stop_session = AsyncMock()
    adapter.gates = gates
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
        runs=[],
        adapter=adapter,
        campaigns=[_campaign(saga, status=WorkflowCampaignStatus.BLOCKED)],
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
    adapter = _adapter([])
    adapter.get_session.return_value = SimpleNamespace(
        activity_state=None, chat_endpoint=None, status="provisioning"
    )
    client = _client(saga, issues=[_issue()], runs=[_run()], adapter=adapter)

    body = client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()

    assert body["tasks"][0]["board"]["columnId"] == "building"
    assert body["tasks"][0]["board"]["state"] == "running"


def test_a_run_whose_ledger_cannot_be_read_is_reported_and_never_guessed() -> None:
    saga = _saga(_snapshot())
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=_adapter(RuntimeError("ledger gone")),
        campaigns=[_campaign(saga, status=WorkflowCampaignStatus.RUNNING)],
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


def test_a_dispatched_session_on_no_forge_connection_is_reported_for_running_tickets() -> None:
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


def _engine(client: TestClient) -> FakeRuns:
    return client.app.state.workflow_runs


def _saved_run(client: TestClient) -> WorkflowCampaign:
    return client.app.state.campaign_repo.save_ticket_campaign.await_args.args[0]


def test_starting_a_ticket_launches_its_workflow_run() -> None:
    saga = _saga(_snapshot())
    coordinators = _Coordinators()
    client = _client(
        saga,
        issues=[_issue(status_type="unstarted")],
        runs=[],
        adapter=_adapter([]),
        coordinators=coordinators,
    )

    response = _start(client, saga)

    assert response.status_code == 200
    assert response.json() == {"status": "coordinating", "sessionId": ""}
    campaign = _saved_run(client)
    assert campaign.session_id == ""
    assert campaign.connection_id == "forge-a"
    assert campaign.metadata["ticket"]["issueId"] == "issue-1"
    launched, payload = _engine(client).launch.await_args.args
    assert launched is campaign
    assert payload["prompt"].startswith("Ticket ISSUE-1")
    record = coordinators.rows[(saga.id, "issue-1")]
    assert record.session_id == ""
    assert record.requested_workflow_id == str(saga.workflow_id)


def test_a_workflow_that_cannot_start_is_refused_and_remembered_on_the_ticket() -> None:
    saga = _saga(_snapshot())
    coordinators = _Coordinators()
    client = _client(
        saga,
        issues=[_issue(status_type="unstarted")],
        runs=[],
        adapter=_adapter([]),
        coordinators=coordinators,
    )
    _engine(client).launch.side_effect = WorkflowDocumentError("no persona pinned for coder")

    response = _start(client, saga)

    assert response.status_code == 422
    assert response.json() == {"detail": "no persona pinned for coder"}
    record = coordinators.rows[(saga.id, "issue-1")]
    assert record.start_error.endswith("no persona pinned for coder")


def test_a_started_ticket_shows_as_starting() -> None:
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
        "sessionId": "",
        "status": "active",
        "connectionId": None,
        "chatEndpoint": None,
        "startError": None,
    }


def test_a_ticket_left_on_starting_can_be_asked_again() -> None:
    saga = _saga(_snapshot())
    client = _client(
        saga,
        issues=[_issue(status_type="unstarted")],
        runs=[],
        adapter=_adapter([]),
        coordinators=_Coordinators(_coordinator(saga)),
    )

    assert _start(client, saga).status_code == 200
    _engine(client).launch.assert_awaited_once()


def test_a_ticket_with_a_run_in_progress_cannot_start_again() -> None:
    saga = _planned_saga()
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=_adapter([]),
        campaigns=[_run_of(saga, DELIVERY, WorkflowCampaignStatus.RUNNING)],
        coordinators=_Coordinators(_coordinator(saga)),
    )

    response = _start(client, saga)

    assert response.status_code == 409
    assert "already has a run in progress" in response.json()["detail"]
    _engine(client).launch.assert_not_awaited()


def test_a_project_without_a_workflow_cannot_start_a_ticket() -> None:
    saga = replace(_saga(None), workflow_id=None)
    client = _client(saga, issues=[_issue()], runs=[], adapter=_adapter([]))

    assert _start(client, saga).status_code == 409


def test_a_run_places_the_ticket_by_its_own_ledger_and_answers_its_gate() -> None:
    saga = _saga(_snapshot())
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=_adapter([_pending_gate()]),
        campaigns=[_campaign(saga, status=WorkflowCampaignStatus.BLOCKED)],
        coordinators=_Coordinators(_coordinator(saga)),
    )

    task = client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()["tasks"][0]

    assert task["board"]["columnId"] == "code-review"
    assert task["workflowRun"]["taskId"] == "a2a-run"
    assert task["workflowRun"]["sessionId"] is None

    assert _decide(client, saga).status_code == 200
    campaign, node_id, decision = _engine(client).resolve_gate.await_args.args
    assert (campaign.slug, node_id, decision) == (
        "a2a-run",
        "delivery-code-gate",
        GateDecision.APPROVE,
    )


def test_a_closed_ticket_closes_its_record() -> None:
    saga = _saga(_snapshot())
    coordinators = _Coordinators(_coordinator(saga))
    client = _client(
        saga,
        issues=[_issue(status_type="completed")],
        runs=[],
        adapter=_adapter([]),
        coordinators=coordinators,
    )

    client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS)

    assert coordinators.rows[(saga.id, "issue-1")].status == TicketCoordinatorStatus.CLOSED


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


def _waiting_client(saga: Saga, *, gates=None, **overrides) -> TestClient:
    return _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=_adapter([_pending_gate()] if gates is None else gates),
        campaigns=[_campaign(saga, status=WorkflowCampaignStatus.BLOCKED)],
        **overrides,
    )


def test_approving_records_the_decision_on_the_run_that_owns_the_gate() -> None:
    saga = _saga(_snapshot())
    client = _waiting_client(saga)

    response = _decide(client, saga)

    assert response.status_code == 200
    assert response.json() == {
        "status": "resolved",
        "sessionId": "",
        "gateId": "gate-1",
    }
    args, kwargs = _engine(client).resolve_gate.await_args
    assert args[0].slug == "a2a-run"
    assert args[1:] == ("delivery-code-gate", GateDecision.APPROVE)
    assert kwargs == {"by": GateActor(ActorKind.PERSON, "owner-1"), "note": ""}


def test_a_decision_names_the_person_by_the_email_they_signed_in_with() -> None:
    saga = _planned_saga()
    client = _run_client(_ticketless_run(saga), _adapter([_pending_gate()]))

    response = client.post(
        "/api/v1/ting/work/runs/a2a-run/gate-decision",
        headers={**HEADERS, "x-auth-email": "jozef@niuu.world", "authorization": "Bearer token"},
        json={"gateId": "gate-1", "activationId": "activation-1", "decision": "approve"},
    )

    assert response.status_code == 200
    by = _engine(client).resolve_gate.await_args.kwargs["by"]
    assert by == GateActor(ActorKind.PERSON, "owner-1", "jozef@niuu.world")


def test_requesting_changes_needs_a_comment() -> None:
    saga = _saga(_snapshot())
    client = _waiting_client(saga)

    response = _decide(client, saga, decision="request_changes", notes="   ")

    assert response.status_code == 422
    _engine(client).resolve_gate.assert_not_awaited()

    response = _decide(client, saga, decision="request_changes", notes="Handle the empty case.")

    assert response.status_code == 200
    args, kwargs = _engine(client).resolve_gate.await_args
    assert args[2] == GateDecision.REQUEST_CHANGES
    assert kwargs["note"] == "Handle the empty case."


@pytest.mark.parametrize(
    "overrides",
    [{"activationId": "activation-0"}, {"gateId": "gate-0"}],
)
def test_a_decision_on_a_gate_that_changed_is_refused(overrides: dict[str, str]) -> None:
    saga = _saga(_snapshot())
    client = _waiting_client(saga)

    response = _decide(client, saga, **overrides)

    assert response.status_code == 409
    assert "Review it again" in response.json()["detail"]
    _engine(client).resolve_gate.assert_not_awaited()


def test_a_ticket_with_no_waiting_gate_cannot_be_decided() -> None:
    saga = _saga(_snapshot())
    client = _waiting_client(saga, gates=[])

    assert _decide(client, saga).status_code == 409


def test_an_unknown_ticket_is_not_found() -> None:
    saga = _saga(_snapshot())
    client = _client(saga, issues=[_issue("issue-2")], runs=[], adapter=_adapter([]))

    assert _decide(client, saga).status_code == 404


@pytest.mark.parametrize("refusal", [GateRefusedError("no open gate"), RunStateError("finished")])
def test_a_decision_the_engine_refuses_stays_loud(refusal: Exception) -> None:
    saga = _saga(_snapshot())
    client = _waiting_client(saga)
    _engine(client).resolve_gate.side_effect = refusal

    response = _decide(client, saga)

    assert response.status_code == 409
    assert response.json() == {"detail": str(refusal)}


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


def test_the_first_start_into_a_dropped_workflow_launches_it() -> None:
    saga = _planned_saga()
    coordinators = _Coordinators()
    client = _client(
        saga,
        issues=[_issue(status_type="unstarted")],
        runs=[],
        adapter=_adapter([]),
        coordinators=coordinators,
    )

    response = _start(client, saga, column_id="planning")

    assert response.status_code == 200
    assert _saved_run(client).workflow_name == PLANNING
    _engine(client).launch.assert_awaited_once()
    assert coordinators.rows[(saga.id, "issue-1")].requested_workflow_id == str(
        _bundled(PLANNING).id
    )


def test_a_start_without_a_column_launches_the_first_workflow() -> None:
    saga = _planned_saga()
    client = _client(saga, issues=[_issue(status_type="unstarted")], runs=[], adapter=_adapter([]))

    response = _start(client, saga)

    assert response.status_code == 200
    assert _saved_run(client).workflow_name == PLANNING


def test_the_next_workflow_launches_on_the_version_the_board_pinned() -> None:
    saga = _planned_saga()
    coordinators = _Coordinators(_coordinator(saga))
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=_adapter([]),
        campaigns=[_run_of(saga, PLANNING, WorkflowCampaignStatus.COMPLETED)],
        coordinators=coordinators,
    )

    response = _start(client, saga)

    assert response.status_code == 200
    campaign = _saved_run(client)
    assert campaign.workflow_name == DELIVERY
    assert campaign.workflow_snapshot == _pin(DELIVERY).workflow_snapshot
    _engine(client).launch.assert_awaited_once()


def test_a_ticket_with_a_pending_request_reads_as_starting() -> None:
    saga = _planned_saga()
    coordinators = _Coordinators(
        replace(
            _coordinator(saga), requested_workflow_id=str(_bundled(DELIVERY).id), requested_at=NOW
        )
    )
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=_adapter([]),
        campaigns=[_run_of(saga, PLANNING, WorkflowCampaignStatus.COMPLETED)],
        coordinators=coordinators,
    )

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
    _engine(client).launch.assert_not_awaited()


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
    _engine(client).launch.assert_not_awaited()


def test_a_failed_run_does_not_hold_back_a_new_start() -> None:
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

    response = _start(client, saga, column_id="planning")

    assert response.status_code == 200
    _engine(client).launch.assert_awaited_once()


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


def test_a_start_that_failed_blocks_the_ticket_with_its_reason() -> None:
    saga = _planned_saga()
    coordinator = replace(
        _coordinator(saga),
        requested_workflow_id=str(_bundled(DELIVERY).id),
        requested_at=NOW + timedelta(hours=1),
        start_error="Reviewed Delivery Flow could not start: no session",
    )
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=_adapter([]),
        campaigns=[_run_of(saga, PLANNING, WorkflowCampaignStatus.COMPLETED)],
        coordinators=_Coordinators(coordinator),
    )

    board = _board_task(client, saga)["board"]

    assert (board["columnId"], board["state"]) == ("building", "blocked")
    assert board["reason"] == "Reviewed Delivery Flow could not start: no session"


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
    client = _client(saga, issues=[_issue()], runs=[_run()], adapter=_adapter([]))

    board = _board_task(client, saga)["board"]

    assert (board["columnId"], board["state"]) == ("building", "running")


def _on_no_forge(name: str) -> MagicMock:
    forge = _adapter([], name=name)
    forge.get_session.return_value = None
    return forge


def test_a_dispatched_run_on_another_forge_reads_its_real_state() -> None:
    saga = _saga(_snapshot())
    primary = _on_no_forge("forge-a")
    other = _adapter([], name="forge-b")
    client = _client(
        saga, issues=[_issue()], runs=[_run()], adapter=primary, forges=[primary, other]
    )

    board = _board_task(client, saga)["board"]

    assert (board["columnId"], board["state"]) == ("building", "running")
    assert board["reason"] != SESSION_GONE_REASON


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


def test_a_run_on_the_step_engine_is_read_from_its_ledger_without_any_forge() -> None:
    saga = _saga(_snapshot())
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=None,
        campaigns=[_campaign(saga, status=WorkflowCampaignStatus.RUNNING)],
    )

    body = client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()

    assert body["tasks"][0]["board"]["state"] == "running"
    assert _coverage(body, "workflowGates")["status"] == "complete"


def test_runs_are_not_read_where_the_step_engine_is_not_configured() -> None:
    saga = _saga(_snapshot())
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=None,
        campaigns=[_campaign(saga, status=WorkflowCampaignStatus.RUNNING)],
    )
    del client.app.state.workflow_runs

    body = client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()

    assert body["tasks"][0]["board"]["state"] == "unknown"
    assert _coverage(body, "workflowGates")["error"]["code"] == "notConfigured"


PLAN_PANEL = {
    "widget": "document",
    "title": "Plan",
    "source": {"kind": "outcome", "value": "plan"},
    "files": [],
}


def _reviews(client: TestClient, saga: Saga, issue_id: str = "issue-1") -> httpx.Response:
    return client.get(
        f"/api/v1/ting/work/project/{saga.id}/tasks/{issue_id}/reviews",
        headers={**HEADERS, "authorization": "Bearer token"},
    )


def _gate_ledger(
    campaign: WorkflowCampaign,
    *,
    node_id: str = "plan-review-gate",
    outcome: dict[str, Any] | None = None,
    note: str = "",
    state: StepState = StepState.WAITING,
    verdict: str | None = None,
) -> RunState:
    """A run's ledger with one outcome event and the gate it opened."""
    event = RunEvent(
        run_id=campaign.id,
        seq=1,
        event_type="plan.drafted",
        generation=0,
        created_at=NOW,
        payload={
            "node_id": "plan",
            "member": "planner",
            "verdict": "drafted",
            "outcome": outcome or {},
            "note": note,
        },
    )
    gate = WorkflowStep(
        id=uuid4(),
        run_id=campaign.id,
        node_id=node_id,
        member=None,
        kind=StepKind.GATE,
        attempt=1,
        generation=0,
        state=state,
        created_at=NOW,
        activated_at=NOW,
        inputs=(
            StepInput(
                seq=1, event_type="plan.drafted", edge_id="e1", node_id="plan", member="planner"
            ),
        ),
        waiting_on=(
            WaitingOn(WaitingOnKind.PERSON, "person", NOW) if state is StepState.WAITING else None
        ),
        verdict=verdict,
    )
    return RunState(run_id=campaign.id, steps=(gate,), events=(event,))


def test_a_ticket_review_is_read_back_from_the_runs_ledger() -> None:
    saga = _planned_saga()
    run = _run_of(saga, PLANNING, WorkflowCampaignStatus.BLOCKED)
    client = _client(saga, issues=[_issue()], runs=[], adapter=_adapter([]), campaigns=[run])
    ledger = _gate_ledger(run, outcome={"plan": "Trace every tick"})
    _engine(client).ledger = ledger

    response = _reviews(client, saga)

    assert response.status_code == 200
    body = response.json()
    assert body["unreadableRuns"] == []
    (review,) = body["reviews"]
    assert review["columnId"] == "plan-review"
    assert review["taskId"] == "a2a-run"
    assert review["sessionId"] == ""
    assert review["panels"] == [PLAN_PANEL]
    assert review["revisions"][0]["fields"] == {"plan": "Trace every tick"}
    assert review["revisions"][0]["status"] == "pending"
    assert review["revisions"][0]["activationId"] == str(ledger.steps[0].id)
    assert review["chatEndpoint"] is None


def test_a_decided_gate_is_a_revision_with_its_decision() -> None:
    saga = _planned_saga()
    run = _run_of(saga, PLANNING, WorkflowCampaignStatus.RUNNING)
    client = _client(saga, issues=[_issue()], runs=[], adapter=_adapter([]), campaigns=[run])
    _engine(client).ledger = _gate_ledger(
        run,
        outcome={"plan": "v1"},
        note="Rename it.",
        state=StepState.DONE,
        verdict="request_changes",
    )

    (review,) = _reviews(client, saga).json()["reviews"]

    assert review["revisions"][0]["status"] == "changes_requested"
    assert review["revisions"][0]["notes"] == ""


def test_another_tickets_runs_are_not_this_tickets_review() -> None:
    saga = _planned_saga()
    run = _run_of(saga, PLANNING, WorkflowCampaignStatus.BLOCKED)
    client = _client(saga, issues=[_issue()], runs=[], adapter=_adapter([]), campaigns=[run])
    _engine(client).ledger = _gate_ledger(run)

    response = _reviews(client, saga, issue_id="issue-2")

    assert response.json() == {"reviews": [], "unreadableRuns": [], "planSlug": None}


def test_reviews_need_the_step_engine() -> None:
    saga = _planned_saga()
    client = _client(saga, issues=[_issue()], runs=[], adapter=None)
    del client.app.state.workflow_runs

    assert _reviews(client, saga).status_code == 503


def test_board_columns_carry_their_panels() -> None:
    saga = _planned_saga()
    client = _client(saga, issues=[_issue()], runs=[], adapter=_adapter([]))

    board = client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()["board"]

    column = next(item for item in board["columns"] if item["id"] == "plan-review")
    assert column["panels"] == [PLAN_PANEL]


def test_reviews_list_runs_across_projects_from_their_campaigns() -> None:
    saga = _planned_saga()
    run = replace(
        _run_of(saga, PLANNING, WorkflowCampaignStatus.BLOCKED),
        metadata={
            **_run_of(saga, PLANNING, WorkflowCampaignStatus.BLOCKED).metadata,
            "pending_workflow_gates": [{"label": "Plan review"}],
        },
    )
    adapter = _adapter([])
    client = _client(saga, issues=[_issue()], runs=[], adapter=adapter, campaigns=[run])
    client.app.dependency_overrides[resolve_saga_repo]().list_sagas = AsyncMock(return_value=[saga])

    response = client.get("/api/v1/ting/work/reviews", headers=HEADERS)

    assert response.status_code == 200
    (item,) = response.json()["items"]
    assert item["state"] == "waiting"
    assert item["identifier"] == "ISSUE-1"
    assert item["projectName"] == saga.name


def test_reviews_list_a_waiting_run_that_has_no_ticket() -> None:
    saga = _planned_saga()
    run = _ticketless_run(saga)
    client = _client(saga, issues=[], runs=[], adapter=_adapter([]), campaigns=[run])
    client.app.dependency_overrides[resolve_saga_repo]().list_sagas = AsyncMock(return_value=[saga])

    (item,) = client.get("/api/v1/ting/work/reviews", headers=HEADERS).json()["items"]

    assert item["kind"] == "run"
    assert item["state"] == "waiting"
    assert item["taskId"] == "a2a-run"
    assert item["title"] == "Plan NIU-1125 only"
    assert item["startedBy"] == "regin"


def _ticketless_run(saga: Saga, **overrides: Any) -> WorkflowCampaign:
    return replace(
        _campaign(saga, status=WorkflowCampaignStatus.BLOCKED),
        workflow_snapshot={},
        metadata={
            "prompt": "Plan NIU-1125 only",
            "actor_id": "system:serviceaccount:nats:regin",
            "repo": "https://github.com/niuulabs/niuu",
            "branch": "regin",
            "pending_workflow_gates": [{"node_id": "merge-gate"}],
        },
        **overrides,
    )


def _run_client(
    run: WorkflowCampaign | None, adapter: MagicMock, saga: Saga | None = None
) -> TestClient:
    client = _client(saga or _planned_saga(), issues=[], runs=[], adapter=adapter)
    repo = client.app.dependency_overrides[resolve_workflow_campaign_repo]()
    repo.get_campaign_by_slug = AsyncMock(return_value=run)
    return client


def _decide_run(client: TestClient, **overrides: Any) -> httpx.Response:
    return client.post(
        "/api/v1/ting/work/runs/a2a-run/gate-decision",
        headers={**HEADERS, "authorization": "Bearer token"},
        json={
            "gateId": "gate-1",
            "activationId": "activation-1",
            "decision": "approve",
            **overrides,
        },
    )


def test_a_run_with_no_ticket_shows_its_gate_for_review() -> None:
    saga = _planned_saga()
    run = _ticketless_run(saga)
    client = _run_client(run, _adapter([]))
    ledger = _gate_ledger(run, node_id="merge-gate", note="Ready to merge")
    _engine(client).ledger = ledger

    response = client.get(
        "/api/v1/ting/work/runs/a2a-run/reviews",
        headers={**HEADERS, "authorization": "Bearer token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["run"]["title"] == "Plan NIU-1125 only"
    assert body["run"]["startedBy"] == "regin"
    assert body["run"]["sessionId"] == ""
    (review,) = body["reviews"]
    assert review["title"] == "merge-gate"
    assert review["chatEndpoint"] is None
    assert review["revisions"][0]["activationId"] == str(ledger.steps[0].id)
    assert review["revisions"][0]["summary"] == "Ready to merge"
    assert body["unreadableRuns"] == []


def test_another_owners_run_is_not_found() -> None:
    client = _run_client(None, _adapter([]))
    repo = client.app.dependency_overrides[resolve_workflow_campaign_repo]()

    response = client.get("/api/v1/ting/work/runs/a2a-run/reviews", headers=HEADERS)

    assert response.status_code == 404
    assert _decide_run(client).status_code == 404
    assert repo.get_campaign_by_slug.await_args.kwargs == {"owner_id": "owner-1"}


def test_a_run_with_no_open_gate_has_nothing_to_answer() -> None:
    saga = _planned_saga()
    client = _run_client(_ticketless_run(saga), _adapter([]))

    body = client.get("/api/v1/ting/work/runs/a2a-run/reviews", headers=HEADERS).json()

    assert body["reviews"] == []
    assert _decide_run(client).status_code == 409


def test_approving_a_run_records_the_decision_on_its_ledger() -> None:
    saga = _planned_saga()
    client = _run_client(_ticketless_run(saga), _adapter([_pending_gate()]))

    response = _decide_run(client)

    assert response.status_code == 200
    assert response.json() == {"status": "resolved", "sessionId": "", "gateId": "gate-1"}
    args, kwargs = _engine(client).resolve_gate.await_args
    assert args[0].slug == "a2a-run"
    assert args[1:] == ("delivery-code-gate", GateDecision.APPROVE)
    assert kwargs == {"by": GateActor(ActorKind.PERSON, "owner-1"), "note": ""}


def test_asking_a_run_for_changes_needs_a_comment() -> None:
    saga = _planned_saga()
    client = _run_client(_ticketless_run(saga), _adapter([_pending_gate()]))

    assert _decide_run(client, decision="request_changes", notes="  ").status_code == 422
    _engine(client).resolve_gate.assert_not_awaited()

    response = _decide_run(client, decision="request_changes", notes="Handle the empty case.")

    assert response.status_code == 200
    assert _engine(client).resolve_gate.await_args.kwargs["note"] == "Handle the empty case."


@pytest.mark.parametrize(
    "gates",
    [
        [],
        [{**_pending_gate(), "activation_id": "activation-0"}],
        [{**_pending_gate(), "id": "gate-0"}],
        [{**_pending_gate(), "status": "approved"}],
    ],
)
def test_a_decision_on_a_run_gate_that_changed_is_refused(gates: list[dict[str, Any]]) -> None:
    saga = _planned_saga()
    client = _run_client(_ticketless_run(saga), _adapter(gates))

    response = _decide_run(client)

    assert response.status_code == 409
    assert "Review it again" in response.json()["detail"]
    _engine(client).resolve_gate.assert_not_awaited()


def test_a_run_gate_the_engine_refuses_stays_loud() -> None:
    saga = _planned_saga()
    client = _run_client(_ticketless_run(saga), _adapter([_pending_gate()]))
    _engine(client).resolve_gate.side_effect = GateRefusedError("gate has no open request")

    response = _decide_run(client)

    assert response.status_code == 409
    assert response.json() == {"detail": "gate has no open request"}


def test_a_run_gate_cannot_be_answered_where_the_step_engine_is_not_configured() -> None:
    saga = _planned_saga()
    client = _run_client(_ticketless_run(saga), _adapter([_pending_gate()]))
    del client.app.state.workflow_runs

    assert _decide_run(client).status_code == 503


def test_a_ticket_review_names_the_plan_its_project_came_from() -> None:
    saga = replace(_planned_saga(), plan_slug="plan-1")
    adapter = _adapter([])
    client = _client(saga, issues=[_issue()], runs=[], adapter=adapter, campaigns=[])

    assert _reviews(client, saga).json()["planSlug"] == "plan-1"


def test_a_widget_page_is_served_as_data_only_when_installed() -> None:
    saga = _planned_saga()
    client = _client(saga, issues=[_issue()], runs=[], adapter=_adapter([]))
    client.app.state.review_widgets = {
        "acme-print/quality": ReviewWidget(
            module="acme-print", name="quality", title="Quality", html="<p>Stills</p>"
        )
    }
    url = "/api/v1/ting/work/review-widgets"

    response = client.get(f"{url}/acme-print/quality", headers=HEADERS)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["html"] == "<p>Stills</p>"
    missing = client.get(f"{url}/acme-print/other", headers=HEADERS)
    assert missing.status_code == 404
    assert "not installed" in missing.json()["detail"]


def test_a_workflow_that_needs_no_repository_is_not_held_back_by_a_missing_one() -> None:
    snapshot = _snapshot()
    snapshot["graph"] = {**snapshot["graph"], "requiresRepository": False}
    saga = replace(_saga(snapshot), repos=[], base_branch="")
    client = _client(
        saga,
        issues=[_issue(status_type="unstarted")],
        runs=[],
        adapter=_adapter([]),
        coordinators=_Coordinators(),
    )

    response = _start(client, saga)

    assert response.status_code == 200
    campaign = _saved_run(client)
    assert "repo" not in campaign.metadata
    _engine(client).launch.assert_awaited_once()


def test_a_workflow_that_needs_a_repository_cannot_start_without_one() -> None:
    saga = replace(_saga(_snapshot()), repos=[], base_branch="")
    adapter = _adapter([])
    client = _client(
        saga,
        issues=[_issue(status_type="unstarted")],
        runs=[],
        adapter=adapter,
        coordinators=_Coordinators(),
    )

    response = _start(client, saga)

    assert response.status_code == 409
    assert "needs a repository" in response.json()["detail"]
    _engine(client).launch.assert_not_awaited()


def test_a_project_whose_forge_is_gone_never_starts_on_another() -> None:
    saga = replace(_saga(_snapshot()), instance_id="forge-b")
    primary = _adapter([], name="forge-a")
    client = _client(
        saga,
        issues=[_issue(status_type="unstarted")],
        runs=[],
        adapter=primary,
        coordinators=_Coordinators(),
    )
    client.app.state.volundr_factory.for_connection = AsyncMock(return_value=None)

    assert _start(client, saga).status_code == 503
    _engine(client).launch.assert_not_awaited()
