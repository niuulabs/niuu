"""Behavioural coverage for pinned-graph continuation event lookups.

These are pure functions over a workflow graph dict; every raise path here
protects an engine assumption (the graph was already validated at load
time), so each fail-loud branch is exercised directly.
"""

from __future__ import annotations

import pytest

from ting.domain.workflow_continuation_events import (
    subworkflow_blocked_event,
    subworkflow_joined_event,
    wait_node_conditions,
    wait_observed_event,
)
from ting.domain.workflow_execution import WorkflowExecutionError


def _graph(nodes: list[dict], edges: list[dict] | None = None) -> dict:
    return {"nodes": nodes, "edges": edges or []}


class TestSubworkflowJoinedEvent:
    def test_returns_the_single_outgoing_event(self) -> None:
        graph = _graph(
            nodes=[{"id": "fanout", "kind": "subworkflow"}],
            edges=[
                {
                    "source": "fanout",
                    "target": "next",
                    "label": "children.joined -> children.joined",
                }
            ],
        )

        assert subworkflow_joined_event(graph, "fanout") == "children.joined"

    def test_raises_when_node_is_missing(self) -> None:
        graph = _graph(nodes=[])

        with pytest.raises(WorkflowExecutionError) as excinfo:
            subworkflow_joined_event(graph, "fanout")

        assert "Workflow graph has no subworkflow node 'fanout'" in str(excinfo.value)

    def test_raises_when_node_is_not_a_subworkflow(self) -> None:
        graph = _graph(nodes=[{"id": "fanout", "kind": "wait"}])

        with pytest.raises(WorkflowExecutionError) as excinfo:
            subworkflow_joined_event(graph, "fanout")

        assert "Workflow graph has no subworkflow node 'fanout'" in str(excinfo.value)

    def test_raises_when_there_is_no_outgoing_event(self) -> None:
        graph = _graph(nodes=[{"id": "fanout", "kind": "subworkflow"}], edges=[])

        with pytest.raises(WorkflowExecutionError) as excinfo:
            subworkflow_joined_event(graph, "fanout")

        assert "Subworkflow node 'fanout' has no single outgoing joined event" in str(excinfo.value)

    def test_raises_when_there_are_multiple_outgoing_events(self) -> None:
        graph = _graph(
            nodes=[{"id": "fanout", "kind": "subworkflow"}],
            edges=[
                {"source": "fanout", "target": "a", "label": "x -> x"},
                {"source": "fanout", "target": "b", "label": "y -> y"},
            ],
        )

        with pytest.raises(WorkflowExecutionError) as excinfo:
            subworkflow_joined_event(graph, "fanout")

        assert "Subworkflow node 'fanout' has no single outgoing joined event" in str(excinfo.value)


class TestSubworkflowBlockedEvent:
    def test_returns_declared_blocked_event(self) -> None:
        graph = _graph(
            nodes=[
                {
                    "id": "fanout",
                    "kind": "subworkflow",
                    "blockedEvent": "children.blocked",
                }
            ]
        )

        assert subworkflow_blocked_event(graph, "fanout") == "children.blocked"

    def test_raises_when_blocked_event_is_missing(self) -> None:
        graph = _graph(nodes=[{"id": "fanout", "kind": "subworkflow"}])

        with pytest.raises(WorkflowExecutionError) as excinfo:
            subworkflow_blocked_event(graph, "fanout")

        assert "Subworkflow node 'fanout' does not declare blockedEvent" in str(excinfo.value)

    def test_raises_when_blocked_event_is_blank(self) -> None:
        graph = _graph(nodes=[{"id": "fanout", "kind": "subworkflow", "blockedEvent": "   "}])

        with pytest.raises(WorkflowExecutionError) as excinfo:
            subworkflow_blocked_event(graph, "fanout")

        assert "Subworkflow node 'fanout' does not declare blockedEvent" in str(excinfo.value)


