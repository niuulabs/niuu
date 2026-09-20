"""Pure projections for public workflow-execution topology and outcomes."""

from __future__ import annotations

from typing import Any, Protocol


class PublicLogEntry(Protocol):
    """Structural view of a public session-log record used by the projector."""

    session_id: str
    seq: int
    payload: dict
    ts: Any


def public_trace_graph(graph: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Project a frozen workflow graph to fields safe for the execution UI."""
    nodes: list[dict[str, Any]] = []
    for raw_node in graph.get("nodes") or []:
        if not isinstance(raw_node, dict):
            continue
        node_id = str(raw_node.get("id") or "").strip()
        if not node_id:
            continue
        node = {
            "id": node_id,
            "label": str(raw_node.get("label") or node_id),
            "kind": str(raw_node.get("kind") or ""),
        }
        for key in (
            "description",
            "executionMode",
            "joinMode",
            "expansionRole",
            "workflowDependency",
            "source",
        ):
            value = raw_node.get(key)
            if isinstance(value, str) and value:
                node[key] = value
        position = raw_node.get("position")
        if isinstance(position, dict):
            public_position = {
                axis: value
                for axis in ("x", "y")
                if isinstance((value := position.get(axis)), (int, float))
            }
            if len(public_position) == 2:
                node["position"] = public_position
        members = []
        for raw_member in raw_node.get("stageMembers") or []:
            if not isinstance(raw_member, dict):
                continue
            persona_id = str(raw_member.get("personaId") or "").strip()
            if not persona_id:
                continue
            member: dict[str, Any] = {"personaId": persona_id}
            model = str(raw_member.get("model") or "").strip()
            if model:
                member["model"] = model
            consumes = raw_member.get("consumesEventTypes")
            if isinstance(consumes, list):
                member["consumesEventTypes"] = [
                    str(item) for item in consumes if isinstance(item, str) and item
                ]
            members.append(member)
        if members:
            node["stageMembers"] = members
        nodes.append(node)

    edges: list[dict[str, Any]] = []
    for raw_edge in graph.get("edges") or []:
        if not isinstance(raw_edge, dict):
            continue
        edge_id = str(raw_edge.get("id") or "").strip()
        source = str(raw_edge.get("source") or "").strip()
        target = str(raw_edge.get("target") or "").strip()
        if not edge_id or not source or not target:
            continue
        edge = {"id": edge_id, "source": source, "target": target}
        label = raw_edge.get("label")
        if isinstance(label, str) and label:
            edge["label"] = label
        edges.append(edge)
    return {"nodes": nodes, "edges": edges}


def workflow_event_sources(graph: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    """Return frozen graph node ownership for emitted event types."""
    sources: dict[str, list[str]] = {}
    for raw_edge in graph.get("edges") or []:
        if not isinstance(raw_edge, dict):
            continue
        node_id = str(raw_edge.get("source") or "").strip()
        label = str(raw_edge.get("label") or "")
        event_type = label.split("->", maxsplit=1)[0].strip()
        if event_type and node_id and node_id not in sources.setdefault(event_type, []):
            sources[event_type].append(node_id)
    for raw_node in graph.get("nodes") or []:
        if not isinstance(raw_node, dict):
            continue
        node_id = str(raw_node.get("id") or "").strip()
        if not node_id:
            continue
        for key in ("dispatchEvent", "completionEvent", "blockedEvent"):
            event_type = str(raw_node.get(key) or "").strip()
            if event_type and event_type not in sources:
                sources[event_type] = [node_id]
    return {event_type: tuple(node_ids) for event_type, node_ids in sources.items()}


def canonical_outcome_event_type(
    *,
    event_type: str,
    persona: Any,
    verdict: Any,
    persona_definitions: dict[str, Any],
) -> str | None:
    """Resolve a verdict alias through the execution's pinned persona source."""
    if not isinstance(persona, str) or not isinstance(verdict, str):
        return None
    portable = persona_definitions.get(persona)
    definition = portable.get("definition") if isinstance(portable, dict) else None
    produces = definition.get("produces") if isinstance(definition, dict) else None
    if not isinstance(produces, dict) or produces.get("event_type") != event_type:
        return None
    event_type_map = produces.get("event_type_map")
    if not isinstance(event_type_map, dict):
        return None
    canonical = event_type_map.get(verdict)
    return canonical if isinstance(canonical, str) and canonical else None


def project_trace_event(
    entry: PublicLogEntry,
    *,
    node_ids: set[str],
    event_sources: dict[str, tuple[str, ...]],
    persona_definitions: dict[str, Any],
) -> dict[str, Any]:
    """Project one public room outcome without inferring lifecycle completion."""
    payload = entry.payload
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    event_type = str(payload.get("eventType") or payload.get("event_type") or "").strip()
    persona = payload.get("persona") if isinstance(payload.get("persona"), str) else ""
    raw_verdict = payload.get("verdict") or fields.get("verdict")
    verdict = raw_verdict if isinstance(raw_verdict, str) else ""
    explicit: list[str] = []
    for source in (payload, fields):
        for key in (
            "workflow_trigger_node_id",
            "workflowTriggerNodeId",
            "workflow_node_id",
            "workflowNodeId",
            "node_id",
            "nodeId",
        ):
            value = source.get(key)
            if isinstance(value, str) and value in node_ids and value not in explicit:
                explicit.append(value)
    canonical_event_type = canonical_outcome_event_type(
        event_type=event_type,
        persona=persona,
        verdict=verdict,
        persona_definitions=persona_definitions,
    )
    graph_event_type = canonical_event_type or event_type
    candidates = [
        node_id for node_id in event_sources.get(graph_event_type, ()) if node_id in node_ids
    ]
    mapped = explicit or (candidates if len(candidates) == 1 else [])
    if explicit:
        mapping = "explicit"
    elif canonical_event_type is not None:
        mapping = "pinned_persona_outcome"
    elif mapped:
        mapping = "workflow_graph_event"
    else:
        mapping = "unmapped"
    summary = payload.get("summary")
    if not isinstance(summary, str):
        summary = fields.get("summary") if isinstance(fields.get("summary"), str) else ""
    event = {
        "eventId": f"{entry.session_id}:{entry.seq}",
        "sessionId": entry.session_id,
        "seq": entry.seq,
        "occurredAt": entry.ts,
        "eventType": event_type,
        "canonicalEventType": graph_event_type,
        "persona": persona,
        "summary": summary,
        "verdict": verdict,
        "valid": bool(payload.get("valid", True)),
        "fields": fields,
        "nodeIds": mapped,
        "candidateNodeIds": candidates if len(candidates) > 1 else [],
        "mapping": mapping,
    }
    for source, target in (("taskId", "taskId"), ("sourceEventId", "sourceEventId")):
        value = payload.get(source)
        if isinstance(value, str) and value:
            event[target] = value
    return event
