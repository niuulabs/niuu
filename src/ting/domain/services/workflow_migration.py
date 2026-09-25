"""Verified one-way migration from PostgreSQL workflow rows to files."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from ravn.domain.persona_document import (
    PersonaDependency,
    PersonaDocumentError,
    PortablePersonaDefinition,
    PortablePersonaSource,
    resolve_persona_dependencies,
)
from ting.domain.exceptions import WorkflowDocumentError
from ting.domain.models import WorkflowDefinition
from ting.domain.workflow_document import referenced_persona_aliases
from ting.domain.workflow_versioning import workflow_version_tuple
from ting.ports.workflow_migration import (
    WorkflowMigrationSource,
    WorkflowMigrationTarget,
    WorkflowPersonaSourceResolver,
)


class LegacyCatalogMigrationTarget(Protocol):
    """The subset of the file catalog the legacy-migration guard needs."""

    def read_migration_marker(self) -> dict[str, Any] | None: ...

    async def get_workflow(self, workflow_id: UUID) -> object | None: ...


async def workflow_catalog_migration_is_current(
    workflow_repo: LegacyCatalogMigrationTarget,
    *,
    legacy_count: int,
    legacy_max_updated_at: datetime | None,
) -> bool:
    """Return whether the file catalog is a verified, current copy of PostgreSQL.

    Shared by the startup guard (``ting.main._assert_workflow_catalog_migrated``)
    and the operator migration command (``ting.migrate_workflows
    --skip-if-migrated``): both need to know whether the configured file
    catalog already reflects the PostgreSQL ``workflows`` table exactly, given
    its row count and most recent ``updated_at``. An empty PostgreSQL table has
    nothing to migrate and is trivially current.
    """
    if not legacy_count:
        return True
    marker = workflow_repo.read_migration_marker()
    marker_max_updated_at = marker.get("source_max_updated_at") if marker else None
    marker_matches = bool(
        marker
        and marker.get("source_count") == legacy_count
        and marker_max_updated_at
        == (legacy_max_updated_at.isoformat() if legacy_max_updated_at is not None else None)
    )
    if not marker_matches:
        return False
    deleted_ids = set(marker.get("deleted_ids", []))
    expected_ids = set(marker.get("inventory_ids", [])) - deleted_ids
    for workflow_id in expected_ids:
        try:
            parsed_workflow_id = UUID(workflow_id)
        except (TypeError, ValueError):
            return False
        if await workflow_repo.get_workflow(parsed_workflow_id) is None:
            return False
    return True


@dataclass(frozen=True)
class WorkflowMigrationReport:
    source_count: int
    referenced_count: int
    bundled_matches: int
    create_count: int
    unchanged_count: int
    errors: tuple[str, ...]
    applied: bool = False
    bundled_superseded: int = 0

    @property
    def can_apply(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_count": self.source_count,
            "referenced_count": self.referenced_count,
            "bundled_matches": self.bundled_matches,
            "bundled_superseded": self.bundled_superseded,
            "create_count": self.create_count,
            "unchanged_count": self.unchanged_count,
            "errors": list(self.errors),
            "can_apply": self.can_apply,
            "applied": self.applied,
        }


async def migrate_workflow_catalog(
    *,
    source: WorkflowMigrationSource,
    target: WorkflowMigrationTarget,
    persona_source_for_workflow: WorkflowPersonaSourceResolver,
    bundled_workflows: Sequence[WorkflowDefinition],
    apply: bool = False,
    replace_divergent_bundled: bool = False,
) -> WorkflowMigrationReport:
    """Inventory, validate, and optionally publish an existing workflow catalog.

    The source intentionally exposes operator-only inventory methods outside the
    request repository port. No writes occur unless every definition, persona
    pin, destination identity, and historical reference validates first.
    """
    source_workflows = await source.list_all_workflows()
    referenced_ids = await source.referenced_workflow_ids()
    bundled = {workflow.id: workflow for workflow in bundled_workflows}
    prepared: list[WorkflowDefinition] = []
    bundled_matches = 0
    bundled_superseded = 0
    unchanged = 0
    errors: list[str] = []
    bundled_replacements: set[UUID] = set()

    source_ids = {workflow.id for workflow in source_workflows}
    missing_references = referenced_ids - source_ids
    if missing_references:
        errors.append(
            "Database references missing workflow row(s): "
            + ", ".join(sorted(str(value) for value in missing_references))
        )

    for workflow in source_workflows:
        packaged = bundled.get(workflow.id)
        # A package-seeded ('bundled', never operator-authored) row that no
        # longer matches the packaged definition is not divergence to
        # resolve when it is an OLDER release of the package's own content --
        # superseded by the current packaged definition under the same
        # identity. The filesystem catalog already serves that current
        # packaged definition for this id by default, so there is nothing to
        # write or reconcile, and no persona resolution is needed for
        # content that will not be published. A bundled row at a NEWER
        # version than this image's own package is not superseded, it is a
        # sign this image is older than the database (a rollback): treat it
        # as an error rather than silently discarding the newer content. A
        # bundled row diverging at the SAME version (or with an unparsable
        # version) is genuinely ambiguous, not an ordering question, so it
        # falls through to the divergence handling below unchanged.
        if (
            packaged is not None
            and workflow.origin == "bundled"
            and not _same_packaged_identity(workflow, packaged)
        ):
            try:
                source_version = workflow_version_tuple(workflow.version)
                packaged_version = workflow_version_tuple(packaged.version)
            except ValueError as exc:
                errors.append(f"Workflow {workflow.id} version cannot be compared: {exc}")
                continue
            if source_version < packaged_version:
                bundled_superseded += 1
                continue
            if source_version > packaged_version:
                errors.append(
                    f"System workflow {workflow.id} ({workflow.name!r}) is at version "
                    f"{workflow.version}, newer than this image's packaged version "
                    f"{packaged.version}. This looks like an image rollback: deploy the "
                    "image that shipped that version, or bump the packaged workflow's "
                    "own version forward past it."
                )
                continue
        try:
            persona_source = await persona_source_for_workflow(workflow)
        except (PersonaDocumentError, WorkflowDocumentError) as exc:
            errors.append(f"Workflow {workflow.id} persona source is invalid: {exc}")
            continue
        pinned, pin_errors = _pin_workflow(workflow, persona_source)
        errors.extend(pin_errors)
        if packaged is not None:
            if not _same_packaged_identity(workflow, packaged):
                if replace_divergent_bundled:
                    replacement = replace(pinned, revision=None, read_only=False, source=None)
                    bundled_replacements.add(workflow.id)
                    existing = await target.get_workflow(workflow.id)
                    if existing is not None and not existing.read_only:
                        if _same_stored_workflow(replacement, existing):
                            unchanged += 1
                            continue
                        errors.append(
                            f"Destination workflow {workflow.id} already exists with "
                            "different content or ownership"
                        )
                        continue
                    prepared.append(replacement)
                    continue
                errors.append(
                    f"System workflow {workflow.id} ({workflow.name!r}) diverges from its "
                    "packaged definition. Inspect the difference, then pass "
                    "--replace-divergent-bundled to preserve the database row under its "
                    "existing identity."
                )
                continue
            bundled_matches += 1
            continue

        existing = await target.get_workflow(workflow.id)
        if existing is not None:
            if _same_stored_workflow(pinned, existing):
                unchanged += 1
                continue
            errors.append(
                f"Destination workflow {workflow.id} already exists with different content "
                "or ownership"
            )
            continue
        prepared.append(replace(pinned, revision=None, read_only=False, source=None))

    report = WorkflowMigrationReport(
        source_count=len(source_workflows),
        referenced_count=len(referenced_ids),
        bundled_matches=bundled_matches,
        bundled_superseded=bundled_superseded,
        create_count=len(prepared),
        unchanged_count=unchanged,
        errors=tuple(errors),
    )
    if not apply or errors:
        return report

    await target.authorize_bundled_replacements(bundled_replacements)
    for workflow in prepared:
        await target.save_workflow(workflow)

    for workflow in prepared:
        migrated = await target.get_workflow(workflow.id)
        if migrated is None or not _same_stored_workflow(workflow, migrated):
            raise RuntimeError(f"Workflow migration verification failed for {workflow.id}")
    for workflow_id in referenced_ids:
        if await target.get_workflow(workflow_id) is None:
            raise RuntimeError(
                f"Workflow migration lost referenced workflow identity {workflow_id}"
            )

    await target.mark_migration_complete(
        {
            "source": "postgres",
            "source_count": len(source_workflows),
            "inventory_ids": [str(workflow.id) for workflow in source_workflows],
            "deleted_ids": [],
            "source_max_updated_at": max(
                (workflow.updated_at.isoformat() for workflow in source_workflows),
                default=None,
            ),
            "referenced_count": len(referenced_ids),
            "bundled_matches": bundled_matches,
            "bundled_superseded": bundled_superseded,
            "migrated_count": len(prepared),
            "unchanged_count": unchanged,
        }
    )
    return replace(report, applied=True)


def _pin_workflow(
    workflow: WorkflowDefinition,
    persona_source: PortablePersonaSource,
) -> tuple[WorkflowDefinition, list[str]]:
    existing_dependencies = dict(workflow.persona_dependencies)
    scoped_definitions = dict(workflow.persona_definitions)
    dependencies: dict[str, PersonaDependency] = {}
    definitions: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    try:
        aliases = referenced_persona_aliases(workflow.graph)
    except WorkflowDocumentError as exc:
        return workflow, [f"Workflow {workflow.id} has an invalid graph: {exc}"]
    for alias in sorted(aliases):
        existing = existing_dependencies.get(alias)
        portable: PortablePersonaDefinition | None = None
        try:
            if alias in scoped_definitions and existing is not None:
                portable = resolve_persona_dependencies(
                    {alias: existing},
                    scoped_definitions,
                    persona_source,
                )[alias]
            elif existing is not None:
                portable = persona_source.load_portable(existing.id, existing.revision)
                if portable is not None and portable.digest != existing.digest:
                    raise PersonaDocumentError(
                        f"Resolved {existing.id}@{existing.revision} with digest "
                        f"{portable.digest}, expected {existing.digest}"
                    )
                if portable is None:
                    current = persona_source.load_current_portable(existing.id)
                    if current is not None and current.digest == existing.digest:
                        # Older catalogs used mutable revision labels. A matching full digest
                        # proves the source is unchanged and permits a content revision upgrade.
                        portable = current
            else:
                portable = persona_source.load_current_portable(alias)
        except PersonaDocumentError as exc:
            errors.append(f"Workflow {workflow.id} persona {alias!r}: {exc}")
            continue
        if portable is None:
            requested = f"{existing.id}@{existing.revision}" if existing else alias
            errors.append(
                f"Workflow {workflow.id} references missing persona {requested!r}; "
                "install or map an exact revision before migration"
            )
            continue
        dependencies[alias] = PersonaDependency(
            id=portable.id,
            revision=portable.revision,
            digest=portable.digest,
            path=existing.path if existing is not None else None,
        )
        definitions[alias] = portable.to_dict()
    return (
        replace(
            workflow,
            persona_dependencies=dependencies,
            persona_definitions=definitions,
        ),
        errors,
    )


def _same_definition_without_pins(
    left: WorkflowDefinition,
    right: WorkflowDefinition,
) -> bool:
    return (
        left.id == right.id
        and left.name == right.name
        and left.description == right.description
        and left.version == right.version
        and left.graph == right.graph
    )


def _same_packaged_identity(
    workflow: WorkflowDefinition,
    packaged: WorkflowDefinition,
) -> bool:
    return (
        _same_definition_without_pins(workflow, packaged)
        and workflow.scope == packaged.scope
        and workflow.owner_id == packaged.owner_id
        and workflow.tenant_id == packaged.tenant_id
    )


def _same_stored_workflow(left: WorkflowDefinition, right: WorkflowDefinition) -> bool:
    return (
        _same_definition_without_pins(left, right)
        and left.scope == right.scope
        and left.owner_id == right.owner_id
        and left.tenant_id == right.tenant_id
        and left.persona_dependencies == right.persona_dependencies
        and left.persona_definitions == right.persona_definitions
        and left.requirements == right.requirements
        and left.schema_version == right.schema_version
        and left.workflow_dependencies == right.workflow_dependencies
        and left.workflow_definitions == right.workflow_definitions
        and left.created_at == right.created_at
    )
