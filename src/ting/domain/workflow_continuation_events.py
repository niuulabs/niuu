"""Continuation event types read from a pinned workflow graph.

A subworkflow node names the events the engine publishes to the parent
session when its children join or block; a wait node names the event
published once its own condition is observed. Both live on the graph because
they belong to whichever workflow declares them, never as Python literals in
the engine that merely delivers them.

``ting.domain.workflow_document`` rejects a graph missing this information at
load time, so the lookups here assume a validated graph and fail loudly
(rather than guess) if that assumption is somehow violated.
"""

from __future__ import annotations

from typing import Any

from ting.domain.workflow_execution import WorkflowExecutionError


def subworkflow_joined_event(graph: dict[str, Any], node_id: str) -> str:
    """Return the event published to the parent once this node's children join."""
    _subworkflow_node(graph, node_id)
    events = _outgoing_events(graph, node_id)
    if len(events) != 1:
        raise WorkflowExecutionError(
            f"Subworkflow node {node_id!r} has no single outgoing joined event"
        )
    return next(iter(events))


def subworkflow_blocked_event(graph: dict[str, Any], node_id: str) -> str:
    """Return the event published to the parent when this node's children block."""
    node = _subworkflow_node(graph, node_id)
    event_type = str(node.get("blockedEvent") or "").strip()
    if not event_type:
        raise WorkflowExecutionError(f"Subworkflow node {node_id!r} does not declare blockedEvent")
    return event_type


def wait_observed_event(graph: dict[str, Any], node_id: str) -> str:
    """Return the event published once this exact wait node's condition is observed."""
    node = next(
        (
            candidate
            for candidate in graph.get("nodes", [])
            if isinstance(candidate, dict) and str(candidate.get("id") or "") == node_id
        ),
        None,
    )
    if node is None or node.get("kind") != "wait":
        raise WorkflowExecutionError(f"Workflow graph has no wait node {node_id!r}")
    events = _outgoing_events(graph, node_id)
    if len(events) != 1:
        raise WorkflowExecutionError(f"Wait node {node_id!r} has no single outgoing observed event")
    return next(iter(events))


def wait_node_conditions(graph: dict[str, Any], node_id: str) -> frozenset[str]:
    """Return the condition types this exact wait node accepts."""
    node = next(
        (
            candidate
            for candidate in graph.get("nodes", [])
            if isinstance(candidate, dict) and str(candidate.get("id") or "") == node_id
        ),
        None,
    )
    if node is None or node.get("kind") != "wait":
        raise WorkflowExecutionError(f"Workflow graph has no wait node {node_id!r}")
    conditions = node.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        raise WorkflowExecutionError(f"Wait node {node_id!r} does not declare any conditions")
    return frozenset(str(item) for item in conditions)


def _subworkflow_node(graph: dict[str, Any], node_id: str) -> dict[str, Any]:
    node = next(
        (
            candidate
            for candidate in graph.get("nodes", [])
            if isinstance(candidate, dict) and str(candidate.get("id") or "") == node_id
        ),
        None,
    )
    if node is None or node.get("kind") != "subworkflow":
        raise WorkflowExecutionError(f"Workflow graph has no subworkflow node {node_id!r}")
    return node


def _outgoing_events(graph: dict[str, Any], node_id: str) -> set[str]:
    events = {
        _source_event(edge)
        for edge in graph.get("edges", [])
        if isinstance(edge, dict) and str(edge.get("source") or "") == node_id
    }
    events.discard("")
    return events


def _source_event(edge: dict[str, Any]) -> str:
    label = edge.get("label")
    if not isinstance(label, str) or "->" not in label:
        return ""
    return label.split("->", 1)[0].strip()
