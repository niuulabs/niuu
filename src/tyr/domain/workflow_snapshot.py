"""Helpers for snapshotting compiled workflows onto sagas and dispatches."""

from __future__ import annotations

import re
from typing import Any

from tyr.domain.models import WorkflowDefinition


def build_workflow_snapshot(workflow: WorkflowDefinition) -> dict[str, Any]:
    """Build a serializable workflow snapshot for saga assignment and dispatch."""
    graph_snapshot = {"graph": workflow.graph}
    personas = workflow_personas_from_snapshot(graph_snapshot)
    resource_nodes = workflow_resource_nodes_from_snapshot(graph_snapshot)
    resource_bindings = workflow_resource_bindings_from_snapshot(graph_snapshot)
    mimir = workflow_mimir_from_snapshot(graph_snapshot)
    return {
        "workflow_id": str(workflow.id),
        "name": workflow.name,
        "version": workflow.version,
        "scope": workflow.scope.value,
        "definition_yaml": workflow.definition_yaml,
        "graph": workflow.graph,
        "personas": personas,
        "resource_nodes": resource_nodes,
        "resource_bindings": resource_bindings,
        "mimir": mimir,
    }


def workflow_name_from_snapshot(snapshot: dict[str, Any] | None) -> str | None:
    if not snapshot:
        return None

    name = snapshot.get("name")
    if isinstance(name, str) and name:
        return name
    return None


