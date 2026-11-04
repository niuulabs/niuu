"""Tests for reading what a person reviewed back out of a run's session log."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.services.ticket_review import (
    campaign_reviews,
    column_revisions,
    pending_documents,
    read_session_log,
    run_reviews,
)
from ting.domain.workflow_board import (
    BoardColumn,
    BoardColumnKind,
    ReviewPanel,
    ReviewSource,
    WorkflowBoard,
    board_from_snapshot,
)
from ting.ports.volundr import PublicSessionLogEntry, PublicSessionLogPage
from ting.system_workflows import load_system_workflows

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)
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


def _entry(seq: int, kind: str, payload: dict[str, Any]) -> PublicSessionLogEntry:
    return PublicSessionLogEntry(session_id="s1", seq=seq, kind=kind, payload=payload, ts=NOW)


def _draft(seq: int, plan: str) -> PublicSessionLogEntry:
    return _entry(seq, "room_outcome", {"fields": {"verdict": "drafted", "plan": plan}})


def _gate(seq: int, gate_id: str, status: str, **fields: Any) -> PublicSessionLogEntry:
    gate = {"id": gate_id, "node_id": "plan-review-gate", "status": status, **fields}
    return _entry(seq, "workflow_gate", {"gate": gate})


def _campaign(snapshot: dict[str, Any] | None) -> WorkflowCampaign:
    return WorkflowCampaign(
        tenant_id="tenant-1",
        id=uuid4(),
        slug="a2a-1",
        name="Plan",
        owner_id="owner-1",
        workflow_id=uuid4(),
        workflow_version="1.1.0",
        workflow_name="Ticket Planning Flow",
        workflow_snapshot=snapshot or {},
        session_id="s1",
        session_name="plan",
        status=WorkflowCampaignStatus.BLOCKED,
        active_stage_id=None,
        stage_state=(),
        metadata={},
        created_at=NOW,
        updated_at=NOW,
        last_activity_at=NOW,
    )


def test_a_revision_is_what_stood_when_the_gate_opened() -> None:
    entries = [
        _draft(1, "first draft"),
        _draft(2, "reviewer bounced it, second draft"),
        _gate(3, "g1", "pending", requested_at="10:00"),
        _gate(
            4,
            "g1",
            "changes_requested",
            requested_at="10:00",
            decided_by="jo",
            notes="Sample",
            updated_at="10:05",
        ),
        _draft(5, "third draft"),
        _gate(6, "g2", "pending"),
    ]

    first, second = column_revisions(COLUMN, entries)

    assert first.number == 1
    assert first.fields == {"plan": "reviewer bounced it, second draft"}
    assert (first.status, first.decided_by, first.notes) == ("changes_requested", "jo", "Sample")
    assert first.requested_at == "10:00"
    assert second.number == 2
    assert second.fields == {"plan": "third draft"}
    assert second.pending


def test_drafts_the_agents_passed_between_them_are_not_revisions() -> None:
    entries = [_draft(seq, f"draft {seq}") for seq in range(1, 69)]

    assert column_revisions(COLUMN, entries) == ()


def test_another_gate_on_the_run_is_not_this_columns_revision() -> None:
    other = {"id": "g9", "node_id": "other-gate", "status": "pending"}

    assert column_revisions(COLUMN, [_entry(1, "workflow_gate", {"gate": other})]) == ()


def test_a_decision_falls_back_to_who_sent_it_only_when_no_one_is_named() -> None:
    (revision,) = column_revisions(COLUMN, [_gate(1, "g1", "approved", source="ting.board")])

    assert revision.decided_by == "ting.board"


def test_malformed_frames_are_passed_over() -> None:
    entries = [
        _entry(1, "room_outcome", {"fields": "not a mapping"}),
        _entry(2, "room_outcome", {"fields": {"plan": "   "}}),
        _entry(3, "workflow_gate", {"gate": "not a mapping"}),
        _entry(4, "user", {"text": "hello"}),
        _gate(5, "g1", "pending"),
    ]

    (revision,) = column_revisions(COLUMN, entries)

    assert revision.fields == {}


@pytest.mark.asyncio
async def test_a_long_log_is_read_to_its_end() -> None:
    forge = MagicMock()
    forge.get_public_session_log_page = AsyncMock(
        side_effect=[
            PublicSessionLogPage((_draft(1, "a"),), scanned_through=7, has_more=True),
            PublicSessionLogPage((_draft(8, "b"),), scanned_through=8, has_more=False),
        ]
    )

    entries = await read_session_log(
        forge, "s1", page_size=50, auth_token="t", principal=MagicMock()
    )

    assert [entry.seq for entry in entries] == [1, 8]
    assert forge.get_public_session_log_page.await_args_list[1].kwargs["after"] == 7


def test_a_gate_that_never_opened_has_no_review() -> None:
    board = WorkflowBoard(columns=(COLUMN,))

    assert run_reviews(board, _campaign(None), (_draft(1, "a"),)) == ()


def test_a_run_review_says_which_run_it_came_from() -> None:
    board = WorkflowBoard(columns=(COLUMN,))

    (review,) = run_reviews(board, _campaign(None), (_gate(1, "g1", "pending"),))

    assert (review.column_id, review.task_id, review.session_id) == ("plan-review", "a2a-1", "s1")
    assert review.workflow_name == "Ticket Planning Flow"


def test_only_the_document_being_decided_is_pending() -> None:
    board = WorkflowBoard(columns=(COLUMN,))
    entries = (
        _draft(1, "old"),
        _gate(2, "g1", "changes_requested"),
        _draft(3, "new"),
        _gate(4, "g2", "pending"),
    )

    documents = pending_documents(run_reviews(board, _campaign(None), entries))

    assert documents == (("Plan", "new"),)


def _log(*entries: PublicSessionLogEntry) -> MagicMock:
    forge = MagicMock()
    forge.get_public_session_log_page = AsyncMock(
        return_value=PublicSessionLogPage(
            entries=entries, scanned_through=len(entries), has_more=False
        )
    )
    return forge


@pytest.mark.asyncio
async def test_a_gate_on_a_workflow_without_a_board_can_still_be_reviewed() -> None:
    snapshot = {"graph": {"nodes": [{"id": "plan-review-gate", "label": "Approve the merge"}]}}

    (review,) = await campaign_reviews(
        _log(_gate(1, "g1", "pending", summary="Ready to merge")),
        _campaign(snapshot),
        page_size=50,
        auth_token=None,
        principal=MagicMock(),
    )

    assert review.column_id == "plan-review-gate"
    assert review.title == "Approve the merge"
    assert [panel.widget for panel in review.panels] == ["summary"]
    assert [revision.summary for revision in review.revisions] == ["Ready to merge"]


def test_a_gate_the_board_has_no_column_for_is_reviewed_after_the_board() -> None:
    declared = _gate(1, "g1", "approved")
    undeclared = _entry(
        2,
        "workflow_gate",
        {"gate": {"id": "g2", "node_id": "delivery-blocked", "status": "pending"}},
    )

    reviews = run_reviews(WorkflowBoard(columns=(COLUMN,)), _campaign(None), (declared, undeclared))

    assert [review.title for review in reviews] == ["Plan review", "delivery-blocked"]


@pytest.mark.asyncio
async def test_a_workflow_without_a_board_or_a_gate_has_no_reviews() -> None:
    reviews = await campaign_reviews(
        _log(_draft(1, "draft")),
        _campaign(None),
        page_size=50,
        auth_token=None,
        principal=MagicMock(),
    )

    assert reviews == ()


def test_the_bundled_ticket_work_flow_reviews_the_workers_result() -> None:
    workflow = next(item for item in load_system_workflows() if item.name == "Ticket Work Flow")
    board = board_from_snapshot({"name": workflow.name, "graph": workflow.graph})
    assert board is not None
    column = board.gate_column("result-review-gate")
    assert column is not None
    gate = {"id": "g1", "node_id": "result-review-gate", "status": "pending"}
    fields = {"verdict": "done", "summary": "Three lines done.", "result": "The three lines"}

    (revision,) = column_revisions(
        column,
        [
            _entry(1, "room_outcome", {"fields": fields}),
            _entry(2, "workflow_gate", {"gate": gate}),
        ],
    )

    assert revision.fields == {"result": "The three lines"}


@pytest.mark.asyncio
async def test_the_bundled_planning_flow_reviews_its_plan() -> None:
    workflow = next(item for item in load_system_workflows() if item.name == "Ticket Planning Flow")
    snapshot = {"name": workflow.name, "graph": workflow.graph}
    assert board_from_snapshot(snapshot) is not None
    forge = MagicMock()
    forge.get_public_session_log_page = AsyncMock(
        return_value=PublicSessionLogPage(
            (_draft(1, "the plan"), _gate(2, "g1", "pending")), scanned_through=2, has_more=False
        )
    )

    (review,) = await campaign_reviews(
        forge, _campaign(snapshot), page_size=50, auth_token=None, principal=MagicMock()
    )

    assert review.title == "Plan review"
    assert review.revisions[0].fields == {"plan": "the plan"}
