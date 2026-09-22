"""``kind: include`` resolves stages/gates from a pinned workflow inline."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
import yaml

from ravn.adapters.personas.loader import PersonaConfig
from ravn.domain.persona_document import portable_persona_from_config
from ting.domain.exceptions import WorkflowDocumentError
from ting.domain.models import WorkflowDefinition, WorkflowScope
from ting.domain.workflow_document import (
    load_workflow_document,
    workflow_document_payload,
    workflow_document_revision,
)
from ting.domain.workflow_includes import (
    graph_has_include_nodes,
    include_resolver_from_workflow_definitions,
    resolve_workflow_includes,
)
from ting.domain.workflow_snapshot import build_workflow_snapshot, pin_workflow_personas
from ting.system_workflows import (
    BUNDLED_SYSTEM_WORKFLOWS_PATH,
    load_bundled_workflow,
    load_system_workflows,
)

_REVIEWER_PORTABLE = portable_persona_from_config(
    PersonaConfig(name="reviewer", system_prompt_template="Review the work under test.")
)
_PERSONA_DIGEST = _REVIEWER_PORTABLE.digest


def _persona_dependency() -> dict:
    return {
        "id": _REVIEWER_PORTABLE.id,
        "revision": _REVIEWER_PORTABLE.revision,
        "digest": _REVIEWER_PORTABLE.digest,
    }


def _child_payload(*, extra_nodes: list[dict] | None = None) -> dict:
    """A tiny standalone workflow with an includable stage and gate, and one
    of every other kind, so tests can pick which node an include targets."""
    return {
        "schema_version": 2,
        "id": str(uuid4()),
        "name": "Child",
        "description": "A child workflow with one of each node kind.",
        "version": "1.0.0",
        "persona_dependencies": {"reviewer": _persona_dependency()},
        "workflow_dependencies": {},
        "graph": {
            "nodes": [
                {
                    "id": "child-trigger",
                    "kind": "trigger",
                    "label": "Start",
                    "source": "test",
                    "dispatchEvent": "child.start",
                },
                {
                    "id": "child-stage",
                    "kind": "stage",
                    "label": "Do the work",
                    "position": {"x": 100, "y": 200},
                    "executionMode": "sequential",
                    "joinMode": "any",
                    "stageMembers": [
                        {
                            "personaId": "reviewer",
                            "model": "gpt-5.5",
                            "budget": 10,
                            "consumesEventTypes": ["child.start"],
                        }
                    ],
                },
                {
                    "id": "child-other-stage",
                    "kind": "stage",
                    "label": "Do more work",
                    "position": {"x": 140, "y": 260},
                    "executionMode": "sequential",
                    "joinMode": "any",
                    "stageMembers": [
                        {
                            "personaId": "reviewer",
                            "model": "gpt-5.5",
                            "budget": 10,
                            "consumesEventTypes": ["child.more"],
                        }
                    ],
                },
                {
                    "id": "child-gate",
                    "kind": "gate",
                    "label": "Human checkpoint",
                    "condition": "Approve before finishing.",
                    "mode": "human_approval",
                    "pendingBehavior": "help_needed",
                    "approvalEvent": "child.gate.approved",
                    "changesRequestedEvent": "child.gate.changes_requested",
                    "instructions": "Approve or request changes.",
                },
                {
                    "id": "child-resource",
                    "kind": "resource",
                    "label": "Memory",
                    "resourceType": "mimir",
                    "bindingMode": "ephemeral_local",
                },
                {
                    "id": "child-end",
                    "kind": "end",
                    "label": "Done",
                    "joinMode": "any",
                    "completionEvent": "child.done",
                },
                *(extra_nodes or []),
            ],
            "edges": [
                {
                    "id": "child-e1",
                    "source": "child-trigger",
                    "target": "child-stage",
                    "label": "child.start -> child.start",
                },
                {
                    "id": "child-e2",
                    "source": "child-stage",
                    "target": "child-other-stage",
                    "label": "child.more -> child.more",
                },
                {
                    "id": "child-e3",
                    "source": "child-other-stage",
                    "target": "child-end",
                    "label": "child.done -> child.done",
                },
            ],
        },
    }


def _load_child():
    return load_workflow_document(yaml.safe_dump(_child_payload(), sort_keys=False))


def _parent_payload(
    *,
    child_document,
    include_nodes: dict,
    overrides: dict | None = None,
    include_position: dict | None = None,
    parent_persona_dependencies: dict | None = None,
    workflow_dependency_alias: str = "child",
    declare_dependency: bool = True,
    schema_version: int = 2,
) -> dict:
    revision = workflow_document_revision(child_document)
    include_node: dict = {
        "id": "parent-include",
        "kind": "include",
        "label": "Include the child",
        "workflow": workflow_dependency_alias,
        "nodes": include_nodes,
    }
    if overrides is not None:
        include_node["overrides"] = overrides
    if include_position is not None:
        include_node["position"] = include_position

    workflow_dependencies = {}
    if declare_dependency:
        workflow_dependencies[workflow_dependency_alias] = {
            "id": str(child_document.id),
            "revision": revision,
            "digest": revision,
        }

    persona_dependencies = (
        parent_persona_dependencies
        if parent_persona_dependencies is not None
        else {"reviewer": _persona_dependency()}
    )

    payload = {
        "schema_version": schema_version,
        "id": str(uuid4()),
        "name": "Parent",
        "description": "A parent that includes stages from the child.",
        "version": "1.0.0",
        "persona_dependencies": persona_dependencies,
        "graph": {
            "nodes": [
                {
                    "id": "parent-trigger",
                    "kind": "trigger",
                    "label": "Start",
                    "source": "test",
                    "dispatchEvent": "parent.start",
                },
                include_node,
            ],
            "edges": [],
        },
    }
    if schema_version >= 2:
        payload["workflow_dependencies"] = workflow_dependencies
    return payload


def _load_parent(**kwargs):
    return load_workflow_document(yaml.safe_dump(_parent_payload(**kwargs), sort_keys=False))


def _resolver_for(child_document):
    return lambda alias: child_document


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_resolution_copies_nodes_with_remapped_ids_and_brings_internal_edges():
    child = _load_child()
    parent = _load_parent(
        child_document=child,
        include_nodes={"child-stage": "local-stage", "child-other-stage": "local-other"},
    )

    resolved = resolve_workflow_includes(
        parent.graph,
        persona_dependencies=parent.persona_dependencies,
        workflow_dependencies=parent.workflow_dependencies,
        resolve_alias=_resolver_for(child),
    )

    nodes_by_id = {node["id"]: node for node in resolved["nodes"]}
    assert {"local-stage", "local-other", "parent-trigger"} <= set(nodes_by_id)
    copied = nodes_by_id["local-stage"]
    assert copied["kind"] == "stage"
    assert copied["stageMembers"][0]["personaId"] == "reviewer"
    assert copied["label"] == "Do the work"

    # The internal edge between the two included nodes is carried over with
    # its endpoints remapped to their local ids.
    edges_by_endpoints = {(edge["source"], edge["target"]): edge for edge in resolved["edges"]}
    assert ("local-stage", "local-other") in edges_by_endpoints
    internal_edge = edges_by_endpoints[("local-stage", "local-other")]
    assert internal_edge["label"] == "child.more -> child.more"
    assert internal_edge["id"] == "parent-include:child-e2"


def test_include_node_is_removed_after_resolution():
    child = _load_child()
    parent = _load_parent(child_document=child, include_nodes={"child-stage": "local-stage"})

    resolved = resolve_workflow_includes(
        parent.graph,
        persona_dependencies=parent.persona_dependencies,
        workflow_dependencies=parent.workflow_dependencies,
        resolve_alias=_resolver_for(child),
    )

    assert not graph_has_include_nodes(resolved)
    assert all(node["kind"] != "include" for node in resolved["nodes"])
    assert "parent-include" not in {node["id"] for node in resolved["nodes"]}


def test_edges_that_leave_the_included_subgraph_are_not_copied():
    """Only edges whose source *and* target are both included come along."""
    child = _load_child()
    parent = _load_parent(child_document=child, include_nodes={"child-stage": "local-stage"})

    resolved = resolve_workflow_includes(
        parent.graph,
        persona_dependencies=parent.persona_dependencies,
        workflow_dependencies=parent.workflow_dependencies,
        resolve_alias=_resolver_for(child),
    )

    # child-e1 (trigger -> child-stage) and child-e2 (child-stage -> other)
    # both have an endpoint outside {child-stage}, so neither is copied.
    assert resolved["edges"] == []


# ---------------------------------------------------------------------------
# Positions and overrides
# ---------------------------------------------------------------------------


def test_overrides_affect_only_position_and_label():
    child = _load_child()
    parent = _load_parent(
        child_document=child,
        include_nodes={"child-stage": "local-stage"},
        overrides={"local-stage": {"label": "Renamed", "position": {"x": 5, "y": 6}}},
    )

    resolved = resolve_workflow_includes(
        parent.graph,
        persona_dependencies=parent.persona_dependencies,
        workflow_dependencies=parent.workflow_dependencies,
        resolve_alias=_resolver_for(child),
    )

    copied = next(node for node in resolved["nodes"] if node["id"] == "local-stage")
    assert copied["label"] == "Renamed"
    assert copied["position"] == {"x": 5, "y": 6}
    # Everything else on the node is still taken verbatim from the source.
    assert copied["stageMembers"] == child.graph["nodes"][1]["stageMembers"]
    assert copied["executionMode"] == "sequential"


def test_default_position_preserves_relative_layout_from_the_source_graph():
    child = _load_child()
    parent = _load_parent(
        child_document=child,
        include_nodes={"child-stage": "local-a", "child-other-stage": "local-b"},
        include_position={"x": 1000, "y": 2000},
    )

    resolved = resolve_workflow_includes(
        parent.graph,
        persona_dependencies=parent.persona_dependencies,
        workflow_dependencies=parent.workflow_dependencies,
        resolve_alias=_resolver_for(child),
    )

    by_id = {node["id"]: node for node in resolved["nodes"]}
    # child-stage sits at the minimum (x, y) among included nodes, so it
    # anchors exactly onto the include node's own position ...
    assert by_id["local-a"]["position"] == {"x": 1000, "y": 2000}
    # ... and child-other-stage's +40/+60 offset in the source graph is
    # preserved relative to that anchor.
    assert by_id["local-b"]["position"] == {"x": 1040, "y": 2060}


# ---------------------------------------------------------------------------
# Structural validation (no resolver needed) — ting.domain.workflow_document
# ---------------------------------------------------------------------------


def test_include_alias_must_exist_in_workflow_dependencies():
    child = _load_child()
    with pytest.raises(WorkflowDocumentError, match="undeclared workflow dependency 'child'"):
        _load_parent(
            child_document=child,
            include_nodes={"child-stage": "local-stage"},
            declare_dependency=False,
        )


def test_include_nodes_mapping_must_be_non_empty():
    child = _load_child()
    with pytest.raises(WorkflowDocumentError, match="non-empty mapping"):
        _load_parent(child_document=child, include_nodes={})


def test_include_local_id_must_not_collide_with_existing_node_id():
    child = _load_child()
    with pytest.raises(WorkflowDocumentError, match="collides with an existing node id"):
        _load_parent(
            child_document=child,
            include_nodes={"child-stage": "parent-trigger"},
        )


def test_include_local_id_must_be_unique_within_the_include():
    child = _load_child()
    with pytest.raises(WorkflowDocumentError, match="duplicate local id 'same'"):
        _load_parent(
            child_document=child,
            include_nodes={"child-stage": "same", "child-other-stage": "same"},
        )


def test_include_requires_schema_version_2():
    child = _load_child()
    payload = _parent_payload(
        child_document=child,
        include_nodes={"child-stage": "local-stage"},
        schema_version=1,
    )
    payload.pop("workflow_dependencies", None)
    with pytest.raises(WorkflowDocumentError, match="requires workflow schema_version 2"):
        load_workflow_document(yaml.safe_dump(payload, sort_keys=False))


def test_include_overrides_reject_unknown_keys():
    child = _load_child()
    with pytest.raises(WorkflowDocumentError, match="only 'position' and/or 'label'"):
        _load_parent(
            child_document=child,
            include_nodes={"child-stage": "local-stage"},
            overrides={"local-stage": {"color": "red"}},
        )


def test_include_overrides_reject_targets_not_included():
    child = _load_child()
    with pytest.raises(WorkflowDocumentError, match="does not include: someone-else"):
        _load_parent(
            child_document=child,
            include_nodes={"child-stage": "local-stage"},
            overrides={"someone-else": {"label": "x"}},
        )


# ---------------------------------------------------------------------------
# Resolver-dependent validation — ting.domain.workflow_includes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source_id", ["child-trigger", "child-end", "child-resource"])
def test_including_a_non_stage_or_gate_kind_is_rejected(source_id):
    """Every non-{stage,gate} kind is rejected by the same membership check;
    trigger/end/resource are exercised as representative non-includable
    kinds (subworkflow and wait need much larger fixtures to be legal on
    their own and would exercise the identical branch)."""
    child = _load_child()
    parent = _load_parent(child_document=child, include_nodes={source_id: "local-x"})

    with pytest.raises(WorkflowDocumentError, match="only stage and gate nodes may be included"):
        resolve_workflow_includes(
            parent.graph,
            persona_dependencies=parent.persona_dependencies,
            workflow_dependencies=parent.workflow_dependencies,
            resolve_alias=_resolver_for(child),
        )


def test_including_a_gate_node_is_allowed():
    child = _load_child()
    parent = _load_parent(child_document=child, include_nodes={"child-gate": "local-gate"})

    resolved = resolve_workflow_includes(
        parent.graph,
        persona_dependencies=parent.persona_dependencies,
        workflow_dependencies=parent.workflow_dependencies,
        resolve_alias=_resolver_for(child),
    )

    copied = next(node for node in resolved["nodes"] if node["id"] == "local-gate")
    assert copied["kind"] == "gate"
    assert copied["approvalEvent"] == "child.gate.approved"


def test_include_source_id_must_exist_in_the_included_graph():
    child = _load_child()
    parent = _load_parent(child_document=child, include_nodes={"does-not-exist": "local-x"})

    with pytest.raises(WorkflowDocumentError, match="unknown node 'does-not-exist'"):
        resolve_workflow_includes(
            parent.graph,
            persona_dependencies=parent.persona_dependencies,
            workflow_dependencies=parent.workflow_dependencies,
            resolve_alias=_resolver_for(child),
        )


def test_include_persona_alias_must_be_pinned_identically_to_the_included_document():
    child = _load_child()
    other_digest = "sha256:" + "b" * 64
    parent = _load_parent(
        child_document=child,
        include_nodes={"child-stage": "local-stage"},
        parent_persona_dependencies={
            "reviewer": {
                "id": "reviewer",
                "revision": "content-reviewer-1",
                "digest": other_digest,
            }
        },
    )

    with pytest.raises(WorkflowDocumentError, match="persona alias 'reviewer'.*same pin"):
        resolve_workflow_includes(
            parent.graph,
            persona_dependencies=parent.persona_dependencies,
            workflow_dependencies=parent.workflow_dependencies,
            resolve_alias=_resolver_for(child),
        )


def test_include_persona_alias_must_be_declared_at_all():
    child = _load_child()
    parent = _load_parent(
        child_document=child,
        include_nodes={"child-stage": "local-stage"},
        parent_persona_dependencies={},
    )

    with pytest.raises(WorkflowDocumentError, match="persona alias 'reviewer'.*same pin"):
        resolve_workflow_includes(
            parent.graph,
            persona_dependencies=parent.persona_dependencies,
            workflow_dependencies=parent.workflow_dependencies,
            resolve_alias=_resolver_for(child),
        )


def test_unresolvable_alias_fails_loudly_rather_than_dropping_the_include():
    child = _load_child()
    parent = _load_parent(child_document=child, include_nodes={"child-stage": "local-stage"})

    def failing_resolver(alias):
        raise WorkflowDocumentError(f"Workflow dependency {alias!r} could not be resolved")

    with pytest.raises(WorkflowDocumentError, match="could not be resolved"):
        resolve_workflow_includes(
            parent.graph,
            persona_dependencies=parent.persona_dependencies,
            workflow_dependencies=parent.workflow_dependencies,
            resolve_alias=failing_resolver,
        )


def test_include_resolver_from_workflow_definitions_raises_for_a_missing_aggregate():
    resolver = include_resolver_from_workflow_definitions({})
    with pytest.raises(WorkflowDocumentError, match="no resolved definition"):
        resolver("missing")


def test_nested_include_is_rejected():
    """An include node cannot itself be the target of an include (item 2:
    'no nested includes'). This falls out of the same kind check: an
    ``include`` node inside the child is simply not in {stage, gate}."""
    nested_alias_id = str(uuid4())
    nested_digest = "sha256:" + "c" * 64
    nested_pin = {"id": nested_alias_id, "revision": nested_digest, "digest": nested_digest}
    child_payload = _child_payload(
        extra_nodes=[
            {
                "id": "child-nested-include",
                "kind": "include",
                "label": "A nested include",
                "workflow": "grandchild",
                "nodes": {"child-stage": "whatever"},
            }
        ]
    )
    child_payload["workflow_dependencies"] = {"grandchild": nested_pin}
    child = load_workflow_document(yaml.safe_dump(child_payload, sort_keys=False))

    parent = _load_parent(
        child_document=child, include_nodes={"child-nested-include": "local-nested"}
    )

    with pytest.raises(WorkflowDocumentError, match="only stage and gate nodes may be included"):
        resolve_workflow_includes(
            parent.graph,
            persona_dependencies=parent.persona_dependencies,
            workflow_dependencies=parent.workflow_dependencies,
            resolve_alias=_resolver_for(child),
        )


# ---------------------------------------------------------------------------
# Adoption: the bundled Developer Delivery workflow
# ---------------------------------------------------------------------------

_EXPECTED_DELIVERY_INCLUDED_NODES = {
    "delivery-plan-author": {
        "id": "delivery-plan-author",
        "kind": "stage",
        "label": "Investigate and maintain the canonical plan",
        "executionMode": "sequential",
        "joinMode": "any",
        "reviewVerdictPolicy": {
            "eventType": "plan.review.completed",
            "passOutcomes": ["developer.plan.approved"],
            "failOutcomes": ["developer.plan.revised"],
            "bindingFields": ["plan_revision"],
        },
        "stageMembers": [
            {
                "personaId": "developer-analyst",
                "model": "gpt-5.5",
                "budget": 32,
                "consumesEventTypes": ["delivery.requested", "plan.review.completed"],
            }
        ],
    },
    "delivery-plan-reviews": {
        "id": "delivery-plan-reviews",
        "kind": "stage",
        "label": "Independently review the current plan revision",
        "executionMode": "parallel",
        "joinMode": "all",
        "stageMembers": [
            {
                "personaId": "developer-architecture-reviewer",
                "model": "gpt-5.5",
                "budget": 20,
                "consumesEventTypes": ["developer.plan.revised"],
            },
            {
                "personaId": "developer-correctness-reviewer",
                "model": "gpt-5.5",
                "budget": 20,
                "consumesEventTypes": ["developer.plan.revised"],
            },
            {
                "personaId": "developer-security-plan-reviewer",
                "model": "gpt-5.5",
                "budget": 20,
                "consumesEventTypes": ["developer.plan.revised"],
            },
        ],
    },
    # The standalone Integration workflow is adopted as the single source
    # for this stage; its label and joinMode differ from what Delivery used
    # to hand-copy (see docs/operator/workflow-authoring.md and the task
    # report: joinMode was "all" in Delivery's stale copy vs "any" here).
    "delivery-integration-tests": {
        "id": "delivery-integration-tests",
        "kind": "stage",
        "label": "Request trusted integration verification",
        "executionMode": "sequential",
        "joinMode": "any",
        "stageMembers": [
            {
                "personaId": "developer-coordinator",
                "model": "gpt-5.5",
                "budget": 12,
                "consumesEventTypes": ["developer.integration.requested"],
            }
        ],
    },
    "delivery-integration-review": {
        "id": "delivery-integration-review",
        "kind": "stage",
        "label": "Review the complete verified change",
        "executionMode": "sequential",
        "joinMode": "all",
        "stageMembers": [
            {
                "personaId": "developer-integration-verifier",
                "model": "gpt-5.5",
                "budget": 28,
                "consumesEventTypes": ["developer.integration.verified"],
            }
        ],
    },
}

_EXPECTED_DELIVERY_INTERNAL_EDGES = {
    (
        "delivery-plan-author",
        "delivery-plan-reviews",
        "developer.plan.revised -> developer.plan.revised",
    ),
    (
        "delivery-plan-reviews",
        "delivery-plan-author",
        "plan.review.completed -> plan.review.completed",
    ),
    (
        "delivery-integration-tests",
        "delivery-integration-review",
        "developer.integration.verified -> developer.integration.verified",
    ),
}


def _load_bundled_delivery():
    return next(
        workflow for workflow in load_system_workflows() if workflow.name == "Developer Delivery"
    )


def test_resolved_bundled_developer_delivery_graph_matches_fixture():
    delivery = _load_bundled_delivery()
    resolved = resolve_workflow_includes(
        delivery.graph,
        persona_dependencies=delivery.persona_dependencies,
        workflow_dependencies=delivery.workflow_dependencies,
        resolve_alias=include_resolver_from_workflow_definitions(delivery.workflow_definitions),
    )

    assert not graph_has_include_nodes(resolved)
    nodes_by_id = {node["id"]: node for node in resolved["nodes"]}
    for node_id, expected in _EXPECTED_DELIVERY_INCLUDED_NODES.items():
        actual = {key: value for key, value in nodes_by_id[node_id].items() if key != "position"}
        assert actual == expected, node_id

    edges = {(edge["source"], edge["target"], edge["label"]) for edge in resolved["edges"]}
    assert _EXPECTED_DELIVERY_INTERNAL_EDGES <= edges


def test_developer_delivery_declares_the_two_new_pinned_include_dependencies():
    delivery = _load_bundled_delivery()
    assert {"workstream", "planning", "integration"} == set(delivery.workflow_dependencies)


# ---------------------------------------------------------------------------
# build_workflow_snapshot
# ---------------------------------------------------------------------------


def test_build_workflow_snapshot_has_no_include_nodes_and_still_pins_included_workflows():
    delivery = _load_bundled_delivery()
    snapshot = build_workflow_snapshot(delivery)

    assert not graph_has_include_nodes(snapshot["graph"])
    node_kinds = {node["id"]: node["kind"] for node in snapshot["graph"]["nodes"]}
    assert node_kinds["delivery-plan-author"] == "stage"
    assert node_kinds["delivery-plan-reviews"] == "stage"
    assert node_kinds["delivery-integration-tests"] == "stage"
    assert node_kinds["delivery-integration-review"] == "stage"

    assert {"workstream", "planning", "integration"} == set(snapshot["workflow_dependencies"])
    assert {"workstream", "planning", "integration"} == set(snapshot["workflow_definitions"])
    for alias, pin in delivery.workflow_dependencies.items():
        assert snapshot["workflow_dependencies"][alias]["id"] == str(pin.id)
        assert snapshot["workflow_dependencies"][alias]["digest"] == pin.digest


def test_synthetic_snapshot_resolves_a_hand_built_include():
    child = _load_child()
    parent_document = _load_parent(
        child_document=child, include_nodes={"child-stage": "local-stage"}
    )
    now = datetime.now(UTC)
    workflow = WorkflowDefinition(
        id=parent_document.id,
        name=parent_document.name,
        description=parent_document.description,
        version=parent_document.version,
        scope=WorkflowScope.USER,
        owner_id="owner-1",
        graph=parent_document.graph,
        created_at=now,
        updated_at=now,
        schema_version=2,
        persona_dependencies=parent_document.persona_dependencies,
        persona_definitions={"reviewer": _REVIEWER_PORTABLE.to_dict()},
        workflow_dependencies=parent_document.workflow_dependencies,
        workflow_definitions={
            "child": {
                "document": workflow_document_payload(child),
                "persona_definitions": {"reviewer": _REVIEWER_PORTABLE.to_dict()},
                "workflow_definitions": {},
            }
        },
    )

    snapshot = build_workflow_snapshot(workflow)

    assert not graph_has_include_nodes(snapshot["graph"])
    assert "local-stage" in {node["id"] for node in snapshot["graph"]["nodes"]}


# ---------------------------------------------------------------------------
# Standalone workflows and pin_workflow_personas
# ---------------------------------------------------------------------------


def test_standalone_planning_and_integration_workflows_still_load_unchanged():
    planning = load_bundled_workflow(BUNDLED_SYSTEM_WORKFLOWS_PATH / "developer-planning.yaml")
    integration = load_bundled_workflow(
        BUNDLED_SYSTEM_WORKFLOWS_PATH / "developer-integration.yaml"
    )

    assert not graph_has_include_nodes(planning.graph)
    assert not graph_has_include_nodes(integration.graph)
    planning_ids = {node["id"] for node in planning.graph["nodes"]}
    integration_ids = {node["id"] for node in integration.graph["nodes"]}
    assert planning_ids == {
        "planning-request",
        "planning-analysis",
        "planning-reviews",
        "planning-complete",
    }
    assert integration_ids == {
        "integration-request",
        "integration-tests",
        "integration-review",
        "integration-complete",
    }


def test_pin_workflow_personas_keeps_include_only_aliases_instead_of_dropping_them():
    child = _load_child()
    parent_document = _load_parent(
        child_document=child, include_nodes={"child-stage": "local-stage"}
    )
    now = datetime.now(UTC)
    workflow = WorkflowDefinition(
        id=parent_document.id,
        name=parent_document.name,
        description=parent_document.description,
        version=parent_document.version,
        scope=WorkflowScope.USER,
        owner_id="owner-1",
        graph=parent_document.graph,
        created_at=now,
        updated_at=now,
        schema_version=2,
        persona_dependencies={"reviewer": _REVIEWER_PORTABLE.dependency},
        persona_definitions={"reviewer": _REVIEWER_PORTABLE.to_dict()},
        workflow_dependencies=parent_document.workflow_dependencies,
    )

    # No persona_source is supplied. Without the include-aware guard,
    # pin_workflow_personas would see no persona referenced by a raw scan of
    # the graph (the reference lives behind the include) and try to treat
    # 'reviewer' as removed, raising for lack of a source to consult.
    result = pin_workflow_personas(workflow, None)

    assert result.persona_dependencies == workflow.persona_dependencies
    assert result.persona_definitions == workflow.persona_definitions
