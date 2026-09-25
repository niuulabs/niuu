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
from uuid import UUID, uuid4

import yaml

from identity.ports import AuthorizationDeniedError
from ting.domain.exceptions import (
    WorkflowConflictError,
    WorkflowDocumentError,
    WorkflowReadOnlyError,
)
from ting.domain.models import WorkflowDefinition, WorkflowScope, WorkflowVersionSummary
from ting.domain.workflow_document import (
    dump_workflow_document,
    load_workflow_document,
    workflow_document_revision,
)
from ting.domain.workflow_versioning import (
    WorkflowVersionBump,
    deserialize_workflow_version,
    next_workflow_version,
    serialize_workflow_version,
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
_VERSION_METADATA_KEYS = {"origin", "based_on_revision"}
_REVISION_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


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
        self._history_path = self._catalog_path / ".history"
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

    async def list_workflow_versions(self, workflow_id: UUID) -> list[WorkflowVersionSummary]:
        return await asyncio.to_thread(self._list_versions_sync, workflow_id)

    async def get_workflow_version(
        self,
        workflow_id: UUID,
        *,
        version: str | None = None,
        document_revision: str | None = None,
    ) -> WorkflowDefinition | None:
        return await asyncio.to_thread(
            self._get_version_sync,
            workflow_id,
            version,
            document_revision,
        )

    async def save_workflow_version(
        self,
        workflow: WorkflowDefinition,
        *,
        expected_revision: str | None,
        base_revision: str,
        bump: WorkflowVersionBump = "patch",
    ) -> WorkflowDefinition:
        return await asyncio.to_thread(
            self._save_version_sync,
            workflow,
            expected_revision,
            base_revision,
            bump,
        )

    async def delete_workflow(self, workflow_id: UUID) -> bool:
        return await asyncio.to_thread(self._delete_sync, workflow_id)

    async def has_recorded_version_history(self, workflow_id: UUID) -> bool:
        """True once at least one immutable snapshot is archived under ``.history``."""
        return await asyncio.to_thread(self._has_recorded_version_history_sync, workflow_id)

    def _has_recorded_version_history_sync(self, workflow_id: UUID) -> bool:
        with self._locked():
            self._recover_transactions()
            return bool(self._load_history_payloads(workflow_id))

    async def adopt_legacy_bundled(self, seed: WorkflowDefinition) -> WorkflowDefinition:
        """No-op here: a bundled id with no local override is always served live.

        There is no persisted legacy system row to reconcile on this
        catalog -- ``get_workflow(seed.id)`` already returns the current
        packaged definition on every read, since bundled workflows are
        loaded directly from the package on each access rather than stored.
        Exists to satisfy the shared ``WorkflowRepository`` port;
        ``seed_system_workflows`` never actually calls this against the
        filesystem adapter, because ``workflow_repository.seed_bundled``
        must be false for it (see charts/ting/templates/configmap.yaml).
        """
        current = await self.get_workflow(seed.id)
        if current is None:
            raise WorkflowDocumentError(f"Workflow {seed.id} not found for legacy adoption")
        return current

    async def reclassify_orphaned_bundled_as_authored(
        self, workflow_id: UUID
    ) -> WorkflowDefinition | None:
        """No-op here, for the same reason as ``adopt_legacy_bundled``: this
        catalog has no persisted ``version_origin`` row to flip -- a bundled
        id is always served live from the package, never orphaned.
        """
        return await self.get_workflow(workflow_id)

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
        self._history_path.mkdir(mode=0o700, exist_ok=True)
        self._transaction_path.mkdir(mode=0o700, exist_ok=True)
        self._lock_path.touch(mode=0o600, exist_ok=True)
        # Unique per call, not just per PID: two instances constructed
        # concurrently in the same process (e.g. separate threads, as in
        # tests) must not collide on the same probe filename.
        probe = self._catalog_path / f".write-probe-{os.getpid()}-{uuid4().hex}"
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

    def _list_versions_sync(self, workflow_id: UUID) -> list[WorkflowVersionSummary]:
        with self._locked():
            self._recover_transactions()
            head = self._load_all().get(workflow_id)
            if head is None:
                return []
            versions = self._version_candidates(workflow_id, head)
        summaries = [
            WorkflowVersionSummary(
                workflow_id=workflow_id,
                version=item.version,
                document_revision=self._document_revision(item),
                created_at=item.updated_at,
                is_head=item.is_head,
                based_on_revision=item.based_on_revision,
                origin=item.origin,
            )
            for item in versions
        ]
        return sorted(summaries, key=lambda item: (item.created_at, item.version), reverse=True)

    def _get_version_sync(
        self,
        workflow_id: UUID,
        version: str | None,
        document_revision: str | None,
    ) -> WorkflowDefinition | None:
        if version is not None and document_revision is not None:
            raise ValueError("Select a workflow version by version or document revision, not both")
        with self._locked():
            self._recover_transactions()
            head = self._load_all().get(workflow_id)
            if head is None:
                return None
            versions = self._version_candidates(workflow_id, head)
        if version is None and document_revision is None:
            return head
        matches = [
            item
            for item in versions
            if (version is not None and item.version == version)
            or (
                document_revision is not None and self._document_revision(item) == document_revision
            )
        ]
        if len(matches) > 1:
            raise WorkflowDocumentError(
                f"Workflow {workflow_id} has multiple immutable snapshots for version {version!r}"
            )
        return matches[0] if matches else None

    def _save_sync(self, workflow: WorkflowDefinition) -> WorkflowDefinition:
        with self._locked():
            self._recover_transactions()
            workflows = self._load_all()
            existing = workflows.get(workflow.id)
            if existing is None and workflow.revision is not None:
                raise WorkflowConflictError(
                    f"Workflow {workflow.id} no longer exists at revision {workflow.revision!r}"
                )
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
                workflow.owner_id != existing.owner_id
                or workflow.tenant_id != existing.tenant_id
                or workflow.scope != existing.scope
            ):
                raise AuthorizationDeniedError("Resource ownership and scope are immutable")

            now = datetime.now(UTC)
            version = workflow.version
            if existing is not None:
                version = next_workflow_version(existing.version)
                if any(
                    payload.get("document", {}).get("version") == version
                    for payload in self._load_history_payloads(workflow.id)
                ):
                    raise WorkflowConflictError(
                        f"Workflow {workflow.id} version {version} already exists"
                    )
            elif self._load_history_payloads(workflow.id):
                raise WorkflowConflictError(
                    f"Workflow {workflow.id} was deleted and cannot be recreated implicitly"
                )
            stored = replace(
                workflow,
                version=version,
                created_at=existing.created_at if existing is not None else workflow.created_at,
                updated_at=now if existing is not None else workflow.updated_at,
                read_only=False,
                source=None,
                revision=None,
                is_head=True,
                origin="authored",
                based_on_revision=self._legacy_base_revision(existing),
            )
            document_path = self._existing_document_path(existing)
            if document_path is None:
                document_path = self._new_document_path(stored)
            stored = replace(stored, source=f"local:{document_path.name}")
            document_text = dump_workflow_document(stored)
            # Validate exactly what will become visible before starting publication.
            load_workflow_document(document_text)
            revision = self._aggregate_revision(stored)
            stored = replace(
                stored,
                revision=revision,
                document_revision=workflow_document_revision(stored),
            )
            metadata_text = self._dump_metadata(stored)
            snapshots = self._snapshots_for_legacy_save(existing, stored)
            self._publish_version_transaction(
                stored.id,
                document_path.name,
                document_text,
                metadata_text,
                snapshots,
                bundled_replacement=self._is_bundled(existing),
            )
            return stored

    def _save_version_sync(
        self,
        workflow: WorkflowDefinition,
        expected_revision: str | None,
        base_revision: str,
        bump: WorkflowVersionBump,
    ) -> WorkflowDefinition:
        with self._locked():
            self._recover_transactions()
            existing = self._load_all().get(workflow.id)
            if existing is None:
                raise WorkflowConflictError(
                    f"Workflow {workflow.id} has no current head to advance"
                )
            if existing.revision != expected_revision:
                raise WorkflowConflictError(
                    f"Workflow {workflow.id} changed after it was read "
                    f"(expected revision {expected_revision!r}, current {existing.revision!r})"
                )
            if (
                workflow.owner_id != existing.owner_id
                or workflow.tenant_id != existing.tenant_id
                or workflow.scope != existing.scope
            ):
                raise AuthorizationDeniedError("Resource ownership and scope are immutable")

            candidates = self._version_candidates(workflow.id, existing)
            bases = [item for item in candidates if self._document_revision(item) == base_revision]
            if not bases:
                raise WorkflowConflictError(
                    f"Workflow {workflow.id} base revision {base_revision!r} does not exist"
                )
            if len(bases) > 1:  # pragma: no cover - document revisions are content hashes
                raise WorkflowDocumentError(
                    f"Workflow {workflow.id} has duplicate base revision {base_revision!r}"
                )

            next_version = next_workflow_version(existing.version, bump)
            if any(item.version == next_version for item in candidates):
                raise WorkflowConflictError(
                    f"Workflow {workflow.id} version {next_version} already exists"
                )
            now = datetime.now(UTC)
            stored = replace(
                workflow,
                version=next_version,
                created_at=existing.created_at,
                updated_at=now,
                read_only=False,
                source=None,
                revision=None,
                document_revision=None,
                is_head=True,
                origin="authored",
                based_on_revision=base_revision,
            )
            document_path = self._existing_document_path(existing)
            if document_path is None:
                document_path = self._new_document_path(stored)
            stored = replace(stored, source=f"local:{document_path.name}")
            document_text = dump_workflow_document(stored)
            load_workflow_document(document_text)
            stored = replace(
                stored,
                revision=self._aggregate_revision(stored),
                document_revision=workflow_document_revision(stored),
            )
            snapshots = self._snapshot_payloads(candidates, stored)
            self._publish_version_transaction(
                stored.id,
                document_path.name,
                document_text,
                self._dump_metadata(stored),
                snapshots,
                bundled_replacement=self._is_bundled(existing),
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

    @staticmethod
    def _document_revision(workflow: WorkflowDefinition) -> str:
        return workflow.document_revision or workflow_document_revision(workflow)

    @staticmethod
    def _is_bundled(workflow: WorkflowDefinition | None) -> bool:
        return bool(
            workflow is not None
            and (workflow.origin == "bundled" or (workflow.source or "").startswith("bundled:"))
        )

    def _find_bundled(self, workflow_id: UUID) -> WorkflowDefinition | None:
        if self._bundled_path is None:
            return None
        found: WorkflowDefinition | None = None
        for path in sorted(self._bundled_path.glob("*.yaml")):
            candidate = self._load_bundled(path)
            if candidate.id != workflow_id:
                continue
            if found is not None:
                raise WorkflowDocumentError(
                    f"Duplicate bundled workflow id {workflow_id} found while loading {path}"
                )
            found = candidate
        return found

    def _history_files(self, workflow_id: UUID) -> list[Path]:
        directory = self._history_path / str(workflow_id)
        if not directory.is_dir():
            return []
        return sorted(directory.glob("*.json"))

    def _load_history_payloads(self, workflow_id: UUID) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for path in self._history_files(workflow_id):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise WorkflowDocumentError(
                    f"Cannot read workflow version snapshot {path}: {exc}"
                ) from exc
            if not isinstance(value, dict):
                raise WorkflowDocumentError(f"Workflow version snapshot {path} must be an object")
            revision = value.get("document_revision")
            if not isinstance(revision, str) or self._history_filename(revision) != path.name:
                raise WorkflowDocumentError(
                    f"Workflow version snapshot {path} does not match its filename"
                )
            try:
                restored = deserialize_workflow_version(
                    value,
                    head_revision=None,
                    is_head=False,
                )
            except (TypeError, ValueError) as exc:
                raise WorkflowDocumentError(
                    f"Invalid immutable workflow snapshot {path}: {exc}"
                ) from exc
            if restored.id != workflow_id:
                raise WorkflowDocumentError(
                    f"Workflow version snapshot {path} belongs to {restored.id}"
                )
            payloads.append(value)
        return payloads

    def _legacy_base_revision(self, existing: WorkflowDefinition | None) -> str | None:
        if existing is None:
            return None
        existing_revision = self._document_revision(existing)
        payloads = self._load_history_payloads(existing.id)
        if any(payload["document_revision"] == existing_revision for payload in payloads):
            return existing_revision
        if not payloads:
            return existing_revision
        latest = max(payloads, key=lambda payload: str(payload["updated_at"]))
        return str(latest["document_revision"])

    def _version_candidates(
        self,
        workflow_id: UUID,
        head: WorkflowDefinition,
    ) -> list[WorkflowDefinition]:
        head_document_revision = self._document_revision(head)
        result: list[WorkflowDefinition] = []
        payloads = self._load_history_payloads(workflow_id)
        revisions = {str(payload["document_revision"]) for payload in payloads}
        for payload in payloads:
            item_revision = str(payload["document_revision"])
            item = deserialize_workflow_version(
                payload,
                head_revision=head.revision,
                is_head=item_revision == head_document_revision,
            )
            result.append(head if item_revision == head_document_revision else item)
        if head_document_revision not in revisions:
            result.append(head)
        versions: dict[str, str] = {}
        for item in result:
            revision = self._document_revision(item)
            prior = versions.get(item.version)
            if prior is not None and prior != revision:
                raise WorkflowDocumentError(
                    f"Workflow {workflow_id} has conflicting snapshots for version {item.version}"
                )
            versions[item.version] = revision
        return result

    def _snapshots_for_legacy_save(
        self,
        existing: WorkflowDefinition | None,
        stored: WorkflowDefinition,
    ) -> list[dict[str, Any]]:
        payloads = self._load_history_payloads(stored.id)
        if not payloads:
            original = existing
            # Migration authorizes a divergent package replacement before it
            # writes the imported row. Preserve that established cutover path:
            # its source version may deliberately equal the bundled label.
            if original is None and stored.id not in self._load_bundled_replacements():
                original = self._find_bundled(stored.id)
            if original is not None:
                payloads.append(serialize_workflow_version(original))
        payloads.append(serialize_workflow_version(stored))
        return self._deduplicate_snapshot_payloads(payloads)

    def _snapshot_payloads(
        self,
        candidates: list[WorkflowDefinition],
        stored: WorkflowDefinition,
    ) -> list[dict[str, Any]]:
        return self._deduplicate_snapshot_payloads(
            [
                *(serialize_workflow_version(item) for item in candidates),
                serialize_workflow_version(stored),
            ]
        )

    @staticmethod
    def _deduplicate_snapshot_payloads(
        payloads: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for payload in payloads:
            revision = payload.get("document_revision")
            if not isinstance(revision, str) or not _REVISION_PATTERN.fullmatch(revision):
                raise WorkflowDocumentError("Workflow version has an invalid document revision")
            existing = result.get(revision)
            if existing is not None and existing != payload:
                raise WorkflowDocumentError(f"Workflow document revision collision for {revision}")
            result[revision] = payload
        return list(result.values())

    def _assert_snapshot_publication_safe(
        self,
        workflow_id: UUID,
        payloads: list[dict[str, Any]],
    ) -> None:
        versions: dict[str, str] = {}
        for payload in payloads:
            document = payload.get("document")
            version = document.get("version") if isinstance(document, dict) else None
            revision = payload.get("document_revision")
            if not isinstance(version, str) or not isinstance(revision, str):
                raise WorkflowDocumentError("Workflow version snapshot is missing identity fields")
            prior = versions.get(version)
            if prior is not None and prior != revision:
                raise WorkflowConflictError(
                    f"Workflow {workflow_id} has conflicting snapshots for version {version}"
                )
            versions[version] = revision
            path = self._history_path / str(workflow_id) / self._history_filename(revision)
            if not path.is_file():
                continue
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise WorkflowDocumentError(
                    f"Cannot verify immutable workflow version {path}: {exc}"
                ) from exc
            if current != payload:
                raise WorkflowDocumentError(f"Immutable workflow version collision at {path}")

    @staticmethod
    def _history_filename(document_revision: str) -> str:
        if not _REVISION_PATTERN.fullmatch(document_revision):
            raise WorkflowDocumentError(f"Invalid workflow document revision {document_revision!r}")
        return f"{document_revision.removeprefix('sha256:')}.json"

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
        workflow = load_bundled_workflow(path, bundle_root=self._bundled_path.parent)
        return replace(
            workflow,
            document_revision=workflow_document_revision(workflow),
            is_head=True,
            origin="bundled",
            based_on_revision=None,
        )

    def _load_local(self, path: Path) -> WorkflowDefinition:
        document = self._read_document(path)
        metadata_path = self._metadata_file(document.id)
        if not metadata_path.is_file():
            raise WorkflowDocumentError(
                f"Workflow {document.id} is missing metadata sidecar {metadata_path}"
            )
        metadata = self._load_metadata(metadata_path, document.id)
        workflow = document.to_workflow(
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
                origin=metadata["origin"],
                based_on_revision=metadata["based_on_revision"],
            ),
            read_only=False,
            source=f"local:{path.name}",
            persona_definitions=metadata["persona_definitions"],
            workflow_definitions=metadata["workflow_definitions"],
            requirements=metadata["requirements"],
        )
        return replace(
            workflow,
            document_revision=workflow_document_revision(document),
            is_head=True,
            origin=metadata["origin"],
            based_on_revision=metadata["based_on_revision"],
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
        unknown = set(raw) - _METADATA_KEYS - _OPTIONAL_METADATA_KEYS - _VERSION_METADATA_KEYS
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
        origin = raw.get("origin", "authored")
        based_on_revision = raw.get("based_on_revision")
        if not isinstance(origin, str) or not origin:
            raise WorkflowDocumentError(f"Workflow metadata {path} origin must be a string")
        if based_on_revision is not None and (
            not isinstance(based_on_revision, str)
            or not _REVISION_PATTERN.fullmatch(based_on_revision)
        ):
            raise WorkflowDocumentError(
                f"Workflow metadata {path} based_on_revision must be a document revision or null"
            )
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
            "origin": origin,
            "based_on_revision": based_on_revision,
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
            "origin": workflow.origin,
            "based_on_revision": workflow.based_on_revision,
        }
        return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100)

    def _publish_version_transaction(
        self,
        workflow_id: UUID,
        document_name: str,
        document_text: str,
        metadata_text: str,
        versions: list[dict[str, Any]],
        *,
        bundled_replacement: bool,
    ) -> None:
        self._assert_snapshot_publication_safe(workflow_id, versions)
        journal = {
            "operation": "write_version",
            "id": str(workflow_id),
            "document_name": document_name,
            "document": document_text,
            "metadata": metadata_text,
            "versions": versions,
            "bundled_replacement": bundled_replacement,
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
        if operation == "write_version":
            if set(raw) != {
                "operation",
                "id",
                "document_name",
                "document",
                "metadata",
                "versions",
                "bundled_replacement",
            }:
                raise WorkflowDocumentError(
                    f"Malformed versioned workflow transaction {journal_path}"
                )
            self._apply_version_write(
                journal_path,
                workflow_id=workflow_id,
                document_path=document_path,
                document_text=raw["document"],
                metadata_text=raw["metadata"],
                versions=raw["versions"],
                bundled_replacement=raw["bundled_replacement"],
            )
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

    def _apply_version_write(
        self,
        journal_path: Path,
        *,
        workflow_id: UUID,
        document_path: Path,
        document_text: object,
        metadata_text: object,
        versions: object,
        bundled_replacement: object,
    ) -> None:
        if (
            not isinstance(document_text, str)
            or not isinstance(metadata_text, str)
            or not isinstance(versions, list)
            or not isinstance(bundled_replacement, bool)
        ):
            raise WorkflowDocumentError(
                f"Malformed versioned workflow transaction content in {journal_path}"
            )
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

        validated: list[tuple[Path, str]] = []
        for value in versions:
            if not isinstance(value, dict):
                raise WorkflowDocumentError(
                    f"Malformed workflow version snapshot in {journal_path}"
                )
            try:
                restored = deserialize_workflow_version(
                    value,
                    head_revision=None,
                    is_head=False,
                )
            except (TypeError, ValueError) as exc:
                raise WorkflowDocumentError(
                    f"Invalid workflow version snapshot in {journal_path}: {exc}"
                ) from exc
            if restored.id != workflow_id:
                raise WorkflowDocumentError(
                    f"Workflow version snapshot in {journal_path} belongs to {restored.id}"
                )
            revision = self._document_revision(restored)
            path = self._history_path / str(workflow_id) / self._history_filename(revision)
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if path.is_file():
                try:
                    current = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise WorkflowDocumentError(
                        f"Cannot verify immutable workflow version {path}: {exc}"
                    ) from exc
                if current != value:
                    raise WorkflowDocumentError(f"Immutable workflow version collision at {path}")
            validated.append((path, encoded))

        # History is durable before the mutable head becomes visible. Recovery
        # repeats each idempotent write while the journal remains present.
        for path, encoded in validated:
            self._atomic_replace(path, encoded)
        if bundled_replacement:
            replacements = self._load_bundled_replacements()
            replacements.add(workflow_id)
            self._atomic_replace(
                self._catalog_path / ".bundled-replacements.yaml",
                yaml.safe_dump(
                    {"workflow_ids": sorted(str(value) for value in replacements)},
                    sort_keys=False,
                ),
            )
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

    @contextmanager
    def cutover_lock(self) -> Iterator[None]:
        """Serialize a concurrent automated-cutover check-then-apply.

        `ting.migrate_workflows --skip-if-migrated` (the chart's optional
        `workflow-catalog-migrate` init container) may run from several pods
        at once against the same catalog. Its "is this already migrated?"
        check and its "apply the migration" write are separate steps that
        each call back into this repository's own operations (``save_workflow``,
        ``mark_migration_complete``, ...), each of which takes ``self._locked()``
        internally. A caller must not hold ``self._locked()`` across that
        whole span — a second ``flock()`` call from the *same process* on a
        new file descriptor for the same lock file blocks forever waiting for
        the first, self-deadlocking. This is a distinct lock file so the
        automated-cutover critical section (check + verified apply) can be
        held for its full duration without deadlocking the operations nested
        inside it.
        """
        path = self._catalog_path / ".migration-cutover.lock"
        path.touch(mode=0o600, exist_ok=True)
        with path.open("a+", encoding="utf-8") as handle:
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
            origin=workflow.origin,
            based_on_revision=workflow.based_on_revision,
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
        origin: str = "authored",
        based_on_revision: str | None = None,
    ) -> str:
        payload = {
            "document_revision": workflow_document_revision(document),
            "scope": scope.value,
            "owner_id": owner_id,
            "tenant_id": tenant_id,
            "persona_definitions": persona_definitions,
            "requirements": requirements,
            "origin": origin,
            "based_on_revision": based_on_revision,
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
