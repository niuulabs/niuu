"""Compile visual workflow DAGs into Tyr pipeline YAML definitions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

from tyr.domain.models import WorkflowDefinition

_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class WorkflowCompileResult:
    definition_yaml: str | None
    errors: list[str]


def compile_workflow_definition(workflow: WorkflowDefinition) -> WorkflowCompileResult:
    """Compile a stored workflow definition into Tyr pipeline YAML."""
    return compile_workflow_graph(workflow.name, workflow.graph)


def compile_workflow_graph(name: str, graph: dict[str, Any]) -> WorkflowCompileResult:
    """Compile a workflow DAG into a linear Tyr pipeline definition.

    The current Tyr runtime supports a linear sequence of stages, with each
    stage executing participants sequentially or in parallel. Conditional graph
    branching and multi-edge fan-out/fan-in are persisted in the workflow graph
    but are not yet executable here.
    """

    nodes = list(graph.get("nodes") or [])
    edges = list(graph.get("edges") or [])
    errors: list[str] = []

    if not nodes:
        return WorkflowCompileResult(
            definition_yaml=None,
            errors=["Workflow graph has no nodes to compile."],
        )

    node_map = {node.get("id"): node for node in nodes if node.get("id")}
    outgoing: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in node_map}
    incoming: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in node_map}

    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        if source not in node_map or target not in node_map:
            errors.append(
                f"Edge '{edge.get('id', '<unknown>')}' references a missing node.",
            )
            continue
        outgoing[source].append(edge)
        incoming[target].append(edge)

    triggers = [node for node in nodes if node.get("kind") == "trigger"]
    if len(triggers) != 1:
        errors.append("Exactly one trigger node is required for executable workflows.")

    cond_nodes = [node for node in nodes if node.get("kind") == "cond"]
    if cond_nodes:
        labels = ", ".join(_node_label(node) for node in cond_nodes)
        errors.append(
            f"Conditional nodes are not yet executable in Tyr runtime: {labels}.",
        )

    if errors:
        return WorkflowCompileResult(definition_yaml=None, errors=errors)

    trigger = triggers[0]
    runtime_nodes, traversal_errors = _linear_runtime_nodes(
        trigger=trigger,
        node_map=node_map,
        incoming=incoming,
        outgoing=outgoing,
    )
    errors.extend(traversal_errors)

    if errors:
        return WorkflowCompileResult(definition_yaml=None, errors=errors)

    used_names: set[str] = set()
    stages: list[dict[str, Any]] = []

    for node in runtime_nodes:
        if node.get("kind") == "stage":
            stage, stage_errors = _compile_stage(node, workflow_name=name, used_names=used_names)
            errors.extend(stage_errors)
            if stage is not None:
                stages.append(stage)
            continue

        if node.get("kind") == "gate":
            stages.append(
                {
                    "name": _unique_stage_name(_node_label(node), used_names),
                    "gate": "human",
                }
            )
            continue

        errors.append(f"Unsupported runtime node kind: {node.get('kind')!r}.")

    if errors:
        return WorkflowCompileResult(definition_yaml=None, errors=errors)

    pipeline = {
        "name": name,
        "feature_branch": "{event.feature_branch}",
        "base_branch": "{event.base_branch}",
        "repos": ["{event.repo}"],
        "stages": stages,
    }

    return WorkflowCompileResult(
        definition_yaml=yaml.safe_dump(
            pipeline,
            sort_keys=False,
            default_flow_style=False,
        ).strip(),
        errors=[],
    )


def _linear_runtime_nodes(
    *,
    trigger: dict[str, Any],
    node_map: dict[str, dict[str, Any]],
    incoming: dict[str, list[dict[str, Any]]],
    outgoing: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    runtime_nodes: list[dict[str, Any]] = []
    visited: set[str] = {str(trigger["id"])}
    current = trigger

    while True:
        next_edges = outgoing.get(str(current["id"]), [])
        if not next_edges:
            break

        if len(next_edges) > 1:
            errors.append(
                f"Node '{_node_label(current)}' fans out to multiple paths, which Tyr "
                "cannot execute yet.",
            )
            break

        next_node = node_map[str(next_edges[0]["target"])]
        next_id = str(next_node["id"])
        if next_id in visited:
            errors.append(f"Workflow graph contains a cycle at node '{_node_label(next_node)}'.")
            break

        visited.add(next_id)

        if next_node.get("kind") == "end":
            break

        if next_node.get("kind") not in {"stage", "gate"}:
            errors.append(
                f"Node '{_node_label(next_node)}' is not executable in Tyr runtime.",
            )
            break

        if len(incoming.get(next_id, [])) > 1:
            errors.append(
                f"Node '{_node_label(next_node)}' has multiple inbound edges, which Tyr "
                "cannot execute yet.",
            )
            break

        runtime_nodes.append(next_node)
        current = next_node

    expected_runtime_ids = {
        str(node["id"])
        for node in node_map.values()
        if node.get("kind") in {"stage", "gate"}
    }
    visited_runtime_ids = {str(node["id"]) for node in runtime_nodes}
    missing = expected_runtime_ids - visited_runtime_ids
    if missing:
        errors.append(
            "Workflow graph contains disconnected or unsupported runtime nodes: "
            + ", ".join(sorted(_node_label(node_map[node_id]) for node_id in missing)),
        )

    return runtime_nodes, errors


def _compile_stage(
    node: dict[str, Any],
    *,
    workflow_name: str,
    used_names: set[str],
) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    label = _node_label(node)
    stage_name = _unique_stage_name(label, used_names)
    participants = _stage_members(node)
    if not participants:
        errors.append(f"Stage '{label}' has no assigned personas.")
        return None, errors

    compiled_participants: list[dict[str, Any]] = []
    for participant in participants:
        persona_id = str(participant.get("personaId") or "").strip()
        if not persona_id:
            errors.append(f"Stage '{label}' contains a member without personaId.")
            continue

        budget = int(participant.get("budget") or 40)
        compiled = {
            "name": f"{persona_id} in {label}",
            "persona": persona_id,
            "prompt": _default_prompt(
                workflow_name=workflow_name,
                stage_label=label,
                persona_id=persona_id,
            ),
        }
        if budget > 0:
            compiled["persona_overrides"] = {"iteration_budget": budget}
        compiled_participants.append(compiled)

    if errors:
        return None, errors

    execution_mode = str(node.get("executionMode") or "parallel")
    participant_key = "parallel" if execution_mode == "parallel" else "sequential"

    stage: dict[str, Any] = {
        "name": stage_name,
        participant_key: compiled_participants,
    }

    fan_in = _fan_in_for_join_mode(str(node.get("joinMode") or "all"))
    if fan_in != "merge":
        stage["fan_in"] = fan_in

    return stage, errors




def _stage_members(node: dict[str, Any]) -> list[dict[str, Any]]:
    members = list(node.get("stageMembers") or [])
    if members:
        return members

    persona_ids = list(node.get("personaIds") or [])
    return [{"personaId": persona_id, "budget": 40} for persona_id in persona_ids]


def _fan_in_for_join_mode(join_mode: str) -> str:
    match join_mode:
        case "all":
            return "all_must_pass"
        case "any":
            return "any_pass"
        case "merge":
            return "merge"
    return "merge"


def _default_prompt(
    *,
    workflow_name: str,
    stage_label: str,
    persona_id: str,
) -> str:
    return (
        f"Execute workflow '{workflow_name}' stage '{stage_label}' as persona "
        f"'{persona_id}'. Work within the stage objective, collaborate with other "
        "participants in this stage when applicable, and produce a concise structured "
        "outcome for downstream workflow steps."
    )


def _unique_stage_name(label: str, used_names: set[str]) -> str:
    base = _slugify(label)
    if base not in used_names:
        used_names.add(base)
        return base

    suffix = 2
    while f"{base}-{suffix}" in used_names:
        suffix += 1

    name = f"{base}-{suffix}"
    used_names.add(name)
    return name


def _slugify(value: str) -> str:
    lowered = value.lower().strip()
    slug = _SLUG_RE.sub("-", lowered).strip("-")
    if slug:
        return slug
    return "stage"


def _node_label(node: dict[str, Any]) -> str:
    return str(node.get("label") or node.get("id") or "<unknown>")
