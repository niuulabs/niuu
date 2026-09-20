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
from ting.domain.services.workflow_migration import migrate_workflow_catalog
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
            document = await registry.get_current_portable_persona(
                workflow.owner_id, persona_id
            )
        else:
            document = builtins.load_current_portable(persona_id)
        if document is not None:
            documents.append(document)
    return PortablePersonaCollection(documents)


async def _run(
    *,
    catalog_path: str,
    apply: bool,
    persona_dirs: list[str],
    persona_database: str,
    replace_divergent_bundled: bool,
) -> int:
    settings = Settings()
    target = FilesystemWorkflowRepository(catalog_path)
    builtins = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True)
    async with database_pool(settings.database) as pool:
        if persona_dirs:
            selected_source = FilesystemPersonaAdapter(
                persona_dirs=persona_dirs,
                include_builtin=True,
            )

            async def source_for_workflow(
                _workflow: WorkflowDefinition,
            ) -> PortablePersonaSource:
                return selected_source

            report = await migrate_workflow_catalog(
                source=PostgresWorkflowRepository(pool),
                target=target,
                persona_source_for_workflow=source_for_workflow,
                bundled_workflows=load_system_workflows(),
                apply=apply,
                replace_divergent_bundled=replace_divergent_bundled,
            )
        else:
            persona_database_config: DatabaseConfig = settings.database.model_copy(
                update={"name": persona_database}
            )
            async with database_pool(persona_database_config) as persona_pool:
                registry = PostgresPersonaRegistry(persona_pool, builtin_loader=builtins)

                async def source_for_workflow(
                    workflow: WorkflowDefinition,
                ) -> PortablePersonaSource:
                    return await _registry_source_for_workflow(
                        workflow, registry, builtins
                    )

                report = await migrate_workflow_catalog(
                    source=PostgresWorkflowRepository(pool),
                    target=target,
                    persona_source_for_workflow=source_for_workflow,
                    bundled_workflows=load_system_workflows(),
                    apply=apply,
                    replace_divergent_bundled=replace_divergent_bundled,
                )
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    if report.errors:
        return 2
    return 0


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
            )
        )
    )


if __name__ == "__main__":
    main()
