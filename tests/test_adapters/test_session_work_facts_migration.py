"""Migration 000094 (G10, F12d) adds the session work-fact columns."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAME = "000094_session_work_facts"


def test_up_is_idempotent_and_down_reverses_it():
    up = (ROOT / "migrations" / f"{NAME}.up.sql").read_text()
    down = (ROOT / "migrations" / f"{NAME}.down.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS last_work_at TIMESTAMPTZ;" in up
    assert "ADD COLUMN IF NOT EXISTS open_tool_count INTEGER NOT NULL DEFAULT 0;" in up
    assert "DROP COLUMN IF EXISTS last_work_at;" in down
    assert "DROP COLUMN IF EXISTS open_tool_count;" in down


def test_number_is_unique_and_follows_its_predecessor():
    numbers = sorted(p.name[:6] for p in (ROOT / "migrations").glob("*.up.sql"))
    assert numbers.count(NAME[:6]) == 1
    assert numbers[numbers.index(NAME[:6]) - 1] == "000093"
