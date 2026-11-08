"""Migration 000113 drops the tables only the removed Ravn runtime used."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UP = (ROOT / "migrations" / "000113_drop_ravn_runtime_tables.up.sql").read_text()
DOWN = (ROOT / "migrations" / "000113_drop_ravn_runtime_tables.down.sql").read_text()

DROPPED = (
    "ravn_budget_ledger",
    "ravn_triggers",
    "ravn_checkpoint_snapshots",
    "ravn_checkpoints",
    "ravn_sessions",
    "ravn_episodes",
)


def test_up_drops_every_table_the_removed_runtime_owned() -> None:
    statements = [line for line in UP.splitlines() if line.startswith("DROP")]

    assert statements == [
        *(f"DROP TABLE IF EXISTS {table};" for table in DROPPED),
        "DROP FUNCTION IF EXISTS ravn_episode_tsv(TEXT, TEXT, TEXT[]);",
    ]


def test_up_keeps_the_persona_registry_table() -> None:
    assert "DROP TABLE IF EXISTS ravn_personas" not in UP


def test_down_recreates_each_dropped_table() -> None:
    for table in DROPPED:
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in DOWN
    assert "CREATE OR REPLACE FUNCTION ravn_episode_tsv" in DOWN


def test_down_restores_the_columns_later_migrations_added() -> None:
    # 000032 added the reflection columns, 000031 the snapshot columns.
    for column in ("reflection", "cost_usd", "duration_seconds", "memory_context", "seq"):
        assert column in DOWN
