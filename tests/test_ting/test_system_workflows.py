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
        # Ids with no recorded version history, i.e. never touched by
        # save_workflow/adopt_legacy_bundled -- a legacy row. Empty by
        # default so every existing fixture keeps today's strict-comparison
        # behaviour; tests that need the legacy path add to this set.
        self.never_versioned_ids: set = set()

    async def has_recorded_version_history(self, workflow_id) -> bool:
        return workflow_id not in self.never_versioned_ids

    async def adopt_legacy_bundled(self, seed: WorkflowDefinition) -> WorkflowDefinition:
        current = self._workflows.get(seed.id)
        successor = replace(
            seed,
            created_at=current.created_at if current is not None else seed.created_at,
            revision=current.revision if current is not None else None,
            read_only=True,
            origin="bundled",
            source="postgres",
        )
        self.never_versioned_ids.discard(seed.id)
        return await self.save_workflow(successor)

    async def reclassify_orphaned_bundled_as_authored(self, workflow_id):
        # No save_workflow call -- no archive, no version bump, matching the
        # real adapters' guarded-UPDATE-only contract.
        current = self._workflows.get(workflow_id)
        if current is None:
            return None
        updated = replace(current, origin="authored")
        self._workflows[workflow_id] = updated
        return updated

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


@pytest.mark.asyncio
async def test_seed_reclassified_legacy_row_matching_package_is_replaced_not_compared(
    monkeypatch,
) -> None:
    """Mirrors a real pre-#1012 system row after migration 000046.

    000046 reclassifies such a row's version_origin from 'authored' to
    'bundled', but the row itself was never touched by the post-#1012
    versioned save path: no persona pins, schema_version defaulted to 1, and
    (mirrored here via has_recorded_version_history returning False) no
    workflow_versions entry. Even though its visible content (name,
    description, version, graph) matches the package exactly -- one of the
    real "6 matching rows" -- comparing its stale shape by document revision
    would always disagree on the missing pins/schema_version and wrongly
    raise "changed content". It must instead be replaced outright, and a
    second pass (now with a recorded version history) must be a no-op.
    """
    seed = next(workflow for workflow in load_system_workflows() if workflow.persona_dependencies)
    legacy = replace(
        seed,
        persona_dependencies={},
        persona_definitions={},
        schema_version=1,
        based_on_revision=None,
        document_revision=None,
    )
    repo = _InMemoryWorkflowRepository([legacy])
    repo.never_versioned_ids.add(seed.id)
    monkeypatch.setattr("ting.system_workflows.load_system_workflows", lambda _path: [seed])

    saved = await seed_system_workflows(repo)

    assert repo.save_calls == saved
    assert saved[0].id == seed.id
    assert saved[0].version == seed.version
    assert saved[0].graph == seed.graph
    assert saved[0].persona_dependencies == seed.persona_dependencies
    assert saved[0].schema_version == seed.schema_version
    assert saved[0].origin == "bundled"
    assert saved[0].read_only is True

    repo.save_calls.clear()

    rerun = await seed_system_workflows(repo)

    assert repo.save_calls == []
    assert rerun[0].id == seed.id


@pytest.mark.asyncio
async def test_seed_reclassified_legacy_research_campaign_reseeds_current_version(
    monkeypatch,
) -> None:
    """The real Research Campaign case: a legacy 1.0.0 row, package now 2.0.0.

    A version mismatch already took the reseed branch before this fix (it
    never reached the strict content-hash comparison), so this locks in that
    a 000046-reclassified row at a stale version still reseeds correctly to
    the current packaged definition, and that a second pass is a no-op.
    """
    seed = next(
        workflow for workflow in load_system_workflows() if workflow.name == "Research Campaign"
    )
    assert seed.version == "2.0.0"
    legacy = replace(
        seed,
        version="1.0.0",
        graph={
            "nodes": [{"id": "research-explore", "kind": "stage", "stageMembers": []}],
            "edges": [],
        },
        persona_dependencies={},
        persona_definitions={},
        schema_version=1,
        based_on_revision=None,
        document_revision=None,
    )
    repo = _InMemoryWorkflowRepository([legacy])
    repo.never_versioned_ids.add(seed.id)
    monkeypatch.setattr("ting.system_workflows.load_system_workflows", lambda _path: [seed])

    saved = await seed_system_workflows(repo)

    assert repo.save_calls == saved
    assert saved[0].id == seed.id
    assert saved[0].version == "2.0.0"
    assert saved[0].graph == seed.graph
    assert saved[0].origin == "bundled"

    repo.save_calls.clear()

    rerun = await seed_system_workflows(repo)

    assert repo.save_calls == []
    assert rerun[0].version == "2.0.0"


@pytest.mark.asyncio
async def test_seed_reclassifies_orphaned_never_versioned_row_as_authored(
    monkeypatch, caplog
) -> None:
    """A 000046-reclassified row with no matching packaged id is not deleted.

    Migration 000046 cannot distinguish a legacy packaged row from an
    admin-created system row at the SQL level; the seeding pass corrects
    that here for ids that turn out not to be in the current package,
    flipping them back to 'authored' and logging a warning instead of
    silently dropping or crashing on them.
    """
    seed = load_system_workflows()[0]
    orphan_id = uuid4()
    orphan = replace(
        seed,
        id=orphan_id,
        name="Admin custom system workflow",
        based_on_revision=None,
        document_revision=None,
    )
    # seed itself is already present and matching, so the main per-seed loop
    # makes no save calls of its own -- isolating the orphan-only assertion.
    repo = _InMemoryWorkflowRepository([orphan, seed])
    repo.never_versioned_ids.add(orphan_id)
    monkeypatch.setattr("ting.system_workflows.load_system_workflows", lambda _path: [seed])

    with caplog.at_level("WARNING"):
        await seed_system_workflows(repo)

    reclassified = await repo.get_workflow(orphan_id)
    assert reclassified is not None
    assert reclassified.origin == "authored"
    assert str(orphan_id) in caplog.text

    # A second pass leaves it alone: it is now 'authored'.
    repo.save_calls.clear()
    await seed_system_workflows(repo)
    assert repo.save_calls == []
