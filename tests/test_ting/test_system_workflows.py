from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ting.domain.exceptions import WorkflowDocumentError
from ting.domain.models import WorkflowDefinition, WorkflowScope
from ting.domain.workflow_document import workflow_document_revision
from ting.ports.workflow_repository import WorkflowRepository
from ting.system_workflows import load_system_workflows, seed_system_workflows


class _InMemoryWorkflowRepository(WorkflowRepository):
    def __init__(self, workflows: list[WorkflowDefinition] | None = None) -> None:
        self._workflows = {workflow.id: workflow for workflow in workflows or []}
        self.save_calls: list[WorkflowDefinition] = []

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
        self.save_calls.append(workflow)
        self._workflows[workflow.id] = workflow
        return workflow

    async def list_workflow_versions(self, workflow_id):
        return []

    async def get_workflow_version(self, workflow_id, *, version=None, document_revision=None):
        workflow = await self.get_workflow(workflow_id)
        return workflow if workflow is not None and workflow.version == version else None

    async def save_workflow_version(self, workflow, **kwargs):
        raise NotImplementedError

    async def delete_workflow(self, workflow_id) -> bool:
        return self._workflows.pop(workflow_id, None) is not None


def test_load_system_workflows_only_keeps_supported_catalog() -> None:
    workflows = load_system_workflows()

    names = {workflow.name for workflow in workflows}
    assert names == {
        "Ting Run Flow + Security + Memory Curation",
        "Research Campaign",
        "Research Thread",
        "Research Thread — Breadth",
        "Research Thread — Depth",
        "Research Thread — Contrarian",
        "Saga Planning",
        "Specification Stack",
        "Tracker Delivery Flow",
        "Code & Review Flow",
        "Tool & Skill Builder",
        "Developer Delivery",
        "Developer Planning",
        "Developer Workstream",
        "Developer Integration",
    }

    run_flow = next(
        workflow
        for workflow in workflows
        if workflow.name == "Ting Run Flow + Security + Memory Curation"
    )
    assert run_flow.scope == WorkflowScope.SYSTEM
    assert run_flow.owner_id is None
    assert set(run_flow.persona_definitions) == set(run_flow.persona_dependencies)
    assert all(
        run_flow.persona_definitions[alias]["revision"] == dependency.revision
        for alias, dependency in run_flow.persona_dependencies.items()
    )
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
    assert research_resources["Research Memory"]["url"] == (
        "https://mimir.yggdrasil.niuu.world/api/v1"
    )
    assert research_resources["Research Memory"]["authRef"] == "integration:volundr"

    planning_flow = next(workflow for workflow in workflows if workflow.name == "Saga Planning")
    assert {"planning", "saga"}.issubset(set(planning_flow.graph["tags"]))
    planning_stage_labels = [
        node["label"] for node in planning_flow.graph["nodes"] if node.get("kind") == "stage"
    ]
    assert planning_stage_labels == [
        "Clarify brief",
        "Draft saga breakdown",
        "Review saga breakdown",
        "Publish planning draft",
    ]
    planning_gate_labels = [
        node["label"] for node in planning_flow.graph["nodes"] if node.get("kind") == "gate"
    ]
    assert planning_gate_labels == ["Planning feedback gate", "Draft plan review gate"]
    planning_stage_personas = {
        node["label"]: [member["personaId"] for member in node.get("stageMembers", [])]
        for node in planning_flow.graph["nodes"]
        if node.get("kind") == "stage"
    }
    assert planning_stage_personas["Clarify brief"] == ["saga-brief-framer"]
    assert planning_stage_personas["Draft saga breakdown"] == ["saga-planner"]
    assert planning_stage_personas["Review saga breakdown"] == ["saga-plan-reviewer"]
    assert planning_stage_personas["Publish planning draft"] == ["saga-plan-publisher"]
    planning_edge_labels = {edge.get("label") for edge in planning_flow.graph["edges"]}
    assert None not in planning_edge_labels
    assert "plan.brief.framed -> plan.brief.framed" in planning_edge_labels
    assert "plan.brief.approved -> plan.brief.approved" in planning_edge_labels
    assert "plan.breakdown.drafted -> plan.breakdown.drafted" in planning_edge_labels
    assert "plan.breakdown.ready_for_gate -> plan.breakdown.ready_for_gate" in (
        planning_edge_labels
    )
    assert "plan.approved -> plan.approved" in planning_edge_labels
    planning_resources = {
        node["label"]: node
        for node in planning_flow.graph["nodes"]
        if node.get("kind") == "resource"
    }
    assert planning_resources["Planning Memory"]["bindingMode"] == "registry"
    assert planning_resources["Planning Memory"]["url"] == (
        "https://mimir.yggdrasil.niuu.world/api/v1"
    )
    assert planning_resources["Planning Memory"]["authRef"] == "integration:volundr"

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
    assert specification_resources["Specification Memory"]["url"] == (
        "https://mimir.yggdrasil.niuu.world/api/v1"
    )
    assert specification_resources["Specification Memory"]["authRef"] == "integration:volundr"

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
    assert [
        member["model"]
        for node in delivery_flow.graph["nodes"]
        if node.get("kind") == "stage"
        for member in node["stageMembers"]
    ] == ["gpt-5.6-terra"] * 4
    delivery_edge_labels = {edge["label"] for edge in delivery_flow.graph["edges"]}
    assert "review.changes_requested -> review.changes_requested" in delivery_edge_labels
    assert "review.passed -> review.passed" in delivery_edge_labels
    delivery_resources = {
        node["label"]: node
        for node in delivery_flow.graph["nodes"]
        if node.get("kind") == "resource"
    }
    assert delivery_resources["Delivery Memory"]["url"] == (
        "https://mimir.yggdrasil.niuu.world/api/v1"
    )
    assert delivery_resources["Delivery Memory"]["authRef"] == "workload:mimir"

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
    assert code_review_resources["Delivery Memory"]["url"] == (
        "https://mimir.yggdrasil.niuu.world/api/v1"
    )
    assert code_review_resources["Delivery Memory"]["authRef"] == "integration:volundr"

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
    assert builder_stage_personas["Publish capability record"] == ["capability-publisher"]
    assert builder_flow.graph["artifactPaths"] == ["capabilities/{slug}/learned_tool.json"]
    builder_members = {
        member["personaId"]: member
        for node in builder_flow.graph["nodes"]
        if node.get("kind") == "stage"
        for member in node.get("stageMembers", [])
    }
    assert "do not initialize Git" in builder_members["coder"]["systemPromptExtra"]
    assert (
        "Do not require or invent a Git checkpoint"
        in (builder_members["reviewer"]["systemPromptExtra"])
    )
    assert (
        "Do not require or invent a Git checkpoint"
        in (builder_members["security-auditor"]["systemPromptExtra"])
    )
    builder_edge_labels = {edge["label"] for edge in builder_flow.graph["edges"]}
    assert {
        "spec.framed -> spec.framed",
        "review.passed -> review.passed",
        "security.passed -> security.passed",
        "review.changes_requested -> review.changes_requested",
        "security.changes_requested -> security.changes_requested",
        "capability.ready -> capability.ready",
    }.issubset(builder_edge_labels)
    builder_resources = {
        node["label"]: node
        for node in builder_flow.graph["nodes"]
        if node.get("kind") == "resource"
    }
    assert builder_resources["Capability Memory"]["bindingMode"] == "registry"
    assert builder_resources["Capability Memory"]["url"] == (
        "https://mimir.yggdrasil.niuu.world/api/v1"
    )
    assert builder_resources["Capability Memory"]["authRef"] == "integration:volundr"
    builder_binding = next(
        binding
        for binding in builder_flow.graph["resourceBindings"]
        if binding["id"] == "binding-capability-memory"
    )
    assert "specifications/" in builder_binding["writePrefixes"]


