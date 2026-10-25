"""Migration 000083 must include the (node_id, kind) unique index that closes
the concurrent-heartbeat duplicate-kind race, and Guild's own migration set
must carry it identically.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
UNIQUE_KIND_INDEX = "idx_niuu_instances_node_kind"


def _chart_migration(chart_configmap: Path, filename: str) -> str:
    template = chart_configmap.read_text()
    literal = template[template.index("\ndata:\n") + 1 : template.rindex("{{- end")]
    return yaml.safe_load(literal)["data"][filename]


def test_up_migration_adds_a_unique_index_on_node_id_and_kind() -> None:
    up = (ROOT / "migrations/000083_guild_node_join.up.sql").read_text()
    assert f"CREATE UNIQUE INDEX IF NOT EXISTS {UNIQUE_KIND_INDEX}" in up
    assert "ON niuu_instances(node_id, kind)" in up


def test_down_migration_drops_the_unique_index() -> None:
    down = (ROOT / "migrations/000083_guild_node_join.down.sql").read_text()
    assert f"DROP INDEX IF EXISTS {UNIQUE_KIND_INDEX}" in down


def test_guild_migration_set_carries_it_identically() -> None:
    up = (ROOT / "migrations/000083_guild_node_join.up.sql").read_text()
    down = (ROOT / "migrations/000083_guild_node_join.down.sql").read_text()

    # Volundr's chart reads migrations/ itself (test_migrations_single_source);
    # Guild's chart carries its own tailored set, so it must be checked here.
    for chart in ("guild",):
        configmap = ROOT / f"charts/{chart}/templates/migrations-configmap.yaml"
        chart_up = _chart_migration(configmap, "000083_guild_node_join.up.sql")
        chart_down = _chart_migration(configmap, "000083_guild_node_join.down.sql")
        assert chart_up.rstrip("\n") == up.rstrip("\n"), chart
        assert chart_down.rstrip("\n") == down.rstrip("\n"), chart


def test_dev_bootstrap_sql_also_creates_the_unique_index() -> None:
    """Mini/docker mode's embedded-postgres bootstrap
    (niuu.service_databases.GUILD_BOOTSTRAP_SQL) must match the migration —
    a dev host never runs `migrate`."""
    from niuu.service_databases import GUILD_BOOTSTRAP_SQL

    assert any(
        UNIQUE_KIND_INDEX in statement and "node_id, kind" in statement
        for statement in GUILD_BOOTSTRAP_SQL
    )
