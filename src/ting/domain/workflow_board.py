"""The board columns a workflow declares, and where a ticket sits on them.

A workflow may declare ``graph.board``: the ordered columns one run moves a
ticket through. A column either holds work nodes or shows one person gate.
Gates and their routes stay on the graph, so the board only ever reads them:
where an answer sends a ticket is derived from the gate's own edges.

A workflow without ``graph.board`` has no board. That is an authoring choice
and the expected state of most workflows, not a degraded one.

A gate column also says what a person reviews there, as panels. ``review`` names
the kind of decision and brings that kind's usual panels; ``panels`` replaces them
when a workflow reviews something else. The review page around them never changes.

A project's board runs one or more such workflows in order. Each keeps its own
columns and gates, and leaving one workflow leads to the first column of the
next. A ticket with a coordinator starts it by itself (see ``ticket_advance``);
one without waits there until someone starts it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from ting.domain.models import RunStatus, Saga, SagaBoardWorkflow

BOARD_VERSION = 1
BACKLOG_COLUMN_ID = "backlog"
DONE_COLUMN_ID = "done"
GATE_STATUS_PENDING = "pending"
GATE_STATUS_APPROVED = "approved"
GATE_STATUS_CHANGES_REQUESTED = "changes_requested"
SESSION_FAILED_REASON = "The session's agent stopped with an error. Open the session to see why."
SESSION_GONE_REASON = "This ticket's session no longer exists. Retry to start a new one."
OFF_BOARD_REASON = "This ticket's run does not match the workflows on this board."

_RESERVED_COLUMN_IDS = frozenset({BACKLOG_COLUMN_ID, DONE_COLUMN_ID})
_REVIEW_KINDS = frozenset({"plan", "code", "output"})
SOURCE_OUTCOME = "outcome"
SOURCE_FILE = "file"
SOURCE_URL = "url"
WIDGET_DOCUMENT = "document"
WIDGET_FILE = "file"
WIDGET_LINK = "link"
WIDGET_PAGE = "page"
WIDGET_DIFF = "diff"
WIDGET_FILES = "files"
WIDGET_PULL_REQUEST = "pullRequest"
WIDGET_SUMMARY = "summary"
# Where each widget reads from; None shows the run's own session and gate.
_WIDGET_SOURCES: Mapping[str, str | None] = {
    WIDGET_DOCUMENT: SOURCE_OUTCOME,
    WIDGET_FILE: SOURCE_FILE,
    WIDGET_LINK: SOURCE_URL,
    WIDGET_PAGE: SOURCE_FILE,
    WIDGET_DIFF: None,
    WIDGET_FILES: None,
    WIDGET_PULL_REQUEST: None,
    WIDGET_SUMMARY: None,
}
_WEB_SCHEMES = ("https://", "http://")
# A widget a module installed, named `module/name`. Whether it is installed is the
# deployment's to answer: a pinned run must still parse after a module is removed.
_MODULE_WIDGET = re.compile(r"^[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9_-]*$")
_SOURCE_KINDS = frozenset({SOURCE_OUTCOME, SOURCE_FILE, SOURCE_URL})
_WORK_NODE_KINDS = frozenset({"stage", "wait", "subworkflow", "gate"})
_CLOSED_STATUS_TYPES = frozenset({"completed", "canceled"})
_STOPPED_RUN_STATUSES = frozenset({RunStatus.FAILED, RunStatus.ESCALATED})
_ANSWERED_GATE_STATUSES = frozenset({GATE_STATUS_APPROVED, GATE_STATUS_CHANGES_REQUESTED})


class WorkflowBoardError(ValueError):
    """A workflow's ``graph.board`` does not describe its graph."""


class BoardColumnKind(StrEnum):
    TRACKER = "tracker"
    WORK = "work"
    GATE = "gate"


class BoardTicketState(StrEnum):
    NOT_STARTED = "notStarted"
    READY = "ready"
    STARTING = "starting"
    RUNNING = "running"
    NEEDS_DECISION = "needsDecision"
    BLOCKED = "blocked"
    DONE = "done"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ReviewSource:
    """Where a panel's content comes from: an outcome field, a workspace file or a URL."""

    kind: str
    value: str


@dataclass(frozen=True)
class ReviewPanel:
    """One thing a person reviews at a gate."""

    widget: str
    title: str
    source: ReviewSource | None = None
    # Workspace files a page may ask for; a page has no network of its own.
    files: tuple[str, ...] = ()


