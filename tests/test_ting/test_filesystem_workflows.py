from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import yaml

from identity.ports import AuthorizationDeniedError
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
async def test_immutable_versions_preserve_complete_snapshots_across_restart(tmp_path) -> None:
    repo = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    original = await repo.save_workflow(_workflow())
    assert original.document_revision is not None
    assert len(list((tmp_path / ".history" / str(original.id)).glob("*.json"))) == 1

    successor = await repo.save_workflow_version(
        replace(
            original,
            description="second revision",
            persona_definitions={"reviewer": {"schema_version": 2}},
            workflow_definitions={"child": {"schema_version": 2}},
            requirements=[{"kind": "resource", "id": "forge", "resolved": True}],
        ),
        expected_revision=original.revision,
        base_revision=original.document_revision,
    )

    assert successor.id == original.id
    assert successor.version == "0.1.0"
    assert successor.document_revision != original.document_revision
    assert successor.based_on_revision == original.document_revision
    assert successor.revision != original.revision
    versions = await repo.list_workflow_versions(original.id)
    assert [(item.version, item.is_head) for item in versions] == [
        ("0.1.0", True),
        ("draft", False),
    ]

    restarted = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    historical = await restarted.get_workflow_version(original.id, version="draft")
    assert historical is not None
    assert historical.description == original.description
    assert historical.persona_definitions == original.persona_definitions
    assert historical.workflow_definitions == original.workflow_definitions
    assert historical.requirements == original.requirements
    assert historical.document_revision == original.document_revision
    assert historical.revision == successor.revision
    assert historical.is_head is False
    assert historical.read_only is True

    current = await restarted.get_workflow_version(
        original.id, document_revision=successor.document_revision
    )
    assert current == await restarted.get_workflow(original.id)
    assert current is not None
    assert current.is_head is True


@pytest.mark.asyncio
async def test_version_save_can_branch_from_history_but_requires_current_head_cas(tmp_path) -> None:
    repo = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    original = await repo.save_workflow(_workflow())
    first = await repo.save_workflow_version(
        replace(original, name="first successor"),
        expected_revision=original.revision,
        base_revision=original.document_revision,
    )

    with pytest.raises(WorkflowConflictError, match="changed after it was read"):
        await repo.save_workflow_version(
            replace(original, name="stale successor"),
            expected_revision=original.revision,
            base_revision=original.document_revision,
        )
    with pytest.raises(WorkflowConflictError, match="base revision"):
        await repo.save_workflow_version(
            replace(first, name="missing base"),
            expected_revision=first.revision,
            base_revision="sha256:" + "0" * 64,
        )

    branched = await repo.save_workflow_version(
        replace(first, name="branched from draft"),
        expected_revision=first.revision,
        base_revision=original.document_revision,
        bump="minor",
    )
    assert branched.version == "0.2.0"
    assert branched.based_on_revision == original.document_revision
    assert [item.version for item in await repo.list_workflow_versions(original.id)] == [
        "0.2.0",
        "0.1.0",
        "draft",
    ]


@pytest.mark.asyncio
async def test_version_and_legacy_saves_reject_identity_changes_and_deleted_head(tmp_path) -> None:
    repo = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    original = await repo.save_workflow(_workflow())

    with pytest.raises(AuthorizationDeniedError, match="ownership and scope are immutable"):
        await repo.save_workflow(replace(original, scope=WorkflowScope.SYSTEM))
    with pytest.raises(AuthorizationDeniedError, match="ownership and scope are immutable"):
        await repo.save_workflow_version(
            replace(original, scope=WorkflowScope.SYSTEM),
            expected_revision=original.revision,
            base_revision=original.document_revision,
        )

    assert await repo.delete_workflow(original.id) is True
    with pytest.raises(WorkflowConflictError, match="no longer exists"):
        await repo.save_workflow(original)
    with pytest.raises(WorkflowConflictError, match="no current head"):
        await repo.save_workflow_version(
            original,
            expected_revision=original.revision,
            base_revision=original.document_revision,
        )
    assert await repo.delete_workflow(original.id) is False
    assert await repo.list_workflow_versions(original.id) == []
    assert await repo.get_workflow_version(original.id, version="draft") is None


@pytest.mark.asyncio
async def test_version_lookup_rejects_ambiguous_selector_and_returns_missing(tmp_path) -> None:
    repo = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    original = await repo.save_workflow(_workflow())
    with pytest.raises(ValueError, match="version or document revision"):
        await repo.get_workflow_version(
            original.id,
            version="draft",
            document_revision=original.document_revision,
        )
    assert await repo.get_workflow_version(original.id, version="9.9.9") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["invalid-json", "not-object", "wrong-filename", "digest"])
