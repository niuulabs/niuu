from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import yaml

from ting.adapters.filesystem_workflows import FilesystemWorkflowRepository
from ting.config import Settings, WorkflowRepositoryConfig
from ting.domain.exceptions import (
    WorkflowConflictError,
    WorkflowDocumentError,
    WorkflowReadOnlyError,
)
from ting.domain.models import WorkflowDefinition, WorkflowScope
from ting.domain.workflow_document import dump_workflow_document
from ting.system_workflows import load_system_workflows


def _workflow(*, owner_id: str = "owner") -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name="Editable workflow",
        description="",
        version="draft",
        scope=WorkflowScope.USER,
        owner_id=owner_id,
        graph={"nodes": [], "edges": [], "custom": {"preserved": True}},
        created_at=now,
        updated_at=now,
        tenant_id="tenant",
        persona_definitions={"reviewer": {"schema_version": 1}},
        requirements=[{"kind": "resource", "id": "memory", "resolved": False}],
    )


@pytest.mark.asyncio
async def test_filesystem_repository_crud_and_external_reload(tmp_path) -> None:
    repo = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    saved = await repo.save_workflow(_workflow())

    assert saved.revision is not None
    assert (await repo.list_workflows(owner_id="other")) == []
    loaded = await repo.get_workflow(saved.id)
    assert loaded == saved
    assert loaded.persona_definitions == {"reviewer": {"schema_version": 1}}
    assert loaded.requirements[0]["resolved"] is False

    edited = replace(saved, description="changed outside the process", revision=None)
    document_path = next(path for path in tmp_path.glob("*.yaml") if not path.name.startswith("."))
    renamed_path = tmp_path / "human-readable-name.yml"
    document_path.rename(renamed_path)
    renamed_path.write_text(dump_workflow_document(edited), encoding="utf-8")
    reloaded = await repo.get_workflow(saved.id)
    assert reloaded is not None
    assert reloaded.description == "changed outside the process"
    assert reloaded.revision != saved.revision

    updated = await repo.save_workflow(
        replace(
            reloaded,
            requirements=[{"kind": "resource", "id": "memory", "resolved": True}],
        )
    )
    assert updated.source == "local:human-readable-name.yml"
    assert renamed_path.is_file()
    assert updated.revision != reloaded.revision
    with pytest.raises(WorkflowConflictError):
        await repo.save_workflow(replace(reloaded, name="stale aggregate update"))

    with pytest.raises(WorkflowDocumentError, match="graph nodes must be a list"):
        await repo.save_workflow(replace(updated, graph={"nodes": "invalid", "edges": []}))
    assert (await repo.get_workflow(saved.id)).requirements[0]["resolved"] is True

    assert await repo.delete_workflow(saved.id) is True
    assert await repo.get_workflow(saved.id) is None


@pytest.mark.asyncio
async def test_filesystem_repository_rejects_stale_concurrent_write(tmp_path) -> None:
    first = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    second = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    saved = await first.save_workflow(_workflow())

    results = await asyncio.gather(
        first.save_workflow(replace(saved, name="first")),
        second.save_workflow(replace(saved, name="second")),
        return_exceptions=True,
    )

    assert sum(isinstance(result, WorkflowDefinition) for result in results) == 1
    assert sum(isinstance(result, WorkflowConflictError) for result in results) == 1


@pytest.mark.asyncio
async def test_bundled_workflows_are_read_only(tmp_path) -> None:
    bundled_path = tmp_path / "bundled"
    catalog_path = tmp_path / "catalog"
    bundled_path.mkdir()
    catalog_path.mkdir()
    bundled = load_system_workflows()[0]
    (bundled_path / "workflow.yaml").write_text(
        dump_workflow_document(bundled), encoding="utf-8"
    )
    repo = FilesystemWorkflowRepository(str(catalog_path), str(bundled_path))

    loaded = await repo.get_workflow(bundled.id)
    assert loaded is not None
    assert loaded.read_only is True
    with pytest.raises(WorkflowReadOnlyError):
        await repo.save_workflow(replace(loaded, name="edited"))
    with pytest.raises(WorkflowReadOnlyError):
        await repo.delete_workflow(loaded.id)


def test_filesystem_repository_requires_existing_writable_catalog(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="does not exist"):
        FilesystemWorkflowRepository(str(tmp_path / "missing"), include_bundled=False)


def test_filesystem_repository_is_default_but_explicit_adapter_has_plain_kwargs() -> None:
    settings = Settings()
    assert settings.workflow_repository.adapter.endswith("FilesystemWorkflowRepository")
    assert settings.workflow_repository.kwargs == {
        "catalog_path": "~/.niuu/workflows",
        "create_directory": True,
    }
    postgres = WorkflowRepositoryConfig(
        adapter="ting.adapters.postgres_workflows.PostgresWorkflowRepository"
    )
    assert postgres.kwargs == {}