class TestWaitObservedEvent:
    def test_returns_the_single_outgoing_event(self) -> None:
        graph = _graph(
            nodes=[{"id": "gate", "kind": "wait"}],
            edges=[
                {
                    "source": "gate",
                    "target": "next",
                    "label": "delivery.observed -> delivery.observed",
                }
            ],
        )

        assert wait_observed_event(graph, "gate") == "delivery.observed"

    def test_raises_when_node_is_missing(self) -> None:
        graph = _graph(nodes=[])

        with pytest.raises(WorkflowExecutionError) as excinfo:
            wait_observed_event(graph, "gate")

        assert "Workflow graph has no wait node 'gate'" in str(excinfo.value)

    def test_raises_when_node_is_not_a_wait_node(self) -> None:
        graph = _graph(nodes=[{"id": "gate", "kind": "subworkflow"}])

        with pytest.raises(WorkflowExecutionError) as excinfo:
            wait_observed_event(graph, "gate")

        assert "Workflow graph has no wait node 'gate'" in str(excinfo.value)

    def test_raises_when_there_is_no_outgoing_event(self) -> None:
        graph = _graph(nodes=[{"id": "gate", "kind": "wait"}], edges=[])

        with pytest.raises(WorkflowExecutionError) as excinfo:
            wait_observed_event(graph, "gate")

        assert "Wait node 'gate' has no single outgoing observed event" in str(excinfo.value)

    def test_raises_when_there_are_multiple_outgoing_events(self) -> None:
        graph = _graph(
            nodes=[{"id": "gate", "kind": "wait"}],
            edges=[
                {"source": "gate", "target": "a", "label": "x -> x"},
                {"source": "gate", "target": "b", "label": "y -> y"},
            ],
        )

        with pytest.raises(WorkflowExecutionError) as excinfo:
            wait_observed_event(graph, "gate")

        assert "Wait node 'gate' has no single outgoing observed event" in str(excinfo.value)


class TestWaitNodeConditions:
    def test_returns_declared_conditions_as_a_frozenset(self) -> None:
        graph = _graph(
            nodes=[
                {
                    "id": "gate",
                    "kind": "wait",
                    "conditions": ["delivery.observed", "delivery.retried"],
                }
            ]
        )

        assert wait_node_conditions(graph, "gate") == frozenset(
            {"delivery.observed", "delivery.retried"}
        )

    def test_raises_when_node_is_missing(self) -> None:
        graph = _graph(nodes=[])

        with pytest.raises(WorkflowExecutionError) as excinfo:
            wait_node_conditions(graph, "gate")

        assert "Workflow graph has no wait node 'gate'" in str(excinfo.value)

    def test_raises_when_node_is_not_a_wait_node(self) -> None:
        graph = _graph(nodes=[{"id": "gate", "kind": "subworkflow"}])

        with pytest.raises(WorkflowExecutionError) as excinfo:
            wait_node_conditions(graph, "gate")

        assert "Workflow graph has no wait node 'gate'" in str(excinfo.value)

    def test_raises_when_conditions_are_missing(self) -> None:
        graph = _graph(nodes=[{"id": "gate", "kind": "wait"}])

        with pytest.raises(WorkflowExecutionError) as excinfo:
            wait_node_conditions(graph, "gate")

        assert "Wait node 'gate' does not declare any conditions" in str(excinfo.value)

    def test_raises_when_conditions_is_an_empty_list(self) -> None:
        graph = _graph(nodes=[{"id": "gate", "kind": "wait", "conditions": []}])

        with pytest.raises(WorkflowExecutionError) as excinfo:
            wait_node_conditions(graph, "gate")

        assert "Wait node 'gate' does not declare any conditions" in str(excinfo.value)

    def test_raises_when_conditions_is_not_a_list(self) -> None:
        graph = _graph(nodes=[{"id": "gate", "kind": "wait", "conditions": "delivery.observed"}])

        with pytest.raises(WorkflowExecutionError) as excinfo:
            wait_node_conditions(graph, "gate")

        assert "Wait node 'gate' does not declare any conditions" in str(excinfo.value)