async def test_corrupt_immutable_history_fails_loudly(tmp_path, damage) -> None:
    repo = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    original = await repo.save_workflow(_workflow())
    history_file = next((tmp_path / ".history" / str(original.id)).glob("*.json"))
    payload = json.loads(history_file.read_text(encoding="utf-8"))
    if damage == "invalid-json":
        history_file.write_text("{", encoding="utf-8")
    elif damage == "not-object":
        history_file.write_text("[]", encoding="utf-8")
    elif damage == "wrong-filename":
        history_file.rename(history_file.with_name("0" * 64 + ".json"))
    else:
        payload["document"]["description"] = "tampered without a new digest"
        history_file.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(WorkflowDocumentError):
        await repo.list_workflow_versions(original.id)


@pytest.mark.asyncio
async def test_history_snapshot_stored_under_another_identity_is_rejected(tmp_path) -> None:
    repo = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    first = await repo.save_workflow(_workflow())
    second = await repo.save_workflow(_workflow(owner_id="other"))
    second_file = next((tmp_path / ".history" / str(second.id)).glob("*.json"))
    wrong_directory = tmp_path / ".history" / str(first.id)
    copied = wrong_directory / second_file.name
    copied.write_bytes(second_file.read_bytes())

    with pytest.raises(WorkflowDocumentError, match="belongs to"):
        await repo.list_workflow_versions(first.id)


@pytest.mark.asyncio
async def test_bundled_successor_archives_package_head_and_delete_does_not_resurrect(
    tmp_path,
) -> None:
    bundled_path = tmp_path / "bundled"
    catalog_path = tmp_path / "catalog"
    bundled_path.mkdir()
    catalog_path.mkdir()
    bundled = load_system_workflows()[0]
    package_path = bundled_path / "workflow.yaml"
    package_path.write_text(dump_workflow_document(bundled), encoding="utf-8")
    package_bytes = package_path.read_bytes()
    repo = FilesystemWorkflowRepository(str(catalog_path), str(bundled_path))
    original = await repo.get_workflow(bundled.id)
    assert original is not None

    successor = await repo.save_workflow_version(
        replace(original, name="Locally edited system workflow"),
        expected_revision=original.revision,
        base_revision=original.document_revision,
    )

    assert package_path.read_bytes() == package_bytes
    assert successor.id == original.id
    assert successor.read_only is False
    assert successor.origin == "authored"
    versions = await repo.list_workflow_versions(original.id)
    assert len(versions) == 2
    assert {item.origin for item in versions} == {"authored", "bundled"}
    archived = await repo.get_workflow_version(
        original.id, document_revision=original.document_revision
    )
    assert archived is not None
    assert archived.origin == "bundled"
    assert archived.read_only is True

    second = await repo.save_workflow_version(
        replace(successor, description="A second local successor"),
        expected_revision=successor.revision,
        base_revision=successor.document_revision,
    )
    repeated_versions = await repo.list_workflow_versions(original.id)
    assert len(repeated_versions) == 3
    assert sum(item.origin == "bundled" for item in repeated_versions) == 1
    assert sum(item.is_head for item in repeated_versions) == 1

    restarted = FilesystemWorkflowRepository(str(catalog_path), str(bundled_path))
    assert (await restarted.get_workflow(original.id)).revision == second.revision
    assert await restarted.delete_workflow(original.id) is True
    assert await restarted.get_workflow(original.id) is None
    assert package_path.read_bytes() == package_bytes


@pytest.mark.asyncio
async def test_version_transaction_recovers_head_and_history_together(
    tmp_path, monkeypatch
) -> None:
    repo = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    original = await repo.save_workflow(_workflow())
    original_replace = FilesystemWorkflowRepository._atomic_replace
    interrupted = False

    def fail_before_new_head(path, content):
        nonlocal interrupted
        if path.parent == tmp_path and path.suffix == ".yaml" and not interrupted:
            interrupted = True
            raise OSError("simulated version publish interruption")
        original_replace(path, content)

    monkeypatch.setattr(
        FilesystemWorkflowRepository,
        "_atomic_replace",
        staticmethod(fail_before_new_head),
    )
    with pytest.raises(OSError, match="simulated version publish interruption"):
        await repo.save_workflow_version(
            replace(original, description="recovered successor"),
            expected_revision=original.revision,
            base_revision=original.document_revision,
        )
    assert len(list((tmp_path / ".history" / str(original.id)).glob("*.json"))) == 2
    assert next((tmp_path / ".transactions").glob("*.yaml")).is_file()

    monkeypatch.setattr(
        FilesystemWorkflowRepository,
        "_atomic_replace",
        staticmethod(original_replace),
    )
    restarted = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    recovered = await restarted.get_workflow(original.id)
    assert recovered is not None
    assert recovered.description == "recovered successor"
    assert [item.version for item in await restarted.list_workflow_versions(original.id)] == [
        "0.1.0",
        "draft",
    ]
    assert not list((tmp_path / ".transactions").glob("*.yaml"))


