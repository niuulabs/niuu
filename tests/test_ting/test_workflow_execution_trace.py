"""Truthfulness regressions for developer execution trace projection."""

from datetime import UTC, datetime
from types import SimpleNamespace

from ting.domain.workflow_execution_trace import (
    canonical_outcome_event_type,
    project_trace_event,
    public_trace_graph,
    workflow_event_sources,
)


def _entry(event_type: str) -> SimpleNamespace:
    return SimpleNamespace(
        session_id="parent-session",
        seq=7,
        ts=datetime.now(UTC),
        payload={
            "eventType": event_type,
            "fields": {"parentNodeId": "delivery-workstreams", "generation": 1},
        },
    )


def test_parent_node_lineage_does_not_override_delivery_observation_source() -> None:
    graph = {
        "nodes": [
            {"id": "delivery-workstreams"},
            {"id": "delivery-publication-wait"},
            {"id": "delivery-publish"},
        ],
        "edges": [
            {
                "source": "delivery-workstreams",
                "target": "delivery-publish",
                "label": "developer.children.verified -> developer.children.verified",
            },
            {
                "source": "delivery-publication-wait",
                "target": "delivery-publish",
                "label": "developer.delivery.observed -> developer.delivery.observed",
            },
        ],
    }
    node_ids = {node["id"] for node in graph["nodes"]}
    sources = workflow_event_sources(graph)

    delivery = project_trace_event(
        _entry("developer.delivery.observed"),
        node_ids=node_ids,
        event_sources=sources,
        persona_definitions={},
    )
    children = project_trace_event(
        _entry("developer.children.verified"),
        node_ids=node_ids,
        event_sources=sources,
        persona_definitions={},
    )

    assert delivery["nodeIds"] == ["delivery-publication-wait"]
    assert delivery["mapping"] == "workflow_graph_event"
    assert children["nodeIds"] == ["delivery-workstreams"]


def test_subworkflow_blocked_event_is_attributed_to_its_owning_node() -> None:
    graph = {
        "nodes": [
            {
                "id": "delivery-workstreams",
                "kind": "subworkflow",
                "blockedEvent": "children.blocked",
            },
            {"id": "delivery-coordinate"},
        ],
        "edges": [],
    }
    node_ids = {node["id"] for node in graph["nodes"]}
    sources = workflow_event_sources(graph)

    blocked = project_trace_event(
        _entry("children.blocked"),
        node_ids=node_ids,
        event_sources=sources,
        persona_definitions={},
    )

    assert blocked["nodeIds"] == ["delivery-workstreams"]
    assert blocked["mapping"] == "workflow_graph_event"


def test_actual_workflow_node_identity_remains_an_explicit_origin() -> None:
    entry = _entry("custom.event")
    entry.payload["fields"]["workflowNodeId"] = "actual-stage"

    event = project_trace_event(
        entry,
        node_ids={"actual-stage", "delivery-workstreams"},
        event_sources={},
        persona_definitions={},
    )

    assert event["nodeIds"] == ["actual-stage"]
    assert event["mapping"] == "explicit"


def test_project_trace_event_reports_unmapped_when_nothing_matches() -> None:
    entry = _entry("unregistered.event")

    event = project_trace_event(
        entry,
        node_ids={"delivery-workstreams"},
        event_sources={},
        persona_definitions={},
    )

    assert event["nodeIds"] == []
    assert event["candidateNodeIds"] == []
    assert event["mapping"] == "unmapped"


def test_project_trace_event_reports_multiple_candidates_without_mapping() -> None:
    entry = _entry("ambiguous.event")

    event = project_trace_event(
        entry,
        node_ids={"stage-a", "stage-b"},
        event_sources={"ambiguous.event": ("stage-a", "stage-b")},
        persona_definitions={},
    )

    assert event["nodeIds"] == []
    assert sorted(event["candidateNodeIds"]) == ["stage-a", "stage-b"]
    assert event["mapping"] == "unmapped"


