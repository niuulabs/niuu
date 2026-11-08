"""Every refusal of the runnable-graph validator, and the graphs it accepts."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from tests.test_ting.graph_builders import edge, end, persona, stage, trigger
from ting.domain.exceptions import WorkflowDocumentError
from ting.domain.workflow_document import validate_workflow_graph
from ting.system_workflows import load_system_workflows

PERSONAS = {
    "coder": persona({"ok": "work.done", "blocked": "work.blocked"}, ["go", "work.changes"]),
    "checker": persona({"ok": "check.ok", "bad": "work.changes"}, ["work.done"]),
}


def member(alias: str, **extra: Any) -> dict[str, Any]:
    return {"personaId": alias, **extra}


def base_graph() -> dict[str, Any]:
    """A trigger, a coder, a checker that can send the work back, and an end."""
    return {
        "nodes": [
            trigger("go"),
            stage("work", [member("coder")]),
            stage("check", [member("checker")]),
            end(),
        ],
        "edges": [
            edge("e-go", "go", "work", "go"),
            edge("e-work", "work", "check", "work.done"),
            edge("e-back", "check", "work", "work.changes", rework=True, maxIterations=3),
            edge("e-ok", "check", "done", "check.ok"),
        ],
    }


def node(graph: dict[str, Any], node_id: str) -> dict[str, Any]:
    return next(item for item in graph["nodes"] if item["id"] == node_id)


def edge_of(graph: dict[str, Any], edge_id: str) -> dict[str, Any]:
    return next(item for item in graph["edges"] if item["id"] == edge_id)


def refuses(graph: dict[str, Any], message: str, *, personas: Any = None) -> None:
    with pytest.raises(WorkflowDocumentError, match=message):
        validate_workflow_graph(graph, persona_definitions=personas)


def test_a_loop_with_a_bounded_rework_edge_is_accepted() -> None:
    validate_workflow_graph(base_graph())
    validate_workflow_graph(base_graph(), persona_definitions=PERSONAS)


# --- node kinds ---------------------------------------------------------------------


def test_an_unknown_node_kind_is_refused_with_the_kinds_the_engine_runs() -> None:
    graph = base_graph()
    node(graph, "work")["kind"] = "teleport"

    refuses(graph, "unknown kind 'teleport'.*the engine runs: end, gate, resource")


def test_cond_nodes_are_refused_because_nothing_interprets_them() -> None:
    graph = base_graph()
    graph["nodes"].append({"id": "branch", "kind": "cond", "label": "If", "predicate": "x"})

    refuses(graph, "'branch' is a cond node; cond nodes are not supported")


def test_an_include_that_was_not_resolved_is_not_a_node_kind_the_engine_runs() -> None:
    graph = base_graph()
    graph["nodes"].append({"id": "inc", "kind": "include", "label": "Inc"})

    refuses(graph, "unknown kind 'include'")


def test_a_node_id_is_unique() -> None:
    graph = base_graph()
    graph["nodes"].append(stage("work", [member("coder")]))

    refuses(graph, "node id 'work' is declared twice")


# --- edges ---------------------------------------------------------------------------


def test_a_dangling_edge_endpoint_is_refused() -> None:
    graph = base_graph()
    edge_of(graph, "e-work")["target"] = "nowhere"
    refuses(graph, "edge 'e-work' target 'nowhere' is not a node")

    graph = base_graph()
    edge_of(graph, "e-work")["source"] = "nobody"
    refuses(graph, "edge 'e-work' source 'nobody' is not a node")


def test_an_edge_needs_an_id_and_a_unique_one() -> None:
    graph = base_graph()
    graph["edges"][0]["id"] = ""
    refuses(graph, "Every workflow edge needs an id")

    graph = base_graph()
    graph["edges"][1]["id"] = "e-go"
    refuses(graph, "edge id 'e-go' is declared twice")


@pytest.mark.parametrize("label", [None, "", "work.done", "-> work.done", "work.done ->"])
def test_an_edge_must_name_its_source_and_target_events(label: str | None) -> None:
    graph = base_graph()
    edge_of(graph, "e-work")["label"] = label

    refuses(graph, "label must be 'source_event -> target_event'")


def test_the_engine_prefix_is_reserved_for_the_engines_own_events() -> None:
    graph = base_graph()
    edge_of(graph, "e-work")["label"] = "engine.work.done -> work.done"
    refuses(graph, "edge 'e-work' event 'engine.work.done' uses the reserved 'engine.' prefix")

    graph = base_graph()
    node(graph, "go")["dispatchEvent"] = "engine.go"
    refuses(graph, "dispatchEvent 'engine.go' uses the reserved")


# --- rework edges -----------------------------------------------------------------------


def test_a_rework_edge_needs_a_positive_whole_number_bound() -> None:
    for bound in (None, 0, -1, 1.5, True, "3"):
        graph = base_graph()
        back = edge_of(graph, "e-back")
        if bound is None:
            del back["maxIterations"]
        else:
            back["maxIterations"] = bound
        refuses(graph, "edge 'e-back' is a rework edge and needs maxIterations")


def test_rework_must_be_a_boolean() -> None:
    graph = base_graph()
    edge_of(graph, "e-back")["rework"] = "yes"

    refuses(graph, "rework must be true or false")


def test_a_bound_on_an_edge_that_is_not_rework_is_refused() -> None:
    graph = base_graph()
    edge_of(graph, "e-work")["maxIterations"] = 2

    refuses(graph, "edge 'e-work' sets maxIterations but is not marked rework")


def test_a_cycle_without_a_rework_edge_is_refused_with_its_path() -> None:
    graph = base_graph()
    back = edge_of(graph, "e-back")
    del back["rework"], back["maxIterations"]

    refuses(graph, "cycle with no rework edge: check -> work -> check")


def test_a_rework_edge_must_actually_go_back() -> None:
    graph = base_graph()
    edge_of(graph, "e-ok")["rework"] = True
    edge_of(graph, "e-ok")["maxIterations"] = 2

    refuses(graph, "edge 'e-ok' is marked rework but does not go back to an earlier step")


def test_two_loops_each_need_their_own_rework_edge() -> None:
    graph = base_graph()
    graph["nodes"].insert(3, stage("ship", [member("coder")]))
    edge_of(graph, "e-ok")["target"] = "ship"
    graph["edges"] += [
        edge("e-ship", "ship", "done", "ship.done"),
        edge("e-loop", "ship", "ship", "ship.again"),
    ]

    refuses(graph, "cycle with no rework edge: ship -> ship")


# --- reachability ------------------------------------------------------------------------


def test_a_graph_without_a_trigger_cannot_start() -> None:
    graph = base_graph()
    graph["nodes"] = [item for item in graph["nodes"] if item["kind"] != "trigger"]
    graph["edges"] = [item for item in graph["edges"] if item["source"] != "go"]

    refuses(graph, "no trigger node")


def test_a_trigger_must_lead_somewhere_with_its_own_event() -> None:
    graph = base_graph()
    graph["edges"] = [item for item in graph["edges"] if item["id"] != "e-go"]
    refuses(graph, "trigger 'go' has no outgoing edge")

    graph = base_graph()
    edge_of(graph, "e-go")["label"] = "other.event -> go"
    refuses(
        graph, "edge 'e-go' leaves trigger 'go' with event 'other.event'.*dispatchEvent is 'go'"
    )

    graph = base_graph()
    del node(graph, "go")["dispatchEvent"]
    refuses(graph, "trigger 'go' needs a dispatchEvent")


def test_a_node_no_trigger_reaches_is_refused() -> None:
    graph = base_graph()
    graph["nodes"].append(stage("island", [member("coder")]))
    graph["edges"].append(edge("e-island", "island", "done", "work.done"))

    refuses(graph, r"unreachable from a trigger: 'island'")


def test_a_graph_needs_an_end_a_trigger_can_reach() -> None:
    graph = base_graph()
    graph["nodes"] = [item for item in graph["nodes"] if item["kind"] != "end"]
    graph["edges"] = [item for item in graph["edges"] if item["id"] != "e-ok"]
    graph["edges"].append(
        edge("e-self", "check", "check", "check.ok", rework=True, maxIterations=1)
    )

    refuses(graph, "no end node reachable from a trigger")


def test_a_node_nothing_leaves_is_a_dead_end() -> None:
    graph = base_graph()
    graph["edges"] = [item for item in graph["edges"] if item["source"] != "check"]

    refuses(graph, "node 'check' is a dead end: no edge leaves it")


def test_a_loop_with_no_way_out_cannot_reach_an_end() -> None:
    graph = base_graph()
    graph["nodes"].insert(3, stage("spin-a", [member("coder")]))
    graph["nodes"].insert(4, stage("spin-b", [member("coder")]))
    graph["edges"] += [
        edge("e-spin-in", "check", "spin-a", "check.spin"),
        edge("e-spin-ab", "spin-a", "spin-b", "spin.on"),
        edge("e-spin-ba", "spin-b", "spin-a", "spin.on", rework=True, maxIterations=2),
    ]

    refuses(graph, "from which no end node can be reached: 'spin-a', 'spin-b'")


def test_resource_nodes_are_not_part_of_the_flow() -> None:
    graph = base_graph()
    graph["nodes"].append({"id": "memory", "kind": "resource", "label": "Memory"})

    validate_workflow_graph(graph)


# --- gates ---------------------------------------------------------------------------------


def gated_graph() -> dict[str, Any]:
    graph = base_graph()
    graph["nodes"].insert(
        3,
        {
            "id": "gate",
            "kind": "gate",
            "label": "Gate",
            "approvalEvent": "gate.approved",
            "changesRequestedEvent": "gate.changes",
        },
    )
    edge_of(graph, "e-ok")["target"] = "gate"
    graph["edges"] += [
        edge("e-yes", "gate", "done", "gate.approved"),
        edge("e-no", "gate", "work", "gate.changes", "work.changes", rework=True, maxIterations=2),
    ]
    return graph


def test_a_gate_that_declares_and_carries_both_decisions_is_accepted() -> None:
    validate_workflow_graph(gated_graph())


@pytest.mark.parametrize("field", ["approvalEvent", "changesRequestedEvent"])
def test_a_gate_must_declare_its_decision_events(field: str) -> None:
    for value in (None, "", "  "):
        graph = gated_graph()
        if value is None:
            del node(graph, "gate")[field]
        else:
            node(graph, "gate")[field] = value
        refuses(graph, f"gate 'gate' does not declare {field}")


def test_a_decision_event_no_edge_carries_would_dead_end_the_run() -> None:
    graph = gated_graph()
    node(graph, "gate")["approvalEvent"] = "gate.approved.elsewhere"
    graph["edges"] = [item for item in graph["edges"] if item["id"] != "e-yes"]
    graph["edges"].append(edge("e-yes", "gate", "done", "gate.approved"))

    refuses(graph, "approvalEvent 'gate.approved.elsewhere' is carried by no outgoing edge")


def test_a_gate_produces_only_its_two_decisions() -> None:
    graph = gated_graph()
    graph["edges"].append(edge("e-extra", "gate", "done", "gate.surprise"))

    refuses(graph, "outgoing edge for gate.surprise, which is neither its approvalEvent")


def test_a_blocked_gate_may_use_one_event_for_both_decisions() -> None:
    graph = base_graph()
    graph["nodes"].insert(
        3,
        {
            "id": "unblock",
            "kind": "gate",
            "label": "Unblock",
            "approvalEvent": "unblock.retry",
            "changesRequestedEvent": "unblock.retry",
        },
    )
    graph["edges"] += [
        edge("e-block", "check", "unblock", "check.blocked"),
        edge(
            "e-retry",
            "unblock",
            "work",
            "unblock.retry",
            "work.changes",
            rework=True,
            maxIterations=2,
        ),
    ]

    validate_workflow_graph(graph)


# --- node fields ----------------------------------------------------------------------------


def test_end_completion_rules_are_refused_because_nothing_interprets_them() -> None:
    graph = base_graph()
    node(graph, "done")["completionRules"] = [{"all": True}]

    refuses(graph, "end node 'done' declares completionRules")


@pytest.mark.parametrize("deadline", [0, -5, True, "soon"])
def test_a_node_deadline_is_a_positive_number(deadline: object) -> None:
    graph = base_graph()
    node(graph, "work")["deadlineSeconds"] = deadline

    refuses(graph, "node 'work' deadlineSeconds must be a positive number")


def test_a_stage_needs_members_and_valid_modes() -> None:
    graph = base_graph()
    node(graph, "work")["stageMembers"] = []
    refuses(graph, "stage 'work' has no members")

    for field, value, message in (
        ("executionMode", "sideways", "executionMode must be one of parallel, sequential"),
        ("joinMode", "some", "joinMode must be one of all, any, merge"),
        ("maxConcurrent", 0, "maxConcurrent must be a positive integer"),
        ("maxConcurrent", "3", "maxConcurrent must be a positive integer"),
    ):
        graph = base_graph()
        node(graph, "work")[field] = value
        refuses(graph, message)


def test_the_modes_the_workflow_builder_writes_are_accepted() -> None:
    graph = base_graph()
    node(graph, "check")["joinMode"] = "merge"
    node(graph, "check")["executionMode"] = "parallel"
    node(graph, "check")["maxConcurrent"] = 3

    validate_workflow_graph(graph)


def test_member_filters_and_consumed_events_must_be_well_formed() -> None:
    graph = base_graph()
    node(graph, "work")["stageMembers"][0]["eventFilters"] = {"kind": 3}
    refuses(graph, "eventFilters must map names to strings")

    graph = base_graph()
    node(graph, "work")["stageMembers"][0]["consumesEventTypes"] = "go"
    refuses(graph, "consumesEventTypes must be a list of event types")


def test_legacy_persona_id_stages_are_still_members() -> None:
    graph = base_graph()
    work = node(graph, "work")
    del work["stageMembers"]
    work["personaIds"] = ["coder"]

    validate_workflow_graph(graph, persona_definitions=PERSONAS)


# --- events against the personas -------------------------------------------------------------


def test_an_edge_may_not_leave_a_stage_with_an_event_no_member_produces() -> None:
    graph = base_graph()
    edge_of(graph, "e-work")["label"] = "work.invented -> work.done"

    refuses(
        graph,
        "edge 'e-work' leaves stage 'work' with event 'work.invented', which no member produces",
        personas=PERSONAS,
    )


def test_an_edge_may_not_deliver_an_event_no_member_consumes() -> None:
    graph = base_graph()
    edge_of(graph, "e-work")["label"] = "work.done -> work.unheard"

    refuses(
        graph,
        "delivers event 'work.unheard' to stage 'check', but no member consumes it",
        personas=PERSONAS,
    )


def test_a_member_may_name_what_it_consumes_instead_of_its_personas_default() -> None:
    graph = base_graph()
    edge_of(graph, "e-work")["label"] = "work.done -> work.unheard"
    node(graph, "check")["stageMembers"][0]["consumesEventTypes"] = ["work.unheard"]

    validate_workflow_graph(graph, persona_definitions=PERSONAS)


def test_a_stage_member_needs_a_persona_definition_to_read_its_events_from() -> None:
    refuses(
        base_graph(),
        "member 'checker' has no persona definition",
        personas={"coder": PERSONAS["coder"]},
    )


def test_a_member_whose_events_nothing_carries_can_only_dead_end() -> None:
    graph = base_graph()
    node(graph, "check")["stageMembers"].append(member("coder"))

    refuses(
        graph,
        "stage 'check' member 'coder' can only dead-end",
        personas=PERSONAS,
    )


def test_a_member_that_produces_nothing_is_an_observer_and_is_accepted() -> None:
    graph = base_graph()
    node(graph, "check")["stageMembers"].append(member("watcher", consumesEventTypes=["work.done"]))
    personas = {**PERSONAS, "watcher": persona(None, ["work.done"])}

    validate_workflow_graph(graph, persona_definitions=personas)


def test_a_persona_verdict_no_edge_carries_is_not_a_graph_defect() -> None:
    # coder can give "blocked", which maps to an event nothing carries. Personas are shared
    # between stages, so that is the engine's loud step failure at run time, not a refusal.
    graph = base_graph()
    assert "work.blocked" not in {item["label"].split(" -> ")[0] for item in graph["edges"]}

    validate_workflow_graph(graph, persona_definitions=PERSONAS)


# --- the shipped workflows ------------------------------------------------------------------


def test_every_shipped_workflow_declares_its_rework_bounds_and_gate_events() -> None:
    workflows = load_system_workflows()
    assert workflows
    for workflow in workflows:
        graph = copy.deepcopy(workflow.graph)
        for item in graph["edges"]:
            if item.get("rework"):
                assert type(item.get("maxIterations")) is int and item["maxIterations"] > 0, (
                    workflow.name,
                    item["id"],
                )
        for item in graph["nodes"]:
            if item["kind"] == "gate":
                assert item["approvalEvent"] and item["changesRequestedEvent"], (
                    workflow.name,
                    item["id"],
                )
        if not any(item["kind"] == "include" for item in graph["nodes"]):
            validate_workflow_graph(graph, persona_definitions=workflow.persona_definitions)