@pytest.mark.asyncio
async def test_filesystem_repository_recovers_partial_write_after_restart(
    tmp_path, monkeypatch
) -> None:
    repo = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    workflow = _workflow()
    original = FilesystemWorkflowRepository._atomic_replace
    interrupted = False

    def fail_before_document_publish(path, content):
        nonlocal interrupted
        if path.parent == tmp_path and path.suffix in {".yaml", ".yml"} and not interrupted:
            interrupted = True
            raise OSError("simulated process interruption")
        original(path, content)

    monkeypatch.setattr(
        FilesystemWorkflowRepository,
        "_atomic_replace",
        staticmethod(fail_before_document_publish),
    )
    with pytest.raises(OSError, match="simulated process interruption"):
        await repo.save_workflow(workflow)

    assert next((tmp_path / ".transactions").glob("*.yaml")).is_file()
    assert (tmp_path / ".metadata" / f"{workflow.id}.yaml").is_file()
    assert not list(tmp_path.glob("*.yaml"))

    monkeypatch.setattr(
        FilesystemWorkflowRepository,
        "_atomic_replace",
        staticmethod(original),
    )
    restarted = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    recovered = await restarted.get_workflow(workflow.id)
    assert recovered is not None
    assert recovered.name == workflow.name
    assert not list((tmp_path / ".transactions").glob("*.yaml"))


@pytest.mark.asyncio
async def test_filesystem_repository_recovers_partial_delete_after_restart(tmp_path) -> None:
    repo = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    saved = await repo.save_workflow(_workflow())
    await repo.mark_migration_complete(
        {"inventory_ids": [str(saved.id)], "deleted_ids": []}
    )
    document_path = next(
        path for path in tmp_path.glob("*.yaml") if not path.name.startswith(".")
    )
    metadata_path = tmp_path / ".metadata" / f"{saved.id}.yaml"
    journal_path = tmp_path / ".transactions" / f"{saved.id}.yaml"
    journal_path.write_text(
        yaml.safe_dump(
            {
                "operation": "delete",
                "id": str(saved.id),
                "document_name": document_path.name,
            }
        ),
        encoding="utf-8",
    )
    document_path.unlink()
    assert metadata_path.is_file()

    restarted = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    assert await restarted.get_workflow(saved.id) is None
    assert not metadata_path.exists()
    assert not journal_path.exists()
    assert restarted.read_migration_marker()["deleted_ids"] == [str(saved.id)]


@pytest.mark.asyncio
async def test_filesystem_repository_rejects_duplicate_id_and_malformed_metadata(
    tmp_path,
) -> None:
    repo = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    saved = await repo.save_workflow(_workflow())
    document_path = next(tmp_path.glob("*.yaml"))
    duplicate_path = tmp_path / "renamed-copy.yml"
    duplicate_path.write_text(document_path.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(WorkflowDocumentError, match="Duplicate workflow id"):
        await repo.get_workflow(saved.id)

    duplicate_path.unlink()
    metadata_path = tmp_path / ".metadata" / f"{saved.id}.yaml"
    metadata_path.write_text("id: wrong\n", encoding="utf-8")
    with pytest.raises(WorkflowDocumentError, match="Invalid workflow metadata"):
        await repo.get_workflow(saved.id)


@pytest.mark.asyncio
async def test_filesystem_repository_rejects_cross_workflow_delete_journal(tmp_path) -> None:
    repo = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    saved = await repo.save_workflow(_workflow())
    document_path = next(
        path for path in tmp_path.glob("*.yaml") if not path.name.startswith(".")
    )
    other_id = uuid4()
    journal_path = tmp_path / ".transactions" / f"{other_id}.yaml"
    journal_path.write_text(
        yaml.safe_dump(
            {
                "operation": "delete",
                "id": str(other_id),
                "document_name": document_path.name,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowDocumentError, match="targets document"):
        FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    assert document_path.is_file()
    assert (tmp_path / ".metadata" / f"{saved.id}.yaml").is_file()


def test_filesystem_repository_rejects_control_file_journal_path(tmp_path) -> None:
    repo = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    workflow_id = uuid4()
    journal_path = tmp_path / ".transactions" / f"{workflow_id}.yaml"
    journal_path.write_text(
        yaml.safe_dump(
            {
                "operation": "delete",
                "id": str(workflow_id),
                "document_name": ".migration-complete.yaml",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowDocumentError, match="Malformed workflow transaction path"):
        repo._recover_transactions()
