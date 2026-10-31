"""What a person reviewed on a run, read back from the run's session log.

Ting stores none of this. Every outcome and every gate frame is already in
Forge's durable log, which outlives the session, so a review is a projection of
that log: a revision is what stood when a gate opened, not every draft the
planner and the agent reviewer passed between them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from niuu.domain.models import Principal
from ting.domain.models import WorkflowCampaign
from ting.domain.workflow_board import (
    GATE_STATUS_PENDING,
    WIDGET_DOCUMENT,
    BoardColumn,
    ReviewPanel,
    WorkflowBoard,
    board_from_snapshot,
)
from ting.ports.volundr import PublicSessionLogEntry, VolundrPort

_OUTCOME_FRAME = "room_outcome"
_GATE_FRAME = "workflow_gate"


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


def _document_fields(column: BoardColumn) -> frozenset[str]:
    return frozenset(
        panel.field for panel in column.panels if panel.widget == WIDGET_DOCUMENT and panel.field
    )


def _revision(number: int, gate: Mapping[str, Any], fields: Mapping[str, str]) -> ReviewRevision:
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
    )


def column_revisions(
    column: BoardColumn,
    entries: Iterable[PublicSessionLogEntry],
) -> tuple[ReviewRevision, ...]:
    """Pair each opening of a column's gate with the fields that stood at that moment."""
    wanted = _document_fields(column)
    latest: dict[str, str] = {}
    gates: dict[str, Mapping[str, Any]] = {}
    shown: dict[str, dict[str, str]] = {}
    for entry in sorted(entries, key=lambda item: item.seq):
        if entry.kind == _OUTCOME_FRAME:
            fields = entry.payload.get("fields")
            if not isinstance(fields, Mapping):
                continue
            latest.update(
                {
                    name: value
                    for name in wanted
                    if isinstance(value := fields.get(name), str) and value.strip()
                }
            )
            continue
        if entry.kind != _GATE_FRAME:
            continue
        gate = entry.payload.get("gate")
        if not isinstance(gate, Mapping) or gate.get("node_id") != column.gate_node_id:
            continue
        gate_id = str(gate.get("id") or "")
        gates[gate_id] = gate
        shown.setdefault(gate_id, dict(latest))
    return tuple(
        _revision(number, gate, shown[gate_id])
        for number, (gate_id, gate) in enumerate(gates.items(), start=1)
    )


async def read_session_log(
    forge: VolundrPort,
    session_id: str,
    *,
    page_size: int,
    auth_token: str | None,
    principal: Principal,
) -> tuple[PublicSessionLogEntry, ...]:
    """Read a session's whole public log, however long a run went on."""
    entries: list[PublicSessionLogEntry] = []
    after = 0
    while True:
        page = await forge.get_public_session_log_page(
            session_id,
            after=after,
            limit=page_size,
            auth_token=auth_token,
            principal=principal,
        )
        entries.extend(page.entries)
        if not page.has_more:
            return tuple(entries)
        after = page.scanned_through


def run_reviews(
    board: WorkflowBoard,
    campaign: WorkflowCampaign,
    entries: tuple[PublicSessionLogEntry, ...],
) -> tuple[RunReview, ...]:
    """A run's reviews in board order; a gate that never opened has nothing to show."""
    reviews = []
    for column in board.columns:
        if column.gate_node_id is None:
            continue
        revisions = column_revisions(column, entries)
        if not revisions:
            continue
        reviews.append(
            RunReview(
                column_id=column.id,
                title=column.title,
                review=column.review,
                workflow_name=campaign.workflow_name,
                task_id=campaign.slug,
                session_id=campaign.session_id,
                panels=column.panels,
                revisions=revisions,
            )
        )
    return tuple(reviews)


async def campaign_reviews(
    forge: VolundrPort,
    campaign: WorkflowCampaign,
    *,
    page_size: int,
    auth_token: str | None,
    principal: Principal,
) -> tuple[RunReview, ...]:
    """Read one run's reviews, or none when its workflow declares no board."""
    board = board_from_snapshot(campaign.workflow_snapshot)
    if board is None:
        return ()
    entries = await read_session_log(
        forge,
        campaign.session_id,
        page_size=page_size,
        auth_token=auth_token,
        principal=principal,
    )
    return run_reviews(board, campaign, entries)


def pending_documents(reviews: Iterable[RunReview]) -> tuple[tuple[str, str], ...]:
    """The (title, text) of each document a person is being asked to decide on now."""
    return tuple(
        (panel.title, revision.fields[panel.field])
        for review in reviews
        for revision in review.revisions
        if revision.pending
        for panel in review.panels
        if panel.field in revision.fields
    )
