"""Real-Postgres regression test for the pre-#1012 legacy system row bug.

Reproduces, against a real PostgreSQL 16 (via `txn_pool`), the startup crash
an independent reviewer found: `seed_system_workflows` raising
`WorkflowConflictError("A different immutable workflow version already
exists")` for legacy system rows migration 000046 reclassifies to
`version_origin: bundled`. That crash came from `_advance()` archiving the
legacy row and the new packaged head under the same `(id, version)` slot
when their versions match — `_InMemoryWorkflowRepository`-based unit tests
cannot reproduce it, since they have no archive-conflict semantics.

Setup mirrors production: real system rows shaped exactly like pre-#1012
content (no persona pins, `schema_version` defaulted to 1, no recorded
`workflow_versions` history, `version_origin: authored` from migration
000044's backfill) plus one ordinary user-scope row, then migration 000046's
own SQL applied directly, then the real `seed_system_workflows` against a
real `PostgresWorkflowRepository`.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from ting.adapters.postgres_workflows import PostgresWorkflowRepository
from ting.domain.models import WorkflowScope
from ting.system_workflows import load_system_workflows, seed_system_workflows

_MIGRATION_000046 = (
    Path(__file__).resolve().parents[3]
    / "migrations"
    / "ting"
    / "000046_reclassify_legacy_system_workflows.up.sql"
)


async def _create_and_regress_to_legacy_shape(repo, pool, workflow, *, scope=WorkflowScope.SYSTEM):
    """Persist `workflow`, then flatten it to a pre-#1012 legacy row shape.

    save_workflow's own initial-creation path already sets modern columns
    (persona pins, schema_version 2, version_origin 'bundled', an archived
    workflow_versions entry). Regressing those columns directly via SQL
    reproduces exactly what a pre-#1012 row looks like today: content the
    package still recognizes, but none of the bookkeeping post-#1012 code
    ever wrote for it.
    """
    seed = replace(
        workflow,
        scope=scope,
        owner_id=None if scope == WorkflowScope.SYSTEM else "legacy-owner",
        revision=None,
        read_only=True,
        source="postgres",
    )
    saved = await repo.save_workflow(seed)
    await pool.execute(
        """
        UPDATE workflows
        SET version_origin = 'authored',
            based_on_revision = NULL,
            schema_version = 1,
            persona_dependencies_json = '{}'::jsonb,
            persona_definitions_json = '{}'::jsonb
        WHERE id = $1
        """,
        saved.id,
    )
    await pool.execute("DELETE FROM workflow_versions WHERE workflow_id = $1", saved.id)
    return saved


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_legacy_system_rows_seed_successfully_after_reclassification(txn_pool) -> None:
    repo = PostgresWorkflowRepository(txn_pool)
    seeds = load_system_workflows()

    legacy_rows = {}
    for seed in seeds:
        legacy_rows[seed.id] = await _create_and_regress_to_legacy_shape(repo, txn_pool, seed)

    user_row = await _create_and_regress_to_legacy_shape(
        repo,
        txn_pool,
        replace(seeds[0], id=uuid4(), name="A user's own workflow"),
        scope=WorkflowScope.USER,
    )

    orphan = await _create_and_regress_to_legacy_shape(
        repo,
        txn_pool,
        replace(seeds[0], id=uuid4(), name="Admin-created system workflow"),
    )

    # Migration 000046, applied for real: reclassifies every qualifying
    # legacy system row (the package-matching ones AND the orphan) to
    # version_origin='bundled'; the user row is untouched (scope != system).
    await txn_pool.execute(_MIGRATION_000046.read_text(encoding="utf-8"))

    saved = await seed_system_workflows(repo)

    assert len(saved) == len(seeds)
    for workflow in saved:
        packaged = next(seed for seed in seeds if seed.id == workflow.id)
        assert workflow.version == packaged.version
        assert workflow.graph == packaged.graph
        assert workflow.persona_dependencies == packaged.persona_dependencies
        assert workflow.schema_version == packaged.schema_version
        assert workflow.origin == "bundled"
        assert workflow.read_only is True
        assert workflow.created_at == legacy_rows[workflow.id].created_at

    unchanged_user_row = await repo.get_workflow(user_row.id)
    assert unchanged_user_row is not None
    assert unchanged_user_row.origin == "authored"
    assert unchanged_user_row.schema_version == 1
    assert unchanged_user_row.persona_dependencies == {}

    reclassified_orphan = await repo.get_workflow(orphan.id)
    assert reclassified_orphan is not None
    assert reclassified_orphan.origin == "authored"

    # Second pass: every row now has recorded history (or is 'authored'), so
    # this is a pure no-op -- no crash, same heads, nothing rewritten.
    rerun = await seed_system_workflows(repo)
    assert {workflow.id: workflow.version for workflow in rerun} == {
        workflow.id: workflow.version for workflow in saved
    }
    for workflow in rerun:
        assert await repo.has_recorded_version_history(workflow.id) is True