_KIND_PANELS: Mapping[str, tuple[ReviewPanel, ...]] = {
    "plan": (
        ReviewPanel(
            widget=WIDGET_DOCUMENT,
            title="Plan",
            source=ReviewSource(kind=SOURCE_OUTCOME, value="plan"),
        ),
    ),
    "code": (
        ReviewPanel(widget=WIDGET_DIFF, title="Changes"),
        ReviewPanel(widget=WIDGET_FILES, title="Files"),
        ReviewPanel(widget=WIDGET_PULL_REQUEST, title="Pull request"),
    ),
    "output": (ReviewPanel(widget=WIDGET_SUMMARY, title="Result"),),
}


@dataclass(frozen=True)
class BoardColumn:
    id: str
    title: str
    kind: BoardColumnKind
    node_ids: tuple[str, ...] = ()
    gate_node_id: str | None = None
    review: str | None = None
    panels: tuple[ReviewPanel, ...] = ()
    approve_column_id: str | None = None
    changes_column_id: str | None = None
    workflow_id: str | None = None
    workflow_name: str | None = None


@dataclass(frozen=True)
class WorkflowBoard:
    """Only the workflow's own columns; Backlog and Done belong to the tracker."""

    columns: tuple[BoardColumn, ...]

    @property
    def first_column(self) -> BoardColumn:
        return self.columns[0]

    def column(self, column_id: str) -> BoardColumn | None:
        return next((item for item in self.columns if item.id == column_id), None)

    def gate_column(self, node_id: str) -> BoardColumn | None:
        return next((item for item in self.columns if item.gate_node_id == node_id), None)

    def with_tracker_columns(self) -> tuple[BoardColumn, ...]:
        return (
            BoardColumn(id=BACKLOG_COLUMN_ID, title="Backlog", kind=BoardColumnKind.TRACKER),
            *self.columns,
            BoardColumn(id=DONE_COLUMN_ID, title="Done", kind=BoardColumnKind.TRACKER),
        )


@dataclass(frozen=True)
class BoardWorkflow:
    """One workflow on a project board, read from the version the board pins."""

    id: str
    name: str
    board: WorkflowBoard


@dataclass(frozen=True)
class ProjectBoard:
    """The workflows a project runs in order, as one row of columns."""

    workflows: tuple[BoardWorkflow, ...]

    def __post_init__(self) -> None:
        if not self.workflows:
            raise WorkflowBoardError("A project board needs at least one workflow")
        workflow_ids = [workflow.id for workflow in self.workflows]
        if len(set(workflow_ids)) != len(workflow_ids):
            raise WorkflowBoardError("A workflow can appear on a project board only once")
        owners: dict[str, str] = {}
        for workflow in self.workflows:
            for column in workflow.board.columns:
                other = owners.setdefault(column.id, workflow.name)
                if other != workflow.name:
                    raise WorkflowBoardError(
                        f"{other} and {workflow.name} both declare board column "
                        f"{column.id!r}; rename one of them"
                    )

    @property
    def columns(self) -> tuple[BoardColumn, ...]:
        """Every workflow's columns between the tracker's own, ready to display."""
        composed = tuple(
            replace(
                column,
                workflow_id=workflow.id,
                workflow_name=workflow.name,
                approve_column_id=(
                    self.column_after(workflow.id)
                    if column.approve_column_id == DONE_COLUMN_ID
                    else column.approve_column_id
                ),
            )
            for workflow in self.workflows
            for column in workflow.board.columns
        )
        return WorkflowBoard(columns=composed).with_tracker_columns()

    @property
    def first_workflow(self) -> BoardWorkflow:
        return self.workflows[0]

    def workflow(self, workflow_id: str) -> BoardWorkflow | None:
        return next((item for item in self.workflows if item.id == workflow_id), None)

    def workflow_starting_at(self, column_id: str) -> BoardWorkflow | None:
        """The workflow a ticket enters at this column, which is only ever its first."""
        return next(
            (item for item in self.workflows if item.board.first_column.id == column_id),
            None,
        )

    def column_after(self, workflow_id: str) -> str:
        """Where a ticket goes when this workflow finishes."""
        position = next(
            index for index, item in enumerate(self.workflows) if item.id == workflow_id
        )
        following = self.workflows[position + 1 :]
        return following[0].board.first_column.id if following else DONE_COLUMN_ID


