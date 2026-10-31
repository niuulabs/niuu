"""Migration 000090 gives realms a tenant on both databases that hold them.

Völundr's chart reads migrations/ itself, while the niuu-shared chart carries
its own set and mini mode bootstraps niuu_shared from
``NIUU_SHARED_BOOTSTRAP_SQL``, so those two are checked here.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from niuu.service_databases import NIUU_SHARED_BOOTSTRAP_SQL

ROOT = Path(__file__).resolve().parents[2]
UP = "000090_realm_tenancy.up.sql"
DOWN = "000090_realm_tenancy.down.sql"


def _shared_chart_migration(filename: str) -> str:
    template = (ROOT / "charts/niuu-shared/templates/migrations-configmap.yaml").read_text()
    literal = template[template.index("\ndata:\n") + 1 : template.rindex("{{- end")]
    return yaml.safe_load(literal)["data"][filename]


def _migration(filename: str) -> str:
    return (ROOT / "migrations" / filename).read_text()


def test_up_replaces_the_global_slug_constraint() -> None:
    up = _migration(UP)

    assert "DROP CONSTRAINT IF EXISTS realms_slug_key" in up
    assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_realms_tenant_slug" in up
    assert "ON realms (tenant_id, slug)" in up


def test_up_tolerates_a_database_without_resident_runtimes() -> None:
    assert "to_regclass('resident_runtimes') IS NOT NULL" in _migration(UP)


def test_backfill_only_fills_empty_tenants() -> None:
    up = _migration(UP)

    assert up.count("UPDATE realms r") == 3
    assert up.count("r.tenant_id = ''") == 3


def test_down_restores_the_global_slug_constraint() -> None:
    down = _migration(DOWN)

    assert "DROP INDEX IF EXISTS idx_realms_tenant_slug" in down
    assert "ADD CONSTRAINT realms_slug_key UNIQUE (slug)" in down
    assert "DROP COLUMN IF EXISTS tenant_id" in down


def test_shared_chart_carries_the_migration_identically() -> None:
    for filename in (UP, DOWN):
        assert _shared_chart_migration(filename).rstrip("\n") == _migration(filename).rstrip("\n")


def test_shared_bootstrap_applies_it_after_memberships_exist() -> None:
    statements = list(NIUU_SHARED_BOOTSTRAP_SQL)
    memberships = next(
        index
        for index, statement in enumerate(statements)
        if "CREATE TABLE IF NOT EXISTS tenant_memberships" in statement
    )
    tenancy = next(
        index for index, statement in enumerate(statements) if "idx_realms_tenant_slug" in statement
    )

    assert memberships < tenancy
    assert "ADD COLUMN IF NOT EXISTS tenant_id" in statements[tenancy]
    assert "DROP CONSTRAINT IF EXISTS realms_slug_key" in statements[tenancy]
