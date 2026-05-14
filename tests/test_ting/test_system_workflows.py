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


def test_load_system_workflows_includes_ting_run_flow() -> None:
    workflows = load_system_workflows()

    names = {workflow.name for workflow in workflows}
    assert "Ting Run Flow" in names
    assert "Ting Run Flow + Security" in names
    assert "Ting Run Flow + Security + Postmortem Memory" in names
    assert "Ting Run Flow + Security + Memory Curation" in names
    run_flow = next(workflow for workflow in workflows if workflow.name == "Ting Run Flow")
    assert run_flow.scope == WorkflowScope.SYSTEM
    assert run_flow.owner_id is None
    assert run_flow.graph["nodes"][0]["kind"] == "trigger"
    stage_labels = [
        node["label"] for node in run_flow.graph["nodes"] if node.get("kind") == "stage"
    ]
    assert stage_labels == [
        "Implement code",
        "Review changes",
    ]
    edge_labels = {edge["label"] for edge in run_flow.graph["edges"]}
    assert "review.changes_requested -> review.changes_requested" in edge_labels
    assert "review.completed -> review.completed" in edge_labels
    stage_personas = {
        node["label"]: [member["personaId"] for member in node.get("stageMembers", [])]
        for node in run_flow.graph["nodes"]
        if node.get("kind") == "stage"
    }
    assert stage_personas["Implement code"] == ["coder"]
    assert stage_personas["Review changes"] == ["reviewer"]
    # The saved workflow graph now includes a real feedback loop
    # (reviewer -> coder on changes requested), so the current linear
    # compiler no longer produces a pipeline YAML for it.
    assert run_flow.definition_yaml is None

    security_flow = next(
        workflow for workflow in workflows if workflow.name == "Ting Run Flow + Security"
    )
    security_stage_labels = [
        node["label"] for node in security_flow.graph["nodes"] if node.get("kind") == "stage"
    ]
    assert security_stage_labels == [
        "Implement code",
        "Review changes",
        "Security review",
    ]
    security_edge_labels = {edge["label"] for edge in security_flow.graph["edges"]}
    assert "code.changed -> code.changed" in security_edge_labels
    assert "security.changes_requested -> security.changes_requested" in security_edge_labels
    assert "security.completed -> security.completed" in security_edge_labels
    security_stage_personas = {
        node["label"]: [member["personaId"] for member in node.get("stageMembers", [])]
        for node in security_flow.graph["nodes"]
        if node.get("kind") == "stage"
    }
    assert security_stage_personas["Implement code"] == ["coder"]
    assert security_stage_personas["Security review"] == ["security-auditor"]
    assert security_flow.definition_yaml is None

    postmortem_flow = next(
        workflow
        for workflow in workflows
        if workflow.name == "Ting Run Flow + Security + Postmortem Memory"
    )
    postmortem_stage_labels = [
        node["label"] for node in postmortem_flow.graph["nodes"] if node.get("kind") == "stage"
    ]
    assert postmortem_stage_labels == [
        "Implement code",
        "Review changes",
        "Security review",
        "Capture post-mortem",
    ]
    postmortem_edge_labels = {edge["label"] for edge in postmortem_flow.graph["edges"]}
    assert "review.completed -> review.completed" in postmortem_edge_labels
    assert "security.completed -> security.completed" in postmortem_edge_labels
    assert "mimir.source.ingested -> mimir.source.ingested" in postmortem_edge_labels
    postmortem_stage_personas = {
        node["label"]: [member["personaId"] for member in node.get("stageMembers", [])]
        for node in postmortem_flow.graph["nodes"]
        if node.get("kind") == "stage"
    }
    assert postmortem_stage_personas["Capture post-mortem"] == ["postmortem-analyst"]
    resource_nodes = [
        node for node in postmortem_flow.graph["nodes"] if node.get("kind") == "resource"
    ]
    assert resource_nodes == [
        {
            "id": "run-memory",
            "kind": "resource",
            "label": "Shared Mimir Memory",
            "resourceType": "mimir",
            "bindingMode": "registry",
            "registryEntryId": "tmp-mimir-test",
            "path": "/tmp/mimir-test",
            "role": "shared",
            "categories": ["memory", "postmortem"],
            "position": {"x": 560, "y": 472},
        }
    ]
    assert postmortem_flow.definition_yaml is None

    curation_flow = next(
        workflow
        for workflow in workflows
        if workflow.name == "Ting Run Flow + Security + Memory Curation"
    )
    curation_stage_personas = {
        node["label"]: [member["personaId"] for member in node.get("stageMembers", [])]
        for node in curation_flow.graph["nodes"]
        if node.get("kind") == "stage"
    }
    assert curation_stage_personas["Capture post-mortem"] == ["postmortem-analyst"]
    assert curation_stage_personas["Curate shared memory"] == ["mimir-memory-curator"]
    curation_edge_labels = {edge["label"] for edge in curation_flow.graph["edges"]}
    assert "mimir.source.ingested -> mimir.source.ingested" in curation_edge_labels
    assert "mimir.curated -> mimir.curated" in curation_edge_labels
    curator_member = next(
        member
        for node in curation_flow.graph["nodes"]
        if node.get("label") == "Curate shared memory"
        for member in node.get("stageMembers", [])
    )
    assert curator_member["eventFilters"] == {"mount_names": "tmp-mimir-test"}
    assert curation_flow.definition_yaml is None

    research_council = next(
        workflow for workflow in workflows if workflow.name == "Research Council"
    )
    research_stage_personas = {
        node["label"]: [member["personaId"] for member in node.get("stageMembers", [])]
        for node in research_council.graph["nodes"]
        if node.get("kind") == "stage"
    }
    assert research_stage_personas == {
        "Brief the council": ["council-chair"],
        "Seat A first opinion": ["council-member-a"],
        "Seat B first opinion": ["council-member-b"],
        "Build anonymized review packet": ["council-chair"],
        "Seat A review packet": ["council-member-a"],
        "Seat B review packet": ["council-member-b"],
        "Synthesize final decision": ["council-chair"],
    }
    research_edge_labels = {edge["label"] for edge in research_council.graph["edges"]}
    assert "research.requested -> research.requested" in research_edge_labels
    assert "council.briefed -> council.briefed" in research_edge_labels
    assert "council.review.requested -> council.review.requested" in research_edge_labels
    assert "research.completed -> research.completed" in research_edge_labels
    research_resources = {
        node["label"]: node
        for node in research_council.graph["nodes"]
        if node.get("kind") == "resource"
    }
    assert research_resources["Council Scratch Board"]["bindingMode"] == "ephemeral_local"
    assert research_resources["Permanent Research Memory"]["bindingMode"] == "registry"
    assert research_resources["Permanent Research Memory"]["registryEntryId"] == "tmp-mimir-test"
    assert research_resources["Permanent Research Memory"]["path"] == "/tmp/mimir-test"
    research_end = next(
        node for node in research_council.graph["nodes"] if node.get("kind") == "end"
    )
    assert research_end["completionEvent"] == "research.completed"
    assert research_council.definition_yaml is None

    research_council_third = next(
        workflow for workflow in workflows if workflow.name == "Research Council + Third Seat"
    )
    third_stage_personas = {
        node["label"]: [member["personaId"] for member in node.get("stageMembers", [])]
        for node in research_council_third.graph["nodes"]
        if node.get("kind") == "stage"
    }
    assert third_stage_personas["Seat C first opinion"] == ["council-member-c"]
    assert third_stage_personas["Seat C review packet"] == ["council-member-c"]
    third_edge_labels = {edge["label"] for edge in research_council_third.graph["edges"]}
    assert "council.c.opinion.submitted -> council.c.opinion.submitted" in third_edge_labels
    assert "council.c.review.submitted -> council.c.review.submitted" in third_edge_labels
    third_end = next(
        node for node in research_council_third.graph["nodes"] if node.get("kind") == "end"
    )
    assert third_end["completionEvent"] == "research.completed"
    assert research_council_third.definition_yaml is None

    research_council_human = next(
        workflow for workflow in workflows if workflow.name == "Research Council + Human Input"
    )
    human_stage_labels = [
        node["label"]
        for node in research_council_human.graph["nodes"]
        if node.get("kind") == "stage"
    ]
    assert human_stage_labels[-1] == "Synthesize or ask operator"
    human_resources = {
        node["label"]: node
        for node in research_council_human.graph["nodes"]
        if node.get("kind") == "resource"
    }
    assert human_resources["Council Scratch Board"]["bindingMode"] == "ephemeral_local"
    assert human_resources["Permanent Research Memory"]["registryEntryId"] == "tmp-mimir-test"
    human_end = next(
        node for node in research_council_human.graph["nodes"] if node.get("kind") == "end"
    )
    assert human_end["completionEvent"] == "research.completed"
    assert research_council_human.definition_yaml is None


@pytest.mark.asyncio
async def test_seed_system_workflows_upserts_existing_by_name() -> None:
    existing = WorkflowDefinition(
        id=uuid4(),
        name="Ting Run Flow",
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

    assert any(workflow.name == "Ting Run Flow" for workflow in saved)
    current = next(
        workflow
        for workflow in await repo.list_workflows(owner_id="", scope=WorkflowScope.SYSTEM)
        if workflow.name == "Ting Run Flow"
    )
    assert current.id == existing.id
    assert current.version == "1.0.0"