def test_load_system_workflows_rejects_missing_bundle_directory(tmp_path) -> None:
    with pytest.raises(WorkflowDocumentError, match="directory does not exist"):
        load_system_workflows(tmp_path / "missing")


def test_load_system_workflows_rejects_empty_bundle_directory(tmp_path) -> None:
    with pytest.raises(WorkflowDocumentError, match="directory is empty"):
        load_system_workflows(tmp_path)


@pytest.mark.asyncio
async def test_seed_system_workflows_handles_empty_seed_set(monkeypatch) -> None:
    repo = _InMemoryWorkflowRepository()
    monkeypatch.setattr("ting.system_workflows.load_system_workflows", lambda _path: [])

    assert await seed_system_workflows(repo) == []
    assert repo.save_calls == []


@pytest.mark.asyncio
async def test_seed_system_workflows_preserves_authored_and_obsolete_entries() -> None:
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
        "Research Thread",
        "Research Thread — Breadth",
        "Research Thread — Depth",
        "Research Thread — Contrarian",
        "Saga Planning",
        "Specification Stack",
        "Tracker Delivery Flow",
        "Code & Review Flow",
        "Tool & Skill Builder",
        "Developer Delivery",
        "Developer Planning",
        "Developer Workstream",
        "Developer Integration",
    }

    current_catalog = await repo.list_workflows(owner_id="", scope=WorkflowScope.SYSTEM)
    assert {workflow.name for workflow in current_catalog} == names | {obsolete.name}
    assert len(current_catalog) == len(seeds) + len([duplicate, obsolete])
    assert obsolete in current_catalog