@dataclass(frozen=True)
class BoardGate:
    id: str
    activation_id: str
    node_id: str
    label: str
    condition: str
    instructions: str
    summary: str
    requested_at: str
    attempt: int


@dataclass(frozen=True)
class BoardDecision:
    """The last answer a person gave at a gate on this ticket's run."""

    label: str
    status: str
    decided_by: str
    notes: str
    decided_at: str


@dataclass(frozen=True)
class BoardPlacement:
    column_id: str | None
    state: BoardTicketState
    gate: BoardGate | None = None
    reason: str | None = None
    passed_workflow_ids: tuple[str, ...] = ()
    last_decision: BoardDecision | None = None


def project_board(saga: Saga) -> ProjectBoard | None:
    """Return a project's board: its ordered workflows, or else its one assigned workflow."""
    if saga.board_workflows:
        return ProjectBoard(workflows=tuple(_pinned_workflow(pin) for pin in saga.board_workflows))
    board = board_from_snapshot(saga.workflow_snapshot)
    if board is None or saga.workflow_id is None:
        return None
    return ProjectBoard(
        workflows=(
            BoardWorkflow(
                id=str(saga.workflow_id),
                name=_snapshot_name(saga.workflow_snapshot),
                board=board,
            ),
        )
    )


def _pinned_workflow(pin: SagaBoardWorkflow) -> BoardWorkflow:
    name = _snapshot_name(pin.workflow_snapshot)
    board = board_from_snapshot(pin.workflow_snapshot)
    if board is None:
        raise WorkflowBoardError(f"{name} declares no board columns, so it cannot be on a board")
    return BoardWorkflow(id=str(pin.workflow_id), name=name, board=board)


def _snapshot_name(snapshot: Mapping[str, Any] | None) -> str:
    return str((snapshot or {}).get("name") or "")


def ticket_is_closed(issue_status_type: str) -> bool:
    return issue_status_type.strip().lower() in _CLOSED_STATUS_TYPES


def board_from_snapshot(snapshot: Mapping[str, Any] | None) -> WorkflowBoard | None:
    """Return the board a pinned workflow snapshot declares, if it declares one."""
    graph = snapshot.get("graph") if isinstance(snapshot, Mapping) else None
    return parse_workflow_board(graph if isinstance(graph, Mapping) else None)


def parse_workflow_board(graph: Mapping[str, Any] | None) -> WorkflowBoard | None:
    """Parse and validate ``graph.board`` against the graph it describes."""
    raw = graph.get("board") if graph is not None else None
    if raw is None:
        return None
    if not isinstance(raw, Mapping) or set(raw) != {"version", "columns"}:
        raise WorkflowBoardError("graph.board must contain only version and columns")
    if raw["version"] != BOARD_VERSION:
        raise WorkflowBoardError(f"graph.board version must be {BOARD_VERSION}")
    columns = raw["columns"]
    if not isinstance(columns, list) or not columns:
        raise WorkflowBoardError("graph.board columns must be a non-empty list")

    nodes = _nodes_by_id(graph)
    parsed = tuple(_parse_column(item, nodes) for item in columns)
    _require_unique(parsed)
    if parsed[0].kind != BoardColumnKind.WORK:
        raise WorkflowBoardError("The first board column must hold work, because a run starts it")
    return WorkflowBoard(columns=_with_gate_routes(parsed, graph, nodes))


def place_ticket(
    board: WorkflowBoard,
    *,
    run_status: RunStatus | None,
    issue_status_type: str,
    gates: Sequence[Mapping[str, Any]],
    session_failed: bool = False,
    blocked_reason: str | None = None,
) -> BoardPlacement:
    """Work out a ticket's column from its run and its session's gates, newest first."""
    # A workflow's closer finishes the ticket in the tracker, whatever the run row still says.
    if ticket_is_closed(issue_status_type):
        return BoardPlacement(column_id=DONE_COLUMN_ID, state=BoardTicketState.DONE)
    if run_status is None:
        return BoardPlacement(column_id=BACKLOG_COLUMN_ID, state=BoardTicketState.NOT_STARTED)
    if run_status == RunStatus.MERGED:
        return BoardPlacement(column_id=DONE_COLUMN_ID, state=BoardTicketState.DONE)

    resting = _resting_column(board, gates)
    if session_failed:
        # Forge can still list the session as running after its agent has died.
        return BoardPlacement(
            column_id=resting,
            state=BoardTicketState.BLOCKED,
            reason=blocked_reason or SESSION_FAILED_REASON,
        )
    pending = next((gate for gate in gates if gate.get("status") == GATE_STATUS_PENDING), None)
    if pending is None:
        if blocked_reason:
            return BoardPlacement(
                column_id=resting,
                state=BoardTicketState.BLOCKED,
                reason=blocked_reason,
            )
        state = (
            BoardTicketState.BLOCKED
            if run_status in _STOPPED_RUN_STATUSES
            else BoardTicketState.RUNNING
        )
        return BoardPlacement(column_id=resting, state=state)

    gate = _board_gate(pending)
    column = board.gate_column(gate.node_id)
    if column is None:
        # A gate the board does not show is an escalation out of normal flow.
        return BoardPlacement(column_id=resting, state=BoardTicketState.BLOCKED, gate=gate)
    return BoardPlacement(column_id=column.id, state=BoardTicketState.NEEDS_DECISION, gate=gate)


