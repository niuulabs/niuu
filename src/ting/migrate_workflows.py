"""Operator command for verified database-to-file workflow migration."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from ravn.adapters.personas.loader import FilesystemPersonaAdapter
from ravn.adapters.personas.postgres_registry import PostgresPersonaRegistry
from ravn.domain.persona_document import PortablePersonaCollection, PortablePersonaSource
from ting.adapters.filesystem_workflows import FilesystemWorkflowRepository
from ting.adapters.postgres_workflows import PostgresWorkflowRepository
from ting.config import DatabaseConfig, Settings
from ting.domain.models import WorkflowDefinition
from ting.domain.services.workflow_migration import (
    migrate_workflow_catalog,
    workflow_catalog_migration_is_current,
)
from ting.domain.workflow_document import referenced_persona_aliases
from ting.infrastructure.database import database_pool
from ting.system_workflows import load_system_workflows


async def _registry_source_for_workflow(
    workflow: WorkflowDefinition,
    registry: PostgresPersonaRegistry,
    builtins: FilesystemPersonaAdapter,
) -> PortablePersonaSource:
    """Hydrate only this workflow owner's persona definitions into memory."""
    documents = []
    for alias in sorted(referenced_persona_aliases(workflow.graph)):
        if alias in workflow.persona_definitions:
            continue
        dependency = workflow.persona_dependencies.get(alias)
        persona_id = dependency.id if dependency is not None else alias
        if workflow.owner_id:
            document = await registry.get_current_portable_persona(workflow.owner_id, persona_id)
        else:
            document = builtins.load_current_portable(persona_id)
        if document is not None:
            documents.append(document)
    return PortablePersonaCollection(documents)


async def _skip_reason(target: FilesystemWorkflowRepository, pool: object) -> str | None:
    """Return why the migration is already satisfied, or None if it must run."""
    legacy_state = await pool.fetchrow(  # type: ignore[attr-defined]
        "SELECT COUNT(*) AS row_count, MAX(updated_at) AS max_updated_at FROM workflows"
    )
    legacy_count = int(legacy_state["row_count"])
    if not legacy_count:
        return "PostgreSQL has zero workflow rows; nothing to migrate"
    is_current = await workflow_catalog_migration_is_current(
        target,
        legacy_count=legacy_count,
        legacy_max_updated_at=legacy_state["max_updated_at"],
    )
    if is_current:
        return "the file catalog is already a verified, current copy of the PostgreSQL catalog"
    return None


def _print_skip_report(reason: str) -> None:
    print(
        json.dumps(
            {
                "skipped": True,
                "reason": reason,
                "applied": False,
                "can_apply": False,
                "errors": [],
            },
            indent=2,
            sort_keys=True,
        )
    )


async def _migrate(
    *,
    pool: object,
    target: FilesystemWorkflowRepository,
    builtins: FilesystemPersonaAdapter,
    settings: Settings,
    persona_dirs: list[str],
    persona_database: str,
    apply: bool,
    replace_divergent_bundled: bool,
) -> object:
    """Resolve the persona source (explicit dirs, or each owner's registry) and migrate."""
    if persona_dirs:
        selected_source = FilesystemPersonaAdapter(
            persona_dirs=persona_dirs,
            include_builtin=True,
        )

        async def source_for_workflow(
            _workflow: WorkflowDefinition,
        ) -> PortablePersonaSource:
            return selected_source

        return await migrate_workflow_catalog(
            source=PostgresWorkflowRepository(pool),
            target=target,
            persona_source_for_workflow=source_for_workflow,
            bundled_workflows=load_system_workflows(),
            apply=apply,
            replace_divergent_bundled=replace_divergent_bundled,
        )

    persona_database_config: DatabaseConfig = settings.database.model_copy(
        update={"name": persona_database}
    )
    async with database_pool(persona_database_config) as persona_pool:
        registry = PostgresPersonaRegistry(persona_pool, builtin_loader=builtins)

        async def source_for_workflow(
            workflow: WorkflowDefinition,
        ) -> PortablePersonaSource:
            return await _registry_source_for_workflow(workflow, registry, builtins)

        return await migrate_workflow_catalog(
            source=PostgresWorkflowRepository(pool),
            target=target,
            persona_source_for_workflow=source_for_workflow,
            bundled_workflows=load_system_workflows(),
            apply=apply,
            replace_divergent_bundled=replace_divergent_bundled,
        )


async def _run(
    *,
    catalog_path: str,
    apply: bool,
    persona_dirs: list[str],
    persona_database: str,
    replace_divergent_bundled: bool,
    skip_if_migrated: bool,
) -> int:
    settings = Settings()
    target = FilesystemWorkflowRepository(catalog_path)
    builtins = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True)
    migrate_kwargs = {
        "target": target,
        "builtins": builtins,
        "settings": settings,
        "persona_dirs": persona_dirs,
        "persona_database": persona_database,
        "apply": apply,
        "replace_divergent_bundled": replace_divergent_bundled,
    }
    async with database_pool(settings.database) as pool:
        if not skip_if_migrated:
            report = await _migrate(pool=pool, **migrate_kwargs)
        else:
            # Several init containers may run this concurrently against the
            # same catalog (N replicas, N pods rolling under Recreate).
            # Holding this lock for the whole check-then-apply span makes the
            # first one to acquire it perform the migration and every later
            # one see the fresh marker and skip -- see
            # FilesystemWorkflowRepository.cutover_lock.
            with target.cutover_lock():
                reason = await _skip_reason(target, pool)
                if reason is not None:
                    _print_skip_report(reason)
                    return 0
                report = await _migrate(pool=pool, **migrate_kwargs)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 2 if report.errors else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and migrate Ting workflow definitions from PostgreSQL to files."
    )
    parser.add_argument(
        "--catalog-path",
        required=True,
        help="Existing durable writable workflow catalog directory.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Publish only after the full inventory validates; omission is a dry run.",
    )
    parser.add_argument(
        "--persona-dir",
        action="append",
        default=[],
        help=(
            "Explicit shared persona source directory. Repeat for multiple directories. "
            "When omitted, personas are read from each workflow owner's registry row."
        ),
    )
    parser.add_argument(
        "--persona-database",
        default="volundr",
        help=(
            "Persona registry database name on the configured PostgreSQL host "
            "(default: volundr). Ignored when --persona-dir is supplied."
        ),
    )
    parser.add_argument(
        "--replace-divergent-bundled",
        action="store_true",
        help=(
            "Explicitly preserve divergent database system rows under their existing UUIDs "
            "and suppress the same packaged identities."
        ),
    )
    parser.add_argument(
        "--skip-if-migrated",
        action="store_true",
        help=(
            "Exit 0 without writing anything when PostgreSQL has zero workflow rows, or the "
            "file catalog is already a verified, current copy of it. For automated cutover: "
            "safe to run on every start once the operator has applied the migration once."
        ),
    )
    args = parser.parse_args()
    catalog_path = Path(args.catalog_path).expanduser().resolve()
    raise SystemExit(
        asyncio.run(
            _run(
                catalog_path=str(catalog_path),
                apply=args.apply,
                persona_dirs=args.persona_dir,
                persona_database=args.persona_database,
                replace_divergent_bundled=args.replace_divergent_bundled,
                skip_if_migrated=args.skip_if_migrated,
            )
        )
    )


if __name__ == "__main__":
    main()
