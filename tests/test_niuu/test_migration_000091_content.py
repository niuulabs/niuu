"""Migration 000091 links realms to a project on every path that creates the table."""

from __future__ import annotations

from pathlib import Path

import yaml

from niuu.service_databases import NIUU_SHARED_BOOTSTRAP_SQL

ROOT = Path(__file__).resolve().parents[2]
UP = "000091_realm_project.up.sql"
DOWN = "000091_realm_project.down.sql"


def _shared_chart_migration(filename: str) -> str:
    template = (ROOT / "charts/niuu-shared/templates/migrations-configmap.yaml").read_text()
    literal = template[template.index("\ndata:\n") + 1 : template.rindex("{{- end")]
    return yaml.safe_load(literal)["data"][filename]


def test_the_shared_chart_carries_the_same_migration() -> None:
    assert _shared_chart_migration(UP) == (ROOT / "migrations" / UP).read_text()
    assert _shared_chart_migration(DOWN) == (ROOT / "migrations" / DOWN).read_text()


def test_mini_mode_bootstraps_the_column() -> None:
    bootstrap = "\n".join(NIUU_SHARED_BOOTSTRAP_SQL)

    assert "ALTER TABLE realms ADD COLUMN IF NOT EXISTS project_id UUID" in bootstrap
    assert bootstrap.index("CREATE TABLE IF NOT EXISTS realms") < bootstrap.index(
        "ALTER TABLE realms ADD COLUMN IF NOT EXISTS project_id UUID"
    )