def test_project_trace_event_copies_task_id_and_source_event_id_when_present() -> None:
    entry = _entry("custom.event")
    entry.payload["taskId"] = "task-123"
    entry.payload["sourceEventId"] = "source-456"

    event = project_trace_event(
        entry,
        node_ids=set(),
        event_sources={},
        persona_definitions={},
    )

    assert event["taskId"] == "task-123"
    assert event["sourceEventId"] == "source-456"


def test_project_trace_event_omits_task_id_when_blank() -> None:
    entry = _entry("custom.event")
    entry.payload["taskId"] = ""

    event = project_trace_event(
        entry,
        node_ids=set(),
        event_sources={},
        persona_definitions={},
    )

    assert "taskId" not in event


def test_project_trace_event_falls_back_to_summary_field() -> None:
    entry = _entry("custom.event")
    entry.payload["fields"]["summary"] = "from fields"

    event = project_trace_event(
        entry,
        node_ids=set(),
        event_sources={},
        persona_definitions={},
    )

    assert event["summary"] == "from fields"


def test_project_trace_event_uses_pinned_persona_outcome_mapping() -> None:
    entry = _entry("review.outcome")
    entry.payload["persona"] = "reviewer"
    entry.payload["verdict"] = "approved"
    persona_definitions = {
        "reviewer": {
            "definition": {
                "produces": {
                    "event_type": "review.outcome",
                    "event_type_map": {"approved": "review.approved"},
                }
            }
        }
    }

    event = project_trace_event(
        entry,
        node_ids={"review-node"},
        event_sources={"review.approved": ("review-node",)},
        persona_definitions=persona_definitions,
    )

    assert event["mapping"] == "pinned_persona_outcome"
    assert event["canonicalEventType"] == "review.approved"
    assert event["nodeIds"] == ["review-node"]


class TestCanonicalOutcomeEventType:
    def test_returns_none_when_persona_or_verdict_is_not_a_string(self) -> None:
        assert (
            canonical_outcome_event_type(
                event_type="x",
                persona=None,
                verdict="approved",
                persona_definitions={},
            )
            is None
        )
        assert (
            canonical_outcome_event_type(
                event_type="x",
                persona="reviewer",
                verdict=None,
                persona_definitions={},
            )
            is None
        )

    def test_returns_none_when_persona_definition_is_unknown(self) -> None:
        assert (
            canonical_outcome_event_type(
                event_type="x",
                persona="ghost",
                verdict="approved",
                persona_definitions={},
            )
            is None
        )

    def test_returns_none_when_produces_event_type_does_not_match(self) -> None:
        persona_definitions = {
            "reviewer": {
                "definition": {"produces": {"event_type": "other.event", "event_type_map": {}}}
            }
        }

        assert (
            canonical_outcome_event_type(
                event_type="review.outcome",
                persona="reviewer",
                verdict="approved",
                persona_definitions=persona_definitions,
            )
            is None
        )

    def test_returns_none_when_event_type_map_is_missing(self) -> None:
        persona_definitions = {
            "reviewer": {"definition": {"produces": {"event_type": "review.outcome"}}}
        }

        assert (
            canonical_outcome_event_type(
                event_type="review.outcome",
                persona="reviewer",
                verdict="approved",
                persona_definitions=persona_definitions,
            )
            is None
        )

    def test_returns_none_when_verdict_alias_is_unmapped(self) -> None:
        persona_definitions = {
            "reviewer": {
                "definition": {
                    "produces": {
                        "event_type": "review.outcome",
                        "event_type_map": {"rejected": "review.rejected"},
                    }
                }
            }
        }

        assert (
            canonical_outcome_event_type(
                event_type="review.outcome",
                persona="reviewer",
                verdict="approved",
                persona_definitions=persona_definitions,
            )
            is None
        )


