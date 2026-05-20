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
        "Research Council + Human Input",
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

    research_flow = next(
        workflow for workflow in workflows if workflow.name == "Research Council + Human Input"
    )
    research_stage_labels = [
        node["label"] for node in research_flow.graph["nodes"] if node.get("kind") == "stage"
    ]
    assert research_stage_labels[-1] == "Synthesize or ask operator"
    research_resources = {
        node["label"]: node
        for node in research_flow.graph["nodes"]
        if node.get("kind") == "resource"
    }
    assert research_resources["Council Scratch Board"]["bindingMode"] == "ephemeral_local"
    assert research_resources["Permanent Research Memory"]["bindingMode"] == "registry"


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
        "Research Council + Human Input",
    }

    current_catalog = await repo.list_workflows(owner_id="", scope=WorkflowScope.SYSTEM)
    assert {workflow.name for workflow in current_catalog} == names
    assert len(current_catalog) == 2
    assert all(workflow.id in {seed.id for seed in seeds} for workflow in current_catalog)
