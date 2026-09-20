"""Multi-process-safe filesystem workflow catalog adapter."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from uuid import UUID

import yaml

from identity.ports import AuthorizationDeniedError
from ting.domain.exceptions import (
    WorkflowConflictError,
    WorkflowDocumentError,
    WorkflowReadOnlyError,
)
from ting.domain.models import WorkflowDefinition, WorkflowScope
from ting.domain.workflow_document import (
    dump_workflow_document,
    load_workflow_document,
    workflow_document_revision,
)
from ting.ports.workflow_repository import WorkflowRepository
from ting.system_workflows import load_bundled_workflow

_METADATA_KEYS = {
    "id",
    "tenant_id",
    "scope",
    "owner_id",
    "created_at",
    "updated_at",
    "persona_definitions",
    "requirements",
}
_OPTIONAL_METADATA_KEYS = {"workflow_definitions"}


class FilesystemWorkflowRepository(WorkflowRepository):
    """Store editable workflows as YAML documents with local metadata sidecars.

    A durable journal and an advisory ``flock`` make document/metadata publication
    recoverable and consistent across all cooperating Ting workers. Direct edits
    are reread and validated on every operation; malformed edits fail loudly.
    """

    requires_legacy_catalog_migration_guard = True

    def __init__(
        self,
        catalog_path: str,
        bundled_path: str | None = None,
        include_bundled: bool = True,
        create_directory: bool = False,
    ) -> None:
        self._catalog_path = Path(catalog_path).expanduser().resolve()
        if include_bundled:
            default_bundled_path = Path(__file__).parent.parent / "workflows"
            self._bundled_path = Path(bundled_path or default_bundled_path).expanduser().resolve()
        else:
            self._bundled_path = None
        self._metadata_path = self._catalog_path / ".metadata"
        self._transaction_path = self._catalog_path / ".transactions"
        self._lock_path = self._catalog_path / ".catalog.lock"
        self._create_directory = create_directory
        self._prepare_storage()

    async def list_workflows(
        self,
        *,
        owner_id: str,
        scope: WorkflowScope | None = None,
    ) -> list[WorkflowDefinition]:
        return await asyncio.to_thread(self._list_sync, owner_id, scope)

    async def get_workflow(self, workflow_id: UUID) -> WorkflowDefinition | None:
        return await asyncio.to_thread(self._get_sync, workflow_id)

    async def save_workflow(self, workflow: WorkflowDefinition) -> WorkflowDefinition:
        return await asyncio.to_thread(self._save_sync, workflow)

    async def delete_workflow(self, workflow_id: UUID) -> bool:
        return await asyncio.to_thread(self._delete_sync, workflow_id)

    async def mark_migration_complete(self, metadata: dict[str, Any]) -> None:
        """Durably record a verified legacy catalog migration."""
        await asyncio.to_thread(self._mark_migration_complete_sync, metadata)

    async def authorize_bundled_replacements(self, workflow_ids: set[UUID]) -> None:
        """Persist explicit identities whose migrated local rows replace packages."""
        await asyncio.to_thread(self._authorize_bundled_replacements_sync, workflow_ids)

    def has_migration_marker(self) -> bool:
        """Return whether an operator completed database-to-file migration."""
        return (self._catalog_path / ".migration-complete.yaml").is_file()

    def read_migration_marker(self) -> dict[str, Any] | None:
        """Read the verified migration marker for startup cutover checks."""
        path = self._catalog_path / ".migration-complete.yaml"
        if not path.is_file():
            return None
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise WorkflowDocumentError(f"Cannot read workflow migration marker: {exc}") from exc
        if not isinstance(value, dict):
            raise WorkflowDocumentError("Workflow migration marker must be a mapping")
        return value

    def _mark_migration_complete_sync(self, metadata: dict[str, Any]) -> None:
        try:
            payload = json.loads(json.dumps(metadata, allow_nan=False))
        except (TypeError, ValueError, RecursionError) as exc:
            raise WorkflowDocumentError(
                "Workflow migration marker must contain JSON-compatible values"
            ) from exc
        payload["completed_at"] = datetime.now(UTC).isoformat()
        with self._locked():
            self._recover_transactions()
            self._atomic_replace(
                self._catalog_path / ".migration-complete.yaml",
                yaml.safe_dump(payload, sort_keys=True, allow_unicode=True),
            )

    def _authorize_bundled_replacements_sync(self, workflow_ids: set[UUID]) -> None:
        if not workflow_ids:
            return
        with self._locked():
            self._recover_transactions()
            existing = self._load_bundled_replacements()
            existing.update(workflow_ids)
            self._atomic_replace(
                self._catalog_path / ".bundled-replacements.yaml",
                yaml.safe_dump(
                    {"workflow_ids": sorted(str(value) for value in existing)},
                    sort_keys=False,
                ),
            )

    def _prepare_storage(self) -> None:
        if not self._catalog_path.exists() and self._create_directory:
            self._catalog_path.mkdir(mode=0o700, parents=True)
        if not self._catalog_path.exists():
            raise RuntimeError(
                f"Workflow catalog directory does not exist: {self._catalog_path}. "
                "Create and mount durable storage before selecting the filesystem adapter."
            )
        if not self._catalog_path.is_dir():
            raise RuntimeError(f"Workflow catalog path is not a directory: {self._catalog_path}")
        if not os.access(self._catalog_path, os.R_OK | os.W_OK | os.X_OK):
            raise RuntimeError(
                f"Workflow catalog directory is not readable and writable: {self._catalog_path}"
            )
        if self._bundled_path is not None:
            if not self._bundled_path.is_dir():
                raise RuntimeError(
                    f"Bundled workflow directory does not exist: {self._bundled_path}"
                )
            if not os.access(self._bundled_path, os.R_OK | os.X_OK):
                raise RuntimeError(
                    f"Bundled workflow directory is not readable: {self._bundled_path}"
                )
        self._metadata_path.mkdir(mode=0o700, exist_ok=True)
        self._transaction_path.mkdir(mode=0o700, exist_ok=True)
        self._lock_path.touch(mode=0o600, exist_ok=True)
        probe = self._catalog_path / f".write-probe-{os.getpid()}"
        try:
            probe.write_text("probe", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            raise RuntimeError(
                f"Workflow catalog directory is not writable: {self._catalog_path}: {exc}"
            ) from exc
        with self._locked():
            self._recover_transactions()

    def _list_sync(
        self,
        owner_id: str,
        scope: WorkflowScope | None,
    ) -> list[WorkflowDefinition]:
        with self._locked():
            self._recover_transactions()
            workflows = self._load_all()
        visible = []
        for workflow in workflows.values():
            if scope is not None and workflow.scope != scope:
                continue
            if workflow.scope == WorkflowScope.USER and workflow.owner_id != owner_id:
                continue
            visible.append(workflow)
        return sorted(
            visible,
            key=lambda workflow: (workflow.updated_at, workflow.created_at),
            reverse=True,
        )

    def _get_sync(self, workflow_id: UUID) -> WorkflowDefinition | None:
        with self._locked():
            self._recover_transactions()
            return self._load_all().get(workflow_id)

    def _save_sync(self, workflow: WorkflowDefinition) -> WorkflowDefinition:
        with self._locked():
            self._recover_transactions()
            workflows = self._load_all()
            existing = workflows.get(workflow.id)
            if existing is not None and existing.read_only:
                raise WorkflowReadOnlyError(
                    f"Bundled workflow {workflow.id} is read-only; save an editable copy "
                    "with a new workflow id"
                )
            if existing is not None and workflow.revision != existing.revision:
                raise WorkflowConflictError(
                    f"Workflow {workflow.id} changed after it was read "
                    f"(expected revision {workflow.revision!r}, current {existing.revision!r})"
                )
            if existing is not None and (
                workflow.owner_id != existing.owner_id or workflow.tenant_id != existing.tenant_id
            ):
                raise AuthorizationDeniedError("Resource ownership is immutable")

            now = datetime.now(UTC)
            stored = replace(
                workflow,
                created_at=existing.created_at if existing is not None else workflow.created_at,
                updated_at=now if existing is not None else workflow.updated_at,
                read_only=False,
                source=None,
                revision=None,
            )
            document_path = self._existing_document_path(existing)
            if document_path is None:
                document_path = self._new_document_path(stored)
            stored = replace(stored, source=f"local:{document_path.name}")
            document_text = dump_workflow_document(stored)
            # Validate exactly what will become visible before starting publication.
            load_workflow_document(document_text)
            revision = self._aggregate_revision(stored)
            stored = replace(stored, revision=revision)
            metadata_text = self._dump_metadata(stored)
            self._publish_transaction(
                stored.id,
                document_path.name,
                document_text,
                metadata_text,
            )
            return stored

    def _delete_sync(self, workflow_id: UUID) -> bool:
        with self._locked():
            self._recover_transactions()
            existing = self._load_all().get(workflow_id)
            if existing is None:
                return False
            if existing.read_only:
                raise WorkflowReadOnlyError(f"Bundled workflow {workflow_id} is read-only")
            document_path = self._existing_document_path(existing)
            if document_path is None:
                raise WorkflowDocumentError(
                    f"Workflow {workflow_id} does not identify its editable source path"
                )
            self._publish_delete(workflow_id, document_path.name)
            return True

    def _load_all(self) -> dict[UUID, WorkflowDefinition]:
        result: dict[UUID, WorkflowDefinition] = {}
        bundled_replacements = self._load_bundled_replacements()
        if self._bundled_path is not None:
            for path in sorted(self._bundled_path.glob("*.yaml")):
                workflow = self._load_bundled(path)
                if workflow.id in bundled_replacements:
                    continue
                self._add_unique(result, workflow, path)
        local_paths = sorted(
            path for path in self._catalog_path.glob("*.yaml") if not path.name.startswith(".")
        )
        local_paths.extend(
            sorted(
                path for path in self._catalog_path.glob("*.yml") if not path.name.startswith(".")
            )
        )
        for path in local_paths:
            workflow = self._load_local(path)
            self._add_unique(result, workflow, path)
        return result

    def _load_bundled_replacements(self) -> set[UUID]:
        path = self._catalog_path / ".bundled-replacements.yaml"
        if not path.is_file():
            return set()
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise WorkflowDocumentError(
                f"Cannot read bundled workflow replacement policy {path}: {exc}"
            ) from exc
        if not isinstance(raw, dict) or set(raw) != {"workflow_ids"}:
            raise WorkflowDocumentError(
                f"Bundled workflow replacement policy {path} must contain only workflow_ids"
            )
        values = raw["workflow_ids"]
        if not isinstance(values, list):
            raise WorkflowDocumentError(
                f"Bundled workflow replacement policy {path} workflow_ids must be a list"
            )
        try:
            return {UUID(str(value)) for value in values}
        except (TypeError, ValueError) as exc:
            raise WorkflowDocumentError(
                f"Bundled workflow replacement policy {path} contains an invalid UUID"
            ) from exc

    @staticmethod
    def _add_unique(
        result: dict[UUID, WorkflowDefinition],
        workflow: WorkflowDefinition,
        path: Path,
    ) -> None:
        if workflow.id in result:
            raise WorkflowDocumentError(
                f"Duplicate workflow id {workflow.id} found while loading {path}"
            )
        result[workflow.id] = workflow

    def _load_bundled(self, path: Path) -> WorkflowDefinition:
        if self._bundled_path is None:  # pragma: no cover - guarded by callers
            raise WorkflowDocumentError("Bundled workflow storage is disabled")
        return load_bundled_workflow(path, bundle_root=self._bundled_path.parent)

    def _load_local(self, path: Path) -> WorkflowDefinition:
        document = self._read_document(path)
        metadata_path = self._metadata_file(document.id)
        if not metadata_path.is_file():
            raise WorkflowDocumentError(
                f"Workflow {document.id} is missing metadata sidecar {metadata_path}"
            )
        metadata = self._load_metadata(metadata_path, document.id)
        return document.to_workflow(
            scope=metadata["scope"],
            owner_id=metadata["owner_id"],
            tenant_id=metadata["tenant_id"],
            created_at=metadata["created_at"],
            updated_at=metadata["updated_at"],
            revision=self._aggregate_revision_from_parts(
                document=document,
                scope=metadata["scope"],
                owner_id=metadata["owner_id"],
                tenant_id=metadata["tenant_id"],
                persona_definitions=metadata["persona_definitions"],
                workflow_definitions=metadata["workflow_definitions"],
                requirements=metadata["requirements"],
            ),
            read_only=False,
            source=f"local:{path.name}",
            persona_definitions=metadata["persona_definitions"],
            workflow_definitions=metadata["workflow_definitions"],
            requirements=metadata["requirements"],
        )

    @staticmethod
    def _read_document(path: Path):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise WorkflowDocumentError(f"Cannot read workflow document {path}: {exc}") from exc
        try:
            return load_workflow_document(text)
        except WorkflowDocumentError as exc:
            raise WorkflowDocumentError(f"Invalid workflow document {path}: {exc}") from exc

    def _load_metadata(self, path: Path, workflow_id: UUID) -> dict[str, Any]:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise WorkflowDocumentError(f"Cannot read workflow metadata {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise WorkflowDocumentError(f"Workflow metadata {path} must be a mapping")
        if any(not isinstance(key, str) for key in raw):
            raise WorkflowDocumentError(f"Workflow metadata {path} has a non-string field name")
        unknown = set(raw) - _METADATA_KEYS - _OPTIONAL_METADATA_KEYS
        missing = _METADATA_KEYS - set(raw)
        if unknown or missing:
            detail = []
            if unknown:
                detail.append("unknown: " + ", ".join(sorted(unknown)))
            if missing:
                detail.append("missing: " + ", ".join(sorted(missing)))
            raise WorkflowDocumentError(f"Invalid workflow metadata {path} ({'; '.join(detail)})")
        if str(raw["id"]) != str(workflow_id):
            raise WorkflowDocumentError(
                f"Workflow metadata {path} id does not match document id {workflow_id}"
            )
        try:
            scope = WorkflowScope(raw["scope"])
        except ValueError as exc:
            raise WorkflowDocumentError(f"Workflow metadata {path} has invalid scope") from exc
        owner_id = raw["owner_id"]
        if owner_id is not None and not isinstance(owner_id, str):
            raise WorkflowDocumentError(
                f"Workflow metadata {path} owner_id must be a string or null"
            )
        tenant_id = raw["tenant_id"]
        if not isinstance(tenant_id, str):
            raise WorkflowDocumentError(f"Workflow metadata {path} tenant_id must be a string")
        persona_definitions = raw["persona_definitions"]
        if not isinstance(persona_definitions, dict):
            raise WorkflowDocumentError(
                f"Workflow metadata {path} persona_definitions must be a mapping"
            )
        requirements = raw["requirements"]
        workflow_definitions = raw.get("workflow_definitions", {})
        if not isinstance(workflow_definitions, dict):
            raise WorkflowDocumentError(
                f"Workflow metadata {path} workflow_definitions must be a mapping"
            )
        if not isinstance(requirements, list) or any(
            not isinstance(requirement, dict) for requirement in requirements
        ):
            raise WorkflowDocumentError(
                f"Workflow metadata {path} requirements must be a list of mappings"
            )
        return {
            "scope": scope,
            "owner_id": owner_id,
            "tenant_id": tenant_id,
            "created_at": self._parse_timestamp(raw["created_at"], path, "created_at"),
            "updated_at": self._parse_timestamp(raw["updated_at"], path, "updated_at"),
            "persona_definitions": persona_definitions,
            "workflow_definitions": workflow_definitions,
            "requirements": requirements,
        }

    @staticmethod
    def _parse_timestamp(value: object, path: Path, field_name: str) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError as exc:
                raise WorkflowDocumentError(
                    f"Workflow metadata {path} {field_name} is not an ISO-8601 timestamp"
                ) from exc
        else:
            raise WorkflowDocumentError(
                f"Workflow metadata {path} {field_name} is not an ISO-8601 timestamp"
            )
        if parsed.tzinfo is None:
            raise WorkflowDocumentError(
                f"Workflow metadata {path} {field_name} must include a timezone"
            )
        return parsed

    @staticmethod
    def _dump_metadata(workflow: WorkflowDefinition) -> str:
        payload = {
            "id": str(workflow.id),
            "tenant_id": workflow.tenant_id,
            "scope": workflow.scope.value,
            "owner_id": workflow.owner_id,
            "created_at": workflow.created_at.isoformat(),
            "updated_at": workflow.updated_at.isoformat(),
            "persona_definitions": workflow.persona_definitions,
            "workflow_definitions": workflow.workflow_definitions,
            "requirements": workflow.requirements,
        }
        return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100)

    def _publish_transaction(
        self,
        workflow_id: UUID,
        document_name: str,
        document_text: str,
        metadata_text: str,
    ) -> None:
        journal = {
            "operation": "write",
            "id": str(workflow_id),
            "document_name": document_name,
            "document": document_text,
            "metadata": metadata_text,
        }
        journal_path = self._transaction_path / f"{workflow_id}.yaml"
        self._atomic_replace(
            journal_path,
            yaml.safe_dump(journal, sort_keys=False, allow_unicode=True),
        )
        self._apply_journal(journal_path)

    def _publish_delete(self, workflow_id: UUID, document_name: str) -> None:
        journal = {
            "operation": "delete",
            "id": str(workflow_id),
            "document_name": document_name,
        }
        journal_path = self._transaction_path / f"{workflow_id}.yaml"
        self._atomic_replace(
            journal_path,
            yaml.safe_dump(journal, sort_keys=False, allow_unicode=True),
        )
        self._apply_journal(journal_path)

    def _record_marker_deletion(self, workflow_id: UUID) -> None:
        marker = self.read_migration_marker()
        if marker is None:
            return
        inventory_ids = marker.get("inventory_ids", [])
        if str(workflow_id) not in inventory_ids:
            return
        deleted_ids = set(marker.get("deleted_ids", []))
        deleted_ids.add(str(workflow_id))
        marker["deleted_ids"] = sorted(deleted_ids)
        self._atomic_replace(
            self._catalog_path / ".migration-complete.yaml",
            yaml.safe_dump(marker, sort_keys=True, allow_unicode=True),
        )

    def _recover_transactions(self) -> None:
        for journal_path in sorted(self._transaction_path.glob("*.yaml")):
            self._apply_journal(journal_path)

    def _apply_journal(self, journal_path: Path) -> None:
        try:
            raw = yaml.safe_load(journal_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise WorkflowDocumentError(
                f"Cannot recover workflow transaction {journal_path}: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise WorkflowDocumentError(f"Malformed workflow transaction {journal_path}")
        try:
            workflow_id = UUID(str(raw["id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkflowDocumentError(
                f"Malformed workflow transaction id in {journal_path}"
            ) from exc
        operation = raw.get("operation")
        document_name = raw.get("document_name")
        if (
            not isinstance(document_name, str)
            or Path(document_name).name != document_name
            or document_name.startswith(".")
            or Path(document_name).suffix.lower() not in {".yaml", ".yml"}
        ):
            raise WorkflowDocumentError(f"Malformed workflow transaction path in {journal_path}")
        document_path = self._catalog_path / document_name
        if operation == "delete":
            if set(raw) != {"operation", "id", "document_name"}:
                raise WorkflowDocumentError(f"Malformed delete transaction {journal_path}")
            if document_path.is_file():
                target = self._read_document(document_path)
                if target.id != workflow_id:
                    raise WorkflowDocumentError(
                        f"Workflow delete transaction {journal_path} targets document "
                        f"{target.id}, not {workflow_id}"
                    )
            document_path.unlink(missing_ok=True)
            self._metadata_file(workflow_id).unlink(missing_ok=True)
            self._fsync_directory(self._catalog_path)
            self._fsync_directory(self._metadata_path)
            # The tombstone is part of the durable delete transaction. Recovery
            # repeats it before removing the journal, so a crash cannot make an
            # intentional post-cutover deletion look like a missing migration.
            self._record_marker_deletion(workflow_id)
            journal_path.unlink()
            self._fsync_directory(self._transaction_path)
            return
        if operation != "write" or set(raw) != {
            "operation",
            "id",
            "document_name",
            "document",
            "metadata",
        }:
            raise WorkflowDocumentError(f"Malformed write transaction {journal_path}")
        document_text = raw["document"]
        metadata_text = raw["metadata"]
        if not isinstance(document_text, str) or not isinstance(metadata_text, str):
            raise WorkflowDocumentError(f"Malformed workflow transaction content in {journal_path}")
        document = load_workflow_document(document_text)
        if document.id != workflow_id:
            raise WorkflowDocumentError(f"Workflow transaction id mismatch in {journal_path}")
        if document_path.is_file():
            target = self._read_document(document_path)
            if target.id != workflow_id:
                raise WorkflowDocumentError(
                    f"Workflow write transaction {journal_path} targets document "
                    f"{target.id}, not {workflow_id}"
                )
        metadata_temp = self._transaction_path / f".{workflow_id}.metadata.validation.yaml"
        try:
            metadata_temp.write_text(metadata_text, encoding="utf-8")
            self._load_metadata(metadata_temp, workflow_id)
        finally:
            metadata_temp.unlink(missing_ok=True)
        self._atomic_replace(self._metadata_file(workflow_id), metadata_text)
        self._atomic_replace(document_path, document_text)
        journal_path.unlink()
        self._fsync_directory(self._transaction_path)

    @staticmethod
    def _atomic_replace(path: Path, content: str) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(temporary_path, path)
            FilesystemWorkflowRepository._fsync_directory(path.parent)
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _metadata_file(self, workflow_id: UUID) -> Path:
        return self._metadata_path / f"{workflow_id}.yaml"

    def _existing_document_path(self, workflow: WorkflowDefinition | None) -> Path | None:
        if workflow is None or workflow.source is None or not workflow.source.startswith("local:"):
            return None
        name = workflow.source.removeprefix("local:")
        if Path(name).name != name or Path(name).suffix not in {".yaml", ".yml"}:
            raise WorkflowDocumentError(f"Workflow {workflow.id} has an unsafe source path")
        return self._catalog_path / name

    def _new_document_path(self, workflow: WorkflowDefinition) -> Path:
        slug = re.sub(r"[^a-z0-9]+", "-", workflow.name.lower()).strip("-") or "workflow"
        return self._catalog_path / f"{slug}-{str(workflow.id)[:8]}.yaml"

    @staticmethod
    def _aggregate_revision(workflow: WorkflowDefinition) -> str:
        from ting.domain.workflow_document import document_from_workflow

        return FilesystemWorkflowRepository._aggregate_revision_from_parts(
            document=document_from_workflow(workflow),
            scope=workflow.scope,
            owner_id=workflow.owner_id,
            tenant_id=workflow.tenant_id,
            persona_definitions=workflow.persona_definitions,
            workflow_definitions=workflow.workflow_definitions,
            requirements=workflow.requirements,
        )

    @staticmethod
    def _aggregate_revision_from_parts(
        *,
        document,
        scope: WorkflowScope,
        owner_id: str | None,
        tenant_id: str,
        persona_definitions: dict[str, dict[str, Any]],
        requirements: list[dict[str, Any]],
        workflow_definitions: dict[str, dict[str, Any]] | None = None,
    ) -> str:
        payload = {
            "document_revision": workflow_document_revision(document),
            "scope": scope.value,
            "owner_id": owner_id,
            "tenant_id": tenant_id,
            "persona_definitions": persona_definitions,
            "requirements": requirements,
        }
        if workflow_definitions:
            payload["workflow_definitions"] = workflow_definitions
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError) as exc:
            raise WorkflowDocumentError(
                "Workflow metadata must contain only finite JSON-compatible values"
            ) from exc
        return f"sha256:{sha256(encoded).hexdigest()}"
