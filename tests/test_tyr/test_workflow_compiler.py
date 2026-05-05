"""Tests for Tyr workflow DAG compilation."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from tyr.domain.models import WorkflowDefinition, WorkflowScope
from tyr.domain.workflow_compiler import compile_workflow_definition, compile_workflow_graph


def _workflow(graph: dict) -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name="Review Flow",
        description="",
        version="1.0.0",
        scope=WorkflowScope.USER,
        owner_id="user-1",
        definition_yaml=None,
        graph=graph,
        created_at=now,
        updated_at=now,
    )


class TestWorkflowCompiler:
    def test_compiles_linear_stage_graph_to_pipeline_yaml(self) -> None:
        workflow = _workflow(
            {
                "nodes": [
                    {
                        "id": "trigger-1",
                        "kind": "trigger",
                        "label": "Start",
                        "source": "manual dispatch",
                    },
                    {
                        "id": "stage-1",
                        "kind": "stage",
                        "label": "Review",
                        "personaIds": ["reviewer"],
                        "stageMembers": [{"personaId": "reviewer", "budget": 40}],
                        "executionMode": "parallel",
                        "joinMode": "all",
                    },
                    {"id": "end-1", "kind": "end", "label": "Done"},
                ],
                "edges": [
                    {"id": "e1", "source": "trigger-1", "target": "stage-1"},
                    {"id": "e2", "source": "stage-1", "target": "end-1"},
                ],
            }
        )

        result = compile_workflow_definition(workflow)

        assert result.errors == []
        assert result.definition_yaml is not None
        assert "name: Review Flow" in result.definition_yaml
        assert "parallel:" in result.definition_yaml
        assert "persona: reviewer" in result.definition_yaml
        assert "fan_in: all_must_pass" in result.definition_yaml

    def test_reports_conditional_nodes_as_not_yet_executable(self) -> None:
        workflow = _workflow(
            {
                "nodes": [
                    {
                        "id": "trigger-1",
                        "kind": "trigger",
                        "label": "Start",
                        "source": "manual dispatch",
                    },
                    {
                        "id": "cond-1",
                        "kind": "cond",
                        "label": "Route",
                        "predicate": "stages.review.verdict == pass",
                    },
                ],
                "edges": [],
            }
        )

        result = compile_workflow_definition(workflow)

        assert result.definition_yaml is None
        assert result.errors == [
            "Conditional nodes are not yet executable in Tyr runtime: Route."
        ]

    def test_reports_branching_as_not_yet_executable(self) -> None:
        workflow = _workflow(
            {
                "nodes": [
                    {
                        "id": "trigger-1",
                        "kind": "trigger",
                        "label": "Start",
                        "source": "manual dispatch",
                    },
                    {
                        "id": "stage-1",
                        "kind": "stage",
                        "label": "Review",
                        "personaIds": ["reviewer"],
                        "stageMembers": [{"personaId": "reviewer", "budget": 40}],
                        "executionMode": "parallel",
                        "joinMode": "all",
                    },
                    {
                        "id": "stage-2",
                        "kind": "stage",
                        "label": "Security",
                        "personaIds": ["security-auditor"],
                        "stageMembers": [{"personaId": "security-auditor", "budget": 40}],
                        "executionMode": "parallel",
                        "joinMode": "all",
                    },
                    {
                        "id": "stage-3",
                        "kind": "stage",
                        "label": "QA",
                        "personaIds": ["qa-agent"],
                        "stageMembers": [{"personaId": "qa-agent", "budget": 40}],
                        "executionMode": "parallel",
                        "joinMode": "all",
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "trigger-1", "target": "stage-1"},
                    {"id": "e2", "source": "stage-1", "target": "stage-2"},
                    {"id": "e3", "source": "stage-1", "target": "stage-3"},
                ],
            }
        )

        result = compile_workflow_definition(workflow)

        assert result.definition_yaml is None
        assert result.errors == [
            "Node 'Review' fans out to multiple paths, which Tyr cannot execute yet.",
            "Workflow graph contains disconnected or unsupported runtime nodes: QA, Security",
        ]

    def test_ignores_resource_nodes_for_pipeline_compilation(self) -> None:
        workflow = _workflow(
            {
                "nodes": [
                    {
                        "id": "trigger-1",
                        "kind": "trigger",
                        "label": "Start",
                        "source": "manual dispatch",
                    },
                    {
                        "id": "stage-1",
                        "kind": "stage",
                        "label": "Review",
                        "personaIds": ["reviewer"],
                        "stageMembers": [{"personaId": "reviewer", "budget": 40}],
                        "executionMode": "parallel",
                        "joinMode": "all",
                    },
                    {
                        "id": "resource-1",
                        "kind": "resource",
                        "label": "Shared Mimir",
                        "resourceType": "mimir",
                        "bindingMode": "registry",
                        "registryEntryId": "mimir-shared",
                    },
                    {"id": "end-1", "kind": "end", "label": "Done"},
                ],
                "edges": [
                    {"id": "e1", "source": "trigger-1", "target": "stage-1"},
                    {"id": "e2", "source": "stage-1", "target": "end-1"},
                ],
                "resourceBindings": [
                    {
                        "id": "binding-1",
                        "resourceNodeId": "resource-1",
                        "targetType": "stage",
                        "targetId": "stage-1",
                        "access": "read_write",
                        "writePrefixes": ["projects/"],
                        "readPriority": 3,
                    }
                ],
            }
        )

        result = compile_workflow_definition(workflow)

        assert result.errors == []
        assert result.definition_yaml is not None
        assert "persona: reviewer" in result.definition_yaml

    def test_reports_missing_nodes_edges_and_trigger_count_errors(self) -> None:
        empty = compile_workflow_graph("Empty", {"nodes": [], "edges": []})
        assert empty.definition_yaml is None
        assert empty.errors == ["Workflow graph has no nodes to compile."]

        invalid = compile_workflow_graph(
            "Broken",
            {
                "nodes": [
                    {"id": "trigger-1", "kind": "trigger"},
                    {"id": "trigger-2", "kind": "trigger"},
                    {"id": "stage-1", "kind": "stage", "personaIds": ["reviewer"]},
                ],
                "edges": [{"id": "e1", "source": "trigger-1", "target": "missing"}],
            },
        )

        assert invalid.definition_yaml is None
        assert invalid.errors == [
            "Edge 'e1' references a missing node.",
            "Exactly one trigger node is required for executable workflows.",
        ]

    def test_compiles_gate_sequential_stage_and_duplicate_stage_names(self) -> None:
        workflow = _workflow(
            {
                "nodes": [
                    {"id": "trigger-1", "kind": "trigger", "label": "Start"},
                    {"id": "gate-1", "kind": "gate", "label": "Approve"},
                    {
                        "id": "stage-1",
                        "kind": "stage",
                        "label": "Review",
                        "stageMembers": [
                            {"personaId": "reviewer", "budget": 0},
                            {"personaId": "writer"},
                        ],
                        "executionMode": "sequential",
                        "joinMode": "any",
                    },
                    {
                        "id": "stage-2",
                        "kind": "stage",
                        "label": "Review",
                        "personaIds": ["reviewer"],
                    },
                    {"id": "end-1", "kind": "end", "label": "Done"},
                ],
                "edges": [
                    {"id": "e1", "source": "trigger-1", "target": "gate-1"},
                    {"id": "e2", "source": "gate-1", "target": "stage-1"},
                    {"id": "e3", "source": "stage-1", "target": "stage-2"},
                    {"id": "e4", "source": "stage-2", "target": "end-1"},
                ],
            }
        )

        result = compile_workflow_definition(workflow)

        assert result.errors == []
        assert result.definition_yaml is not None
        assert "name: approve" in result.definition_yaml
        assert "gate: human" in result.definition_yaml
        assert "sequential:" in result.definition_yaml
        assert "fan_in: any_pass" in result.definition_yaml
        assert "name: review-2" in result.definition_yaml

    def test_reports_cycles_non_runtime_nodes_and_multiple_inbound_edges(self) -> None:
        cycle = compile_workflow_graph(
            "Cycle",
            {
                "nodes": [
                    {"id": "trigger-1", "kind": "trigger", "label": "Start"},
                    {"id": "stage-1", "kind": "stage", "label": "Review", "personaIds": ["a"]},
                ],
                "edges": [
                    {"source": "trigger-1", "target": "stage-1"},
                    {"source": "stage-1", "target": "trigger-1"},
                ],
            },
        )
        assert cycle.definition_yaml is None
        assert cycle.errors == [
            "Workflow graph contains a cycle at node 'Start'."
        ]

        unsupported = compile_workflow_graph(
            "Unsupported",
            {
                "nodes": [
                    {"id": "trigger-1", "kind": "trigger", "label": "Start"},
                    {"id": "note-1", "kind": "note", "label": "Comment"},
                ],
                "edges": [{"source": "trigger-1", "target": "note-1"}],
            },
        )
        assert unsupported.definition_yaml is None
        assert unsupported.errors == [
            "Node 'Comment' is not executable in Tyr runtime."
        ]

        inbound = compile_workflow_graph(
            "Inbound",
            {
                "nodes": [
                    {"id": "trigger-1", "kind": "trigger", "label": "Start"},
                    {"id": "stage-1", "kind": "stage", "label": "One", "personaIds": ["a"]},
                    {"id": "stage-2", "kind": "stage", "label": "Two", "personaIds": ["b"]},
                    {"id": "stage-3", "kind": "stage", "label": "Three", "personaIds": ["c"]},
                ],
                "edges": [
                    {"source": "trigger-1", "target": "stage-1"},
                    {"source": "stage-1", "target": "stage-3"},
                    {"source": "stage-2", "target": "stage-3"},
                ],
            },
        )
        assert inbound.definition_yaml is None
        assert inbound.errors == [
            "Node 'Three' has multiple inbound edges, which Tyr cannot execute yet.",
            "Workflow graph contains disconnected or unsupported runtime nodes: Three, Two",
        ]

    def test_reports_stage_validation_errors(self) -> None:
        no_personas = compile_workflow_graph(
            "No Personas",
            {
                "nodes": [
                    {"id": "trigger-1", "kind": "trigger"},
                    {"id": "stage-1", "kind": "stage", "label": "Review"},
                ],
                "edges": [{"source": "trigger-1", "target": "stage-1"}],
            },
        )
        assert no_personas.definition_yaml is None
        assert no_personas.errors == ["Stage 'Review' has no assigned personas."]

        missing_member_id = compile_workflow_graph(
            "Bad Member",
            {
                "nodes": [
                    {"id": "trigger-1", "kind": "trigger"},
                    {
                        "id": "stage-1",
                        "kind": "stage",
                        "label": "Review",
                        "stageMembers": [{"personaId": ""}],
                    },
                ],
                "edges": [{"source": "trigger-1", "target": "stage-1"}],
            },
        )
        assert missing_member_id.definition_yaml is None
        assert missing_member_id.errors == [
            "Stage 'Review' contains a member without personaId."
        ]
