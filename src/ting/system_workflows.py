"""Bundled Ting system workflow seeding."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid5

import yaml

from ting.domain.models import WorkflowDefinition, WorkflowScope
from ting.ports.workflow_repository import WorkflowRepository

_SYSTEM_WORKFLOW_NAMESPACE = UUID("1ff2db43-b5f4-49ec-a945-d9a2b4b2bd3f")
BUNDLED_SYSTEM_WORKFLOWS_PATH = (Path(__file__).parent / "system_workflows.yaml").resolve()


def load_system_workflows(path: Path = BUNDLED_SYSTEM_WORKFLOWS_PATH) -> list[WorkflowDefinition]:
    """Load bundled system workflows from YAML."""
    if not path.exists():
        return []

    raw = yaml.safe_load(path.read_text()) or []
    if not isinstance(raw, list):
        return []

    now = datetime.now(UTC)
    workflows: list[WorkflowDefinition] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        description = str(entry.get("description") or "").strip()
        version = str(entry.get("version") or "1.0.0").strip() or "1.0.0"
        graph = dict(entry.get("graph") or {})
        workflow_id = uuid5(_SYSTEM_WORKFLOW_NAMESPACE, name)
        workflows.append(
            WorkflowDefinition(
                id=workflow_id,
                name=name,
                description=description,
                version=version,
                scope=WorkflowScope.SYSTEM,
                owner_id=None,
                graph=graph,
                created_at=now,
                updated_at=now,
            )
        )
    return workflows


async def seed_system_workflows(
    repo: WorkflowRepository,
    *,
    path: Path = BUNDLED_SYSTEM_WORKFLOWS_PATH,
) -> list[WorkflowDefinition]:
    """Upsert bundled system workflows into the workflow catalog.

    The bundled YAML is the source of truth for system workflows. Any older
    system workflow rows that are no longer present in the bundle, or duplicate
    rows left behind by earlier seeds, are removed during this pass.
    """
    seeds = load_system_workflows(path)
    existing = await repo.list_workflows(owner_id="", scope=WorkflowScope.SYSTEM)
    seed_by_id = {workflow.id: workflow for workflow in seeds}

    for workflow in existing:
        if workflow.id not in seed_by_id:
            await repo.delete_workflow(workflow.id)

    if not seeds:
        return []

    existing_by_id = {workflow.id: workflow for workflow in existing}

    saved: list[WorkflowDefinition] = []
    for seed in seeds:
        current = existing_by_id.get(seed.id)
        if current is not None:
            seed = replace(
                seed,
                id=current.id,
                created_at=current.created_at,
                updated_at=datetime.now(UTC),
            )
        saved.append(await repo.save_workflow(seed))
    return saved