def place_on_project(
    project: ProjectBoard,
    workflow_id: str,
    placement: BoardPlacement,
) -> BoardPlacement:
    """Carry a placement from a run's pinned board onto the project's columns.

    A run knows only its own workflow, so leaving it reads as Done. On a project
    board that is the next workflow's first column, where a finished ticket waits
    to be started. A run whose columns the board no longer has is never guessed.
    """
    if project.workflow(workflow_id) is None:
        return off_board_placement()
    if placement.column_id == DONE_COLUMN_ID:
        following = project.column_after(workflow_id)
        if following == DONE_COLUMN_ID:
            return placement
        finished = placement.state == BoardTicketState.DONE
        state = BoardTicketState.READY if finished else placement.state
        return replace(placement, column_id=following, state=state)
    if any(column.id == placement.column_id for column in project.columns):
        return placement
    return off_board_placement()


def off_board_placement() -> BoardPlacement:
    return BoardPlacement(column_id=None, state=BoardTicketState.UNKNOWN, reason=OFF_BOARD_REASON)


def last_board_decision(gates: Sequence[Mapping[str, Any]]) -> BoardDecision | None:
    """The newest gate a person has answered, from gates listed newest first."""
    answered = next(
        (gate for gate in gates if gate.get("status") in _ANSWERED_GATE_STATUSES),
        None,
    )
    if answered is None:
        return None
    return BoardDecision(
        label=str(answered.get("label") or answered.get("node_id") or ""),
        status=str(answered["status"]),
        decided_by=str(answered.get("decided_by") or answered.get("source") or ""),
        notes=str(answered.get("notes") or ""),
        decided_at=str(answered.get("updated_at") or ""),
    )


def _resting_column(board: WorkflowBoard, gates: Sequence[Mapping[str, Any]]) -> str:
    for gate in gates:
        column = board.gate_column(str(gate.get("node_id") or ""))
        if column is None:
            continue
        status = gate.get("status")
        if status == GATE_STATUS_APPROVED:
            return column.approve_column_id or DONE_COLUMN_ID
        if status == GATE_STATUS_CHANGES_REQUESTED and column.changes_column_id:
            return column.changes_column_id
    return board.first_column.id


def _board_gate(raw: Mapping[str, Any]) -> BoardGate:
    return BoardGate(
        id=str(raw.get("id") or ""),
        activation_id=str(raw.get("activation_id") or ""),
        node_id=str(raw.get("node_id") or ""),
        label=str(raw.get("label") or ""),
        condition=str(raw.get("condition") or ""),
        instructions=str(raw.get("instructions") or ""),
        summary=str(raw.get("summary") or ""),
        requested_at=str(raw.get("requested_at") or ""),
        attempt=int(raw.get("attempt") or 1),
    )


def _nodes_by_id(graph: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(node.get("id") or ""): node
        for node in graph.get("nodes") or []
        if isinstance(node, Mapping)
    }


def _parse_column(raw: object, nodes: Mapping[str, Mapping[str, Any]]) -> BoardColumn:
    if not isinstance(raw, Mapping):
        raise WorkflowBoardError("Each graph.board column must be a mapping")
    unknown = set(raw) - {"id", "title", "nodes", "gate", "review", "panels"}
    if unknown:
        raise WorkflowBoardError(f"Unknown board column field(s): {', '.join(sorted(unknown))}")
    column_id = _required_text(raw.get("id"), "id")
    title = _required_text(raw.get("title"), f"{column_id} title")
    if column_id in _RESERVED_COLUMN_IDS:
        raise WorkflowBoardError(f"Board column id {column_id!r} is reserved for the tracker")
    if ("nodes" in raw) == ("gate" in raw):
        raise WorkflowBoardError(f"Board column {column_id!r} needs exactly one of nodes or gate")
    if "gate" in raw:
        return _parse_gate_column(raw, column_id, title, nodes)
    if "review" in raw or "panels" in raw:
        raise WorkflowBoardError(f"Board column {column_id!r} sets review without a gate")
    return BoardColumn(
        id=column_id,
        title=title,
        kind=BoardColumnKind.WORK,
        node_ids=_work_nodes(raw["nodes"], column_id, nodes),
    )


