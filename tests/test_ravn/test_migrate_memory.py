"""Tests for the SQLite → PostgreSQL episode migration tool."""

from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ravn.adapters.memory.migrate import migrate_sqlite_to_postgres

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _populate_sqlite(path: Path, episodes: list[dict]) -> None:
    """Create a minimal SQLite episodes table and insert the given rows."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS episodes (
            episode_id       TEXT PRIMARY KEY,
            session_id       TEXT NOT NULL,
            timestamp        TEXT NOT NULL,
            summary          TEXT NOT NULL,
            task_description TEXT NOT NULL,
            tools_used       TEXT NOT NULL,
            outcome          TEXT NOT NULL,
            tags             TEXT NOT NULL,
            embedding        TEXT
        )
        """
    )
    for ep in episodes:
        conn.execute(
            "INSERT INTO episodes VALUES (?,?,?,?,?,?,?,?,?)",
            (
                ep["episode_id"],
                ep["session_id"],
                ep.get("timestamp", datetime.now(UTC).isoformat()),
                ep["summary"],
                ep.get("task_description", "task"),
                json.dumps(ep.get("tools_used", [])),
                ep.get("outcome", "success"),
                json.dumps(ep.get("tags", [])),
                json.dumps(ep["embedding"]) if ep.get("embedding") else None,
            ),
        )
    conn.commit()
    conn.close()


