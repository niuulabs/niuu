"""What a person reviewed on a run, read back from the run's session log.

Ting stores none of this. Every outcome and every gate frame is already in
Forge's durable log, which outlives the session, so a review is a projection of
that log: a revision is what stood when a gate opened, not every draft the
planner and the agent reviewer passed between them.

A revision also names the artifact versions the gate puts in front of a person:
the documents the agents presented (``present_file`` with an artifact key) since
the run's previous gate opened, plus the latest version of whatever this gate
showed before. Each is Forge's publication by ``provider_key`` and
``revision_id``, so a decision on the gate is a decision on those versions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from niuu.domain.models import Principal
from niuu.domain.presented_files import PresentedArtifact, presented_artifacts
from niuu.domain.workflow_gate import WORKFLOW_GATE_FRAME
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
from ting.ports.volundr import PublicSessionLogEntry, VolundrPort

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


def column_revisions(
    column: BoardColumn,
    entries: Iterable[PublicSessionLogEntry],
) -> tuple[ReviewRevision, ...]:
    """Pair each opening of a column's gate with the fields and artifact versions that
    stood at that moment."""
    wanted = _outcome_fields(column)
    latest: dict[str, str] = {}
    gates: dict[str, Mapping[str, Any]] = {}
    shown: dict[str, dict[str, str]] = {}
    # Versions presented since any gate last opened, and what this column showed.
    presented: dict[str, PresentedArtifact] = {}
    column_artifacts: dict[str, PresentedArtifact] = {}
    shown_artifacts: dict[str, tuple[PresentedArtifact, ...]] = {}
    opened: set[str] = set()
    for entry in sorted(entries, key=lambda item: item.seq):
        turn = entry.payload.get("turn") if entry.kind == _TURN_FRAME else None
        if isinstance(turn, Mapping):
            for artifact in presented_artifacts(entry.session_id, turn):
                presented[artifact.artifact_key] = artifact
            continue
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
        if entry.kind != WORKFLOW_GATE_FRAME:
            continue
        gate = entry.payload.get("gate")
        if not isinstance(gate, Mapping):
            continue
        gate_id = str(gate.get("id") or "")
        first_opening = gate_id not in opened
        opened.add(gate_id)
        if gate.get("node_id") != column.gate_node_id:
            if first_opening:
                presented.clear()  # another gate opened: what came before was its to show
            continue
        gates[gate_id] = gate
        shown.setdefault(gate_id, dict(latest))
        if first_opening:
            column_artifacts.update(presented)
            presented.clear()
            shown_artifacts[gate_id] = tuple(column_artifacts.values())
    return tuple(
        _revision(number, gate, shown[gate_id], shown_artifacts.get(gate_id, ()))
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


def _undeclared_gate_columns(
    board: WorkflowBoard,
    campaign: WorkflowCampaign,
    entries: Iterable[PublicSessionLogEntry],
) -> tuple[BoardColumn, ...]:
    """Gates the board has no column for: someone still has to see and answer them."""
    node_ids: dict[str, None] = {}
    for entry in sorted(entries, key=lambda item: item.seq):
        gate = entry.payload.get("gate") if entry.kind == WORKFLOW_GATE_FRAME else None
        node_id = gate.get("node_id") if isinstance(gate, Mapping) else None
        if isinstance(node_id, str) and node_id and board.gate_column(node_id) is None:
            node_ids[node_id] = None
    return tuple(
        BoardColumn(
            id=node_id,
            title=node_label(campaign.workflow_snapshot, node_id),
            kind=BoardColumnKind.GATE,
            gate_node_id=node_id,
            panels=_UNDECLARED_GATE_PANELS,
        )
        for node_id in node_ids
    )


def run_reviews(
    board: WorkflowBoard,
    campaign: WorkflowCampaign,
    entries: tuple[PublicSessionLogEntry, ...],
) -> tuple[RunReview, ...]:
    """A run's reviews in board order; a gate that never opened has nothing to show."""
    reviews = []
    for column in (*board.columns, *_undeclared_gate_columns(board, campaign, entries)):
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
    """Read one run's reviews; a workflow with no board still has its gates to answer."""
    board = board_from_snapshot(campaign.workflow_snapshot) or WorkflowBoard(columns=())
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
        (panel.title, revision.fields[field])
        for review in reviews
        for revision in review.revisions
        if revision.pending
        for panel in review.panels
        if (field := _outcome_field(panel)) in revision.fields
    )
