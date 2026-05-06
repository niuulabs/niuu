"""Tests for the task_outcomes → episodes data migration script (NIU-574)."""

from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ravn.adapters.memory.outcome_to_episode_migration import main, migrate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _make_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE episodes (
            episode_id       TEXT PRIMARY KEY,
            session_id       TEXT NOT NULL,
            timestamp        TEXT NOT NULL,
            reflection       TEXT,
            errors           TEXT,
            cost_usd         REAL,
            duration_seconds REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE task_outcomes (
            task_id          TEXT PRIMARY KEY,
            task_summary     TEXT,
            outcome          TEXT,
            reflection       TEXT,
            errors           TEXT,
            cost_usd         REAL,
            duration_seconds REAL,
            timestamp        TEXT
        )
        """
    )
    conn.commit()
    return conn


def _insert_episode(
    conn: sqlite3.Connection,
    episode_id: str,
    session_id: str,
    ts: datetime,
    *,
    reflection: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO episodes VALUES (?, ?, ?, ?, NULL, NULL, NULL)",
        (episode_id, session_id, ts.isoformat(), reflection),
    )
    conn.commit()


def _insert_outcome(
    conn: sqlite3.Connection,
    task_id: str,
    ts: datetime,
    *,
    reflection: str = "good work",
    errors: str = "[]",
    cost_usd: float = 0.01,
    duration: float = 1.5,
) -> None:
    conn.execute(
        """
        INSERT INTO task_outcomes
          (task_id, task_summary, outcome, reflection, errors,
           cost_usd, duration_seconds, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (task_id, "summary", "success", reflection, errors, cost_usd, duration, ts.isoformat()),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMigrateFunction:
    def test_no_task_outcomes_table_returns_zero(self, tmp_path: Path) -> None:
        """If task_outcomes doesn't exist, migrate returns 0."""
        db = tmp_path / "mem.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE episodes (episode_id TEXT PRIMARY KEY, session_id TEXT, timestamp TEXT, "
            "reflection TEXT, errors TEXT, cost_usd REAL, duration_seconds REAL)"
        )
        conn.commit()
        conn.close()

        result = migrate(db)
        assert result == 0

    def test_empty_task_outcomes_returns_zero(self, tmp_path: Path) -> None:
        """If task_outcomes exists but is empty, migrate returns 0."""
        db = tmp_path / "mem.db"
        conn = _make_db(db)
        conn.close()

        result = migrate(db)
        assert result == 0

    def test_matching_episode_updated(self, tmp_path: Path) -> None:
        """Outcome matching an episode within 60s updates the episode."""
        db = tmp_path / "mem.db"
        conn = _make_db(db)
        _insert_episode(conn, "ep-1", "sess-1", _NOW)
        _insert_outcome(conn, "oc-1", _NOW, reflection="great job", cost_usd=0.05)
        conn.close()

        updated = migrate(db)
        assert updated == 1

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM episodes WHERE episode_id='ep-1'").fetchone()
        conn.close()
        assert row["reflection"] == "great job"
        assert row["cost_usd"] == pytest.approx(0.05)

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        """dry_run=True counts matches but does not modify the database."""
        db = tmp_path / "mem.db"
        conn = _make_db(db)
        _insert_episode(conn, "ep-1", "sess-1", _NOW)
        _insert_outcome(conn, "oc-1", _NOW, reflection="dry")
        conn.close()

        updated = migrate(db, dry_run=True)
        assert updated == 1  # counted but not written

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT reflection FROM episodes WHERE episode_id='ep-1'").fetchone()
        conn.close()
        assert row["reflection"] is None  # not touched

    def test_no_episode_within_60s_skipped(self, tmp_path: Path) -> None:
        """Outcome whose closest episode is >60s away is skipped."""

        db = tmp_path / "mem.db"
        conn = _make_db(db)
        far_ts = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        _insert_episode(conn, "ep-far", "sess-1", far_ts)
        # outcome is 2 hours after the episode
        outcome_ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        _insert_outcome(conn, "oc-1", outcome_ts)
        conn.close()

        updated = migrate(db)
        assert updated == 0

    def test_existing_reflection_not_overwritten(self, tmp_path: Path) -> None:
        """Episode that already has a reflection is not overwritten (WHERE reflection IS NULL)."""
        db = tmp_path / "mem.db"
        conn = _make_db(db)
        conn.execute(
            "INSERT INTO episodes VALUES (?, ?, ?, ?, NULL, NULL, NULL)",
            ("ep-1", "sess-1", _NOW.isoformat(), "existing reflection"),
        )
        conn.commit()
        _insert_outcome(conn, "oc-1", _NOW, reflection="new reflection")
        conn.close()

        migrate(db)
        # The outcome matched but UPDATE WHERE reflection IS NULL skipped it
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT reflection FROM episodes WHERE episode_id='ep-1'").fetchone()
        conn.close()
        assert row["reflection"] == "existing reflection"

    def test_invalid_timestamp_falls_back(self, tmp_path: Path) -> None:
        """Outcome with an invalid/missing timestamp does not crash."""
        db = tmp_path / "mem.db"
        conn = _make_db(db)
        _insert_episode(conn, "ep-1", "sess-1", _NOW)
        # Insert an outcome with a bad timestamp
        conn.execute(
            "INSERT INTO task_outcomes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("oc-bad", "s", "success", "ok", "[]", 0.01, 1.0, "NOT_A_DATE"),
        )
        conn.commit()
        conn.close()

        # Should not raise; just skip or match to the nearest episode
        result = migrate(db)
        assert isinstance(result, int)

    def test_errors_field_defaults_to_empty_json(self, tmp_path: Path) -> None:
        """NULL errors in task_outcomes is stored as '[]' in episodes."""
        db = tmp_path / "mem.db"
        conn = _make_db(db)
        _insert_episode(conn, "ep-1", "sess-1", _NOW)
        conn.execute(
            "INSERT INTO task_outcomes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("oc-1", "s", "ok", "refl", None, 0.01, 1.0, _NOW.isoformat()),
        )
        conn.commit()
        conn.close()

        migrate(db)

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT errors FROM episodes WHERE episode_id='ep-1'").fetchone()
        conn.close()
        assert row["errors"] == "[]"


class TestMainCli:
    def test_main_db_not_found_exits(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """main() exits with code 1 if the db file doesn't exist."""
        monkeypatch.setattr(sys, "argv", ["migrate", "--db", str(tmp_path / "nonexistent.db")])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    def test_main_runs_and_prints(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """main() runs against a real db and prints the updated count."""
        db = tmp_path / "mem.db"
        conn = _make_db(db)
        _insert_episode(conn, "ep-1", "sess-1", _NOW)
        _insert_outcome(conn, "oc-1", _NOW, reflection="done")
        conn.close()

        monkeypatch.setattr(sys, "argv", ["migrate", "--db", str(db)])
        main()

        out = capsys.readouterr().out
        assert "Updated 1 episode(s)" in out

    def test_main_dry_run_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """main() with --dry-run prints dry-run message and doesn't write."""
        db = tmp_path / "mem.db"
        conn = _make_db(db)
        _insert_episode(conn, "ep-1", "sess-1", _NOW)
        _insert_outcome(conn, "oc-1", _NOW, reflection="done")
        conn.close()

        monkeypatch.setattr(sys, "argv", ["migrate", "--db", str(db), "--dry-run"])
        main()

        out = capsys.readouterr().out
        assert "dry-run" in out.lower()

        # Nothing written
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT reflection FROM episodes WHERE episode_id='ep-1'").fetchone()
        conn.close()
        assert row["reflection"] is None