def _parse_gate_column(
    raw: Mapping[str, Any],
    column_id: str,
    title: str,
    nodes: Mapping[str, Mapping[str, Any]],
) -> BoardColumn:
    gate_id = _required_text(raw["gate"], f"{column_id} gate")
    node = nodes.get(gate_id)
    if node is None or node.get("kind") != "gate":
        raise WorkflowBoardError(f"Board column {column_id!r} names no gate node {gate_id!r}")
    if str(node.get("mode") or "human_approval") != "human_approval":
        raise WorkflowBoardError(
            f"Board column {column_id!r} needs a human_approval gate; evidence gates pass "
            "without a person, so list them under nodes"
        )
    review = raw.get("review")
    if review not in _REVIEW_KINDS:
        raise WorkflowBoardError(
            f"Board column {column_id!r} review must be one of {', '.join(sorted(_REVIEW_KINDS))}"
        )
    return BoardColumn(
        id=column_id,
        title=title,
        kind=BoardColumnKind.GATE,
        gate_node_id=gate_id,
        review=str(review),
        panels=_review_panels(raw, column_id, str(review)),
    )


def _review_panels(
    raw: Mapping[str, Any],
    column_id: str,
    review: str,
) -> tuple[ReviewPanel, ...]:
    if "panels" not in raw:
        return _KIND_PANELS[review]
    panels = raw["panels"]
    if not isinstance(panels, list) or not panels:
        raise WorkflowBoardError(f"Board column {column_id!r} panels must be a non-empty list")
    return tuple(_parse_panel(item, column_id) for item in panels)


def _parse_panel(raw: object, column_id: str) -> ReviewPanel:
    if not isinstance(raw, Mapping):
        raise WorkflowBoardError(f"Each panel of board column {column_id!r} must be a mapping")
    unknown = set(raw) - {"widget", "title", "source", "files"}
    if unknown:
        raise WorkflowBoardError(f"Unknown review panel field(s): {', '.join(sorted(unknown))}")
    widget = raw.get("widget")
    if widget not in _WIDGET_SOURCES and not is_module_widget(widget):
        raise WorkflowBoardError(
            f"Board column {column_id!r} panel widget must be one of "
            f"{', '.join(sorted(_WIDGET_SOURCES))}, or a module's as module/name"
        )
    title = _required_text(raw.get("title"), f"{column_id} panel title")
    return ReviewPanel(
        widget=str(widget),
        title=title,
        source=_parse_source(raw.get("source"), str(widget), title),
        files=_parse_files(raw.get("files"), str(widget), title),
    )


def is_module_widget(widget: object) -> bool:
    return isinstance(widget, str) and _MODULE_WIDGET.match(widget) is not None


def module_widgets_of(board: ProjectBoard) -> frozenset[str]:
    """The module widgets a board needs a deployment to have installed."""
    return frozenset(
        panel.widget
        for workflow in board.workflows
        for column in workflow.board.columns
        for panel in column.panels
        if is_module_widget(panel.widget)
    )


def _parse_files(raw: object, widget: str, title: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if widget != WIDGET_PAGE and not is_module_widget(widget):
        raise WorkflowBoardError(f"Review panel {title!r} takes no files; only a page does")
    if not isinstance(raw, list):
        raise WorkflowBoardError(f"Review panel {title!r} files must be a list")
    files = tuple(_required_text(item, f"{title} file") for item in raw)
    for path in files:
        _require_workspace_path(path, title)
    return files


def _require_workspace_path(path: str, title: str) -> None:
    if path.startswith("/") or ".." in path.split("/"):
        raise WorkflowBoardError(f"Review panel {title!r} must name a file inside the workspace")


def _parse_module_source(raw: object, title: str) -> ReviewSource | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping) or len(raw) != 1 or not set(raw) <= _SOURCE_KINDS:
        raise WorkflowBoardError(
            f"Review panel {title!r} source must be one of {', '.join(sorted(_SOURCE_KINDS))}"
        )
    kind, value = next(iter(raw.items()))
    return _checked_source(str(kind), value, title)