@pytest.mark.asyncio
async def test_seed_system_workflows_is_idempotent_for_identical_bundle(monkeypatch) -> None:
    seed = load_system_workflows()[0]
    repo = _InMemoryWorkflowRepository([seed])
    monkeypatch.setattr("ting.system_workflows.load_system_workflows", lambda _path: [seed])

    saved = await seed_system_workflows(repo)

    assert saved == [seed]
    assert repo.save_calls == []


@pytest.mark.asyncio
async def test_seed_system_workflows_preserves_authored_successor(monkeypatch) -> None:
    seed = load_system_workflows()[0]
    authored = replace(
        seed,
        version="9.0.0",
        origin="authored",
        read_only=False,
        graph={"nodes": [{"id": "operator-edit", "kind": "stage"}], "edges": []},
    )
    repo = _InMemoryWorkflowRepository([authored])
    monkeypatch.setattr("ting.system_workflows.load_system_workflows", lambda _path: [seed])

    saved = await seed_system_workflows(repo)

    assert saved == [authored]
    assert repo.save_calls == []


@pytest.mark.asyncio
async def test_seed_system_workflows_rejects_reused_changed_bundle_version(monkeypatch) -> None:
    seed = load_system_workflows()[0]
    changed = replace(
        seed,
        graph={"nodes": [{"id": "changed", "kind": "stage"}], "edges": []},
        document_revision=None,
    )
    changed = replace(changed, document_revision=workflow_document_revision(changed))
    repo = _InMemoryWorkflowRepository([changed])
    monkeypatch.setattr("ting.system_workflows.load_system_workflows", lambda _path: [seed])

    with pytest.raises(WorkflowDocumentError, match="changed content"):
        await seed_system_workflows(repo)

    assert repo.save_calls == []


@pytest.mark.asyncio
async def test_seed_system_workflows_records_distinct_bundle_upgrade(monkeypatch) -> None:
    seed = load_system_workflows()[0]
    previous = replace(
        seed,
        version="0.9.0",
        created_at=seed.created_at - timedelta(days=30),
        updated_at=seed.updated_at - timedelta(days=30),
        document_revision=None,
    )
    previous = replace(previous, document_revision=workflow_document_revision(previous))
    repo = _InMemoryWorkflowRepository([previous])
    monkeypatch.setattr("ting.system_workflows.load_system_workflows", lambda _path: [seed])

    saved = await seed_system_workflows(repo)

    assert saved[0].version == seed.version
    assert saved[0].origin == "bundled"
    assert saved[0].created_at == previous.created_at
    assert repo.save_calls == saved
