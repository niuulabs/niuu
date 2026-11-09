"""Bundled Ting system workflow loading and legacy PostgreSQL seeding."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from personas.adapters.loader import FilesystemPersonaAdapter
from personas.archived import ARCHIVED_BUILTIN_PERSONAS, archived_persona_message
from personas.ports.persona import PersonaPort
from ting.domain.exceptions import WorkflowDocumentError
from ting.domain.models import WorkflowDefinition, WorkflowScope
from ting.domain.workflow_document import (
    WorkflowDocument,
    load_workflow_document,
    validate_resolved_workflow_graph,
    workflow_document_payload,
    workflow_document_revision,
)
from ting.domain.workflow_includes import (
    include_resolver_from_workflow_definitions,
    resolve_workflow_includes,
)
from ting.ports.workflow_repository import WorkflowRepository

BUNDLED_SYSTEM_WORKFLOWS_PATH = (Path(__file__).parent / "workflows").resolve()
BUNDLED_SYSTEM_PACKAGE_ROOT = BUNDLED_SYSTEM_WORKFLOWS_PATH.parent

# Packaged system workflows archived out of the catalogue (see archive/README.md).
# The files live under ``archive/workflows`` in the repository; the wheel and the
# images do not carry them, so this set is code. ``tests/test_catalog_archive.py`` keeps it
# equal to the ids of the archived documents, and restoring a workflow means
# moving the file back *and* removing its id here.
ARCHIVED_SYSTEM_WORKFLOW_IDS: frozenset[UUID] = frozenset(
    {
        # "Code & Review Flow"
        UUID("03dba0d8-4f29-560a-a4e1-1fc7e5f9ee23"),
        # "Developer Delivery"
        UUID("ca01b422-7ab7-4614-901b-8784ef65c76e"),
        # "Developer Integration"
        UUID("f776d181-ed63-4e3a-b5c4-7b128e4d2094"),
        # "Developer Planning"
        UUID("195d5026-7adb-4ce7-97ee-fb1e18ea4c3a"),
        # "Developer Workstream"
        UUID("98133eab-cd89-44d1-b32d-26c82f99dddc"),
        # "Infra Change Flow"
        UUID("0b51b458-0aeb-5a1b-8f79-d805834491e3"),
        # "Research Campaign"
        UUID("1bfc01b1-4d01-5d20-86e1-2956dff11464"),
        # "Research Thread"
        UUID("4e7ebec4-0cfa-4567-98dd-2ee4ce0bc308"),
        # "Research Thread — Breadth"
        UUID("42951d75-dd82-4596-92ea-1c0a6ef4b965"),
        # "Research Thread — Contrarian"
        UUID("5777251e-7ff1-47f7-b182-d040ed9a7a2f"),
        # "Research Thread — Depth"
        UUID("bf5f97dd-d89b-4f3e-a8fc-ea61a42685a5"),
        # "Reviewed Delivery Flow"
        UUID("7c1f7d52-6a0e-5f0a-9d3e-2b7d9a4e8c11"),
        # "Saga Planning"
        UUID("a4c7067a-ac22-5b64-bde1-ec8ad1507e73"),
        # "Specification Stack"
        UUID("96ecf5df-18a0-542b-9df6-aef6aef6a5db"),
        # "Ticket Planning Flow"
        UUID("3f0e6a14-9b2d-5c47-8e61-4d7a2c91b5e3"),
        # "Ticket Work Flow"
        UUID("9a41d7c6-3e52-5b08-a7f4-6c1e0d2b8f37"),
        # "Tracker Delivery Flow"
        UUID("3f444455-6b63-53c2-b057-3205345a56da"),
    }
)

# Packaged system workflows a release removed. A file catalog migrated while one
# still shipped lists its id in the migration inventory without storing it,
# because bundled ids are served from the package, and its legacy PostgreSQL row
# is package content rather than an operator's. The migration guard stops
# expecting these ids and the migration does not copy their rows; sagas that
# named one are detached by a Ting SQL migration. Without an archived id here a
# Ting whose file catalog was migrated while it shipped would refuse to start.
RETIRED_SYSTEM_WORKFLOW_IDS: frozenset[UUID] = (
    frozenset(
        {
            # "Ting Run Flow + Security + Memory Curation", removed in niuu#1282
            # with the wardens and memory curation it ran.
            UUID("29393e53-5a29-5f37-aa87-1171aaf20726"),
        }
    )
    | ARCHIVED_SYSTEM_WORKFLOW_IDS
)
logger = logging.getLogger(__name__)


def load_system_workflows(
    path: Path = BUNDLED_SYSTEM_WORKFLOWS_PATH,
    *,
    persona_source: PersonaPort | None = None,
    allow_archived: bool = False,
) -> list[WorkflowDefinition]:
    """Load and validate every packaged workflow document.

    ``allow_archived`` exists for tests that load the archived documents as fixtures
    (``tests/support/full_catalogue.py``); nothing that ships passes it, and a shipped
    directory holding an archived workflow stays a startup error.
    """
    if not path.is_dir():
        raise WorkflowDocumentError(f"Bundled workflow directory does not exist: {path}")
    workflows: list[WorkflowDefinition] = []
    seen: set[UUID] = set()
    persona_source = persona_source or FilesystemPersonaAdapter(
        persona_dirs=[], include_builtin=True
    )
    retired = RETIRED_SYSTEM_WORKFLOW_IDS - (
        ARCHIVED_SYSTEM_WORKFLOW_IDS if allow_archived else set()
    )
    for document_path in sorted(path.glob("*.yaml")):
        workflow = load_bundled_workflow(
            document_path,
            persona_source=persona_source,
            bundle_root=path.parent,
            allow_archived=allow_archived,
        )
        if workflow.id in seen:
            raise WorkflowDocumentError(
                f"Duplicate bundled workflow id {workflow.id} in {document_path}"
            )
        if workflow.id in retired:
            raise WorkflowDocumentError(
                f"Bundled workflow {document_path} uses retired system workflow id "
                f"{workflow.id}; give it a new id or remove it from "
                "RETIRED_SYSTEM_WORKFLOW_IDS"
            )
        seen.add(workflow.id)
        workflows.append(workflow)
    if not workflows:
        raise WorkflowDocumentError(f"Bundled workflow directory is empty: {path}")
    return workflows


def load_bundled_workflow(
    document_path: Path,
    *,
    persona_source: PersonaPort | None = None,
    bundle_root: Path | None = None,
    allow_archived: bool = False,
) -> WorkflowDefinition:
    """Load a package workflow with immutable built-in persona source documents."""
    try:
        document = load_workflow_document(document_path.read_text(encoding="utf-8"))
    except (OSError, WorkflowDocumentError) as exc:
        raise WorkflowDocumentError(f"Invalid bundled workflow {document_path}: {exc}") from exc
    if document.id in ARCHIVED_SYSTEM_WORKFLOW_IDS and not allow_archived:
        raise WorkflowDocumentError(
            f"Bundled workflow {document_path} is archived (id {document.id}) and must not "
            "ship. Move it back to archive/workflows, or restore it properly: remove its id "
            "from ARCHIVED_SYSTEM_WORKFLOW_IDS (see archive/README.md)."
        )
    source = persona_source or FilesystemPersonaAdapter(
        persona_dirs=[],
        include_builtin=True,
    )
    # Transport paths in a workflow document are package-root relative (for
    # example ``workflows/child.yaml``), never relative to the
    # document itself.  Keep the direct loader consistent with
    # ``load_system_workflows`` so callers cannot accidentally resolve a
    # dependency under ``workflows/workflows``.
    root = (bundle_root or BUNDLED_SYSTEM_PACKAGE_ROOT).resolve()
    persona_definitions, workflow_definitions = _load_bundled_definitions(
        document,
        document_path=document_path.resolve(),
        bundle_root=root,
        persona_source=source,
        lineage=(),
        allow_archived=allow_archived,
    )
    # Whole-graph validation that needs a copied stage's exact content — edge
    # endpoints, review-verdict-policy quorum membership, reviewAttestation's
    # joinMode-all binding, referenced persona aliases — is only meaningful
    # once every ``include`` node is resolved. The bundled set always has a
    # resolver (its own transitive closure, loaded above), so it always runs
    # this check; an include that cannot be resolved fails loudly here rather
    # than loading as a silently smaller graph.
    resolved_graph = resolve_workflow_includes(
        document.graph,
        persona_dependencies=document.persona_dependencies,
        workflow_dependencies=document.workflow_dependencies,
        resolve_alias=include_resolver_from_workflow_definitions(workflow_definitions),
    )
    validate_resolved_workflow_graph(
        resolved_graph,
        schema_version=document.schema_version,
        persona_dependencies=document.persona_dependencies,
        workflow_dependencies=document.workflow_dependencies,
        persona_definitions=persona_definitions,
    )
    timestamp = datetime.fromtimestamp(document_path.stat().st_mtime, tz=UTC)
    revision = workflow_document_revision(document)
    workflow = document.to_workflow(
        scope=WorkflowScope.SYSTEM,
        owner_id=None,
        created_at=timestamp,
        updated_at=timestamp,
        revision=revision,
        read_only=True,
        source=f"bundled:{document_path.name}",
        persona_definitions=persona_definitions,
        workflow_definitions=workflow_definitions,
    )
    return replace(workflow, document_revision=revision, origin="bundled")


def _load_bundled_definitions(
    document: WorkflowDocument,
    *,
    document_path: Path,
    bundle_root: Path,
    persona_source: PersonaPort,
    lineage: tuple[UUID, ...],
    allow_archived: bool,
) -> tuple[dict[str, dict], dict[str, dict]]:
    if document.id in lineage:
        cycle = " -> ".join(str(item) for item in (*lineage, document.id))
        raise WorkflowDocumentError(f"Bundled workflow dependency cycle: {cycle}")
    current_lineage = (*lineage, document.id)
    persona_definitions: dict[str, dict] = {}
    for alias, dependency in document.persona_dependencies.items():
        if dependency.id in ARCHIVED_BUILTIN_PERSONAS and not allow_archived:
            raise WorkflowDocumentError(
                archived_persona_message(
                    dependency.id,
                    where=f"Bundled workflow {document_path} persona {alias!r}",
                )
            )
        portable = persona_source.load_portable(dependency.id, dependency.revision)
        if portable is None or portable.digest != dependency.digest:
            raise WorkflowDocumentError(
                f"Bundled workflow {document_path} persona {alias!r} does not match exact pin "
                f"{dependency.id}@{dependency.revision} ({dependency.digest})"
            )
        persona_definitions[alias] = portable.to_dict()

    workflow_definitions: dict[str, dict] = {}
    for alias, dependency in document.workflow_dependencies.items():
        if dependency.id in ARCHIVED_SYSTEM_WORKFLOW_IDS and not allow_archived:
            raise WorkflowDocumentError(
                f"Bundled workflow {document_path} dependency {alias!r} is the archived "
                f"workflow {dependency.id}. Drop the dependency, or restore the workflow "
                "(see archive/README.md)."
            )
        if dependency.path is None:
            raise WorkflowDocumentError(
                f"Bundled workflow {document_path} dependency {alias!r} has no bundle path"
            )
        child_path = (bundle_root / dependency.path).resolve()
        if not child_path.is_relative_to(bundle_root) or not child_path.is_file():
            raise WorkflowDocumentError(
                f"Bundled workflow {document_path} dependency {alias!r} is unavailable at "
                f"{dependency.path}"
            )
        try:
            child = load_workflow_document(child_path.read_text(encoding="utf-8"))
        except (OSError, WorkflowDocumentError) as exc:
            raise WorkflowDocumentError(
                f"Invalid bundled child workflow {child_path}: {exc}"
            ) from exc
        child_revision = workflow_document_revision(child)
        if (
            child.id != dependency.id
            or child_revision != dependency.revision
            or child_revision != dependency.digest
        ):
            raise WorkflowDocumentError(
                f"Bundled workflow {document_path} dependency {alias!r} does not match exact "
                f"pin {dependency.id}@{dependency.revision} ({dependency.digest})"
            )
        child_personas, child_workflows = _load_bundled_definitions(
            child,
            document_path=child_path,
            bundle_root=bundle_root,
            persona_source=persona_source,
            lineage=current_lineage,
            allow_archived=allow_archived,
        )
        workflow_definitions[alias] = {
            "document": workflow_document_payload(child),
            "persona_definitions": child_personas,
            "workflow_definitions": child_workflows,
        }
    return persona_definitions, workflow_definitions


async def seed_system_workflows(
    repo: WorkflowRepository,
    *,
    path: Path = BUNDLED_SYSTEM_WORKFLOWS_PATH,
) -> list[WorkflowDefinition]:
    """Register bundled versions without replacing authored successors.

    A package restart is idempotent. Once an operator has created an authored
    successor for the same stable workflow identity, that head wins over future
    package seeding. Reusing one semantic version for different bundled content
    is rejected because versions are immutable -- but only once compared
    against a row this seeding path (or an authored save) actually wrote
    itself. A row with no recorded version history at all was never written
    by either: it is a legacy row that predates persona pinning and schema
    versioning (its ``version_origin`` was reclassified from ``authored`` to
    ``bundled`` by migration 000046, having previously been skipped entirely
    by the ``origin == "authored"`` check below). Comparing its stale shape
    against the current packaged document would always disagree even when
    the visible content (name, graph, version) matches, so it is instead
    handed to ``adopt_legacy_bundled``, which publishes the packaged
    rendering under its immutable identity without an intermediate content
    comparison and without colliding the legacy and current snapshots in the
    same archived version slot.

    A never-versioned row reclassified by 000046 whose id is not in the
    current package at all (an admin-created system row, not something
    Ting ever packaged) is not deleted -- 000046 cannot tell that apart from
    a legacy packaged row at the SQL level -- it is flipped back to
    ``authored`` here instead, and logged.
    """
    seeds = load_system_workflows(path)
    existing = await repo.list_workflows(owner_id="", scope=WorkflowScope.SYSTEM)

    if not seeds:
        return []

    existing_by_id = {workflow.id: workflow for workflow in existing}
    seed_ids = {seed.id for seed in seeds}

    for workflow_id, orphan in existing_by_id.items():
        if workflow_id in seed_ids or orphan.origin != "bundled":
            continue
        if await repo.has_recorded_version_history(workflow_id):
            continue
        logger.warning(
            "System workflow %s (%r) is not part of the current package and has no "
            "recorded version history (likely a pre-#1012 admin-created row "
            "migration 000046 could not tell apart from legacy packaged content); "
            "reclassifying it as authored instead of deleting it.",
            workflow_id,
            orphan.name,
        )
        await repo.reclassify_orphaned_bundled_as_authored(workflow_id)

    saved: list[WorkflowDefinition] = []
    for seed in seeds:
        current = existing_by_id.get(seed.id)
        if current is None:
            saved.append(
                await repo.save_workflow(
                    replace(seed, revision=None, read_only=True, source="postgres")
                )
            )
            continue
        if current.origin == "authored":
            saved.append(current)
            continue
        if not await repo.has_recorded_version_history(seed.id):
            saved.append(await repo.adopt_legacy_bundled(seed))
            continue
        current_document_revision = current.document_revision or workflow_document_revision(current)
        seed_document_revision = seed.document_revision or workflow_document_revision(seed)
        if current.version == seed.version:
            if current_document_revision != seed_document_revision:
                raise WorkflowDocumentError(
                    f"Bundled workflow {seed.id} version {seed.version} changed content; "
                    "publish it under a new version"
                )
            saved.append(current)
            continue
        saved.append(
            await repo.save_workflow(
                replace(
                    seed,
                    created_at=current.created_at,
                    updated_at=datetime.now(UTC),
                    revision=current.revision,
                    read_only=True,
                    source="postgres",
                )
            )
        )
    return saved
