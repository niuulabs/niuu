"""What a person reviewed on a run, read back from the run's step ledger.

A review is a projection of the ledger, which already records every outcome event and
every gate the run opened: a revision is one opening of a gate, with the outcome fields
that stood when it opened and what a person decided. Ting stores nothing else for it.

A run has no single session, so a review names no session and offers no chat. Documents
the agents presented in their sessions (``present_file``) are not attached to a revision:
the ledger does not record which session presented what before which gate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from niuu.domain.presented_files import PresentedArtifact
from ting.domain.campaign_run_projection import run_gates
from ting.domain.models import WorkflowCampaign
from ting.domain.workflow_board import (
    GATE_STATUS_PENDING,
    SOURCE_OUTCOME,
    WIDGET_SUMMARY,
    BoardColumn,
    BoardColumnKind,
    ReviewPanel,
    WorkflowBoard,
    board_from_snapshot,
    node_label,
)
from ting.domain.workflow_steps import RunState

_OUTCOME_FRAME = "room_outcome"
_TURN_FRAME = "conversation.turn"
_UNDECLARED_GATE_PANELS = (ReviewPanel(widget=WIDGET_SUMMARY, title="Summary"),)


@dataclass(frozen=True)
class ReviewRevision:
    """One version of the work, as a person saw it at one gate."""

    number: int
    gate_id: str
    activation_id: str
    requested_at: str
    status: str
    summary: str
    decided_by: str
    notes: str
    decided_at: str
    fields: Mapping[str, str] = field(default_factory=dict)
    artifacts: tuple[PresentedArtifact, ...] = ()

    @property
    def pending(self) -> bool:
        return self.status == GATE_STATUS_PENDING


@dataclass(frozen=True)
class RunReview:
    """Everything one gate column of one run has put in front of a person."""

    column_id: str
    title: str
    review: str | None
    workflow_name: str
    task_id: str
    session_id: str
    panels: tuple[ReviewPanel, ...]
    revisions: tuple[ReviewRevision, ...]
    chat_endpoint: str | None = None


def _outcome_field(panel: ReviewPanel) -> str | None:
    source = panel.source
    return source.value if source is not None and source.kind == SOURCE_OUTCOME else None


def _outcome_fields(column: BoardColumn) -> frozenset[str]:
    return frozenset(field for panel in column.panels if (field := _outcome_field(panel)))


def _revision(
    number: int,
    gate: Mapping[str, Any],
    fields: Mapping[str, str],
    artifacts: tuple[PresentedArtifact, ...],
) -> ReviewRevision:
    return ReviewRevision(
        number=number,
        gate_id=str(gate.get("id") or ""),
        activation_id=str(gate.get("activation_id") or ""),
        requested_at=str(gate.get("requested_at") or ""),
        status=str(gate.get("status") or ""),
        summary=str(gate.get("summary") or ""),
        decided_by=str(gate.get("decided_by") or gate.get("source") or ""),
        notes=str(gate.get("notes") or ""),
        decided_at=str(gate.get("updated_at") or ""),
        fields=fields,
        artifacts=artifacts,
    )


def _ledger_fields(state: RunState, wanted: frozenset[str], *, through_seq: int) -> dict[str, str]:
    """The outcome fields a column shows, as they stood when its gate opened."""
    latest: dict[str, str] = {}
    for event in sorted(state.events, key=lambda item: item.seq):
        if event.seq > through_seq:
            break
        if event.is_engine_event:
            continue
        latest.update(
            {
                name: value
                for name in wanted
                if isinstance(value := event.outcome.get(name), str) and value.strip()
            }
        )
    return latest


def _gate_opened_through(state: RunState, gate: Mapping[str, Any]) -> int:
    """The last event the gate's step consumed: what stood on the table when it opened."""
    step = state.step(UUID(str(gate["activation_id"])))
    if step is None or not step.inputs:
        return 0
    return max(item.seq for item in step.inputs)


def ledger_run_reviews(
    board: WorkflowBoard,
    campaign: WorkflowCampaign,
    state: RunState,
) -> tuple[RunReview, ...]:
    """A run's reviews in board order, read from the step ledger.

    A revision is one opening of a gate: the gate step, the outcome fields that stood
    when it opened, and what a person decided. A gate that never opened has nothing
    to show. A run has no single session, so a review carries no session id and no
    chat endpoint, and documents agents presented in their sessions are not attached.
    """
    gates = sorted(run_gates(campaign, state), key=lambda gate: gate["requested_at"])
    by_node: dict[str, list[Mapping[str, Any]]] = {}
    for gate in gates:
        by_node.setdefault(str(gate["node_id"]), []).append(gate)
    undeclared = tuple(
        BoardColumn(
            id=node_id,
            title=node_label(campaign.workflow_snapshot, node_id),
            kind=BoardColumnKind.GATE,
            gate_node_id=node_id,
            panels=_UNDECLARED_GATE_PANELS,
        )
        for node_id in by_node
        if board.gate_column(node_id) is None
    )
    reviews = []
    for column in (*board.columns, *undeclared):
        node_gates = by_node.get(column.gate_node_id or "")
        if not node_gates:
            continue
        wanted = _outcome_fields(column)
        revisions = tuple(
            _revision(
                number,
                gate,
                _ledger_fields(state, wanted, through_seq=_gate_opened_through(state, gate)),
                (),
            )
            for number, gate in enumerate(node_gates, start=1)
        )
        reviews.append(
            RunReview(
                column_id=column.id,
                title=column.title,
                review=column.review,
                workflow_name=campaign.workflow_name,
                task_id=campaign.slug,
                session_id="",
                panels=column.panels,
                revisions=revisions,
            )
        )
    return tuple(reviews)


def ledger_reviews(campaign: WorkflowCampaign, state: RunState) -> tuple[RunReview, ...]:
    """One run's reviews; a workflow with no board still has its gates to answer."""
    board = board_from_snapshot(campaign.workflow_snapshot) or WorkflowBoard(columns=())
    return ledger_run_reviews(board, campaign, state)
