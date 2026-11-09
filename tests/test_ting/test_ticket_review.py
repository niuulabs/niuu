"""Tests for reading what a person reviewed back out of a run's step ledger."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from tests.support.full_catalogue import load_full_catalogue as load_system_workflows
from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.services.ticket_review import ledger_reviews, ledger_run_reviews
from ting.domain.workflow_board import (
    BoardColumn,
    BoardColumnKind,
    ReviewPanel,
    ReviewSource,
    WorkflowBoard,
    board_from_snapshot,
)
from ting.domain.workflow_steps import (
    RunEvent,
    RunState,
    StepInput,
    StepKind,
    StepState,
    WaitingOn,
    WaitingOnKind,
    WorkflowStep,
)

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)
RUN_ID = uuid4()
COLUMN = BoardColumn(
    id="plan-review",
    title="Plan review",
    kind=BoardColumnKind.GATE,
    gate_node_id="plan-review-gate",
    review="plan",
    panels=(
        ReviewPanel(
            widget="document", title="Plan", source=ReviewSource(kind="outcome", value="plan")
        ),
    ),
)


def _campaign(snapshot: dict[str, Any] | None = None) -> WorkflowCampaign:
    return WorkflowCampaign(
        tenant_id="tenant-1",
        id=RUN_ID,
        slug="a2a-1",
        name="Plan",
        owner_id="owner-1",
        workflow_id=uuid4(),
        workflow_version="1.1.0",
        workflow_name="Ticket Planning Flow",
        workflow_snapshot=snapshot or {},
        session_id="",
        session_name="",
        status=WorkflowCampaignStatus.BLOCKED,
        active_stage_id=None,
        stage_state=[],
        metadata={},
        created_at=NOW,
        updated_at=NOW,
        last_activity_at=NOW,
    )


def _draft(seq: int, plan: str, *, note: str = "") -> RunEvent:
    return RunEvent(
        run_id=RUN_ID,
        seq=seq,
        event_type="plan.drafted",
        generation=0,
        created_at=NOW,
        payload={
            "node_id": "plan",
            "member": "planner",
            "verdict": "drafted",
            "outcome": {"verdict": "drafted", "plan": plan},
            "note": note,
        },
    )


def _engine_event(seq: int) -> RunEvent:
    return RunEvent(
        run_id=RUN_ID,
        seq=seq,
        event_type="engine.step.closed",
        generation=0,
        created_at=NOW,
        payload={"outcome": {"plan": "engine bookkeeping is not a draft"}},
    )


def _gate(
    through_seq: int,
    *,
    attempt: int = 1,
    node_id: str = "plan-review-gate",
    state: StepState = StepState.WAITING,
    verdict: str | None = None,
    note: str = "",
    opened: int = 0,
) -> WorkflowStep:
    return WorkflowStep(
        id=uuid4(),
        run_id=RUN_ID,
        node_id=node_id,
        member=None,
        kind=StepKind.GATE,
        attempt=attempt,
        generation=0,
        state=state,
        created_at=NOW + timedelta(minutes=opened),
        activated_at=NOW + timedelta(minutes=opened),
        closed_at=None if state is StepState.WAITING else NOW + timedelta(minutes=opened + 5),
        inputs=(
            StepInput(
                seq=through_seq,
                event_type="plan.drafted",
                edge_id="e1",
                node_id="plan",
                member="planner",
            ),
        ),
        waiting_on=(
            WaitingOn(WaitingOnKind.PERSON, "person", NOW) if state is StepState.WAITING else None
        ),
        verdict=verdict,
        note=note,
    )


def _state(*events: RunEvent, steps: tuple[WorkflowStep, ...]) -> RunState:
    return RunState(run_id=RUN_ID, steps=steps, events=events)


def _board_campaign() -> tuple[WorkflowBoard, WorkflowCampaign]:
    workflow = next(item for item in load_system_workflows() if item.name == "Ticket Planning Flow")
    snapshot = {"name": workflow.name, "version": workflow.version, "graph": workflow.graph}
    board = board_from_snapshot(snapshot)
    assert board is not None
    return board, _campaign(snapshot)


def test_a_revision_is_what_stood_when_the_gate_opened() -> None:
    first_gate = _gate(2, state=StepState.DONE, verdict="request_changes", note="Sample", opened=0)
    second_gate = _gate(4, attempt=2, opened=30)
    state = _state(
        _draft(1, "first draft"),
        _draft(2, "reviewer bounced it, second draft"),
        _engine_event(3),
        _draft(4, "third draft"),
        steps=(first_gate, second_gate),
    )

    (review,) = ledger_run_reviews(WorkflowBoard(columns=(COLUMN,)), _campaign(), state)

    first, second = review.revisions
    assert (first.number, second.number) == (1, 2)
    assert first.fields == {"plan": "reviewer bounced it, second draft"}
    assert first.status == "changes_requested"
    assert first.notes == "Sample"
    assert second.fields == {"plan": "third draft"}
    assert second.status == "pending"
    assert second.pending
    assert (first.activation_id, second.activation_id) == (str(first_gate.id), str(second_gate.id))
    assert review.session_id == ""
    assert review.chat_endpoint is None
    assert review.task_id == "a2a-1"
    assert review.workflow_name == "Ticket Planning Flow"


def test_a_gate_that_never_opened_has_nothing_to_show() -> None:
    state = _state(_draft(1, "a"), steps=())

    assert ledger_run_reviews(WorkflowBoard(columns=(COLUMN,)), _campaign(), state) == ()


def test_another_nodes_gate_is_not_this_columns_revision() -> None:
    state = _state(_draft(1, "a"), steps=(_gate(1, node_id="merge-gate"),))

    (review,) = ledger_run_reviews(WorkflowBoard(columns=(COLUMN,)), _campaign(), state)

    assert review.column_id == "merge-gate"
    assert [panel.title for panel in review.panels] == ["Summary"]


def test_a_decided_gate_names_who_decided_it() -> None:
    gate = _gate(1, state=StepState.DONE, verdict="approve", note="Looks right")
    state = _state(_draft(1, "a"), steps=(gate,))

    (review,) = ledger_run_reviews(WorkflowBoard(columns=(COLUMN,)), _campaign(), state)

    (revision,) = review.revisions
    assert revision.status == "approved"
    assert revision.notes == "Looks right"
    assert revision.decided_at == (NOW + timedelta(minutes=5)).isoformat()
    assert not revision.pending


def test_the_gate_summary_is_the_note_of_the_outcome_that_opened_it() -> None:
    state = _state(_draft(1, "a", note="Ready for review"), steps=(_gate(1),))

    (review,) = ledger_run_reviews(WorkflowBoard(columns=(COLUMN,)), _campaign(), state)

    assert review.revisions[0].summary == "Ready for review"


def test_a_workflow_with_a_board_shows_its_declared_gate_column() -> None:
    board, campaign = _board_campaign()
    gate_column = next(column for column in board.columns if column.gate_node_id)
    gate = _gate(1, node_id=gate_column.gate_node_id)
    state = _state(_draft(1, "a"), steps=(gate,))

    (review,) = ledger_reviews(campaign, state)

    assert review.column_id == gate_column.id
    assert review.title == gate_column.title
    assert review.revisions[0].gate_id == gate_column.gate_node_id


def test_a_workflow_with_no_board_still_has_its_gates_to_answer() -> None:
    campaign = _campaign({"name": "Plain", "graph": {"nodes": [], "edges": []}})
    state = _state(_draft(1, "a"), steps=(_gate(1, node_id="merge-gate"),))

    (review,) = ledger_reviews(campaign, state)

    assert review.column_id == "merge-gate"
    assert review.revisions[0].pending