def workflow_personas_from_snapshot(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Extract an ordered unique persona list from a workflow snapshot graph."""
    if not snapshot:
        return []

    personas = snapshot.get("personas")
    if isinstance(personas, list):
        normalized = [persona for persona in personas if isinstance(persona, dict)]
        if normalized:
            return normalized

    graph = snapshot.get("graph")
    if not isinstance(graph, dict):
        return []

    nodes = list(graph.get("nodes") or [])
    seen: set[str] = set()
    personas: list[dict[str, Any]] = []

    for node in nodes:
        if not isinstance(node, dict) or node.get("kind") != "stage":
            continue

        stage_members = list(node.get("stageMembers") or [])
        if stage_members:
            for member in stage_members:
                persona = _persona_from_member(member)
                if persona is None:
                    continue
                if persona["name"] in seen:
                    continue
                seen.add(persona["name"])
                personas.append(persona)
            continue

        for persona_id in list(node.get("personaIds") or []):
            if not isinstance(persona_id, str) or not persona_id or persona_id in seen:
                continue
            seen.add(persona_id)
            personas.append({"name": persona_id})

    return personas


def workflow_resource_nodes_from_snapshot(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Extract Mimir resource nodes from a workflow snapshot."""
    if not snapshot:
        return []

    resource_nodes = snapshot.get("resource_nodes")
    if isinstance(resource_nodes, list):
        normalized = [
            resource_node for resource_node in resource_nodes if isinstance(resource_node, dict)
        ]
        if normalized:
            return normalized

    graph = snapshot.get("graph")
    if not isinstance(graph, dict):
        return []

    nodes = list(graph.get("nodes") or [])
    return [
        node
        for node in nodes
        if isinstance(node, dict)
        and str(node.get("kind") or "") == "resource"
        and str(node.get("resourceType") or "") == "mimir"
    ]


def workflow_resource_bindings_from_snapshot(
    snapshot: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Extract resource bindings from a workflow snapshot."""
    if not snapshot:
        return []

    resource_bindings = snapshot.get("resource_bindings")
    if isinstance(resource_bindings, list):
        normalized = [
            binding for binding in resource_bindings if isinstance(binding, dict)
        ]
        if normalized:
            return normalized

    graph = snapshot.get("graph")
    if not isinstance(graph, dict):
        return []

    raw = graph.get("resourceBindings") or graph.get("resource_bindings") or []
    return [binding for binding in raw if isinstance(binding, dict)]


def workflow_mimir_from_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Build the richer Mimir workload payload from workflow resource data."""
    if not snapshot:
        return {}

    cached = snapshot.get("mimir")
    if isinstance(cached, dict) and cached:
        return dict(cached)

    resource_nodes = workflow_resource_nodes_from_snapshot(snapshot)
    resource_bindings = workflow_resource_bindings_from_snapshot(snapshot)
    if not resource_nodes:
        return {}

    resource_mount_names: dict[str, str] = {}
    registry_refs: list[dict[str, Any]] = []
    ephemeral_locals: list[dict[str, Any]] = []

    for node in resource_nodes:
        resource_node_id = str(node.get("id") or "").strip()
        if not resource_node_id:
            continue

        mount_name = _resource_mount_name(node)
        resource_mount_names[resource_node_id] = mount_name
        categories = _string_list(node.get("categories"))

        if str(node.get("bindingMode") or "registry") == "ephemeral_local":
            ephemeral_locals.append(
                {
                    "resource_node_id": resource_node_id,
                    "mount_name": mount_name,
                    "label": str(node.get("label") or mount_name),
                    "seed_from_registry_id": _optional_string(node.get("seedFromRegistryId")),
                    "categories": categories,
                }
            )
            continue

        ref = {
            "resource_node_id": resource_node_id,
            "registry_entry_id": _optional_string(node.get("registryEntryId")),
            "mount_name": mount_name,
            "label": str(node.get("label") or mount_name),
            "categories": categories,
        }
        if path := _optional_string(node.get("path")):
            ref["path"] = path
        if url := _optional_string(node.get("url")):
            ref["url"] = url
        if role := _optional_string(node.get("role")):
            ref["role"] = role
        if auth_ref := _optional_string(
            node.get("authRef") if "authRef" in node else node.get("auth_ref")
        ):
            ref["auth_ref"] = auth_ref
        default_read_priority = (
            node.get("defaultReadPriority")
            if "defaultReadPriority" in node
            else node.get("default_read_priority")
        )
        if isinstance(default_read_priority, int):
            ref["default_read_priority"] = default_read_priority
        registry_refs.append(ref)

    bindings: list[dict[str, Any]] = []
    for binding in resource_bindings:
        resource_node_id = str(binding.get("resourceNodeId") or "").strip()
        mount_name = resource_mount_names.get(resource_node_id)
        if not mount_name:
            continue
        bindings.append(
            {
                "resource_node_id": resource_node_id,
                "mount_name": mount_name,
                "target_type": str(binding.get("targetType") or "workflow"),
                "target_id": str(binding.get("targetId") or ""),
                "access": str(binding.get("access") or "read"),
                "write_prefixes": _string_list(binding.get("writePrefixes")),
                "read_priority": _int_value(binding.get("readPriority"), default=10),
            }
        )

    mimir: dict[str, Any] = {
        "registry_refs": registry_refs,
        "ephemeral_locals": ephemeral_locals,
        "bindings": bindings,
    }
    if registry_refs or ephemeral_locals:
        default_mount = (
            ephemeral_locals[0]["mount_name"]
            if ephemeral_locals
            else registry_refs[0]["mount_name"]
        )
        mimir["default_mounts"] = [default_mount]
    return mimir


def _persona_from_member(member: Any) -> dict[str, Any] | None:
    if not isinstance(member, dict):
        return None

    persona_id = member.get("personaId")
    if not isinstance(persona_id, str) or not persona_id:
        return None

    persona = {"name": persona_id}
    budget = member.get("budget")
    if isinstance(budget, int) and budget > 0:
        persona["iteration_budget"] = budget
    return persona


def _resource_mount_name(node: dict[str, Any]) -> str:
    registry_entry_id = _optional_string(node.get("registryEntryId"))
    if registry_entry_id:
        return registry_entry_id

    label = _optional_string(node.get("label"))
    if label:
        slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
        if slug:
            return slug

    node_id = _optional_string(node.get("id"))
    if node_id:
        return node_id
    return "mimir-resource"


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _int_value(value: Any, *, default: int) -> int:
    if isinstance(value, int):
        return value
    return default
