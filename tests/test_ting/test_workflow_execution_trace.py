"""Truthfulness regressions for developer execution trace projection."""

from datetime import UTC, datetime
from types import SimpleNamespace

from ting.domain.workflow_execution_trace import project_trace_event, workflow_event_sources


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