class TestWorkflowEventSources:
    def test_skips_non_dict_nodes_and_nodes_without_an_id(self) -> None:
        graph = {
            "nodes": ["not-a-dict", {"id": ""}, {"id": "gate", "dispatchEvent": "gate.dispatched"}],
            "edges": [],
        }

        sources = workflow_event_sources(graph)

        assert sources == {"gate.dispatched": ("gate",)}

    def test_does_not_override_an_edge_derived_source(self) -> None:
        graph = {
            "nodes": [{"id": "gate", "dispatchEvent": "gate.dispatched"}],
            "edges": [
                {
                    "source": "upstream",
                    "target": "gate",
                    "label": "gate.dispatched -> gate.dispatched",
                }
            ],
        }

        sources = workflow_event_sources(graph)

        assert sources["gate.dispatched"] == ("upstream",)


class TestPublicTraceGraph:
    def test_skips_non_dict_and_unidentified_nodes(self) -> None:
        graph = {"nodes": ["nope", {"id": "  "}], "edges": []}

        projected = public_trace_graph(graph)

        assert projected["nodes"] == []

    def test_includes_optional_string_fields_and_templates(self) -> None:
        graph = {
            "nodes": [
                {
                    "id": "stage",
                    "label": "Stage",
                    "kind": "task",
                    "description": "does work",
                    "executionMode": "parallel",
                    "joinMode": "all",
                    "expansionRole": "leader",
                    "source": "template",
                    "templates": {"main": "main-alias", "bad": 1},
                }
            ],
            "edges": [],
        }

        projected = public_trace_graph(graph)

        node = projected["nodes"][0]
        assert node["description"] == "does work"
        assert node["executionMode"] == "parallel"
        assert node["joinMode"] == "all"
        assert node["expansionRole"] == "leader"
        assert node["source"] == "template"
        assert node["templates"] == {"main": "main-alias"}

    def test_includes_position_only_when_both_axes_are_numeric(self) -> None:
        graph = {
            "nodes": [
                {"id": "with-position", "position": {"x": 1, "y": 2.5}},
                {"id": "partial-position", "position": {"x": 1}},
                {"id": "bad-position", "position": {"x": "nope", "y": 2}},
            ],
            "edges": [],
        }

        projected = public_trace_graph(graph)

        by_id = {node["id"]: node for node in projected["nodes"]}
        assert by_id["with-position"]["position"] == {"x": 1, "y": 2.5}
        assert "position" not in by_id["partial-position"]
        assert "position" not in by_id["bad-position"]

    def test_includes_stage_members_and_skips_invalid_ones(self) -> None:
        graph = {
            "nodes": [
                {
                    "id": "stage",
                    "stageMembers": [
                        "not-a-dict",
                        {"personaId": ""},
                        {
                            "personaId": "reviewer",
                            "model": "sonnet",
                            "consumesEventTypes": ["a.event", 3, "b.event"],
                        },
                    ],
                }
            ],
            "edges": [],
        }

        projected = public_trace_graph(graph)

        node = projected["nodes"][0]
        assert node["stageMembers"] == [
            {
                "personaId": "reviewer",
                "model": "sonnet",
                "consumesEventTypes": ["a.event", "b.event"],
            }
        ]

    def test_omits_stage_members_when_none_are_valid(self) -> None:
        graph = {"nodes": [{"id": "stage", "stageMembers": ["nope"]}], "edges": []}

        projected = public_trace_graph(graph)

        assert "stageMembers" not in projected["nodes"][0]

    def test_skips_non_dict_edges_and_edges_missing_required_fields(self) -> None:
        graph = {
            "nodes": [],
            "edges": [
                "not-a-dict",
                {"id": "", "source": "a", "target": "b"},
                {"id": "e1", "source": "", "target": "b"},
                {"id": "e2", "source": "a", "target": ""},
                {"id": "e3", "source": "a", "target": "b", "label": "x -> x"},
            ],
        }

        projected = public_trace_graph(graph)

        assert projected["edges"] == [{"id": "e3", "source": "a", "target": "b", "label": "x -> x"}]
