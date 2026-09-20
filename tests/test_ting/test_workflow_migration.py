from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from ravn.adapters.personas.loader import FilesystemPersonaAdapter
from ravn.domain.persona_document import (
    PersonaDependency,
    PersonaDocumentError,
    PortablePersonaCollection,
    PortablePersonaDefinition,
    persona_revision_for_definition,
)
from ting.adapters.filesystem_workflows import FilesystemWorkflowRepository
from ting.domain.models import WorkflowDefinition, WorkflowScope
from ting.domain.services.workflow_migration import migrate_workflow_catalog
from ting.migrate_workflows import _registry_source_for_workflow
from ting.system_workflows import load_system_workflows


class _Source:
    def __init__(
        self,
        workflows: list[WorkflowDefinition],
        references: set[UUID] | None = None,
    ) -> None:
        self.workflows = workflows
        self.references = references or set()

    async def list_all_workflows(self) -> list[WorkflowDefinition]:
        return self.workflows

    async def referenced_workflow_ids(self) -> set[UUID]:
        return self.references


def _resolver(persona_source):
    async def resolve(_workflow: WorkflowDefinition):
        return persona_source

    return resolve


def _workflow(persona_alias: str = "coder") -> WorkflowDefinition:
    created_at = datetime(2025, 1, 2, 3, 4, tzinfo=UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name="Migrated workflow",
        description="preserved",
        version="1.2.3",
        scope=WorkflowScope.USER,
        owner_id="owner",
        tenant_id="tenant",
        graph={
            "nodes": [
                {"id": "stage", "stageMembers": [{"personaId": persona_alias}]}
            ],
            "edges": [],
        },
        created_at=created_at,
        updated_at=created_at,
    )


@pytest.mark.asyncio
async def test_workflow_migration_dry_run_apply_and_idempotent_rerun(tmp_path) -> None:
    workflow = _workflow()
    source = _Source([workflow], {workflow.id})
    target = FilesystemWorkflowRepository(str(tmp_path))
    personas = FilesystemPersonaAdapter()

    dry_run = await migrate_workflow_catalog(
        source=source,
        target=target,
        persona_source_for_workflow=_resolver(personas),
        bundled_workflows=load_system_workflows(),
    )
    assert dry_run.can_apply is True
    assert dry_run.create_count == 1
    assert await target.get_workflow(workflow.id) is None

    applied = await migrate_workflow_catalog(
        source=source,
        target=target,
        persona_source_for_workflow=_resolver(personas),
        bundled_workflows=load_system_workflows(),
        apply=True,
    )
    assert applied.applied is True
    migrated = await target.get_workflow(workflow.id)
    assert migrated is not None
    assert migrated.owner_id == "owner"
    assert migrated.tenant_id == "tenant"
    assert migrated.created_at == workflow.created_at
    assert migrated.persona_dependencies["coder"].revision.startswith("content-")
    assert migrated.persona_definitions["coder"]["id"] == "coder"
    assert (
        migrated.persona_definitions["coder"]["revision"]
        == migrated.persona_dependencies["coder"].revision
    )
    assert target.has_migration_marker() is True
    assert target.read_migration_marker()["inventory_ids"] == [str(workflow.id)]

    rerun = await migrate_workflow_catalog(
        source=source,
        target=target,
        persona_source_for_workflow=_resolver(personas),
        bundled_workflows=load_system_workflows(),
        apply=True,
    )
    assert rerun.applied is True
    assert rerun.create_count == 0
    assert rerun.unchanged_count == 1


@pytest.mark.asyncio
async def test_workflow_migration_reports_missing_persona_without_writing(tmp_path) -> None:
    workflow = _workflow("persona-that-does-not-exist")
    target = FilesystemWorkflowRepository(str(tmp_path))
    report = await migrate_workflow_catalog(
        source=_Source([workflow]),
        target=target,
        persona_source_for_workflow=_resolver(FilesystemPersonaAdapter()),
        bundled_workflows=load_system_workflows(),
        apply=True,
    )

    assert report.applied is False
    assert "missing persona" in report.errors[0]
    assert await target.get_workflow(workflow.id) is None
    assert target.has_migration_marker() is False


@pytest.mark.asyncio
async def test_migrated_workflow_delete_records_explicit_tombstone(tmp_path) -> None:
    workflow = _workflow()
    target = FilesystemWorkflowRepository(str(tmp_path))
    await migrate_workflow_catalog(
        source=_Source([workflow]),
        target=target,
        persona_source_for_workflow=_resolver(FilesystemPersonaAdapter()),
        bundled_workflows=load_system_workflows(),
        apply=True,
    )

    assert await target.delete_workflow(workflow.id) is True
    assert target.read_migration_marker()["deleted_ids"] == [str(workflow.id)]


