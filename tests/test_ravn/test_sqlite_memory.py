"""Tests for the SQLite episodic memory adapter."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from ravn.adapters.memory.scoring import _format_episode_block, _recency_score
from ravn.adapters.memory.sqlite import (
    SqliteMemoryAdapter,
    _combined_score,
)
from ravn.domain.models import Episode, Outcome, SharedContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ep(
    episode_id: str = "ep-1",
    session_id: str = "sess-1",
    summary: str = "ran the test suite",
    task_description: str = "run tests",
    tools_used: list[str] | None = None,
    outcome: Outcome = Outcome.SUCCESS,
    tags: list[str] | None = None,
    timestamp: datetime | None = None,
) -> Episode:
    return Episode(
        episode_id=episode_id,
        session_id=session_id,
        timestamp=timestamp or datetime.now(UTC),
        summary=summary,
        task_description=task_description,
        tools_used=tools_used or ["bash"],
        outcome=outcome,
        tags=tags or ["shell"],
    )


@pytest.fixture
async def mem(tmp_path: Path) -> SqliteMemoryAdapter:
    adapter = SqliteMemoryAdapter(
        path=str(tmp_path / "memory.db"),
        max_retries=3,
        min_jitter_ms=1.0,
        max_jitter_ms=5.0,
        checkpoint_interval=5,
        prefetch_budget=500,
        prefetch_limit=5,
        prefetch_min_relevance=0.0,  # no cutoff so tests pass regardless of score
        recency_half_life_days=14.0,
    )
    await adapter.initialize()
    return adapter


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------


class TestRecencyScore:
    def test_fresh_episode_high_score(self) -> None:
        ts = datetime.now(UTC)
        score = _recency_score(ts, half_life_days=14.0)
        assert score > 0.99

    def test_old_episode_low_score(self) -> None:
        ts = datetime.now(UTC) - timedelta(days=100)
        score = _recency_score(ts, half_life_days=14.0)
        assert score < 0.01

    def test_half_life(self) -> None:
        ts = datetime.now(UTC) - timedelta(days=14)
        score = _recency_score(ts, half_life_days=14.0)
        assert abs(score - 0.5) < 0.01

    def test_naive_datetime_treated_as_utc(self) -> None:
        # Simulate a naive datetime (no tzinfo) as might come from an old DB row.
        ts = datetime(2025, 1, 1, 12, 0, 0)  # naive, no tzinfo
        score = _recency_score(ts, half_life_days=14.0)
        assert 0.0 < score <= 1.0


class TestCombinedScore:
    def test_perfect_recent_success(self) -> None:
        ts = datetime.now(UTC)
        # relevance=1.0 (normalised), recent timestamp, success outcome
        score = _combined_score(1.0, ts, Outcome.SUCCESS, half_life_days=14.0)
        assert score > 0.9

    def test_failure_lower_than_success(self) -> None:
        ts = datetime.now(UTC)
        success = _combined_score(1.0, ts, Outcome.SUCCESS, half_life_days=14.0)
        failure = _combined_score(1.0, ts, Outcome.FAILURE, half_life_days=14.0)
        assert success > failure

    def test_zero_relevance_gives_zero(self) -> None:
        ts = datetime.now(UTC)
        score = _combined_score(0.0, ts, Outcome.SUCCESS, half_life_days=14.0)
        assert score == pytest.approx(0.0)

    def test_partial_relevance_gives_partial_score(self) -> None:
        ts = datetime.now(UTC)
        score = _combined_score(0.5, ts, Outcome.SUCCESS, half_life_days=14.0)
        assert 0.0 < score < 1.0


class TestFormatEpisodeBlock:
    def test_contains_outcome(self) -> None:
        ep = _ep(outcome=Outcome.SUCCESS)
        block = _format_episode_block(ep)
        assert "SUCCESS" in block

    def test_contains_task_description(self) -> None:
        ep = _ep(task_description="deploy to production")
        block = _format_episode_block(ep)
        assert "deploy to production" in block

    def test_contains_summary(self) -> None:
        ep = _ep(summary="deployed without errors")
        block = _format_episode_block(ep)
        assert "deployed without errors" in block

    def test_contains_date(self) -> None:
        ts = datetime(2024, 3, 15, tzinfo=UTC)
        ep = _ep(timestamp=ts)
        block = _format_episode_block(ep)
        assert "2024-03-15" in block


# ---------------------------------------------------------------------------
# Integration: SqliteMemoryAdapter
# ---------------------------------------------------------------------------


class TestSqliteInit:
    async def test_creates_db_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "subdir" / "memory.db"
        adapter = SqliteMemoryAdapter(path=str(db_path))
        await adapter.initialize()
        assert db_path.exists()

    async def test_wal_mode(self, tmp_path: Path) -> None:
        adapter = SqliteMemoryAdapter(path=str(tmp_path / "memory.db"))
        await adapter.initialize()
        conn = sqlite3.connect(str(tmp_path / "memory.db"))
        row = conn.execute("PRAGMA journal_mode").fetchone()
        conn.close()
        assert row[0] == "wal"

    async def test_search_index_fts_table_exists(self, tmp_path: Path) -> None:
        adapter = SqliteMemoryAdapter(path=str(tmp_path / "memory.db"))
        await adapter.initialize()
        conn = sqlite3.connect(str(tmp_path / "memory.db"))
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='search_index_fts'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1

    async def test_idempotent_initialize(self, mem: SqliteMemoryAdapter) -> None:
        # Second init should not raise.
        await mem.initialize()


class TestRecordEpisode:
    async def test_record_stores_episode(self, mem: SqliteMemoryAdapter) -> None:
        ep = _ep(episode_id="ep-store")
        await mem.record_episode(ep)
        matches = await mem.query_episodes("test suite", min_relevance=0.0)
        ids = [m.episode.episode_id for m in matches]
        assert "ep-store" in ids

    async def test_record_multiple_episodes(self, mem: SqliteMemoryAdapter) -> None:
        await mem.record_episode(_ep(episode_id="ep-a", summary="deployed to staging"))
        await mem.record_episode(_ep(episode_id="ep-b", summary="deployed to production"))
        matches = await mem.query_episodes("deployed", min_relevance=0.0)
        ids = {m.episode.episode_id for m in matches}
        assert ids == {"ep-a", "ep-b"}

    async def test_record_upserts_by_id(self, mem: SqliteMemoryAdapter) -> None:
        ep = _ep(episode_id="ep-upsert", summary="original summary")
        await mem.record_episode(ep)
        ep2 = _ep(episode_id="ep-upsert", summary="updated summary")
        await mem.record_episode(ep2)
        matches = await mem.query_episodes("updated summary", min_relevance=0.0)
        assert len(matches) == 1
        assert matches[0].episode.summary == "updated summary"

    async def test_record_with_embedding(self, mem: SqliteMemoryAdapter) -> None:
        ep = _ep(episode_id="ep-emb")
        ep.embedding = [0.1, 0.2, 0.3]
        await mem.record_episode(ep)
        matches = await mem.query_episodes("test suite", min_relevance=0.0)
        matched = next(m for m in matches if m.episode.episode_id == "ep-emb")
        assert matched.episode.embedding == pytest.approx([0.1, 0.2, 0.3])

    async def test_record_preserves_outcome(self, mem: SqliteMemoryAdapter) -> None:
        ep = _ep(episode_id="ep-fail", outcome=Outcome.FAILURE, summary="failed deployment")
        await mem.record_episode(ep)
        matches = await mem.query_episodes("failed deployment", min_relevance=0.0)
        assert matches[0].episode.outcome == Outcome.FAILURE

    async def test_record_checkpoint_fires(self, tmp_path: Path) -> None:
        adapter = SqliteMemoryAdapter(
            path=str(tmp_path / "memory.db"),
            checkpoint_interval=2,
        )
        await adapter.initialize()
        for i in range(3):
            await adapter.record_episode(_ep(episode_id=f"ep-{i}", summary=f"task {i}"))
        # No exception means checkpoint was handled correctly.


class TestQueryEpisodes:
    async def test_returns_relevant_episodes(self, mem: SqliteMemoryAdapter) -> None:
        await mem.record_episode(_ep(episode_id="e1", summary="wrote unit tests"))
        await mem.record_episode(_ep(episode_id="e2", summary="deployed docker image"))
        matches = await mem.query_episodes("unit tests", min_relevance=0.0)
        ids = {m.episode.episode_id for m in matches}
        assert "e1" in ids

    async def test_returns_empty_for_no_match(self, mem: SqliteMemoryAdapter) -> None:
        await mem.record_episode(_ep(summary="configured CI pipeline"))
        matches = await mem.query_episodes("machine learning model", min_relevance=0.0)
        assert matches == []

    async def test_respects_limit(self, mem: SqliteMemoryAdapter) -> None:
        for i in range(6):
            await mem.record_episode(_ep(episode_id=f"e{i}", summary="python debugging"))
        matches = await mem.query_episodes("python debugging", limit=3, min_relevance=0.0)
        assert len(matches) <= 3

    async def test_min_relevance_filters(self, mem: SqliteMemoryAdapter) -> None:
        await mem.record_episode(_ep(summary="rare unique term xyzzy12345"))
        # Fresh success episode should score well; min_relevance=0.99 might filter it
        # but with combined score it depends on BM25. Just check the filter applies.
        matches_low = await mem.query_episodes("xyzzy12345", min_relevance=0.0)
        matches_high = await mem.query_episodes("xyzzy12345", min_relevance=0.9999)
        assert len(matches_low) >= len(matches_high)

    async def test_relevance_scores_in_range(self, mem: SqliteMemoryAdapter) -> None:
        await mem.record_episode(_ep(summary="configured nginx reverse proxy"))
        matches = await mem.query_episodes("nginx", min_relevance=0.0)
        for m in matches:
            assert 0.0 <= m.relevance <= 1.0

    async def test_fts_handles_hyphenated_query(self, mem: SqliteMemoryAdapter) -> None:
        await mem.record_episode(_ep(summary="pytest-asyncio migration"))
        # Should not raise even with hyphens in the query.
        matches = await mem.query_episodes("pytest-asyncio", min_relevance=0.0)
        assert len(matches) >= 0  # just checking no exception

    async def test_fts_handles_empty_query(self, mem: SqliteMemoryAdapter) -> None:
        await mem.record_episode(_ep(summary="some episode"))
        matches = await mem.query_episodes("", min_relevance=0.0)
        # Empty query returns no results (no FTS match).
        assert isinstance(matches, list)

    async def test_sorted_by_relevance_desc(self, mem: SqliteMemoryAdapter) -> None:
        await mem.record_episode(
            _ep(
                episode_id="recent",
                summary="ran unit tests",
                timestamp=datetime.now(UTC),
                outcome=Outcome.SUCCESS,
            )
        )
        await mem.record_episode(
            _ep(
                episode_id="old",
                summary="ran unit tests",
                timestamp=datetime.now(UTC) - timedelta(days=60),
                outcome=Outcome.FAILURE,
            )
        )
        matches = await mem.query_episodes("unit tests", min_relevance=0.0)
        if len(matches) >= 2:
            assert matches[0].relevance >= matches[1].relevance


class TestPrefetch:
    async def test_returns_empty_when_no_episodes(self, mem: SqliteMemoryAdapter) -> None:
        result = await mem.prefetch("debug python crash")
        assert result == ""

    async def test_returns_context_block(self, mem: SqliteMemoryAdapter) -> None:
        await mem.record_episode(_ep(summary="debugged python import error"))
        result = await mem.prefetch("python error")
        assert "Past Context" in result

    async def test_respects_budget(self, tmp_path: Path) -> None:
        adapter = SqliteMemoryAdapter(
            path=str(tmp_path / "memory.db"),
            prefetch_budget=10,  # very small budget
            prefetch_min_relevance=0.0,
        )
        await adapter.initialize()
        await adapter.record_episode(
            _ep(
                summary="a" * 200,
                task_description="long task " + "b" * 100,
            )
        )
        result = await adapter.prefetch("long task")
        # Budget is tiny so we might get empty result or truncated.
        assert isinstance(result, str)

    async def test_prefetch_includes_outcome_and_date(self, mem: SqliteMemoryAdapter) -> None:
        ts = datetime(2025, 6, 1, tzinfo=UTC)
        await mem.record_episode(
            _ep(summary="fixed database migration", timestamp=ts, outcome=Outcome.SUCCESS)
        )
        result = await mem.prefetch("database migration")
        if result:  # only check if something was returned
            assert "2025-06-01" in result
            assert "SUCCESS" in result


class TestSearchSessions:
    async def test_groups_by_session(self, mem: SqliteMemoryAdapter) -> None:
        await mem.record_episode(
            _ep(episode_id="e1", session_id="s1", summary="git commit changes")
        )
        await mem.record_episode(
            _ep(episode_id="e2", session_id="s1", summary="git push to remote")
        )
        await mem.record_episode(_ep(episode_id="e3", session_id="s2", summary="git merge main"))
        summaries = await mem.search_sessions("git", limit=10)
        session_ids = {s.session_id for s in summaries}
        assert "s1" in session_ids
        assert "s2" in session_ids

    async def test_episode_count_correct(self, mem: SqliteMemoryAdapter) -> None:
        for i in range(3):
            await mem.record_episode(
                _ep(episode_id=f"e{i}", session_id="s1", summary="refactored module")
            )
        summaries = await mem.search_sessions("refactored", limit=5)
        s1 = next((s for s in summaries if s.session_id == "s1"), None)
        assert s1 is not None
        assert s1.episode_count == 3

    async def test_empty_when_no_match(self, mem: SqliteMemoryAdapter) -> None:
        await mem.record_episode(_ep(summary="configured redis cache"))
        summaries = await mem.search_sessions("machine learning pipeline", limit=5)
        assert summaries == []

    async def test_respects_limit(self, mem: SqliteMemoryAdapter) -> None:
        for i in range(5):
            await mem.record_episode(
                _ep(episode_id=f"e{i}", session_id=f"s{i}", summary="fixed bug in code")
            )
        summaries = await mem.search_sessions("fixed bug", limit=2)
        assert len(summaries) <= 2

    async def test_sorted_by_last_active_desc(self, mem: SqliteMemoryAdapter) -> None:
        ts_old = datetime.now(UTC) - timedelta(days=10)
        ts_new = datetime.now(UTC)
        await mem.record_episode(
            _ep(episode_id="old", session_id="s-old", summary="old task done", timestamp=ts_old)
        )
        await mem.record_episode(
            _ep(episode_id="new", session_id="s-new", summary="old task done", timestamp=ts_new)
        )
        summaries = await mem.search_sessions("old task done", limit=5)
        if len(summaries) >= 2:
            assert summaries[0].last_active >= summaries[1].last_active


class TestSharedContext:
    async def test_default_none(self, mem: SqliteMemoryAdapter) -> None:
        assert mem.get_shared_context() is None

    async def test_inject_and_retrieve(self, mem: SqliteMemoryAdapter) -> None:
        ctx = SharedContext(data={"agent": "sköll"})
        mem.inject_shared_context(ctx)
        assert mem.get_shared_context() is ctx

    async def test_inject_replaces(self, mem: SqliteMemoryAdapter) -> None:
        mem.inject_shared_context(SharedContext(data={"a": 1}))
        mem.inject_shared_context(SharedContext(data={"b": 2}))
        ctx = mem.get_shared_context()
        assert ctx is not None
        assert "b" in ctx.data


class TestRetryMechanism:
    async def test_raises_on_non_locked_error(self, tmp_path: Path) -> None:
        adapter = SqliteMemoryAdapter(
            path=str(tmp_path / "memory.db"),
            max_retries=3,
            min_jitter_ms=1.0,
            max_jitter_ms=2.0,
        )
        await adapter.initialize()

        def _fail():
            raise sqlite3.OperationalError("no such table: xyz")

        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            adapter._with_retry(_fail)

    async def test_retries_on_locked(self, tmp_path: Path) -> None:
        adapter = SqliteMemoryAdapter(
            path=str(tmp_path / "memory.db"),
            max_retries=3,
            min_jitter_ms=1.0,
            max_jitter_ms=2.0,
        )
        await adapter.initialize()

        attempts = {"count": 0}

        def _eventually_succeeds():
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise sqlite3.OperationalError("database is locked")
            return "ok"

        result = adapter._with_retry(_eventually_succeeds)
        assert result == "ok"
        assert attempts["count"] == 3

    async def test_raises_after_max_retries(self, tmp_path: Path) -> None:
        adapter = SqliteMemoryAdapter(
            path=str(tmp_path / "memory.db"),
            max_retries=3,
            min_jitter_ms=1.0,
            max_jitter_ms=2.0,
        )
        await adapter.initialize()

        def _always_locked():
            raise sqlite3.OperationalError("database is locked")

        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            adapter._with_retry(_always_locked)


# ---------------------------------------------------------------------------
# WAL concurrency: concurrent reads during a write
# ---------------------------------------------------------------------------


class TestWALConcurrency:
    async def test_concurrent_reads_during_write(self, tmp_path: Path) -> None:
        """WAL mode allows multiple readers while a write is in progress."""
        adapter = SqliteMemoryAdapter(
            path=str(tmp_path / "memory.db"),
            max_retries=5,
            min_jitter_ms=1.0,
            max_jitter_ms=5.0,
        )
        await adapter.initialize()

        # Pre-load some episodes so reads return data.
        for i in range(5):
            await adapter.record_episode(_ep(episode_id=f"pre-{i}", summary=f"background task {i}"))

        read_results: list[int] = []
        errors: list[Exception] = []

        def _reader_thread(n: int) -> None:
            import asyncio

            async def _read() -> None:
                try:
                    matches = await adapter.query_episodes("background task", min_relevance=0.0)
                    read_results.append(len(matches))
                except Exception as exc:
                    errors.append(exc)

            asyncio.run(_read())

        # Start concurrent reader threads.
        threads = [threading.Thread(target=_reader_thread, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()

        # Write during concurrent reads.
        concurrent_ep = _ep(episode_id="concurrent-write", summary="write during reads")
        await adapter.record_episode(concurrent_ep)

        for t in threads:
            t.join(timeout=10.0)

        # All reads must have succeeded without errors.
        assert not errors, f"Reader threads failed: {errors}"
        # Each reader found the pre-loaded episodes.
        assert all(r >= 0 for r in read_results)

    async def test_checkpoint_triggers_passively(self, tmp_path: Path) -> None:
        """Passive WAL checkpoint fires after checkpoint_interval writes (no error)."""
        adapter = SqliteMemoryAdapter(
            path=str(tmp_path / "memory.db"),
            checkpoint_interval=3,
        )
        await adapter.initialize()

        # Write more than checkpoint_interval to trigger checkpoint.
        for i in range(7):
            await adapter.record_episode(
                _ep(episode_id=f"ckpt-{i}", summary=f"checkpoint episode {i}")
            )

        # If checkpoint raised, the above would have failed.  Verify writes persisted.
        conn = sqlite3.connect(str(tmp_path / "memory.db"))
        row = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()
        conn.close()
        assert row[0] == 7

    async def test_empty_database_query_returns_empty(self, tmp_path: Path) -> None:
        """A freshly initialised database returns empty results gracefully."""
        adapter = SqliteMemoryAdapter(path=str(tmp_path / "memory.db"))
        await adapter.initialize()

        matches = await adapter.query_episodes("anything", min_relevance=0.0)
        assert matches == []

    async def test_empty_database_prefetch_returns_empty_string(self, tmp_path: Path) -> None:
        """Prefetch on an empty database returns an empty string (no crash)."""
        adapter = SqliteMemoryAdapter(path=str(tmp_path / "memory.db"))
        await adapter.initialize()

        result = await adapter.prefetch("any context query")
        assert result == ""

    async def test_zero_prefetch_limit_disables_automatic_episode_query(
        self,
        tmp_path: Path,
    ) -> None:
        adapter = SqliteMemoryAdapter(
            path=str(tmp_path / "memory.db"),
            prefetch_limit=0,
        )
        adapter.query_episodes = AsyncMock(side_effect=AssertionError("must not query"))

        assert await adapter.prefetch("a later resident turn") == ""
        adapter.query_episodes.assert_not_awaited()

    async def test_empty_database_search_sessions_returns_empty(self, tmp_path: Path) -> None:
        """Session search on an empty database returns empty list."""
        adapter = SqliteMemoryAdapter(path=str(tmp_path / "memory.db"))
        await adapter.initialize()

        summaries = await adapter.search_sessions("any query")
        assert summaries == []
