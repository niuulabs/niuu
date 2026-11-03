"""Tests for the columns a workflow declares and the ticket's place on them."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
import yaml

from ting.domain.exceptions import WorkflowDocumentError
from ting.domain.models import RunStatus, Saga, SagaBoardWorkflow, SagaStatus, WorkflowScope
from ting.domain.workflow_board import (
    BoardColumnKind,
    BoardPlacement,
    BoardTicketState,
    BoardWorkflow,
    ProjectBoard,
    ReviewPanel,
    ReviewSource,
    WorkflowBoardError,
    board_from_snapshot,
    module_widgets_of,
    parse_workflow_board,
    place_on_project,
    place_ticket,
    repository_gap,
    workflow_requires_repository,
)
from ting.domain.workflow_document import load_workflow_document
from ting.system_workflows import BUNDLED_SYSTEM_WORKFLOWS_PATH, load_system_workflows


def _gate_column(graph: dict[str, Any]) -> dict[str, Any]:
    return graph["board"]["columns"][1]


def test_a_review_kind_brings_its_usual_panels() -> None:
    board = parse_workflow_board(_graph())

    assert board is not None
    assert board.columns[1].panels == (
        ReviewPanel(widget="diff", title="Changes"),
        ReviewPanel(widget="files", title="Files"),
        ReviewPanel(widget="pullRequest", title="Pull request"),
    )


def test_a_workflow_declares_its_own_panels() -> None:
    graph = _graph()
    _gate_column(graph)["panels"] = [
        {"widget": "document", "title": "Design", "source": {"outcome": "design"}},
        {"widget": "file", "title": "Report", "source": {"file": "report/quality.pdf"}},
        {"widget": "link", "title": "Camera", "source": {"url": "https://printer.local/cam"}},
        {"widget": "summary", "title": "Checks"},
    ]

    board = parse_workflow_board(graph)

    assert board is not None
    assert board.columns[1].panels == (
        ReviewPanel(
            widget="document",
            title="Design",
            source=ReviewSource(kind="outcome", value="design"),
        ),
        ReviewPanel(
            widget="file",
            title="Report",
            source=ReviewSource(kind="file", value="report/quality.pdf"),
        ),
        ReviewPanel(
            widget="link",
            title="Camera",
            source=ReviewSource(kind="url", value="https://printer.local/cam"),
        ),
        ReviewPanel(widget="summary", title="Checks"),
    )


@pytest.mark.parametrize(
    ("panels", "reason"),
    [
        ([], "non-empty list"),
        (["document"], "must be a mapping"),
        ([{"widget": "hologram", "title": "Nope"}], "widget must be one of"),
        ([{"widget": "document", "title": "Plan"}], "needs source"),
        ([{"widget": "document", "title": "Plan", "source": {"file": "a.md"}}], "needs source"),
        ([{"widget": "summary", "title": "Result", "source": {"outcome": "x"}}], "reads no source"),
        (
            [{"widget": "file", "title": "Log", "source": {"file": "/etc/passwd"}}],
            "inside the work",
        ),
        ([{"widget": "file", "title": "Log", "source": {"file": "a/../../b"}}], "inside the work"),
        ([{"widget": "link", "title": "Cam", "source": {"url": "file:///x"}}], "http or https"),
        ([{"widget": "summary", "title": "Result", "colour": "red"}], "Unknown review panel"),
    ],
)
def test_a_panel_that_cannot_be_shown_is_refused(panels: Any, reason: str) -> None:
    graph = _graph()
    _gate_column(graph)["panels"] = panels

    with pytest.raises(WorkflowBoardError, match=reason):
        parse_workflow_board(graph)


def test_a_page_declares_the_files_it_may_read() -> None:
    graph = _graph()
    _gate_column(graph)["panels"] = [
        {
            "widget": "page",
            "title": "Layers",
            "source": {"file": "report/index.html"},
            "files": ["report/unit-17.jpg"],
        }
    ]

    board = parse_workflow_board(graph)

    assert board is not None
    assert board.columns[1].panels == (
        ReviewPanel(
            widget="page",
            title="Layers",
            source=ReviewSource(kind="file", value="report/index.html"),
            files=("report/unit-17.jpg",),
        ),
    )


@pytest.mark.parametrize(
    ("panel", "reason"),
    [
        (
            {"widget": "file", "title": "R", "source": {"file": "a.pdf"}, "files": ["b.png"]},
            "only a page",
        ),
        (
            {"widget": "page", "title": "P", "source": {"file": "a.html"}, "files": "b.png"},
            "must be a list",
        ),
        (
            {"widget": "page", "title": "P", "source": {"file": "a.html"}, "files": ["../b"]},
            "inside the workspace",
        ),
        (
            {"widget": "page", "title": "P", "source": {"file": "a.html"}, "files": [""]},
            "non-empty",
        ),
    ],
)
def test_a_page_cannot_reach_past_its_workspace(panel: dict[str, Any], reason: str) -> None:
    graph = _graph()
    _gate_column(graph)["panels"] = [panel]

    with pytest.raises(WorkflowBoardError, match=reason):
        parse_workflow_board(graph)


def test_a_workflow_can_name_a_module_widget_with_any_source() -> None:
    graph = _graph()
    _gate_column(graph)["panels"] = [
        {
            "widget": "acme-print/print-quality",
            "title": "Quality",
            "source": {"outcome": "inspection"},
            "files": ["stills/unit-17.jpg"],
        },
        {"widget": "acme-print/slicer", "title": "Slicer"},
    ]

    board = parse_workflow_board(graph)

    assert board is not None
    assert board.columns[1].panels == (
        ReviewPanel(
            widget="acme-print/print-quality",
            title="Quality",
            source=ReviewSource(kind="outcome", value="inspection"),
            files=("stills/unit-17.jpg",),
        ),
        ReviewPanel(widget="acme-print/slicer", title="Slicer"),
    )


@pytest.mark.parametrize(
    ("panel", "reason"),
    [
        ({"widget": "Acme/Print", "title": "Q"}, "module/name"),
        ({"widget": "a/b/c", "title": "Q"}, "module/name"),
        ({"widget": "a/b", "title": "Q", "source": "plan"}, "source must be one of"),
        ({"widget": "a/b", "title": "Q", "source": {"gate": "x"}}, "source must be one of"),
        ({"widget": "a/b", "title": "Q", "source": {"file": "/etc/passwd"}}, "inside the work"),
        ({"widget": "a/b", "title": "Q", "source": {"url": "ftp://x"}}, "http or https"),
    ],
)
def test_a_module_widget_keeps_the_same_rules(panel: dict[str, Any], reason: str) -> None:
    graph = _graph()
    _gate_column(graph)["panels"] = [panel]

    with pytest.raises(WorkflowBoardError, match=reason):
        parse_workflow_board(graph)


def test_a_board_says_which_module_widgets_it_needs() -> None:
    graph = _graph()
    _gate_column(graph)["panels"] = [
        {"widget": "acme-print/print-quality", "title": "Quality"},
        {"widget": "summary", "title": "Result"},
    ]
    board = parse_workflow_board(graph)
    assert board is not None

    project = ProjectBoard(workflows=(BoardWorkflow(id="w1", name="Print", board=board),))

    assert module_widgets_of(project) == {"acme-print/print-quality"}


def test_panels_need_a_gate() -> None:
    graph = _graph()
    graph["board"]["columns"][0]["panels"] = [{"widget": "summary", "title": "Result"}]

    with pytest.raises(WorkflowBoardError, match="without a gate"):
        parse_workflow_board(graph)


def _graph() -> dict[str, Any]:
    return {
        "board": {
            "version": 1,
            "columns": [
                {"id": "building", "title": "Building", "nodes": ["implement"]},
                {"id": "code-review", "title": "Code review", "gate": "gate", "review": "code"},
                {"id": "merging", "title": "Merging", "nodes": ["close"]},
            ],
        },
        "nodes": [
            {"id": "implement", "kind": "stage"},
            {
                "id": "gate",
                "kind": "gate",
                "mode": "human_approval",
                "approvalEvent": "merge.approved",
                "changesRequestedEvent": "review.changes_requested",
            },
            {"id": "close", "kind": "stage"},
            {"id": "blocked", "kind": "gate"},
            {"id": "end", "kind": "end"},
        ],
        "edges": [
            {"source": "gate", "target": "close", "label": "merge.approved -> merge.approved"},
            {
                "source": "gate",
                "target": "implement",
                "label": "review.changes_requested -> review.changes_requested",
            },
            {"source": "close", "target": "end", "label": "delivery.merged -> delivery.merged"},
        ],
    }


def _board():
    board = parse_workflow_board(_graph())
    assert board is not None
    return board


def _gate(node_id: str = "gate", status: str = "pending") -> dict[str, Any]:
    return {
        "id": f"id-{node_id}",
        "activation_id": "activation-1",
        "node_id": node_id,
        "label": "Code review",
        "condition": "Is this ready to merge?",
        "status": status,
        "attempt": 2,
    }


def test_a_workflow_without_a_board_has_none() -> None:
    assert parse_workflow_board({"nodes": [], "edges": []}) is None
    assert board_from_snapshot({"version": "1.0.0"}) is None
    assert board_from_snapshot(None) is None


def test_gate_routes_come_from_the_gate_edges() -> None:
    column = _board().gate_column("gate")

    assert column is not None
    assert column.kind == BoardColumnKind.GATE
    assert column.approve_column_id == "merging"
    assert column.changes_column_id == "building"


def test_a_gate_approved_straight_to_the_end_leaves_for_done() -> None:
    graph = _graph()
    graph["edges"][0]["target"] = "end"

    assert parse_workflow_board(graph).gate_column("gate").approve_column_id == "done"


def test_tracker_columns_wrap_the_workflow_columns() -> None:
    columns = _board().with_tracker_columns()

    assert [column.id for column in columns] == [
        "backlog",
        "building",
        "code-review",
        "merging",
        "done",
    ]
    assert columns[0].kind == BoardColumnKind.TRACKER


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda g: g["board"].update(version=2), "version"),
        (lambda g: g["board"].update(extra=True), "only version and columns"),
        (lambda g: g["board"].update(columns=[]), "non-empty"),
        (lambda g: g["board"]["columns"][0].update(id="done"), "reserved"),
        (lambda g: g["board"]["columns"][0].update(title=""), "non-empty string"),
        (lambda g: g["board"]["columns"][0].update(gate="gate"), "exactly one"),
        (lambda g: g["board"]["columns"][0].update(nodes=["missing"]), "no work node"),
        (lambda g: g["board"]["columns"][0].update(nodes=["blocked"]), "own gate column"),
        (lambda g: g["board"]["columns"][1].update(gate="close"), "no gate node"),
        (lambda g: g["board"]["columns"][1].update(review="nope"), "review must be"),
        (lambda g: g["board"]["columns"][2].update(review="code"), "without a gate"),
        (lambda g: g["board"]["columns"][2].update(nodes=["implement"]), "only one board column"),
        (lambda g: g["board"]["columns"][2].update(id="building"), "unique"),
        (lambda g: g["nodes"][1].pop("approvalEvent"), "must set approvalEvent"),
        (lambda g: g["edges"].pop(0), "no edge"),
        (lambda g: g["nodes"][1].update(mode="evidence"), "human_approval"),
    ],
)
def test_a_board_that_does_not_match_its_graph_is_rejected(change, message) -> None:
    graph = deepcopy(_graph())
    change(graph)

    with pytest.raises(WorkflowBoardError, match=message):
        parse_workflow_board(graph)


def test_the_first_column_must_hold_work() -> None:
    graph = _graph()
    columns = graph["board"]["columns"]
    columns[0], columns[1] = columns[1], columns[0]

    with pytest.raises(WorkflowBoardError, match="first board column"):
        parse_workflow_board(graph)


def test_a_gate_routing_outside_the_board_is_rejected() -> None:
    graph = _graph()
    graph["board"]["columns"].pop()

    with pytest.raises(WorkflowBoardError, match="has no column"):
        parse_workflow_board(graph)


def test_an_unstarted_ticket_sits_where_the_tracker_says() -> None:
    backlog = place_ticket(_board(), run_status=None, issue_status_type="unstarted", gates=[])
    closed = place_ticket(_board(), run_status=None, issue_status_type="Completed", gates=[])

    assert (backlog.column_id, backlog.state) == ("backlog", BoardTicketState.NOT_STARTED)
    assert (closed.column_id, closed.state) == ("done", BoardTicketState.DONE)


def test_a_running_ticket_starts_in_the_first_column() -> None:
    placement = place_ticket(
        _board(), run_status=RunStatus.RUNNING, issue_status_type="started", gates=[]
    )

    assert (placement.column_id, placement.state) == ("building", BoardTicketState.RUNNING)


def test_a_pending_gate_puts_the_ticket_in_its_column() -> None:
    placement = place_ticket(
        _board(), run_status=RunStatus.RUNNING, issue_status_type="started", gates=[_gate()]
    )

    assert placement.column_id == "code-review"
    assert placement.state == BoardTicketState.NEEDS_DECISION
    assert placement.gate is not None
    assert placement.gate.activation_id == "activation-1"
    assert placement.gate.attempt == 2


def test_approval_moves_the_ticket_on_and_changes_send_it_back() -> None:
    approved = place_ticket(
        _board(),
        run_status=RunStatus.RUNNING,
        issue_status_type="started",
        gates=[_gate(status="approved")],
    )
    changes = place_ticket(
        _board(),
        run_status=RunStatus.RUNNING,
        issue_status_type="started",
        gates=[_gate(status="changes_requested"), _gate(status="approved")],
    )

    assert approved.column_id == "merging"
    assert changes.column_id == "building"


def test_a_gate_the_board_does_not_show_blocks_the_ticket_where_it_is() -> None:
    placement = place_ticket(
        _board(),
        run_status=RunStatus.RUNNING,
        issue_status_type="started",
        gates=[_gate(node_id="blocked")],
    )

    assert placement.column_id == "building"
    assert placement.state == BoardTicketState.BLOCKED
    assert placement.gate is not None


def test_a_failed_session_blocks_even_a_ticket_waiting_at_a_gate() -> None:
    placement = place_ticket(
        _board(),
        run_status=RunStatus.RUNNING,
        issue_status_type="started",
        gates=[_gate()],
        session_failed=True,
    )

    assert placement.state == BoardTicketState.BLOCKED
    assert placement.gate is None
    assert placement.reason


@pytest.mark.parametrize("status", [RunStatus.FAILED, RunStatus.ESCALATED])
def test_a_stopped_run_is_blocked(status: RunStatus) -> None:
    placement = place_ticket(_board(), run_status=status, issue_status_type="started", gates=[])

    assert placement.state == BoardTicketState.BLOCKED


def test_a_merged_run_is_done() -> None:
    placement = place_ticket(
        _board(), run_status=RunStatus.MERGED, issue_status_type="started", gates=[_gate()]
    )

    assert (placement.column_id, placement.state) == ("done", BoardTicketState.DONE)


def test_a_closed_ticket_is_done_whatever_its_run_says() -> None:
    placement = place_ticket(
        _board(), run_status=RunStatus.RUNNING, issue_status_type="completed", gates=[]
    )

    assert (placement.column_id, placement.state) == ("done", BoardTicketState.DONE)


def test_the_reviewed_delivery_flow_declares_a_code_gate() -> None:
    workflow = next(
        item for item in load_system_workflows() if item.name == "Reviewed Delivery Flow"
    )
    board = parse_workflow_board(workflow.graph)

    assert board is not None
    assert [column.id for column in board.columns] == ["building", "code-review", "merging"]
    gate = board.gate_column("delivery-code-gate")
    assert gate is not None
    assert gate.review == "code"
    assert gate.approve_column_id == "merging"
    assert gate.changes_column_id == "building"


def test_an_invalid_board_fails_the_workflow_document() -> None:
    path = BUNDLED_SYSTEM_WORKFLOWS_PATH / "reviewed-delivery-flow.yaml"
    document = path.read_text(encoding="utf-8").replace(
        "gate: delivery-code-gate", "gate: delivery-missing-gate"
    )

    with pytest.raises(WorkflowDocumentError, match="Invalid workflow board"):
        load_workflow_document(document)


def _two_workflows() -> ProjectBoard:
    graph = _graph()
    graph["board"]["columns"] = [
        {"id": "planning", "title": "Planning", "nodes": ["implement"]},
        {"id": "plan-review", "title": "Plan review", "gate": "gate", "review": "plan"},
    ]
    graph["edges"][0]["target"] = "end"
    planning = parse_workflow_board(graph)
    assert planning is not None
    return ProjectBoard(
        workflows=(
            BoardWorkflow(id="plan", name="Planning", board=planning),
            BoardWorkflow(id="deliver", name="Delivery", board=_board()),
        )
    )


def test_leaving_a_workflow_leads_to_the_first_column_of_the_next() -> None:
    project = _two_workflows()

    review = next(column for column in project.columns if column.id == "plan-review")

    assert review.approve_column_id == "building"
    assert review.changes_column_id == "planning"
    assert (review.workflow_id, review.workflow_name) == ("plan", "Planning")
    assert project.columns[-2].approve_column_id is None


def test_only_a_first_column_starts_a_workflow() -> None:
    project = _two_workflows()

    assert project.workflow_starting_at("building").id == "deliver"
    assert project.workflow_starting_at("planning").id == "plan"
    assert project.workflow_starting_at("merging") is None
    assert project.workflow_starting_at("backlog") is None


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (BoardTicketState.DONE, BoardTicketState.READY),
        (BoardTicketState.RUNNING, BoardTicketState.RUNNING),
    ],
)
def test_a_workflow_left_behind_rests_on_the_next_one(state, expected) -> None:
    placement = place_on_project(
        _two_workflows(), "plan", BoardPlacement(column_id="done", state=state)
    )

    assert (placement.column_id, placement.state) == ("building", expected)


def test_the_last_workflow_leaves_for_done() -> None:
    placement = place_on_project(
        _two_workflows(),
        "deliver",
        BoardPlacement(column_id="done", state=BoardTicketState.DONE),
    )

    assert (placement.column_id, placement.state) == ("done", BoardTicketState.DONE)


def test_a_placement_on_columns_the_board_lacks_is_unknown() -> None:
    placement = place_on_project(
        _two_workflows(),
        "plan",
        BoardPlacement(column_id="drafting", state=BoardTicketState.RUNNING),
    )

    assert placement.column_id is None
    assert placement.state == BoardTicketState.UNKNOWN


def test_two_workflows_cannot_share_a_column() -> None:
    board = _board()
    with pytest.raises(WorkflowBoardError, match="both declare board column 'building'"):
        ProjectBoard(
            workflows=(
                BoardWorkflow(id="a", name="First", board=board),
                BoardWorkflow(id="b", name="Second", board=board),
            )
        )


def test_a_workflow_appears_on_a_board_once() -> None:
    entry = BoardWorkflow(id="a", name="First", board=_board())
    with pytest.raises(WorkflowBoardError, match="only once"):
        ProjectBoard(workflows=(entry, entry))


def test_a_board_needs_a_workflow() -> None:
    with pytest.raises(WorkflowBoardError, match="at least one"):
        ProjectBoard(workflows=())


def test_the_ticket_planning_flow_declares_a_plan_gate() -> None:
    workflow = next(item for item in load_system_workflows() if item.name == "Ticket Planning Flow")

    board = parse_workflow_board(workflow.graph)

    assert [column.id for column in board.columns] == ["planning", "plan-review", "plan-record"]
    gate = board.column("plan-review")
    assert (gate.kind, gate.review) == (BoardColumnKind.GATE, "plan")
    assert (gate.approve_column_id, gate.changes_column_id) == ("plan-record", "planning")
    node = next(item for item in workflow.graph["nodes"] if item["id"] == gate.gate_node_id)
    assert "autoForwardAfter" not in node


def test_a_blocked_plan_reaches_a_person_who_can_send_it_on() -> None:
    workflow = next(item for item in load_system_workflows() if item.name == "Ticket Planning Flow")
    nodes = {item["id"]: item for item in workflow.graph["nodes"]}
    edges = {(item["source"], item["target"]) for item in workflow.graph["edges"]}

    blocked = nodes["plan-blocked"]

    assert blocked["mode"] == "human_approval"
    assert {("plan-draft", "plan-blocked"), ("plan-check", "plan-blocked")} <= edges
    assert ("plan-blocked", "plan-draft") in edges
    assert blocked["approvalEvent"] == blocked["changesRequestedEvent"]
    assert (
        blocked["approvalEvent"] in (nodes["plan-draft"]["stageMembers"][0]["consumesEventTypes"])
    )


def test_planning_and_delivery_share_a_board() -> None:
    workflows = {item.name: item for item in load_system_workflows()}

    project = ProjectBoard(
        workflows=tuple(
            BoardWorkflow(
                id=name,
                name=name,
                board=parse_workflow_board(workflows[name].graph),
            )
            for name in ("Ticket Planning Flow", "Reviewed Delivery Flow")
        )
    )

    assert project.column_after("Ticket Planning Flow") == "building"


def test_a_coordinator_can_start_every_workflow_that_has_a_board() -> None:
    boarded = [item for item in load_system_workflows() if item.graph.get("board")]

    assert {item.name for item in boarded} >= {"Ticket Planning Flow", "Reviewed Delivery Flow"}
    assert {item.scope for item in boarded} == {WorkflowScope.SYSTEM}


def _repo_less_saga(**overrides: Any) -> Saga:
    return Saga(
        id=uuid4(),
        tracker_id="project-1",
        tracker_type="linear",
        slug="shop",
        name="Shop",
        repos=[],
        feature_branch="feat/shop",
        status=SagaStatus.ACTIVE,
        created_at=datetime(2026, 11, 1, tzinfo=UTC),
        base_branch="",
        **overrides,
    )


def test_a_workflow_needs_a_repository_unless_it_says_otherwise() -> None:
    assert workflow_requires_repository(None) is True
    assert workflow_requires_repository({"graph": {}}) is True
    assert workflow_requires_repository({"graph": {"requiresRepository": False}}) is False


def test_the_ticket_work_flow_is_the_bundled_workflow_that_needs_no_repository() -> None:
    needing_none = [
        workflow.name
        for workflow in load_system_workflows()
        if not workflow_requires_repository({"graph": workflow.graph})
    ]

    assert needing_none == ["Ticket Work Flow"]


def test_requires_repository_must_be_a_boolean() -> None:
    document = yaml.safe_load((BUNDLED_SYSTEM_WORKFLOWS_PATH / "ticket-work-flow.yaml").read_text())
    document["graph"]["requiresRepository"] = "no"

    with pytest.raises(WorkflowDocumentError, match="requiresRepository"):
        load_workflow_document(yaml.safe_dump(document))


def test_a_project_with_a_repository_has_no_gap() -> None:
    saga = replace(_repo_less_saga(), repos=["jve/repo"], workflow_id=uuid4())

    assert repository_gap(saga) is None


def test_a_project_without_a_workflow_has_no_gap() -> None:
    assert repository_gap(_repo_less_saga()) is None


def test_a_project_without_a_repository_names_the_workflow_that_needs_one() -> None:
    saga = _repo_less_saga(workflow_id=uuid4(), workflow_snapshot={"name": "Delivery", "graph": {}})

    assert "Delivery needs a repository" in (repository_gap(saga) or "")


def test_only_board_workflows_that_need_a_repository_are_named() -> None:
    pins = (
        SagaBoardWorkflow(
            workflow_id=uuid4(),
            workflow_version="1.0.0",
            workflow_snapshot={"name": "Work", "graph": {"requiresRepository": False}},
        ),
        SagaBoardWorkflow(
            workflow_id=uuid4(),
            workflow_version="1.0.0",
            workflow_snapshot={"name": "Delivery", "graph": {}},
        ),
    )

    assert repository_gap(_repo_less_saga(board_workflows=pins)).startswith("Delivery needs")
    assert repository_gap(_repo_less_saga(board_workflows=pins[:1])) is None