@pytest.mark.asyncio
async def test_bundled_workflows_are_read_only(tmp_path) -> None:
    bundled_path = tmp_path / "bundled"
    catalog_path = tmp_path / "catalog"
    bundled_path.mkdir()
    catalog_path.mkdir()
    bundled = load_system_workflows()[0]
    (bundled_path / "workflow.yaml").write_text(dump_workflow_document(bundled), encoding="utf-8")
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
    await repo.mark_migration_complete({"inventory_ids": [str(saved.id)], "deleted_ids": []})
    document_path = next(path for path in tmp_path.glob("*.yaml") if not path.name.startswith("."))
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
    document_path = next(path for path in tmp_path.glob("*.yaml") if not path.name.startswith("."))
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


@pytest.mark.asyncio
async def test_restart_recovers_legacy_write_journal_before_version_history(tmp_path) -> None:
    """Journals left by the pre-history adapter remain recoverable after upgrade."""
    repo = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    workflow = _workflow()
    journal_path = tmp_path / ".transactions" / f"{workflow.id}.yaml"
    journal_path.write_text(
        yaml.safe_dump(
            {
                "operation": "write",
                "id": str(workflow.id),
                "document_name": "legacy-pending.yaml",
                "document": dump_workflow_document(workflow),
                "metadata": repo._dump_metadata(workflow),
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    restarted = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    recovered = await restarted.get_workflow(workflow.id)
    assert recovered is not None
    assert recovered.name == workflow.name
    assert recovered.document_revision is not None
    assert not journal_path.exists()

    successor = await restarted.save_workflow(replace(recovered, description="versioned"))
    assert [item.version for item in await restarted.list_workflow_versions(workflow.id)] == [
        successor.version,
        recovered.version,
    ]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda raw: raw.pop("versions"), "Malformed versioned workflow transaction"),
        (lambda raw: raw.update(versions="not-a-list"), "Malformed versioned workflow transaction"),
        (lambda raw: raw.update(versions=[None]), "Malformed workflow version snapshot"),
    ],
)
def test_restart_rejects_malformed_version_journal(tmp_path, mutation, message) -> None:
    repo = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    workflow = _workflow()
    payload = {
        "operation": "write_version",
        "id": str(workflow.id),
        "document_name": "pending.yaml",
        "document": dump_workflow_document(workflow),
        "metadata": repo._dump_metadata(workflow),
        "versions": [],
        "bundled_replacement": False,
    }
    mutation(payload)
    journal_path = tmp_path / ".transactions" / f"{workflow.id}.yaml"
    journal_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(WorkflowDocumentError, match=message):
        FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)


