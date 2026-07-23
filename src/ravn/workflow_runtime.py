"""Workflow graph interpretation for Ravn runtime composition."""

from __future__ import annotations

from typing import Any

from ravn.config import Settings


def _split_workflow_edge_label(label: Any) -> tuple[str, str]:
    if not isinstance(label, str):
        return "", ""
    parts = [part.strip() for part in label.split("->", 1)]
    if len(parts) != 2:
        return "", ""
    return parts[0], parts[1]


def _stage_personas(node: dict[str, Any]) -> set[str]:
    personas = {
        str(persona)
        for persona in (node.get("personaIds") or [])
        if isinstance(persona, str) and persona
    }
    for member in node.get("stageMembers") or []:
        if not isinstance(member, dict):
            continue
        persona_id = member.get("personaId")
        if isinstance(persona_id, str) and persona_id:
            personas.add(persona_id)
    return personas


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _join_mode_to_fan_in(join_mode: str) -> str:
    if join_mode == "all":
        return "all_must_pass"
    if join_mode == "any":
        return "any_pass"
    return "merge"


def _workflow_graph(settings: Settings) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    workflow_cfg = getattr(settings, "workflow", None)
    graph = getattr(workflow_cfg, "graph", None)
    if not isinstance(graph, dict):
        return [], []
    nodes = [node for node in graph.get("nodes", []) if isinstance(node, dict)]
    edges = [edge for edge in graph.get("edges", []) if isinstance(edge, dict)]
    return nodes, edges


def _normalize_workflow_event_filters(member: dict[str, Any]) -> dict[str, str]:
    raw_filters = member.get("eventFilters")
    if not isinstance(raw_filters, dict):
        raw_filters = member.get("event_filters")
    if not isinstance(raw_filters, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in raw_filters.items():
        key_text = str(key).strip()
        value_text = str(value).strip()
        if key_text and value_text:
            normalized[key_text] = value_text
    return normalized


def _workflow_event_matches_filters(
    payload: dict[str, Any],
    filters: dict[str, str],
) -> bool:
    if not filters:
        return True

    nested_fields = payload.get("fields")
    if not isinstance(nested_fields, dict):
        nested_fields = {}
    nested_outcome = payload.get("outcome")
    if not isinstance(nested_outcome, dict):
        nested_outcome = {}

    for key, expected in filters.items():
        actual: Any = None
        if key in payload:
            actual = payload.get(key)
        elif key in nested_fields:
            actual = nested_fields.get(key)
        elif key in nested_outcome:
            actual = nested_outcome.get(key)

        if isinstance(actual, list):
            if expected not in {str(item).strip() for item in actual if str(item).strip()}:
                return False
            continue

        if str(actual or "").strip() != expected:
            return False

    return True


def _workflow_runtime_for_persona(settings: Settings, persona_name: str) -> dict[str, Any] | None:
    nodes, edges = _workflow_graph(settings)
    if not nodes:
        return None

    matching_nodes: list[tuple[dict[str, Any], list[str], dict[str, str]]] = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("kind") != "stage":
            continue
        stage_members = list(node.get("stageMembers") or [])
        for member in stage_members:
            if not isinstance(member, dict) or member.get("personaId") != persona_name:
                continue
            member_override_events: list[str] = []
            consumes_event_types = member.get("consumesEventTypes")
            if not isinstance(consumes_event_types, list):
                consumes_event_types = member.get("consumes_event_types")
            if isinstance(consumes_event_types, list):
                member_override_events.extend(
                    str(event_type).strip()
                    for event_type in consumes_event_types
                    if str(event_type).strip()
                )
            matching_nodes.append(
                (
                    node,
                    member_override_events,
                    _normalize_workflow_event_filters(member),
                )
            )
            break
        else:
            persona_ids = list(node.get("personaIds") or [])
            if persona_name in persona_ids:
                matching_nodes.append((node, [], {}))

    if not matching_nodes:
        return None

    consumer_groups: list[dict[str, Any]] = []
    aggregated_event_types: list[str] = []
    for index, (node, member_override_events, member_event_filters) in enumerate(matching_nodes):
        node_id = str(node.get("id") or f"{persona_name}-stage-{index}")
        group_event_types: list[str] = []
        for edge in edges:
            if str(edge.get("target")) != node_id:
                continue
            _, target_event = _split_workflow_edge_label(edge.get("label"))
            if target_event:
                group_event_types.append(target_event)

        resolved_event_types = _dedupe_preserve_order(member_override_events or group_event_types)
        if not resolved_event_types:
            continue

        fan_in_strategy = "merge"
        if len(resolved_event_types) > 1:
            fan_in_strategy = _join_mode_to_fan_in(str(node.get("joinMode") or "all"))

        consumer_groups.append(
            {
                "id": node_id,
                "label": str(node.get("label") or node_id),
                "event_types": resolved_event_types,
                "fan_in_strategy": fan_in_strategy,
                **({"event_filters": member_event_filters} if member_event_filters else {}),
            }
        )
        aggregated_event_types.extend(resolved_event_types)

    if not consumer_groups:
        return None

    default_strategy = (
        str(consumer_groups[0]["fan_in_strategy"]) if len(consumer_groups) == 1 else "merge"
    )

    return {
        "event_types": _dedupe_preserve_order(aggregated_event_types),
        "fan_in_strategy": default_strategy,
        "consumer_groups": consumer_groups,
    }


def _workflow_allowed_task_targets(
    settings: Settings,
    persona_name: str,
    *,
    node_id: str | None = None,
) -> set[str] | None:
    nodes, edges = _workflow_graph(settings)
    if not nodes or not edges:
        return None

    if node_id:
        matching_node_ids = {node_id}
    else:
        matching_node_ids = {
            str(node.get("id"))
            for node in nodes
            if node.get("kind") == "stage" and persona_name in _stage_personas(node)
        }
    if not matching_node_ids:
        return None

    allowed_personas: set[str] = set()
    for edge in edges:
        if str(edge.get("source")) not in matching_node_ids:
            continue
        target_id = str(edge.get("target"))
        if not target_id:
            continue
        for node in nodes:
            if str(node.get("id")) != target_id:
                continue
            if node.get("kind") != "stage":
                continue
            allowed_personas.update(_stage_personas(node))
            break

    return allowed_personas if allowed_personas else None


def _workflow_allowed_outcome_topics(
    settings: Settings,
    *,
    node_id: str | None,
) -> set[str] | None:
    if not node_id:
        return None
    nodes, edges = _workflow_graph(settings)
    if not nodes or not edges:
        return None
    topics = {
        source_event
        for edge in edges
        if str(edge.get("source")) == node_id
        for source_event, _target_event in [_split_workflow_edge_label(edge.get("label"))]
        if source_event
    }
    return topics if topics else None


def _workflow_stage_context(
    settings: Settings,
    *,
    node_id: str,
) -> str:
    """Render configured guidance for one workflow stage."""
    if not node_id:
        return ""
    nodes, _edges = _workflow_graph(settings)
    for node in nodes:
        if str(node.get("id") or "") != node_id or node.get("kind") != "stage":
            continue
        label = str(node.get("label") or "").strip()
        description = str(node.get("description") or "").strip()
        lines = [f"Workflow stage: {label or node_id}"]
        if description:
            lines.extend(["Stage instructions:", description])
        return "\n".join(lines)
    return ""
