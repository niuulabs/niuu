"""Migration 000125 widens the token counts a long session outgrows (int4 overflow, HTTP 500).

Reporting usage past 2^31 - 1 against PostgreSQL is covered in
``tests/integration/volundr/test_token_usage.py``.
"""

from pathlib import Path

import pytest

from volundr.infrastructure import database

ROOT = Path(__file__).resolve().parents[2]
UP = (ROOT / "migrations/000125_token_counts_bigint.up.sql").read_text()
DOWN = (ROOT / "migrations/000125_token_counts_bigint.down.sql").read_text()
COLUMNS = [("sessions", "tokens_used"), ("token_usage", "tokens"), ("chronicles", "token_usage")]


@pytest.mark.parametrize(("table", "column"), COLUMNS)
def test_up_widens_and_down_restores(table, column):
    assert f"ALTER TABLE {table} ALTER COLUMN {column} TYPE BIGINT;" in UP
    assert f"ALTER TABLE {table} ALTER COLUMN {column} TYPE INTEGER;" in DOWN


@pytest.mark.parametrize(
    "ddl",
    [
        (database.SESSIONS_TABLE_SQL, "tokens_used BIGINT"),
        (database.TOKEN_USAGE_TABLE_SQL, "tokens BIGINT"),
        (database.CHRONICLES_TABLE_SQL, "token_usage BIGINT"),
    ],
)
def test_the_development_schema_matches(ddl):
    sql, column = ddl
    assert column in sql


@pytest.mark.parametrize("chart", ["guild", "niuu-shared"])
def test_the_tailored_migration_sets_own_none_of_these_tables(chart):
    """A table one of them created would need the same widening there."""
    tailored = (ROOT / f"charts/{chart}/templates/migrations-configmap.yaml").read_text()

    for table, _ in COLUMNS:
        assert f"CREATE TABLE IF NOT EXISTS {table} " not in tailored
        assert f"ALTER TABLE {table} " not in tailored