def _parse_source(raw: object, widget: str, title: str) -> ReviewSource | None:
    if is_module_widget(widget):
        return _parse_module_source(raw, title)
    kind = _WIDGET_SOURCES[widget]
    if kind is None:
        if raw is not None:
            raise WorkflowBoardError(f"Review panel {title!r} reads no source")
        return None
    if not isinstance(raw, Mapping) or set(raw) != {kind}:
        raise WorkflowBoardError(f"Review panel {title!r} needs source {{{kind}: ...}}")
    return _checked_source(kind, raw[kind], title)


def _checked_source(kind: str, raw: object, title: str) -> ReviewSource:
    value = _required_text(raw, f"{title} source")
    if kind == SOURCE_FILE:
        _require_workspace_path(value, title)
    if kind == SOURCE_URL and not value.startswith(_WEB_SCHEMES):
        raise WorkflowBoardError(f"Review panel {title!r} needs an http or https URL")
    return ReviewSource(kind=kind, value=value)


def _work_nodes(
    raw: object,
    column_id: str,
    nodes: Mapping[str, Mapping[str, Any]],
) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise WorkflowBoardError(f"Board column {column_id!r} nodes must be a non-empty list")
    node_ids = tuple(_required_text(item, f"{column_id} node") for item in raw)
    for node_id in node_ids:
        node = nodes.get(node_id)
        if node is None or node.get("kind") not in _WORK_NODE_KINDS:
            raise WorkflowBoardError(f"Board column {column_id!r} names no work node {node_id!r}")
        if node.get("kind") == "gate" and node.get("mode") != "evidence":
            raise WorkflowBoardError(
                f"Board column {column_id!r} lists person gate {node_id!r}; "
                "give it its own gate column"
            )
    return node_ids


def _require_unique(columns: tuple[BoardColumn, ...]) -> None:
    column_ids = [column.id for column in columns]
    if len(set(column_ids)) != len(column_ids):
        raise WorkflowBoardError("Board column ids must be unique")
    placed = [
        node_id
        for column in columns
        for node_id in (*column.node_ids, *filter(None, [column.gate_node_id]))
    ]
    if len(set(placed)) != len(placed):
        raise WorkflowBoardError("A workflow node can sit in only one board column")


def _with_gate_routes(
    columns: tuple[BoardColumn, ...],
    graph: Mapping[str, Any],
    nodes: Mapping[str, Mapping[str, Any]],
) -> tuple[BoardColumn, ...]:
    by_node = {
        node_id: column.id
        for column in columns
        for node_id in (*column.node_ids, *filter(None, [column.gate_node_id]))
    }
    routed: list[BoardColumn] = []
    for column in columns:
        if column.gate_node_id is None:
            routed.append(column)
            continue
        gate = nodes[column.gate_node_id]
        routed.append(
            replace(
                column,
                approve_column_id=_route(gate, "approvalEvent", graph, nodes, by_node),
                changes_column_id=_route(gate, "changesRequestedEvent", graph, nodes, by_node),
            )
        )
    return tuple(routed)


def _route(
    gate: Mapping[str, Any],
    field: str,
    graph: Mapping[str, Any],
    nodes: Mapping[str, Mapping[str, Any]],
    by_node: Mapping[str, str],
) -> str:
    gate_id = str(gate.get("id") or "")
    event = str(gate.get(field) or "").strip()
    if not event:
        raise WorkflowBoardError(f"Board gate {gate_id!r} must set {field}")
    target = next(
        (
            str(edge.get("target") or "")
            for edge in graph.get("edges") or []
            if isinstance(edge, Mapping)
            and edge.get("source") == gate_id
            and str(edge.get("label") or "").split("->", 1)[0].strip() == event
        ),
        None,
    )
    if target is None:
        raise WorkflowBoardError(f"Board gate {gate_id!r} has no edge for {event!r}")
    if nodes.get(target, {}).get("kind") == "end":
        return DONE_COLUMN_ID
    column_id = by_node.get(target)
    if column_id is None:
        raise WorkflowBoardError(
            f"Board gate {gate_id!r} routes to {target!r}, which has no column"
        )
    return column_id


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowBoardError(f"Board column {field} must be a non-empty string")
    return value.strip()