def _make_pg_conn(execute_result: str = "INSERT 0 1") -> AsyncMock:
    """Build a mock asyncpg Connection."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=execute_result)

    def _new_transaction():
        @asynccontextmanager
        async def _ctx():
            yield

        return _ctx()

    conn.transaction = MagicMock(side_effect=lambda: _new_transaction())
    return conn


def _make_reusable_pool(conn: AsyncMock) -> MagicMock:
    """Build a mock pool whose acquire() always returns a fresh context manager."""
    pool = MagicMock()
    pool.close = AsyncMock()

    def _new_acquire():
        @asynccontextmanager
        async def _ctx():
            yield conn

        return _ctx()

    pool.acquire = MagicMock(side_effect=lambda: _new_acquire())
    return pool


def _patch_asyncpg(pool: MagicMock):
    """Return a patch.object context manager replacing asyncpg in migrate_memory."""
    mock = MagicMock()
    mock.create_pool = AsyncMock(return_value=pool)
    return patch.object(sys.modules["ravn.adapters.memory.migrate"], "asyncpg", mock)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMigrateSqliteToPostgres:
    async def test_missing_sqlite_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="not found"):
            await migrate_sqlite_to_postgres(
                sqlite_path=tmp_path / "nonexistent.db",
                postgres_dsn="postgresql://u:p@h/db",
            )

    async def test_empty_db_returns_zero(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        _populate_sqlite(db_path, [])
        conn = _make_pg_conn()
        pool = _make_reusable_pool(conn)
        with _patch_asyncpg(pool):
            count = await migrate_sqlite_to_postgres(
                sqlite_path=db_path,
                postgres_dsn="postgresql://u:p@h/db",
            )
        assert count == 0

    async def test_migrates_three_episodes(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        episodes = [
            {"episode_id": f"ep-{i}", "session_id": "s1", "summary": f"task {i}"} for i in range(3)
        ]
        _populate_sqlite(db_path, episodes)
        conn = _make_pg_conn(execute_result="INSERT 0 1")
        pool = _make_reusable_pool(conn)
        with _patch_asyncpg(pool):
            count = await migrate_sqlite_to_postgres(
                sqlite_path=db_path,
                postgres_dsn="postgresql://u:p@h/db",
            )
        assert count == 3

    async def test_conflict_skipped_not_counted(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        _populate_sqlite(
            db_path,
            [{"episode_id": "ep-exists", "session_id": "s1", "summary": "already there"}],
        )
        # Simulate PostgreSQL returning "INSERT 0 0" (conflict, no row inserted).
        conn = _make_pg_conn(execute_result="INSERT 0 0")
        pool = _make_reusable_pool(conn)
        with _patch_asyncpg(pool):
            count = await migrate_sqlite_to_postgres(
                sqlite_path=db_path,
                postgres_dsn="postgresql://u:p@h/db",
            )
        assert count == 0

    async def test_progress_callback_called(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        episodes = [
            {"episode_id": f"ep-{i}", "session_id": "s1", "summary": f"task {i}"} for i in range(5)
        ]
        _populate_sqlite(db_path, episodes)
        conn = _make_pg_conn()
        pool = _make_reusable_pool(conn)
        calls: list[tuple[int, int]] = []

        def _on_progress(done: int, total: int) -> None:
            calls.append((done, total))

        with _patch_asyncpg(pool):
            await migrate_sqlite_to_postgres(
                sqlite_path=db_path,
                postgres_dsn="postgresql://u:p@h/db",
                batch_size=2,
                on_progress=_on_progress,
            )
        assert len(calls) > 0
        assert all(total == 5 for _, total in calls)

    async def test_batch_size_respected(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        episodes = [
            {"episode_id": f"ep-{i}", "session_id": "s1", "summary": f"task {i}"} for i in range(10)
        ]
        _populate_sqlite(db_path, episodes)
        conn = _make_pg_conn()
        pool = _make_reusable_pool(conn)
        with _patch_asyncpg(pool):
            await migrate_sqlite_to_postgres(
                sqlite_path=db_path,
                postgres_dsn="postgresql://u:p@h/db",
                batch_size=3,
            )
        # 10 episodes in batches of 3 → 4 acquire() calls.
        assert pool.acquire.call_count == 4

    async def test_pool_closed_after_migration(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        _populate_sqlite(
            db_path,
            [{"episode_id": "ep-1", "session_id": "s1", "summary": "task"}],
        )
        conn = _make_pg_conn()
        pool = _make_reusable_pool(conn)
        with _patch_asyncpg(pool):
            await migrate_sqlite_to_postgres(
                sqlite_path=db_path,
                postgres_dsn="postgresql://u:p@h/db",
            )
        pool.close.assert_awaited_once()

    async def test_pool_closed_even_on_error(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        _populate_sqlite(
            db_path,
            [{"episode_id": "ep-1", "session_id": "s1", "summary": "task"}],
        )
        conn = _make_pg_conn()
        conn.execute = AsyncMock(side_effect=RuntimeError("db error"))
        pool = _make_reusable_pool(conn)
        with _patch_asyncpg(pool):
            with pytest.raises(RuntimeError, match="db error"):
                await migrate_sqlite_to_postgres(
                    sqlite_path=db_path,
                    postgres_dsn="postgresql://u:p@h/db",
                )
        pool.close.assert_awaited_once()

    async def test_embedding_passed_as_text(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        _populate_sqlite(
            db_path,
            [
                {
                    "episode_id": "ep-1",
                    "session_id": "s1",
                    "summary": "task",
                    "embedding": [0.1, 0.2],
                }
            ],
        )
        conn = _make_pg_conn()
        pool = _make_reusable_pool(conn)
        with _patch_asyncpg(pool):
            await migrate_sqlite_to_postgres(
                sqlite_path=db_path,
                postgres_dsn="postgresql://u:p@h/db",
            )
        call_args = conn.execute.call_args[0]
        # Embedding is the last positional arg — stored as TEXT from SQLite.
        embedding_arg = call_args[-1]
        assert embedding_arg is not None

    async def test_invalid_timestamp_handled_gracefully(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        raw_conn = sqlite3.connect(str(db_path))
        raw_conn.execute(
            """
            CREATE TABLE episodes (
                episode_id TEXT PRIMARY KEY, session_id TEXT, timestamp TEXT,
                summary TEXT, task_description TEXT, tools_used TEXT,
                outcome TEXT, tags TEXT, embedding TEXT
            )
            """
        )
        raw_conn.execute(
            "INSERT INTO episodes VALUES (?,?,?,?,?,?,?,?,?)",
            ("ep-bad", "s1", "not-a-timestamp", "summary", "task", "[]", "success", "[]", None),
        )
        raw_conn.commit()
        raw_conn.close()

        pg_conn = _make_pg_conn()
        pool = _make_reusable_pool(pg_conn)
        with _patch_asyncpg(pool):
            # Should complete without raising despite invalid timestamp.
            count = await migrate_sqlite_to_postgres(
                sqlite_path=db_path,
                postgres_dsn="postgresql://u:p@h/db",
            )
        assert count >= 0

    async def test_tilde_path_raises_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            await migrate_sqlite_to_postgres(
                sqlite_path="~/nonexistent_ravn_test_db_xyz_99.db",
                postgres_dsn="postgresql://u:p@h/db",
            )

    async def test_upsert_sql_uses_on_conflict(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        _populate_sqlite(
            db_path,
            [{"episode_id": "ep-1", "session_id": "s1", "summary": "task"}],
        )
        conn = _make_pg_conn()
        pool = _make_reusable_pool(conn)
        with _patch_asyncpg(pool):
            await migrate_sqlite_to_postgres(
                sqlite_path=db_path,
                postgres_dsn="postgresql://u:p@h/db",
            )
        sql_arg = conn.execute.call_args[0][0]
        assert "ON CONFLICT" in sql_arg
        assert "DO NOTHING" in sql_arg
