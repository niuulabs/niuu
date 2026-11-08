"""Migration 000112 strips the persona ``executor`` key on every path that holds the table."""

from __future__ import annotations

from pathlib import Path

import yaml

from niuu.service_databases import NIUU_SHARED_BOOTSTRAP_SQL

ROOT = Path(__file__).resolve().parents[2]
UP = "000112_strip_persona_executor.up.sql"
DOWN = "000112_strip_persona_executor.down.sql"


def _shared_chart_migration(filename: str) -> str:
    template = (ROOT / "charts/niuu-shared/templates/migrations-configmap.yaml").read_text()
    literal = template[template.index("\ndata:\n") + 1 : template.rindex("{{- end")]
    return yaml.safe_load(literal)["data"][filename]


def test_the_migration_removes_the_key_from_both_stored_documents() -> None:
    up = (ROOT / "migrations" / UP).read_text()

    assert "SET config_json = config_json - 'executor'" in up
    assert "runtime_config_json = runtime_config_json - 'executor'" in up
    assert "WHERE config_json ? 'executor'" in up
    assert "OR runtime_config_json ? 'executor'" in up


def test_the_down_migration_is_a_declared_no_op() -> None:
    down = (ROOT / "migrations" / DOWN).read_text()

    assert "deliberately a no-op" in down
    assert "SELECT 1;" in down


def test_the_shared_chart_carries_the_same_migration() -> None:
    assert _shared_chart_migration(UP) == (ROOT / "migrations" / UP).read_text()
    assert _shared_chart_migration(DOWN) == (ROOT / "migrations" / DOWN).read_text()


def test_mini_mode_strips_the_key_after_the_table_exists() -> None:
    bootstrap = "\n".join(NIUU_SHARED_BOOTSTRAP_SQL)
    strip = "SET config_json = config_json - 'executor'"

    assert strip in bootstrap
    assert bootstrap.index("CREATE TABLE IF NOT EXISTS ravn_personas") < bootstrap.index(strip)