@pytest.mark.asyncio
async def test_recovery_rejects_immutable_snapshot_collision(tmp_path, monkeypatch) -> None:
    repo = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    original = await repo.save_workflow(_workflow())
    original_replace = FilesystemWorkflowRepository._atomic_replace
    interrupted = False

    def fail_before_new_head(path, content):
        nonlocal interrupted
        if path.parent == tmp_path and path.suffix == ".yaml" and not interrupted:
            interrupted = True
            raise OSError("leave version journal")
        original_replace(path, content)

    monkeypatch.setattr(
        FilesystemWorkflowRepository,
        "_atomic_replace",
        staticmethod(fail_before_new_head),
    )
    with pytest.raises(OSError, match="leave version journal"):
        await repo.save_workflow_version(
            replace(original, description="pending"),
            expected_revision=original.revision,
            base_revision=original.document_revision,
        )
    monkeypatch.setattr(
        FilesystemWorkflowRepository,
        "_atomic_replace",
        staticmethod(original_replace),
    )

    journal_path = next((tmp_path / ".transactions").glob("*.yaml"))
    journal = yaml.safe_load(journal_path.read_text(encoding="utf-8"))
    pending = journal["versions"][-1]
    revision = pending["document_revision"].removeprefix("sha256:")
    history_path = tmp_path / ".history" / str(original.id) / f"{revision}.json"
    collided = dict(pending)
    collided["origin"] = "tampered-origin"
    history_path.write_text(json.dumps(collided), encoding="utf-8")

    with pytest.raises(WorkflowDocumentError, match="Immutable workflow version collision"):
        FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("damage", "message"),
    [
        ("not-mapping", "must be a mapping"),
        ("non-string-key", "non-string field name"),
        ("unknown", "unknown: surprise"),
        ("wrong-id", "id does not match"),
        ("scope", "invalid scope"),
        ("owner", "owner_id must be a string or null"),
        ("tenant", "tenant_id must be a string"),
        ("personas", "persona_definitions must be a mapping"),
        ("origin", "origin must be a string"),
        ("base", "based_on_revision must be a document revision"),
        ("workflows", "workflow_definitions must be a mapping"),
        ("requirements", "requirements must be a list of mappings"),
        ("timestamp-text", "not an ISO-8601 timestamp"),
        ("timestamp-type", "not an ISO-8601 timestamp"),
        ("timestamp-naive", "must include a timezone"),
    ],
)
async def test_metadata_corruption_fails_loudly(tmp_path, damage, message) -> None:
    repo = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    saved = await repo.save_workflow(_workflow())
    metadata_path = tmp_path / ".metadata" / f"{saved.id}.yaml"
    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))

    if damage == "not-mapping":
        metadata = []
    elif damage == "non-string-key":
        metadata[1] = "invalid"
    elif damage == "unknown":
        metadata["surprise"] = True
    elif damage == "wrong-id":
        metadata["id"] = str(uuid4())
    elif damage == "scope":
        metadata["scope"] = "unknown"
    elif damage == "owner":
        metadata["owner_id"] = 42
    elif damage == "tenant":
        metadata["tenant_id"] = 42
    elif damage == "personas":
        metadata["persona_definitions"] = []
    elif damage == "origin":
        metadata["origin"] = ""
    elif damage == "base":
        metadata["based_on_revision"] = "not-a-revision"
    elif damage == "workflows":
        metadata["workflow_definitions"] = []
    elif damage == "requirements":
        metadata["requirements"] = ["invalid"]
    elif damage == "timestamp-text":
        metadata["created_at"] = "yesterday"
    elif damage == "timestamp-type":
        metadata["created_at"] = 42
    else:
        metadata["created_at"] = "2026-09-20T10:00:00"
    metadata_path.write_text(yaml.safe_dump(metadata), encoding="utf-8")

    with pytest.raises(WorkflowDocumentError, match=message):
        await repo.get_workflow(saved.id)


@pytest.mark.asyncio
async def test_missing_sidecar_and_invalid_document_fail_loudly(tmp_path) -> None:
    repo = FilesystemWorkflowRepository(str(tmp_path), include_bundled=False)
    saved = await repo.save_workflow(_workflow())
    metadata_path = tmp_path / ".metadata" / f"{saved.id}.yaml"
    metadata_text = metadata_path.read_text(encoding="utf-8")
    document_path = next(path for path in tmp_path.glob("*.yaml") if not path.name.startswith("."))

    metadata_path.unlink()
    with pytest.raises(WorkflowDocumentError, match="missing metadata sidecar"):
        await repo.get_workflow(saved.id)
    metadata_path.write_text(metadata_text, encoding="utf-8")
    document_path.write_text("not: a workflow", encoding="utf-8")
    with pytest.raises(WorkflowDocumentError, match="Invalid workflow document"):
        await repo.get_workflow(saved.id)


def test_duplicate_bundled_identity_is_rejected_during_archive_lookup(tmp_path) -> None:
    bundled_path = tmp_path / "bundled"
    catalog_path = tmp_path / "catalog"
    bundled_path.mkdir()
    catalog_path.mkdir()
    bundled = load_system_workflows()[0]
    document = dump_workflow_document(bundled)
    (bundled_path / "first.yaml").write_text(document, encoding="utf-8")
    (bundled_path / "second.yaml").write_text(document, encoding="utf-8")
    repo = FilesystemWorkflowRepository(str(catalog_path), str(bundled_path))

    with pytest.raises(WorkflowDocumentError, match="Duplicate bundled workflow id"):
        repo._find_bundled(bundled.id)
