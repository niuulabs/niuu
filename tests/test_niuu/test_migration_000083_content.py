"""Migration 000083 must exist, byte-identical, in all four required
locations, and must include the (node_id, kind) unique index that closes
the concurrent-heartbeat duplicate-kind race.

See `.claude/rules/migrations.md` (dual-location requirement) and the
security review that added the fourth/fifth locations (CLI-embedded
migration stream, both chart configmaps).
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


def test_all_four_locations_are_byte_identical() -> None:
    up = (ROOT / "migrations/000083_guild_node_join.up.sql").read_text()
    down = (ROOT / "migrations/000083_guild_node_join.down.sql").read_text()

    cli_up = (ROOT / "src/cli/migrations/volundr/000083_guild_node_join.up.sql").read_text()
    cli_down = (ROOT / "src/cli/migrations/volundr/000083_guild_node_join.down.sql").read_text()
    assert cli_up == up
    assert cli_down == down

    for chart in ("volundr", "guild"):
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
