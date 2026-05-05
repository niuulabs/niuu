from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from tyr.domain.models import WorkflowDefinition, WorkflowScope
from tyr.domain.workflow_snapshot import (
    build_workflow_snapshot,
    workflow_mimir_from_snapshot,
    workflow_name_from_snapshot,
    workflow_personas_from_snapshot,
    workflow_resource_bindings_from_snapshot,
    workflow_resource_nodes_from_snapshot,
)


def test_build_workflow_snapshot_preserves_mimir_resource_data() -> None:
    workflow = WorkflowDefinition(
        id=uuid4(),
        name="Knowledge Flow",
        description="Workflow with explicit resources",
        version="1.0.0",
        scope=WorkflowScope.USER,
        owner_id="user-1",
        definition_yaml="name: Knowledge Flow",
        graph={
            "nodes": [
                {
                    "id": "stage-1",
                    "kind": "stage",
                    "label": "Review",
                    "stageMembers": [{"personaId": "reviewer", "budget": 40}],
                },
                {
                    "id": "mimir-shared",
                    "kind": "resource",
                    "label": "Shared Mimir",
                    "resourceType": "mimir",
                    "bindingMode": "registry",
                    "registryEntryId": "shared-team-mimir",
                    "categories": ["entity", "decision"],
                },
                {
                    "id": "mimir-scratch",
                    "kind": "resource",
                    "label": "Scratchpad",
                    "resourceType": "mimir",
                    "bindingMode": "ephemeral_local",
                    "seedFromRegistryId": "shared-team-mimir",
                    "categories": ["draft"],
                },
            ],
            "edges": [],
            "resourceBindings": [
                {
                    "id": "binding-1",
                    "resourceNodeId": "mimir-shared",
                    "targetType": "stage",
                    "targetId": "stage-1",
                    "access": "read_write",
                    "writePrefixes": ["project/"],
                    "readPriority": 3,
                },
                {
                    "id": "binding-2",
                    "resourceNodeId": "mimir-scratch",
                    "targetType": "workflow",
                    "targetId": "workflow-1",
                    "access": "write",
                    "writePrefixes": ["draft/"],
                    "readPriority": 10,
                },
            ],
        },
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    snapshot = build_workflow_snapshot(workflow)

    assert [node["id"] for node in snapshot["resource_nodes"]] == [
        "mimir-shared",
        "mimir-scratch",
    ]
    assert [binding["id"] for binding in snapshot["resource_bindings"]] == [
        "binding-1",
        "binding-2",
    ]
    assert snapshot["mimir"]["registry_refs"] == [
        {
            "resource_node_id": "mimir-shared",
            "registry_entry_id": "shared-team-mimir",
            "mount_name": "shared-team-mimir",
            "label": "Shared Mimir",
            "categories": ["entity", "decision"],
        }
    ]
    assert snapshot["mimir"]["ephemeral_locals"] == [
        {
            "resource_node_id": "mimir-scratch",
            "mount_name": "scratchpad",
            "label": "Scratchpad",
            "seed_from_registry_id": "shared-team-mimir",
            "categories": ["draft"],
        }
    ]
    assert snapshot["mimir"]["bindings"] == [
        {
            "resource_node_id": "mimir-shared",
            "mount_name": "shared-team-mimir",
            "target_type": "stage",
            "target_id": "stage-1",
            "access": "read_write",
            "write_prefixes": ["project/"],
            "read_priority": 3,
        },
        {
            "resource_node_id": "mimir-scratch",
            "mount_name": "scratchpad",
            "target_type": "workflow",
            "target_id": "workflow-1",
            "access": "write",
            "write_prefixes": ["draft/"],
            "read_priority": 10,
        },
    ]


def test_build_workflow_snapshot_preserves_concrete_registry_mount_metadata() -> None:
    workflow = WorkflowDefinition(
        id=uuid4(),
        name="Knowledge Flow",
        description="Workflow with concrete registry resource metadata",
        version="1.0.0",
        scope=WorkflowScope.USER,
        owner_id="user-1",
        definition_yaml="name: Knowledge Flow",
        graph={
            "nodes": [
                {
                    "id": "mimir-shared",
                    "kind": "resource",
                    "label": "Shared Mimir",
                    "resourceType": "mimir",
                    "bindingMode": "registry",
                    "registryEntryId": "shared-team-mimir",
                    "url": "https://mimir.shared.test/api/v1",
                    "role": "shared",
                    "authRef": "mimir-token",
                    "defaultReadPriority": 3,
                    "categories": ["entity"],
                }
            ],
            "edges": [],
            "resourceBindings": [],
        },
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    snapshot = build_workflow_snapshot(workflow)

    assert snapshot["mimir"]["registry_refs"] == [
        {
            "resource_node_id": "mimir-shared",
            "registry_entry_id": "shared-team-mimir",
            "mount_name": "shared-team-mimir",
            "label": "Shared Mimir",
            "categories": ["entity"],
            "url": "https://mimir.shared.test/api/v1",
            "role": "shared",
            "auth_ref": "mimir-token",
            "default_read_priority": 3,
        }
    ]


def test_workflow_snapshot_helpers_fall_back_to_graph_and_dedupe_personas() -> None:
    snapshot = {
        "name": "Incident Flow",
        "graph": {
            "nodes": [
                {
                    "id": "stage-1",
                    "kind": "stage",
                    "stageMembers": [
                        {"personaId": "reviewer", "budget": 12},
                        {"personaId": "reviewer", "budget": 99},
                        {"personaId": ""},
                        "invalid",
                    ],
                },
                {
                    "id": "stage-2",
                    "kind": "stage",
                    "personaIds": ["reviewer", "writer", "", None],
                },
                {
                    "id": "resource-1",
                    "kind": "resource",
                    "resourceType": "mimir",
                    "label": "Shared Space",
                },
                {
                    "id": "resource-2",
                    "kind": "resource",
                    "resourceType": "other",
                },
            ],
            "resource_bindings": [
                {"resourceNodeId": "resource-1", "targetId": "stage-1"},
                "invalid",
            ],
        },
    }

    assert workflow_name_from_snapshot(snapshot) == "Incident Flow"
    assert workflow_personas_from_snapshot(snapshot) == [
        {"name": "reviewer", "iteration_budget": 12},
        {"name": "writer"},
    ]
    assert workflow_resource_nodes_from_snapshot(snapshot) == [
        {
            "id": "resource-1",
            "kind": "resource",
            "resourceType": "mimir",
            "label": "Shared Space",
        }
    ]
    assert workflow_resource_bindings_from_snapshot(snapshot) == [
        {"resourceNodeId": "resource-1", "targetId": "stage-1"}
    ]


def test_workflow_snapshot_prefers_cached_values_when_present() -> None:
    snapshot = {
        "name": "Cached Flow",
        "personas": [{"name": "cached"}],
        "resource_nodes": [{"id": "cached-node"}],
        "resource_bindings": [{"id": "cached-binding"}],
        "mimir": {"registry_refs": [{"mount_name": "cached"}]},
        "graph": {
            "nodes": [
                {
                    "id": "stage-1",
                    "kind": "stage",
                    "personaIds": ["graph-only"],
                }
            ]
        },
    }

    assert workflow_personas_from_snapshot(snapshot) == [{"name": "cached"}]
    assert workflow_resource_nodes_from_snapshot(snapshot) == [{"id": "cached-node"}]
    assert workflow_resource_bindings_from_snapshot(snapshot) == [{"id": "cached-binding"}]
    assert workflow_mimir_from_snapshot(snapshot) == {
        "registry_refs": [{"mount_name": "cached"}]
    }


def test_workflow_mimir_from_snapshot_handles_defaults_and_optional_metadata() -> None:
    snapshot = {
        "graph": {
            "nodes": [
                {
                    "id": "resource-1",
                    "kind": "resource",
                    "resourceType": "mimir",
                    "bindingMode": "registry",
                    "label": "Shared Notes",
                    "path": " /docs ",
                    "url": "https://mimir.example.test",
                    "role": "team",
                    "auth_ref": " secret-ref ",
                    "default_read_priority": "not-an-int",
                    "categories": ["docs", " ", 1],
                },
                {
                    "id": "resource-2",
                    "kind": "resource",
                    "resourceType": "mimir",
                    "bindingMode": "ephemeral_local",
                    "label": "Scratch Area",
                    "seedFromRegistryId": "seed-1",
                },
                {
                    "id": "",
                    "kind": "resource",
                    "resourceType": "mimir",
                },
            ],
            "resourceBindings": [
                {
                    "resourceNodeId": "resource-1",
                    "targetType": "stage",
                    "targetId": "stage-1",
                    "access": "read_write",
                    "writePrefixes": ["notes/", "", None],
                    "readPriority": "bad",
                },
                {
                    "resourceNodeId": "missing",
                    "targetId": "ignored",
                },
            ],
        }
    }

    mimir = workflow_mimir_from_snapshot(snapshot)

    assert mimir["registry_refs"] == [
        {
            "resource_node_id": "resource-1",
            "registry_entry_id": None,
            "mount_name": "shared-notes",
            "label": "Shared Notes",
            "categories": ["docs"],
            "path": "/docs",
            "url": "https://mimir.example.test",
            "role": "team",
            "auth_ref": "secret-ref",
        }
    ]
    assert mimir["ephemeral_locals"] == [
        {
            "resource_node_id": "resource-2",
            "mount_name": "scratch-area",
            "label": "Scratch Area",
            "seed_from_registry_id": "seed-1",
            "categories": [],
        }
    ]
    assert mimir["bindings"] == [
        {
            "resource_node_id": "resource-1",
            "mount_name": "shared-notes",
            "target_type": "stage",
            "target_id": "stage-1",
            "access": "read_write",
            "write_prefixes": ["notes/"],
            "read_priority": 10,
        }
    ]
    assert mimir["default_mounts"] == ["scratch-area"]


def test_workflow_snapshot_empty_inputs_return_empty_structures() -> None:
    assert workflow_name_from_snapshot(None) is None
    assert workflow_name_from_snapshot({}) is None
    assert workflow_personas_from_snapshot(None) == []
    assert workflow_resource_nodes_from_snapshot({"graph": []}) == []
    assert workflow_resource_bindings_from_snapshot({"graph": {}}) == []
    assert workflow_mimir_from_snapshot({"graph": {"nodes": []}}) == {}
