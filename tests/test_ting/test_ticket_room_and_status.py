"""What Ting learns from a ticket's run: its room, status, pull request and next step."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ting.domain.models import (
    PR_STATE_MERGED,
    PRStatus,
    WorkflowCampaign,
    WorkflowCampaignStatus,
)
from ting.domain.services.workflow_campaign_projector import WorkflowCampaignProjector
from ting.domain.ticket_coordinator import (
    COORDINATOR_JOINED_KEY,
    PENDING_GATES_KEY,
    PULL_REQUEST_KEY,
    ticket_metadata,
)
from ting.ports.volundr import ActivityEvent

from .test_work_board_api import (
    DELIVERY,
    HEADERS,
    NOW,
    PLANNING,
    _adapter,
    _board_task,
    _bundled,
    _campaign,
    _client,
    _coordinator,
    _Coordinators,
    _issue,
    _pending_gate,
    _planned_saga,
    _run_of,
    _saga,
    _snapshot,
    _start,
)

OWNER = "owner-1"
SESSION = "workflow-session"
PR = PRStatus(
    pr_id="4", url="https://github.com/o/r/pull/4", state="open", mergeable=True, ci_passed=None
)


def _ticket_campaign(
    saga, *, status=WorkflowCampaignStatus.RUNNING, **metadata
) -> WorkflowCampaign:
    return replace(
        _campaign(saga, status=status),
        session_id=SESSION,
        metadata={
            "ticket": ticket_metadata(
                saga_id=saga.id, issue_id="issue-1", identifier="NIU-1", title="T"
            ),
            **metadata,
        },
    )


def _forge() -> MagicMock:
    forge = MagicMock()
    forge.join_room = AsyncMock()
    forge.leave_room = AsyncMock()
    forge.send_directed_room_message = AsyncMock()
    forge.get_help_requests = AsyncMock(return_value=[])
    forge.get_workflow_gates = AsyncMock(return_value=[])
    forge.get_pr_status = AsyncMock(return_value=PR)
    forge.stop_session = AsyncMock()
    return forge


def _projector(
    campaign: WorkflowCampaign | None,
    forge: MagicMock,
    *,
    coordinators: _Coordinators | None = None,
    advancer: Any = None,
) -> tuple[WorkflowCampaignProjector, SimpleNamespace]:
    repo = SimpleNamespace(
        get_active_campaign_by_session=AsyncMock(return_value=campaign),
        save_campaign=AsyncMock(side_effect=lambda item: item),
        list_active_campaigns=AsyncMock(return_value=[campaign] if campaign else []),
    )
    factory = SimpleNamespace(
        primary_for_owner=AsyncMock(return_value=forge),
        for_connection=AsyncMock(return_value=forge),
    )
    projector = WorkflowCampaignProjector(
        repo=repo,
        volundr_factory=factory,
        event_bus=SimpleNamespace(emit=AsyncMock()),
        coordinator_repo=coordinators,
        coordinator_persona="ticket-coordinator",
        ticket_advancer=advancer,
    )
    return projector, repo


def _event(state: str = "active", *, session_status: str = "", **metadata) -> ActivityEvent:
    return ActivityEvent(
        session_id=SESSION,
        state=state,
        metadata=metadata,
        owner_id=OWNER,
        session_status=session_status,
    )


def _saved(repo: SimpleNamespace) -> WorkflowCampaign:
    return repo.save_campaign.await_args.args[0]


async def test_a_live_run_seats_its_coordinator_as_an_observer_of_its_room() -> None:
    saga = _saga(_snapshot())
    forge = _forge()
    projector, repo = _projector(
        _ticket_campaign(saga), forge, coordinators=_Coordinators(_coordinator(saga))
    )

    await projector.handle_activity(_event(), OWNER)

    forge.join_room.assert_awaited_once()
    assert forge.join_room.await_args.args == (SESSION,)
    assert forge.join_room.await_args.kwargs["member_session_id"] == "coordinator-session"
    context = forge.join_room.await_args.kwargs["notice_context"]
    assert context == "Ticket NIU-1, Reviewed Delivery Flow, run a2a-run"
    assert _saved(repo).metadata[COORDINATOR_JOINED_KEY] is True


async def test_a_seat_is_taken_once() -> None:
    saga = _saga(_snapshot())
    forge = _forge()
    joined = _ticket_campaign(saga, **{COORDINATOR_JOINED_KEY: True})
    projector, _ = _projector(joined, forge, coordinators=_Coordinators(_coordinator(saga)))

    await projector.handle_activity(_event(), OWNER)

    forge.join_room.assert_not_awaited()


async def test_a_run_for_no_ticket_joins_nothing() -> None:
    saga = _saga(_snapshot())
    forge = _forge()
    campaign = replace(
        _campaign(saga, status=WorkflowCampaignStatus.RUNNING), session_id=SESSION, metadata={}
    )
    projector, _ = _projector(campaign, forge, coordinators=_Coordinators(_coordinator(saga)))

    await projector.handle_activity(_event(), OWNER)

    forge.join_room.assert_not_awaited()


async def test_a_failed_join_is_an_error_and_is_tried_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    saga = _saga(_snapshot())
    forge = _forge()
    forge.join_room = AsyncMock(side_effect=[RuntimeError("no room yet"), None])
    projector, repo = _projector(
        _ticket_campaign(saga), forge, coordinators=_Coordinators(_coordinator(saga))
    )

    await projector.handle_activity(_event(), OWNER)
    assert "could not join the room" in caplog.text
    repo.save_campaign.assert_not_awaited()

    await projector.handle_activity(_event(), OWNER)

    assert forge.join_room.await_count == 2
    assert _saved(repo).metadata[COORDINATOR_JOINED_KEY] is True


async def test_a_finished_run_ends_the_seat_and_records_its_pull_request() -> None:
    saga = _saga(_snapshot())
    forge = _forge()
    joined = _ticket_campaign(saga, **{COORDINATOR_JOINED_KEY: True})
    projector, repo = _projector(joined, forge, coordinators=_Coordinators(_coordinator(saga)))

    await projector.handle_activity(_event("", session_status="completed"), OWNER)

    forge.leave_room.assert_awaited_once()
    assert forge.leave_room.await_args.kwargs["reason"] == "run_finished"
    assert _saved(repo).status == WorkflowCampaignStatus.COMPLETED


async def test_a_failed_run_tells_the_coordinator_what_failed() -> None:
    saga = _saga(_snapshot())
    forge = _forge()
    joined = _ticket_campaign(saga, **{COORDINATOR_JOINED_KEY: True})
    projector, _ = _projector(joined, forge, coordinators=_Coordinators(_coordinator(saga)))

    await projector.handle_activity(_event("error", error="The build broke"), OWNER)

    session, peer, message = forge.send_directed_room_message.await_args.args
    assert (session, peer) == ("coordinator-session", "flock-ticket-coordinator")
    assert message.startswith("[Ticket NIU-1, Reviewed Delivery Flow, run a2a-run] ")
    assert "The build broke" in message
    forge.leave_room.assert_awaited_once()


async def test_a_room_already_gone_with_its_session_is_not_an_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    saga = _saga(_snapshot())
    forge = _forge()
    forge.leave_room = AsyncMock(side_effect=LookupError("no active room endpoint"))
    joined = _ticket_campaign(saga, **{COORDINATOR_JOINED_KEY: True})
    projector, _ = _projector(joined, forge, coordinators=_Coordinators(_coordinator(saga)))

    await projector.handle_activity(_event("", session_status="completed"), OWNER)

    assert "could not leave the room" not in caplog.text


async def test_a_waiting_brief_goes_out_once_the_session_reports_in() -> None:
    saga = _saga(_snapshot())
    forge = _forge()
    coordinators = _Coordinators(replace(_coordinator(saga), pending_brief="Start the workflow"))
    projector, _ = _projector(None, forge, coordinators=coordinators)
    event = replace(_event("idle"), session_id="coordinator-session")

    await projector.deliver_coordinator_brief(event, forge)
    await projector.deliver_coordinator_brief(event, forge)

    forge.send_directed_room_message.assert_awaited_once_with(
        "coordinator-session",
        "flock-ticket-coordinator",
        "Start the workflow",
        metadata={"queue_first": True},
    )
    assert coordinators.rows[(saga.id, "issue-1")].pending_brief is None


async def test_a_brief_that_could_not_be_sent_keeps_waiting(
    caplog: pytest.LogCaptureFixture,
) -> None:
    saga = _saga(_snapshot())
    forge = _forge()
    forge.send_directed_room_message = AsyncMock(side_effect=RuntimeError("not listening"))
    coordinators = _Coordinators(replace(_coordinator(saga), pending_brief="Start the workflow"))
    projector, _ = _projector(None, forge, coordinators=coordinators)

    await projector.deliver_coordinator_brief(
        replace(_event("idle"), session_id="coordinator-session"), forge
    )

    assert coordinators.rows[(saga.id, "issue-1")].pending_brief == "Start the workflow"
    assert "waiting brief" in caplog.text


async def test_a_session_that_is_not_up_gets_no_brief() -> None:
    saga = _saga(_snapshot())
    forge = _forge()
    coordinators = _Coordinators(replace(_coordinator(saga), pending_brief="Start the workflow"))
    projector, _ = _projector(None, forge, coordinators=coordinators)

    await projector.deliver_coordinator_brief(
        replace(_event("", session_status="stopped"), session_id="coordinator-session"), forge
    )

    forge.send_directed_room_message.assert_not_awaited()


async def test_coordinators_keep_their_owners_followed() -> None:
    saga = _saga(_snapshot())
    projector, repo = _projector(None, _forge(), coordinators=_Coordinators(_coordinator(saga)))
    repo.list_active_owner_ids = AsyncMock(return_value=["someone-else"])

    assert await projector.list_active_owner_ids() == ["owner-1", "someone-else"]


async def test_a_gate_opening_records_the_pull_request() -> None:
    saga = _saga(_snapshot())
    forge = _forge()
    projector, repo = _projector(
        _ticket_campaign(saga), forge, coordinators=_Coordinators(_coordinator(saga))
    )

    await projector.record_help_needed(
        {"summary": "Ready"}, OWNER, session_id=SESSION, gate={"node_id": "delivery-code-gate"}
    )

    assert _saved(repo).metadata[PULL_REQUEST_KEY] == {
        "id": "4",
        "url": PR.url,
        "state": "open",
    }


async def test_a_session_with_no_pull_request_records_none() -> None:
    saga = _saga(_snapshot())
    forge = _forge()
    forge.get_pr_status = AsyncMock(return_value=None)
    projector, repo = _projector(_ticket_campaign(saga), forge)

    await projector.record_help_needed(
        {"summary": "Ready"}, OWNER, session_id=SESSION, gate={"node_id": "gate"}
    )

    assert PULL_REQUEST_KEY not in _saved(repo).metadata


async def test_an_unreadable_pull_request_never_stops_a_gate_opening(
    caplog: pytest.LogCaptureFixture,
) -> None:
    saga = _saga(_snapshot())
    forge = _forge()
    forge.get_pr_status = AsyncMock(side_effect=RuntimeError("forge is down"))
    projector, repo = _projector(_ticket_campaign(saga), forge)

    await projector.record_help_needed(
        {"summary": "Ready"}, OWNER, session_id=SESSION, gate={"node_id": "gate"}
    )

    assert _saved(repo).status == WorkflowCampaignStatus.BLOCKED
    assert "Could not read the pull request" in caplog.text


async def test_work_starting_again_after_a_gate_clears_the_block() -> None:
    saga = _saga(_snapshot())
    forge = _forge()
    blocked = _ticket_campaign(
        saga,
        status=WorkflowCampaignStatus.BLOCKED,
        pending_workflow_gates=[{"node_id": "gate"}],
    )
    projector, repo = _projector(blocked, forge)

    await projector.handle_activity(_event("active"), OWNER)

    assert _saved(repo).status == WorkflowCampaignStatus.RUNNING
    assert "pending_workflow_gates" not in _saved(repo).metadata


async def test_a_gate_still_waiting_keeps_the_block() -> None:
    saga = _saga(_snapshot())
    forge = _forge()
    forge.get_workflow_gates = AsyncMock(return_value=[{"status": "pending"}])
    blocked = _ticket_campaign(saga, status=WorkflowCampaignStatus.BLOCKED)
    projector, repo = _projector(blocked, forge)

    await projector.handle_activity(_event("active"), OWNER)

    repo.save_campaign.assert_not_awaited()


async def test_a_gate_list_that_cannot_be_read_keeps_the_block() -> None:
    saga = _saga(_snapshot())
    forge = _forge()
    forge.get_workflow_gates = AsyncMock(side_effect=RuntimeError("forge is down"))
    blocked = _ticket_campaign(saga, status=WorkflowCampaignStatus.BLOCKED)
    projector, repo = _projector(blocked, forge)

    await projector.handle_activity(_event("active"), OWNER)

    repo.save_campaign.assert_not_awaited()


async def test_a_run_reaching_completed_advances_the_ticket_once() -> None:
    saga = _saga(_snapshot())
    forge = _forge()
    advancer = SimpleNamespace(advance=AsyncMock())
    campaign = _ticket_campaign(saga)
    projector, repo = _projector(campaign, forge, advancer=advancer)

    await projector.handle_activity(_event("", session_status="completed"), OWNER)

    advancer.advance.assert_awaited_once()
    assert advancer.advance.await_args.args[0].status == WorkflowCampaignStatus.COMPLETED

    # The same event redelivered finds nothing left to change.
    repo.get_active_campaign_by_session = AsyncMock(
        return_value=replace(campaign, status=WorkflowCampaignStatus.COMPLETED)
    )
    await projector.handle_activity(_event("", session_status="completed"), OWNER)

    advancer.advance.assert_awaited_once()


@pytest.mark.parametrize(
    "event",
    [_event("error", error="broke"), _event("", session_status="stopped")],
)
async def test_a_run_that_did_not_succeed_never_advances(event: ActivityEvent) -> None:
    saga = _saga(_snapshot())
    advancer = SimpleNamespace(advance=AsyncMock())
    projector, _ = _projector(_ticket_campaign(saga), _forge(), advancer=advancer)

    await projector.handle_activity(event, OWNER)

    advancer.advance.assert_not_awaited()


async def test_a_reconnect_catches_up_on_finished_runs() -> None:
    advancer = SimpleNamespace(advance_finished=AsyncMock())
    projector, _ = _projector(None, _forge(), advancer=advancer)

    await projector.reconcile_owner(OWNER)

    advancer.advance_finished.assert_awaited_once_with(OWNER)


def test_a_failed_automatic_start_shows_blocked_and_can_be_retried() -> None:
    saga = _planned_saga()
    finished = _run_of(saga, PLANNING, WorkflowCampaignStatus.COMPLETED)
    coordinators = _Coordinators(
        replace(
            _coordinator(saga),
            requested_workflow_id=str(_bundled(DELIVERY).id),
            requested_at=NOW + timedelta(hours=1),
            start_error="Reviewed Delivery Flow could not start: no session",
        )
    )
    adapter = _adapter([])
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=adapter,
        campaigns=[finished],
        coordinators=coordinators,
    )

    task = _board_task(client, saga)

    assert (task["board"]["columnId"], task["board"]["state"]) == ("building", "blocked")
    assert task["board"]["reason"] == "Reviewed Delivery Flow could not start: no session"
    assert task["coordinator"]["startError"] == task["board"]["reason"]

    assert _start(client, saga).status_code == 200
    adapter.send_directed_room_message.assert_awaited_once()
    assert coordinators.rows[(saga.id, "issue-1")].start_error is None


def test_a_blocked_stage_shows_as_blocked_with_its_reason() -> None:
    saga = _planned_saga()
    adapter = _adapter([])
    adapter.get_session.return_value.activity_metadata = {
        "flock_idle": {
            "event_type": "plan.blocked",
            "verdict": "blocked",
            "persona": "ticket-plan-publisher",
            "summary": "Mimir refused the write.",
        }
    }
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=adapter,
        campaigns=[_run_of(saga, PLANNING, WorkflowCampaignStatus.RUNNING)],
    )

    board = _board_task(client, saga)["board"]

    assert board["state"] == "blocked"
    assert board["reason"] == "Mimir refused the write."


def test_a_pending_gate_outranks_an_earlier_blocked_outcome() -> None:
    saga = _planned_saga()
    gate = {**_pending_gate(), "node_id": "plan-review-gate"}
    adapter = _adapter([gate])
    adapter.get_session.return_value.activity_metadata = {
        "flock_idle": {"verdict": "blocked", "summary": "Stale"}
    }
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=adapter,
        campaigns=[_run_of(saga, PLANNING, WorkflowCampaignStatus.BLOCKED)],
    )

    assert _board_task(client, saga)["board"]["state"] == "needsDecision"


def test_the_board_shows_who_decided_last_and_the_pull_request() -> None:
    saga = _planned_saga()
    decided = {
        **_pending_gate(),
        "status": "changes_requested",
        "decided_by": "jozef@niuu.world",
        "source": "ting.board",
        "notes": "Rename it.",
        "updated_at": "2026-10-30T10:00:00+00:00",
    }
    running = _run_of(saga, DELIVERY, WorkflowCampaignStatus.RUNNING)
    campaign = replace(
        running,
        metadata={
            **running.metadata,
            PULL_REQUEST_KEY: {"id": "4", "url": PR.url, "state": "open"},
        },
    )
    client = _client(
        saga, issues=[_issue()], runs=[], adapter=_adapter([decided]), campaigns=[campaign]
    )

    response = client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()
    task = response["tasks"][0]

    assert task["board"]["lastDecision"]["decidedBy"] == "jozef@niuu.world"
    assert task["board"]["lastDecision"]["status"] == "changes_requested"
    assert task["board"]["lastDecision"]["notes"] == "Rename it."
    assert task["board"]["lastDecision"]["label"]
    assert task["workflowRun"]["prUrl"] == PR.url


_COMPLETION = {
    "completion_source": "ravn_flock",
    "completion_event_type": "delivery.completed",
    "completion_peer_id": "workflow-stop:delivery-complete",
    "outcome_valid": True,
    "structured_outcome": {"verdict": "approve", "authoritative": True, "summary": "Done"},
}
MERGED = replace(PR, state=PR_STATE_MERGED)


def _delivery_campaign(saga, status=WorkflowCampaignStatus.BLOCKED, **metadata) -> WorkflowCampaign:
    running = _run_of(saga, DELIVERY, status)
    return replace(
        running,
        session_id=SESSION,
        metadata={**running.metadata, **metadata},
    )


async def test_gate_approved_then_merged_ends_completed() -> None:
    """The real sequence: approval restarts the work, and its final report finishes the run."""
    saga = _planned_saga()
    forge = _forge()
    forge.get_pr_status = AsyncMock(return_value=MERGED)
    blocked = _delivery_campaign(saga, pending_workflow_gates=[{"node_id": "delivery-code-gate"}])
    projector, repo = _projector(blocked, forge)

    await projector.handle_activity(_event("active"), OWNER)
    running = _saved(repo)
    assert running.status == WorkflowCampaignStatus.RUNNING

    repo.get_active_campaign_by_session = AsyncMock(return_value=running)
    await projector.handle_activity(_event("idle", **_COMPLETION), OWNER)

    finished = _saved(repo)
    assert finished.status == WorkflowCampaignStatus.COMPLETED
    assert finished.metadata[PULL_REQUEST_KEY]["state"] == PR_STATE_MERGED


async def test_a_session_stopping_after_its_merge_ends_completed() -> None:
    saga = _planned_saga()
    forge = _forge()
    forge.get_pr_status = AsyncMock(return_value=MERGED)
    advancer = SimpleNamespace(advance=AsyncMock())
    projector, repo = _projector(_delivery_campaign(saga), forge, advancer=advancer)

    await projector.handle_activity(_event("", session_status="stopped"), OWNER)

    assert _saved(repo).status == WorkflowCampaignStatus.COMPLETED
    advancer.advance.assert_awaited_once()


async def test_a_session_stopping_before_any_merge_stays_blocked() -> None:
    saga = _planned_saga()
    projector, repo = _projector(_delivery_campaign(saga), _forge())

    await projector.handle_activity(_event("", session_status="stopped"), OWNER)

    repo.save_campaign.assert_not_awaited()


async def test_a_merge_after_the_session_stopped_is_found_by_the_sweep() -> None:
    """NIU-1164: stopped at the gate, merged later, and no event would ever say so."""
    saga = _planned_saga()
    forge = _forge()
    forge.get_session = AsyncMock(return_value=SimpleNamespace(status="stopped"))
    forge.get_pr_status = AsyncMock(return_value=MERGED)
    projector, repo = _projector(_delivery_campaign(saga), forge)

    assert await projector.settle_merged_campaigns() == 1

    assert _saved(repo).status == WorkflowCampaignStatus.COMPLETED
    assert "pending_workflow_gates" not in _saved(repo).metadata


async def test_the_sweep_leaves_a_live_session_to_its_own_reports() -> None:
    saga = _planned_saga()
    forge = _forge()
    forge.get_session = AsyncMock(return_value=SimpleNamespace(status="running"))
    forge.get_pr_status = AsyncMock(return_value=MERGED)
    projector, repo = _projector(_delivery_campaign(saga), forge)

    assert await projector.settle_merged_campaigns() == 0

    repo.save_campaign.assert_not_awaited()


async def test_the_sweep_leaves_an_unmerged_run_blocked() -> None:
    saga = _planned_saga()
    forge = _forge()
    forge.get_session = AsyncMock(return_value=SimpleNamespace(status="stopped"))
    projector, repo = _projector(_delivery_campaign(saga), forge)

    assert await projector.settle_merged_campaigns() == 0

    repo.save_campaign.assert_not_awaited()


async def test_the_sweep_skips_a_campaign_for_no_ticket() -> None:
    saga = _planned_saga()
    forge = _forge()
    forge.get_session = AsyncMock(return_value=SimpleNamespace(status="stopped"))
    forge.get_pr_status = AsyncMock(return_value=MERGED)
    plain = replace(_delivery_campaign(saga), metadata={})
    projector, repo = _projector(plain, forge)

    assert await projector.settle_merged_campaigns() == 0

    repo.save_campaign.assert_not_awaited()


async def test_a_sweep_that_cannot_read_a_session_is_an_error_and_moves_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    saga = _planned_saga()
    forge = _forge()
    forge.get_session = AsyncMock(side_effect=RuntimeError("forge is down"))
    projector, repo = _projector(_delivery_campaign(saga), forge)

    assert await projector.settle_merged_campaigns() == 0

    assert "Could not check whether campaign" in caplog.text
    repo.save_campaign.assert_not_awaited()


def _remembering_run(saga) -> WorkflowCampaign:
    return _run_of(saga, DELIVERY, WorkflowCampaignStatus.BLOCKED)


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("stopped", "The run's session is stopped."),
        ("provisioning", "The run's session is starting."),
    ],
)
def test_a_session_that_is_away_leaves_the_ticket_at_its_gate(status: str, reason: str) -> None:
    saga = _planned_saga()
    run = _remembering_run(saga)
    campaign = replace(
        run,
        metadata={
            **run.metadata,
            PENDING_GATES_KEY: [{"id": "gate-1", "node_id": "delivery-code-gate"}],
        },
    )
    adapter = _adapter([])
    adapter.get_session.return_value.status = status
    client = _client(saga, issues=[_issue()], runs=[], adapter=adapter, campaigns=[campaign])

    board = _board_task(client, saga)["board"]

    assert (board["columnId"], board["state"]) == ("code-review", "blocked")
    assert board["reason"].startswith(reason)
    assert board["gate"] is None
    adapter.get_workflow_gates.assert_not_awaited()


def test_a_stopped_session_with_no_gate_reads_blocked_not_running() -> None:
    saga = _planned_saga()
    run = _run_of(saga, DELIVERY, WorkflowCampaignStatus.RUNNING)
    adapter = _adapter([])
    adapter.get_session.return_value.status = "stopped"
    client = _client(saga, issues=[_issue()], runs=[], adapter=adapter, campaigns=[run])

    board = _board_task(client, saga)["board"]

    assert board["state"] == "blocked"
    assert board["reason"].startswith("The run's session is stopped.")


def test_a_session_that_came_back_answers_for_itself() -> None:
    saga = _planned_saga()
    run = _remembering_run(saga)
    campaign = replace(
        run,
        metadata={
            **run.metadata,
            PENDING_GATES_KEY: [{"id": "stale", "node_id": "delivery-code-gate"}],
        },
    )
    client = _client(
        saga, issues=[_issue()], runs=[], adapter=_adapter([_pending_gate()]), campaigns=[campaign]
    )

    board = _board_task(client, saga)["board"]

    assert board["state"] == "needsDecision"
    assert board["gate"]["id"] == "gate-1"


def test_a_launch_that_failed_shows_its_reason_on_the_board() -> None:
    saga = _planned_saga()
    run = _run_of(saga, DELIVERY, WorkflowCampaignStatus.FAILED)
    campaign = replace(
        run,
        session_id="",
        metadata={
            **run.metadata,
            "failure_error": "No session slot is free: 4 of 4 sessions are running on this host.",
        },
    )
    client = _client(saga, issues=[_issue()], runs=[], adapter=_adapter([]), campaigns=[campaign])

    board = _board_task(client, saga)["board"]

    assert board["state"] == "blocked"
    assert board["reason"] == "No session slot is free: 4 of 4 sessions are running on this host."
