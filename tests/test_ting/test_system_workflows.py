from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ting.domain.models import WorkflowDefinition, WorkflowScope
from ting.ports.workflow_repository import WorkflowRepository
from ting.system_workflows import load_system_workflows, seed_system_workflows


class _InMemoryWorkflowRepository(WorkflowRepository):
    def __init__(self, workflows: list[WorkflowDefinition] | None = None) -> None:
        self._workflows = {workflow.id: workflow for workflow in workflows or []}

    async def list_workflows(
        self,
        *,
        owner_id: str,
        scope: WorkflowScope | None = None,
    ) -> list[WorkflowDefinition]:
        workflows = list(self._workflows.values())
        if scope == WorkflowScope.SYSTEM:
            return [workflow for workflow in workflows if workflow.scope == WorkflowScope.SYSTEM]
        return workflows

    async def get_workflow(self, workflow_id):
        return self._workflows.get(workflow_id)

    async def save_workflow(self, workflow: WorkflowDefinition) -> WorkflowDefinition:
        self._workflows[workflow.id] = workflow
        return workflow

    async def delete_workflow(self, workflow_id) -> bool:
        return self._workflows.pop(workflow_id, None) is not None


def test_load_system_workflows_only_keeps_supported_catalog() -> None:
    workflows = load_system_workflows()

    names = {workflow.name for workflow in workflows}
    assert names == {
        "Ting Run Flow + Security + Memory Curation",
        "Research Campaign",
        "Specification Stack",
        "Tracker Delivery Flow",
        "Code & Review Flow",
        "Tool & Skill Builder",
    }

    run_flow = next(
        workflow
        for workflow in workflows
        if workflow.name == "Ting Run Flow + Security + Memory Curation"
    )
    assert run_flow.scope == WorkflowScope.SYSTEM
    assert run_flow.owner_id is None
    stage_personas = {
        node["label"]: [member["personaId"] for member in node.get("stageMembers", [])]
        for node in run_flow.graph["nodes"]
        if node.get("kind") == "stage"
    }
    assert stage_personas["Implement code"] == ["coder"]
    assert stage_personas["Security review"] == ["security-auditor"]
    assert stage_personas["Curate shared memory"] == ["mimir-memory-curator"]
    edge_labels = {edge["label"] for edge in run_flow.graph["edges"]}
    assert "security.completed -> security.completed" in edge_labels
    assert "mimir.curated -> mimir.curated" in edge_labels

    research_flow = next(workflow for workflow in workflows if workflow.name == "Research Campaign")
    research_stage_labels = [
        node["label"] for node in research_flow.graph["nodes"] if node.get("kind") == "stage"
    ]
    assert research_stage_labels[0] == "Frame the inquiry"
    assert "Curate learnings and follow-ups" in research_stage_labels
    assert research_stage_labels[-1] == "Publish to Mimir"
    research_resources = {
        node["label"]: node
        for node in research_flow.graph["nodes"]
        if node.get("kind") == "resource"
    }
    assert research_resources["Research Memory"]["bindingMode"] == "registry"
    assert research_resources["Research Memory"]["path"] == "/tmp/mimir"

    specification_flow = next(
        workflow for workflow in workflows if workflow.name == "Specification Stack"
    )
    specification_stage_labels = [
        node["label"] for node in specification_flow.graph["nodes"] if node.get("kind") == "stage"
    ]
    assert specification_stage_labels[:3] == [
        "Frame initiative",
        "Draft PRD",
        "Review PRD",
    ]
    assert "Draft SRD" in specification_stage_labels
    assert specification_stage_labels[-1] == "Publish specification pack"
    specification_gate_labels = [
        node["label"] for node in specification_flow.graph["nodes"] if node.get("kind") == "gate"
    ]
    assert specification_gate_labels == [
        "PRD approval gate",
        "SRD approval gate",
        "SDD approval gate",
        "Breakdown approval gate",
    ]
    specification_stage_personas = {
        node["label"]: [member["personaId"] for member in node.get("stageMembers", [])]
        for node in specification_flow.graph["nodes"]
        if node.get("kind") == "stage"
    }
    assert specification_stage_personas["Draft PRD"] == ["specification-prd-author"]
    assert specification_stage_personas["Review PRD"] == ["specification-prd-critic"]
    specification_gate_behaviors = {
        node["label"]: node.get("pendingBehavior")
        for node in specification_flow.graph["nodes"]
        if node.get("kind") == "gate"
    }
    assert specification_gate_behaviors == {
        "PRD approval gate": "help_needed",
        "SRD approval gate": "help_needed",
        "SDD approval gate": "help_needed",
        "Breakdown approval gate": "help_needed",
    }
    specification_gate_modes = {
        node["label"]: node.get("mode")
        for node in specification_flow.graph["nodes"]
        if node.get("kind") == "gate"
    }
    assert specification_gate_modes == {
        "PRD approval gate": "human_approval",
        "SRD approval gate": "human_approval",
        "SDD approval gate": "human_approval",
        "Breakdown approval gate": "human_approval",
    }
    specification_resources = {
        node["label"]: node
        for node in specification_flow.graph["nodes"]
        if node.get("kind") == "resource"
    }
    assert specification_resources["Specification Memory"]["path"] == "/tmp/mimir"

    delivery_flow = next(
        workflow for workflow in workflows if workflow.name == "Tracker Delivery Flow"
    )
    delivery_stage_labels = [
        node["label"] for node in delivery_flow.graph["nodes"] if node.get("kind") == "stage"
    ]
    assert delivery_stage_labels == [
        "Implement tracker ticket",
        "Review implementation",
        "Merge and close ticket",
        "Publish delivery record",
    ]
    delivery_stage_personas = {
        node["label"]: [member["personaId"] for member in node.get("stageMembers", [])]
        for node in delivery_flow.graph["nodes"]
        if node.get("kind") == "stage"
    }
    assert delivery_stage_personas["Implement tracker ticket"] == ["coder"]
    assert delivery_stage_personas["Review implementation"] == ["reviewer"]
    assert delivery_stage_personas["Merge and close ticket"] == ["closer"]
    assert delivery_stage_personas["Publish delivery record"] == ["publisher"]
    delivery_edge_labels = {edge["label"] for edge in delivery_flow.graph["edges"]}
    assert "review.changes_requested -> review.changes_requested" in delivery_edge_labels
    assert "review.passed -> review.passed" in delivery_edge_labels
    delivery_resources = {
        node["label"]: node
        for node in delivery_flow.graph["nodes"]
        if node.get("kind") == "resource"
    }
    assert delivery_resources["Delivery Memory"]["path"] == "/tmp/mimir"

    code_review_flow = next(
        workflow for workflow in workflows if workflow.name == "Code & Review Flow"
    )
    code_review_stage_labels = [
        node["label"] for node in code_review_flow.graph["nodes"] if node.get("kind") == "stage"
    ]
    assert code_review_stage_labels == [
        "Implement tracker ticket",
        "Review implementation",
        "Merge and close ticket",
    ]
    code_review_stage_members = {
        node["label"]: node.get("stageMembers", [])
        for node in code_review_flow.graph["nodes"]
        if node.get("kind") == "stage"
    }
    assert [
        member["personaId"] for member in code_review_stage_members["Implement tracker ticket"]
    ] == ["coder"]
    assert [
        member["personaId"] for member in code_review_stage_members["Review implementation"]
    ] == ["reviewer"]
    assert [
        member["personaId"] for member in code_review_stage_members["Merge and close ticket"]
    ] == ["closer"]
    assert all(
        member.get("model") == "gpt-5.5"
        for members in code_review_stage_members.values()
        for member in members
    )
    code_review_edge_labels = {edge["label"] for edge in code_review_flow.graph["edges"]}
    assert "review.passed -> review.passed" in code_review_edge_labels
    assert "delivery.merged -> delivery.merged" in code_review_edge_labels
    code_review_resources = {
        node["label"]: node
        for node in code_review_flow.graph["nodes"]
        if node.get("kind") == "resource"
    }
    assert code_review_resources["Delivery Memory"]["path"] == "/tmp/mimir"

    builder_flow = next(
        workflow for workflow in workflows if workflow.name == "Tool & Skill Builder"
    )
    assert {"tool-builder", "skill-builder", "capability-builder"}.issubset(
        set(builder_flow.graph["tags"])
    )
    builder_stage_labels = [
        node["label"] for node in builder_flow.graph["nodes"] if node.get("kind") == "stage"
    ]
    assert builder_stage_labels == [
        "Frame missing capability",
        "Build tool or skill",
        "Review capability",
        "Publish capability record",
    ]
    builder_stage_personas = {
        node["label"]: [member["personaId"] for member in node.get("stageMembers", [])]
        for node in builder_flow.graph["nodes"]
        if node.get("kind") == "stage"
    }
    assert builder_stage_personas["Frame missing capability"] == ["specification-framer"]
    assert builder_stage_personas["Build tool or skill"] == ["coder"]
    assert builder_stage_personas["Review capability"] == ["reviewer", "security-auditor"]
    builder_resources = {
        node["label"]: node
        for node in builder_flow.graph["nodes"]
        if node.get("kind") == "resource"
    }
    assert builder_resources["Capability Memory"]["bindingMode"] == "registry"
    assert builder_resources["Capability Memory"]["path"] == "/tmp/mimir"


