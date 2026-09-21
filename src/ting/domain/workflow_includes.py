"""Resolve ``kind: include`` nodes into the parent graph that names them.

An ``include`` node names another pinned workflow declared in this document's
own ``workflow_dependencies`` and a mapping of the included graph's node ids
to the local ids they take in this graph. Resolution copies each named node,
and every edge of the included graph whose source *and* target are both
named, into the parent graph under their local ids, then removes the include
node itself. Everything about a copied node other than its id, and the
presentational ``position``/``label`` an include may override, is taken
verbatim from the included document — that document remains the single
source of truth for the stage or gate it defines.

This is a shared-definition mechanism, not dispatch: the copied nodes run
inline in whatever session runs the parent graph, exactly like a hand-written
stage. A ``subworkflow`` node remains the only way to run a child workflow as
a separate session.

Resolution itself needs no I/O beyond the caller-supplied ``resolve_alias``
callback, which returns the exact pinned document a ``workflow_dependencies``
alias names. Callers that already hold a validated pinned-dependency closure
(a bundled workflow's own transitive definitions, a launch snapshot's frozen
``workflow_definitions``, or an authoring save that already resolved its
pins) build that resolver with ``include_resolver_from_workflow_definitions``
instead of making a second catalog lookup.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Any

import yaml

from ting.domain.exceptions import WorkflowDocumentError
from ting.domain.models import PersonaDependency, WorkflowDependency
from ting.domain.workflow_document import (
    WorkflowDocument,
    graph_has_include_nodes,
    load_workflow_document,
    referenced_persona_aliases,
)

__all__ = [
    "WorkflowIncludeResolver",
    "graph_has_include_nodes",
    "include_resolver_from_workflow_definitions",
    "resolve_workflow_includes",
]

_INCLUDABLE_KINDS = frozenset({"stage", "gate"})
_OVERRIDE_KEYS = frozenset({"position", "label"})

WorkflowIncludeResolver = Callable[[str], WorkflowDocument]


def include_resolver_from_workflow_definitions(
    workflow_definitions: Mapping[str, dict[str, Any]],
) -> WorkflowIncludeResolver:
    """Build a resolver from an already-validated pinned workflow closure.

    ``workflow_definitions`` is the aggregate this codebase already produces
    whenever it pins a ``workflow_dependencies`` alias — a mapping to
    ``{"document": <portable payload>, "persona_definitions": ..., "workflow_definitions": ...}``.
    Reusing it here means an include never triggers a second catalog lookup:
    whatever already proved the alias's pinned content supplies the included
    document too.
    """

    def resolve_alias(alias: str) -> WorkflowDocument:
        aggregate = workflow_definitions.get(alias)
        document = aggregate.get("document") if isinstance(aggregate, dict) else None
        if not isinstance(document, dict):
            raise WorkflowDocumentError(
                f"Workflow dependency {alias!r} has no resolved definition to include from"
            )
        return load_workflow_document(yaml.safe_dump(document, sort_keys=False, allow_unicode=True))

    return resolve_alias


def resolve_workflow_includes(
    graph: dict[str, Any],
    *,
    persona_dependencies: Mapping[str, PersonaDependency],
    workflow_dependencies: Mapping[str, WorkflowDependency],
    resolve_alias: WorkflowIncludeResolver,
) -> dict[str, Any]:
    """Return a graph with every ``include`` node expanded away.

    Every other node and edge, including a parent edge that names an
    include's local id as its own source or target, is copied through
    unchanged: those already name the id the copied node takes. Raises
    rather than dropping an include that cannot be resolved — an
    unresolvable include is a fatal document, never a silently smaller graph.
    """
    nodes = list(graph.get("nodes") or [])

    def _is_include(node: Any) -> bool:
        return isinstance(node, dict) and node.get("kind") == "include"

    include_nodes = [node for node in nodes if _is_include(node)]
    if not include_nodes:
        return copy.deepcopy(graph)

    kept_nodes = [node for node in nodes if not _is_include(node)]
    claimed_local_ids = {str(node.get("id") or "") for node in kept_nodes if isinstance(node, dict)}
    resolved_nodes: list[dict[str, Any]] = copy.deepcopy(kept_nodes)
    resolved_edges: list[dict[str, Any]] = copy.deepcopy(list(graph.get("edges") or []))

    for include_node in include_nodes:
        new_nodes, new_edges = _resolve_one_include(
            include_node,
            persona_dependencies=persona_dependencies,
            workflow_dependencies=workflow_dependencies,
            resolve_alias=resolve_alias,
            claimed_local_ids=claimed_local_ids,
        )
        resolved_nodes.extend(new_nodes)
        resolved_edges.extend(new_edges)

    return {**copy.deepcopy(graph), "nodes": resolved_nodes, "edges": resolved_edges}


def _resolve_one_include(
    include_node: dict[str, Any],
    *,
    persona_dependencies: Mapping[str, PersonaDependency],
    workflow_dependencies: Mapping[str, WorkflowDependency],
    resolve_alias: WorkflowIncludeResolver,
    claimed_local_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    include_id = str(include_node.get("id") or "").strip()
    alias = str(include_node.get("workflow") or "").strip()
    if alias not in workflow_dependencies:
        raise WorkflowDocumentError(
            f"Include node {include_id!r} references undeclared workflow dependency {alias!r}"
        )
    node_map = include_node.get("nodes")
    if not isinstance(node_map, dict) or not node_map:
        raise WorkflowDocumentError(
            f"Include node {include_id!r} nodes must be a non-empty mapping of source node id "
            "to local node id"
        )

    document = resolve_alias(alias)
    included_nodes = {
        str(node.get("id") or ""): node
        for node in document.graph.get("nodes") or []
        if isinstance(node, dict)
    }

    local_ids: dict[str, str] = {}
    for source_id, local_id in node_map.items():
        source_id = str(source_id).strip()
        local_id = str(local_id).strip()
        if local_id in claimed_local_ids:
            raise WorkflowDocumentError(
                f"Include node {include_id!r} local id {local_id!r} collides with another node id"
            )
        claimed_local_ids.add(local_id)
        local_ids[source_id] = local_id

    resolved_nodes: list[dict[str, Any]] = []
    include_position = include_node.get("position")
    anchor = _position_anchor(included_nodes, local_ids)
    overrides = include_node.get("overrides") or {}
    for source_id, local_id in local_ids.items():
        source_node = included_nodes.get(source_id)
        if source_node is None:
            raise WorkflowDocumentError(
                f"Include node {include_id!r} references unknown node {source_id!r} in "
                f"workflow dependency {alias!r}"
            )
        if source_node.get("kind") not in _INCLUDABLE_KINDS:
            raise WorkflowDocumentError(
                f"Include node {include_id!r} cannot include {source_id!r}: only stage and gate "
                f"nodes may be included, got kind {source_node.get('kind')!r}"
            )
        resolved_nodes.append(
            _copy_included_node(
                source_node,
                local_id=local_id,
                override=overrides.get(local_id) if isinstance(overrides, dict) else None,
                include_position=include_position,
                anchor=anchor,
            )
        )

    _validate_include_personas(
        included_document=document,
        included_node_ids=set(local_ids),
        parent_persona_dependencies=persona_dependencies,
        include_id=include_id,
        alias=alias,
    )

    resolved_edges: list[dict[str, Any]] = []
    for edge in document.graph.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source not in local_ids or target not in local_ids:
            continue
        resolved_edges.append(
            {
                **copy.deepcopy(edge),
                "id": f"{include_id}:{edge.get('id')}",
                "source": local_ids[source],
                "target": local_ids[target],
            }
        )

    return resolved_nodes, resolved_edges


def _position_anchor(
    included_nodes: Mapping[str, dict[str, Any]],
    local_ids: Mapping[str, str],
) -> tuple[float, float] | None:
    positions = [
        node["position"]
        for source_id, node in included_nodes.items()
        if source_id in local_ids and isinstance(node.get("position"), dict)
    ]
    positions = [
        position
        for position in positions
        if isinstance(position.get("x"), (int, float))
        and isinstance(position.get("y"), (int, float))
    ]
    if not positions:
        return None
    return (
        min(position["x"] for position in positions),
        min(position["y"] for position in positions),
    )


def _copy_included_node(
    source_node: dict[str, Any],
    *,
    local_id: str,
    override: Any,
    include_position: Any,
    anchor: tuple[float, float] | None,
) -> dict[str, Any]:
    if override is not None and (not isinstance(override, dict) or set(override) - _OVERRIDE_KEYS):
        raise WorkflowDocumentError(
            f"Include node override for {local_id!r} must be a mapping with only "
            "'position' and/or 'label'"
        )
    copied = copy.deepcopy(source_node)
    copied["id"] = local_id
    source_position = source_node.get("position")
    if (
        anchor is not None
        and isinstance(include_position, dict)
        and isinstance(source_position, dict)
        and isinstance(source_position.get("x"), (int, float))
        and isinstance(source_position.get("y"), (int, float))
    ):
        copied["position"] = {
            "x": include_position.get("x", 0) + (source_position["x"] - anchor[0]),
            "y": include_position.get("y", 0) + (source_position["y"] - anchor[1]),
        }
    elif "position" in copied and not isinstance(include_position, dict):
        copied.pop("position", None)
    if override:
        if "position" in override:
            copied["position"] = override["position"]
        if "label" in override:
            copied["label"] = override["label"]
    return copied


def _validate_include_personas(
    *,
    included_document: WorkflowDocument,
    included_node_ids: set[str],
    parent_persona_dependencies: Mapping[str, PersonaDependency],
    include_id: str,
    alias: str,
) -> None:
    included_subset = {
        "nodes": [
            node
            for node in included_document.graph.get("nodes") or []
            if isinstance(node, dict) and str(node.get("id") or "") in included_node_ids
        ],
        "edges": [],
    }
    for persona_alias in sorted(referenced_persona_aliases(included_subset)):
        included_pin = included_document.persona_dependencies.get(persona_alias)
        parent_pin = parent_persona_dependencies.get(persona_alias)
        if (
            included_pin is None
            or parent_pin is None
            or parent_pin.revision != included_pin.revision
            or parent_pin.digest != included_pin.digest
        ):
            raise WorkflowDocumentError(
                f"Include node {include_id!r} requires persona alias {persona_alias!r} to be "
                f"declared in persona_dependencies with the same pin as workflow dependency "
                f"{alias!r}"
            )