@pytest.mark.asyncio
async def test_divergent_bundled_row_requires_explicit_same_id_replacement(tmp_path) -> None:
    packaged = load_system_workflows()[0]
    divergent = replace(
        packaged,
        description="Administrator-owned divergent definition",
        read_only=False,
        source="postgres",
    )
    target = FilesystemWorkflowRepository(str(tmp_path))

    preview = await migrate_workflow_catalog(
        source=_Source([divergent]),
        target=target,
        persona_source_for_workflow=_resolver(FilesystemPersonaAdapter()),
        bundled_workflows=load_system_workflows(),
    )
    assert preview.can_apply is False
    assert "diverges" in preview.errors[0]

    applied = await migrate_workflow_catalog(
        source=_Source([divergent]),
        target=target,
        persona_source_for_workflow=_resolver(FilesystemPersonaAdapter()),
        bundled_workflows=load_system_workflows(),
        apply=True,
        replace_divergent_bundled=True,
    )
    assert applied.applied is True
    migrated = await target.get_workflow(divergent.id)
    assert migrated is not None
    assert migrated.id == packaged.id
    assert migrated.description == divergent.description
    assert migrated.read_only is False

    rerun = await migrate_workflow_catalog(
        source=_Source([divergent]),
        target=target,
        persona_source_for_workflow=_resolver(FilesystemPersonaAdapter()),
        bundled_workflows=load_system_workflows(),
        apply=True,
        replace_divergent_bundled=True,
    )
    assert rerun.applied is True
    assert rerun.create_count == 0
    assert rerun.unchanged_count == 1


@pytest.mark.asyncio
async def test_bundled_uuid_with_user_ownership_is_not_silently_skipped(tmp_path) -> None:
    packaged = load_system_workflows()[0]
    collision = replace(
        packaged,
        scope=WorkflowScope.USER,
        owner_id="alice",
        tenant_id="tenant-a",
        read_only=False,
        source="postgres",
    )

    report = await migrate_workflow_catalog(
        source=_Source([collision]),
        target=FilesystemWorkflowRepository(str(tmp_path)),
        persona_source_for_workflow=_resolver(FilesystemPersonaAdapter()),
        bundled_workflows=load_system_workflows(),
    )

    assert report.can_apply is False
    assert "diverges from its packaged definition" in report.errors[0]


@pytest.mark.asyncio
async def test_workflow_migration_resolves_personas_in_each_owner_scope(tmp_path) -> None:
    alice_workflow = replace(_workflow("reviewer"), owner_id="alice")
    bob_workflow = replace(_workflow("reviewer"), owner_id="bob")
    base = FilesystemPersonaAdapter(
        persona_dirs=[], include_builtin=True
    ).load_current_portable("reviewer")
    assert base is not None

    def owner_document(owner: str) -> PortablePersonaDefinition:
        definition = dict(base.definition)
        definition["system_prompt_template"] = f"Private reviewer for {owner}"
        return PortablePersonaDefinition(
            id="reviewer",
            revision=persona_revision_for_definition(definition),
            definition=definition,
        )

    owner_sources = {
        owner: PortablePersonaCollection([owner_document(owner)])
        for owner in ("alice", "bob")
    }

    async def source_for_workflow(workflow: WorkflowDefinition):
        assert workflow.owner_id is not None
        return owner_sources[workflow.owner_id]

    target = FilesystemWorkflowRepository(str(tmp_path))
    report = await migrate_workflow_catalog(
        source=_Source([alice_workflow, bob_workflow]),
        target=target,
        persona_source_for_workflow=source_for_workflow,
        bundled_workflows=load_system_workflows(),
        apply=True,
    )

    assert report.applied is True
    alice = await target.get_workflow(alice_workflow.id)
    bob = await target.get_workflow(bob_workflow.id)
    assert alice is not None and bob is not None
    assert alice.persona_definitions["reviewer"] != bob.persona_definitions["reviewer"]
    assert (
        alice.persona_definitions["reviewer"]["definition"]["system_prompt_template"]
        == "Private reviewer for alice"
    )
    assert (
        bob.persona_definitions["reviewer"]["definition"]["system_prompt_template"]
        == "Private reviewer for bob"
    )


@pytest.mark.asyncio
async def test_workflow_migration_rejects_matching_revision_with_wrong_digest(
    tmp_path,
) -> None:
    personas = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True)
    portable = personas.load_current_portable("coder")
    assert portable is not None
    workflow = replace(
        _workflow(),
        persona_dependencies={
            "coder": PersonaDependency(
                id=portable.id,
                revision=portable.revision,
                digest="sha256:" + "0" * 64,
            )
        },
    )

    report = await migrate_workflow_catalog(
        source=_Source([workflow]),
        target=FilesystemWorkflowRepository(str(tmp_path)),
        persona_source_for_workflow=_resolver(personas),
        bundled_workflows=load_system_workflows(),
    )

    assert report.can_apply is False
    assert "expected sha256:0000" in report.errors[0]


@pytest.mark.asyncio
async def test_workflow_migration_reports_invalid_resolved_source(tmp_path) -> None:
    async def invalid_source(_workflow: WorkflowDefinition):
        raise PersonaDocumentError("duplicate persona revision")

    report = await migrate_workflow_catalog(
        source=_Source([_workflow()]),
        target=FilesystemWorkflowRepository(str(tmp_path)),
        persona_source_for_workflow=invalid_source,
        bundled_workflows=load_system_workflows(),
    )

    assert report.can_apply is False
    assert "persona source is invalid: duplicate persona revision" in report.errors[0]


@pytest.mark.asyncio
async def test_registry_source_hydrates_workflow_owner_only() -> None:
    portable = FilesystemPersonaAdapter(
        persona_dirs=[], include_builtin=True
    ).load_current_portable("reviewer")
    assert portable is not None
    registry = SimpleNamespace(
        get_current_portable_persona=AsyncMock(return_value=portable)
    )
    workflow = replace(_workflow("reviewer"), owner_id="alice")

    source = await _registry_source_for_workflow(
        workflow,
        registry,
        FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True),
    )

    registry.get_current_portable_persona.assert_awaited_once_with("alice", "reviewer")
    assert source.load_current_portable("reviewer") == portable
