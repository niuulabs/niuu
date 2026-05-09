from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tyr.domain.models import WorkflowDefinition, WorkflowScope
from tyr.ports.workflow_repository import WorkflowRepository
from tyr.system_workflows import load_system_workflows, seed_system_workflows


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


def test_load_system_workflows_includes_tyr_raid_flow() -> None:
    workflows = load_system_workflows()

    names = {workflow.name for workflow in workflows}
    assert "Tyr Raid Flow" in names
    raid_flow = next(workflow for workflow in workflows if workflow.name == "Tyr Raid Flow")
    assert raid_flow.scope == WorkflowScope.SYSTEM
    assert raid_flow.owner_id is None
    assert raid_flow.graph["nodes"][0]["kind"] == "trigger"
    stage_labels = [
        node["label"]
        for node in raid_flow.graph["nodes"]
        if node.get("kind") == "stage"
    ]
    assert stage_labels == ["Coordinate raid", "Implement code", "Review changes"]
    assert "name: coordinate-raid" in str(raid_flow.definition_yaml)
    assert "name: implement-code" in str(raid_flow.definition_yaml)
    assert "name: review-changes" in str(raid_flow.definition_yaml)


@pytest.mark.asyncio
async def test_seed_system_workflows_upserts_existing_by_name() -> None:
    existing = WorkflowDefinition(
        id=uuid4(),
        name="Tyr Raid Flow",
        description="Old",
        version="0.9.0",
        scope=WorkflowScope.SYSTEM,
        owner_id=None,
        definition_yaml="name: old",
        graph={"nodes": [], "edges": []},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    repo = _InMemoryWorkflowRepository([existing])

    saved = await seed_system_workflows(repo)

    assert any(workflow.name == "Tyr Raid Flow" for workflow in saved)
    current = next(
        workflow
        for workflow in await repo.list_workflows(owner_id="", scope=WorkflowScope.SYSTEM)
        if workflow.name == "Tyr Raid Flow"
    )
    assert current.id == existing.id
    assert current.version == "1.0.0"
