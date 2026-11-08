"""Builders for small hand-written workflow graphs in tests."""

from __future__ import annotations

from typing import Any


def persona(produces: dict[str, str] | None, consumes: list[str]) -> dict[str, Any]:
    document: dict[str, Any] = {"consumes": {"event_types": consumes}}
    if produces is not None:
        document["produces"] = {"event_type_map": produces}
    return document


def trigger(event: str, node_id: str = "go") -> dict[str, Any]:
    return {"id": node_id, "kind": "trigger", "label": node_id, "dispatchEvent": event}


def end(node_id: str = "done", **extra: Any) -> dict[str, Any]:
    return {"id": node_id, "kind": "end", "label": node_id, **extra}


def stage(
    node_id: str,
    members: list[dict[str, Any]],
    *,
    execution: str = "sequential",
    join: str = "any",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "kind": "stage",
        "label": node_id,
        "executionMode": execution,
        "joinMode": join,
        "stageMembers": members,
        **extra,
    }


def edge(
    edge_id: str,
    source: str,
    target: str,
    source_event: str,
    target_event: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": edge_id,
        "source": source,
        "target": target,
        "label": f"{source_event} -> {target_event or source_event}",
        **extra,
    }