@pytest.mark.asyncio
async def test_seed_system_workflows_prunes_obsolete_and_duplicate_entries() -> None:
    seeds = load_system_workflows()
    current = seeds[0]
    duplicate = WorkflowDefinition(
        id=uuid4(),
        name=current.name,
        description="Old duplicate",
        version="0.9.0",
        scope=WorkflowScope.SYSTEM,
        owner_id=None,
        graph={"nodes": [], "edges": []},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    obsolete = WorkflowDefinition(
        id=uuid4(),
        name="Ting Run Flow",
        description="Old bundled flow",
        version="0.9.0",
        scope=WorkflowScope.SYSTEM,
        owner_id=None,
        graph={"nodes": [], "edges": []},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    repo = _InMemoryWorkflowRepository([duplicate, obsolete])

    saved = await seed_system_workflows(repo)
    names = {workflow.name for workflow in saved}
    assert names == {
        "Ting Run Flow + Security + Memory Curation",
        "Research Campaign",
        "Specification Stack",
        "Tracker Delivery Flow",
        "Code & Review Flow",
        "Tool & Skill Builder",
    }

    current_catalog = await repo.list_workflows(owner_id="", scope=WorkflowScope.SYSTEM)
    assert {workflow.name for workflow in current_catalog} == names
    assert len(current_catalog) == 6
    assert all(workflow.id in {seed.id for seed in seeds} for workflow in current_catalog)
