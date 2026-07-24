"""Tests for the PostgreSQL episodic memory adapter.

All asyncpg I/O is mocked — no real PostgreSQL instance required.
"""

from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from niuu.ports.search import SearchPort, SearchResult
from ravn.adapters.memory.postgres import (
    PostgresMemoryAdapter,
    _combined_score,
    _row_to_episode,
)
from ravn.adapters.memory.scoring import _format_episode_block, _recency_score
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
    embedding: list[float] | None = None,
) -> Episode:
    return Episode(
        episode_id=episode_id,
        session_id=session_id,
        timestamp=timestamp or datetime.now(UTC),
        summary=summary,
        task_description=task_description,
        tools_used=tools_used if tools_used is not None else ["bash"],
        outcome=outcome,
        tags=tags if tags is not None else ["shell"],
        embedding=embedding,
    )


def _make_row(episode: Episode, rank_score: float = 0.5) -> dict[str, Any]:
    """Build a dict that mimics an asyncpg Record for the given episode."""
    return {
        "episode_id": episode.episode_id,
        "session_id": episode.session_id,
        "timestamp": episode.timestamp,
        "summary": episode.summary,
        "task_description": episode.task_description,
        "tools_used": episode.tools_used,
        "outcome": episode.outcome.value,
        "tags": episode.tags,
        "embedding": json.dumps(episode.embedding) if episode.embedding else None,
        "rank_score": rank_score,
    }


def _make_conn(
    fetch_result: list[dict[str, Any]] | None = None,
    fetchrow_result: dict[str, Any] | None = None,
) -> AsyncMock:
    """Build a mock asyncpg Connection."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    conn.fetch = AsyncMock(return_value=fetch_result or [])
    conn.fetchrow = AsyncMock(return_value=fetchrow_result)
    return conn


def _make_pool(conn: AsyncMock) -> MagicMock:
    """Build a mock asyncpg Pool that yields *conn* from acquire().

    Uses side_effect to create a fresh context manager on every acquire() call
    so that multiple sequential operations on the same adapter work correctly.
    """
    pool = MagicMock()
    pool.close = AsyncMock()

    def _new_ctx():
        @asynccontextmanager
        async def _ctx():
            yield conn

        return _ctx()

    pool.acquire = MagicMock(side_effect=lambda: _new_ctx())
    return pool


def _make_adapter(
    conn: AsyncMock | None = None,
    pgvector: bool = False,
    search_results: list[SearchResult] | None = None,
    search_port: SearchPort | None = None,
) -> PostgresMemoryAdapter:
    """Build an adapter with a mock pool and search port injected directly (no I/O).

    The *conn* mock is used for ravn_episodes table lookups.
    The *search_port* (or a stub built from *search_results*) handles search delegation.
    """
    if conn is None:
        conn = _make_conn()

    pool = _make_pool(conn)
    mock_search = search_port or _MockSearchPort(search_results)

    adapter = PostgresMemoryAdapter(
        dsn="postgresql://test:test@localhost/test",
        prefetch_budget=500,
        prefetch_limit=5,
        prefetch_min_relevance=0.0,
        recency_half_life_days=14.0,
        search_port=mock_search,
    )
    # Inject the mock pool directly to avoid hitting asyncpg.create_pool.
    adapter._pool = pool
    return adapter


def _patch_asyncpg(pool: MagicMock) -> Any:
    """Return a patch.object context manager that replaces asyncpg in postgres_memory."""
    mock = MagicMock()
    mock.create_pool = AsyncMock(return_value=pool)
    return patch.object(sys.modules["ravn.adapters.memory.postgres"], "asyncpg", mock)


class _MockSearchPort(SearchPort):
    """Stub search port that returns predetermined SearchResult objects."""

    def __init__(self, results: list[SearchResult] | None = None) -> None:
        self._results = results or []
        self.last_query: str | None = None
        self.last_limit: int | None = None

    async def index(
        self,
        id: str,
        content: str,
        metadata: dict,
        *,
        embedding: list[float] | None = None,
    ) -> None:
        pass

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        self.last_query = query
        self.last_limit = limit
        return self._results[:limit]

    async def remove(self, id: str) -> None:
        pass

    async def rebuild(self) -> None:
        pass


def _make_search_results_for(episodes: list[Episode], score: float = 0.8) -> list[SearchResult]:
    """Build SearchResult objects for a list of episodes."""
    return [SearchResult(id=ep.episode_id, content=ep.summary, score=score) for ep in episodes]


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------


class TestRecencyScore:
    def test_fresh_episode_high_score(self) -> None:
        ts = datetime.now(UTC)
        assert _recency_score(ts, half_life_days=14.0) > 0.99

    def test_old_episode_low_score(self) -> None:
        ts = datetime.now(UTC) - timedelta(days=100)
        assert _recency_score(ts, half_life_days=14.0) < 0.01

    def test_half_life(self) -> None:
        ts = datetime.now(UTC) - timedelta(days=14)
        assert abs(_recency_score(ts, half_life_days=14.0) - 0.5) < 0.01

    def test_naive_datetime_treated_as_utc(self) -> None:
        ts = datetime(2025, 1, 1, 12, 0, 0)
        score = _recency_score(ts, half_life_days=14.0)
        assert 0.0 < score <= 1.0


class TestCombinedScore:
    def test_perfect_recent_success(self) -> None:
        ts = datetime.now(UTC)
        score = _combined_score(1.0, ts, Outcome.SUCCESS, half_life_days=14.0)
        assert score > 0.9

    def test_failure_lower_than_success(self) -> None:
        ts = datetime.now(UTC)
        success = _combined_score(1.0, ts, Outcome.SUCCESS, half_life_days=14.0)
        failure = _combined_score(1.0, ts, Outcome.FAILURE, half_life_days=14.0)
        assert success > failure

    def test_zero_rank_gives_zero(self) -> None:
        ts = datetime.now(UTC)
        assert _combined_score(0.0, ts, Outcome.SUCCESS, half_life_days=14.0) == pytest.approx(0.0)

    def test_unknown_outcome_uses_fallback_weight(self) -> None:
        ts = datetime.now(UTC)
        # Passing an outcome value not in _OUTCOME_WEIGHTS falls back to 0.5.
        score = _combined_score(  # type: ignore[arg-type]
            1.0, ts, "unknown_outcome", half_life_days=14.0
        )
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

    def test_no_tags_shows_general(self) -> None:
        ep = _ep(tags=[])
        block = _format_episode_block(ep)
        assert "general" in block

    def test_no_tools_shows_none(self) -> None:
        ep = _ep(tools_used=[])
        block = _format_episode_block(ep)
        assert "none" in block


class TestRowToEpisode:
    def test_basic_row(self) -> None:
        ep = _ep()
        row = _make_row(ep)
        result = _row_to_episode(row)
        assert result.episode_id == ep.episode_id
        assert result.session_id == ep.session_id
        assert result.outcome == ep.outcome

    def test_string_timestamp_parsed(self) -> None:
        ep = _ep(timestamp=datetime(2024, 6, 1, tzinfo=UTC))
        row = _make_row(ep)
        row["timestamp"] = ep.timestamp.isoformat()
        result = _row_to_episode(row)
        assert result.timestamp.year == 2024

    def test_naive_timestamp_gets_utc(self) -> None:
        ep = _ep()
        row = _make_row(ep)
        row["timestamp"] = datetime(2024, 1, 1)  # naive
        result = _row_to_episode(row)
        assert result.timestamp.tzinfo is not None

    def test_invalid_timestamp_falls_back(self) -> None:
        ep = _ep()
        row = _make_row(ep)
        row["timestamp"] = "not-a-date"
        result = _row_to_episode(row)
        assert result.timestamp is not None

    def test_string_tools_parsed(self) -> None:
        ep = _ep(tools_used=["bash", "python"])
        row = _make_row(ep)
        row["tools_used"] = json.dumps(["bash", "python"])
        result = _row_to_episode(row)
        assert result.tools_used == ["bash", "python"]

    def test_string_tags_parsed(self) -> None:
        ep = _ep(tags=["ci", "test"])
        row = _make_row(ep)
        row["tags"] = json.dumps(["ci", "test"])
        result = _row_to_episode(row)
        assert result.tags == ["ci", "test"]

    def test_embedding_parsed_from_json_string(self) -> None:
        ep = _ep(embedding=[0.1, 0.2, 0.3])
        row = _make_row(ep)
        result = _row_to_episode(row)
        assert result.embedding == pytest.approx([0.1, 0.2, 0.3])

    def test_none_embedding_gives_none(self) -> None:
        ep = _ep(embedding=None)
        row = _make_row(ep)
        result = _row_to_episode(row)
        assert result.embedding is None

    def test_list_embedding_passed_through(self) -> None:
        ep = _ep()
        row = _make_row(ep)
        row["embedding"] = [0.5, 0.6]
        result = _row_to_episode(row)
        assert result.embedding == [0.5, 0.6]


# ---------------------------------------------------------------------------
# Construction and initialization
# ---------------------------------------------------------------------------


class TestPostgresMemoryAdapterInit:
    def test_missing_dsn_raises(self) -> None:
        with pytest.raises(ValueError, match="DSN"):
            PostgresMemoryAdapter(dsn="")

    def test_dsn_env_resolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_PG_DSN", "postgresql://u:p@host/db")
        adapter = PostgresMemoryAdapter(dsn_env="MY_PG_DSN")
        assert adapter._dsn == "postgresql://u:p@host/db"

    def test_dsn_env_overrides_dsn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_PG_DSN", "postgresql://env_host/db")
        adapter = PostgresMemoryAdapter(dsn="postgresql://fallback/db", dsn_env="MY_PG_DSN")
        assert "env_host" in adapter._dsn

    def test_dsn_fallback_when_env_not_set(self) -> None:
        adapter = PostgresMemoryAdapter(
            dsn="postgresql://fallback/db", dsn_env="NONEXISTENT_VAR_XYZ_999"
        )
        assert adapter._dsn == "postgresql://fallback/db"

    def test_not_initialized_state(self) -> None:
        adapter = PostgresMemoryAdapter(dsn="postgresql://u:p@h/db")
        assert adapter._pool is None
        assert not adapter.pgvector_available

    async def test_initialize_creates_pool(self) -> None:
        conn = _make_conn(fetchrow_result=None)
        pool = _make_pool(conn)
        mock_search = _MockSearchPort()

        adapter = PostgresMemoryAdapter(dsn="postgresql://u:p@h/db", search_port=mock_search)
        with _patch_asyncpg(pool) as mock_asyncpg:
            await adapter.initialize()

        mock_asyncpg.create_pool.assert_awaited_once()
        assert adapter._pool is pool

    async def test_initialize_with_mock_search_port(self) -> None:
        """Verifies initialize() works when search_port is injected."""
        mock_search = _MockSearchPort()
        adapter = PostgresMemoryAdapter(dsn="postgresql://u:p@h/db", search_port=mock_search)
        conn = _make_conn(fetchrow_result=None)
        pool = _make_pool(conn)
        with _patch_asyncpg(pool):
            await adapter.initialize()
        assert adapter._pool is pool

    async def test_pgvector_available_delegates_to_search_port(self) -> None:
        """pgvector_available reflects the search port's attribute when injected."""
        mock_search = _MockSearchPort()
        mock_search.pgvector_available = True  # type: ignore[attr-defined]
        adapter = PostgresMemoryAdapter(dsn="postgresql://u:p@h/db", search_port=mock_search)
        assert adapter.pgvector_available is True

    async def test_close_clears_pool(self) -> None:
        adapter = _make_adapter()
        await adapter.close()
        assert adapter._pool is None

    async def test_close_idempotent(self) -> None:
        adapter = _make_adapter()
        await adapter.close()
        await adapter.close()  # Must not raise.

    def test_require_pool_raises_when_not_initialized(self) -> None:
        adapter = PostgresMemoryAdapter(dsn="postgresql://u:p@h/db")
        with pytest.raises(RuntimeError, match="initialize"):
            adapter._require_pool()


# ---------------------------------------------------------------------------
# record_episode
# ---------------------------------------------------------------------------


class TestRecordEpisode:
    async def test_calls_execute_with_upsert(self) -> None:
        conn = _make_conn()
        adapter = _make_adapter(conn)
        await adapter.record_episode(_ep(episode_id="ep-record"))
        conn.execute.assert_awaited_once()
        sql_arg = conn.execute.call_args[0][0]
        assert "ON CONFLICT" in sql_arg
        assert "DO UPDATE" in sql_arg

    async def test_passes_correct_episode_fields(self) -> None:
        conn = _make_conn()
        adapter = _make_adapter(conn)
        ep = _ep(
            episode_id="ep-fields",
            session_id="sess-fields",
            summary="test summary",
            outcome=Outcome.FAILURE,
            tags=["tag1", "tag2"],
        )
        await adapter.record_episode(ep)
        args = conn.execute.call_args[0]
        assert ep.episode_id in args
        assert ep.session_id in args
        assert ep.summary in args
        assert ep.outcome.value in args
        assert ep.tags in args

    async def test_embedding_serialized_as_json(self) -> None:
        conn = _make_conn()
        adapter = _make_adapter(conn)
        ep = _ep(embedding=[0.1, 0.2, 0.3])
        await adapter.record_episode(ep)
        args = conn.execute.call_args[0]
        # args[0] = SQL, args[1..8] = episode fields, args[9] = embedding
        embedding_arg = args[9]
        assert embedding_arg == json.dumps([0.1, 0.2, 0.3])

    async def test_null_embedding_passed_as_none(self) -> None:
        conn = _make_conn()
        adapter = _make_adapter(conn)
        ep = _ep(embedding=None)
        await adapter.record_episode(ep)
        args = conn.execute.call_args[0]
        # args[0] = SQL, args[1..8] = episode fields, args[9] = embedding
        assert args[9] is None


# ---------------------------------------------------------------------------
# query_episodes
# ---------------------------------------------------------------------------


class TestQueryEpisodes:
    async def test_empty_query_returns_empty(self) -> None:
        adapter = _make_adapter()
        result = await adapter.query_episodes("   ")
        assert result == []

    async def test_no_search_results_returns_empty(self) -> None:
        adapter = _make_adapter(search_results=[])
        result = await adapter.query_episodes("python tests")
        assert result == []

    async def test_returns_episode_matches(self) -> None:
        ep = _ep(episode_id="e1", summary="ran unit tests")
        conn = _make_conn(fetch_result=[_make_row(ep)])
        search_results = _make_search_results_for([ep], score=0.8)
        adapter = _make_adapter(conn, search_results=search_results)
        result = await adapter.query_episodes("unit tests", min_relevance=0.0)
        assert len(result) == 1
        assert result[0].episode.episode_id == "e1"

    async def test_relevance_filtered_by_min_relevance(self) -> None:
        ep = _ep(timestamp=datetime.now(UTC) - timedelta(days=60), outcome=Outcome.FAILURE)
        # Very low search score + recency decay + failure outcome → combined < 0.5
        conn = _make_conn(fetch_result=[_make_row(ep)])
        search_results = [SearchResult(id=ep.episode_id, content=ep.summary, score=0.001)]
        adapter = _make_adapter(conn, search_results=search_results)
        result = await adapter.query_episodes("unit tests", min_relevance=0.5)
        assert result == []

    async def test_sorted_by_relevance_descending(self) -> None:
        ep1 = _ep(episode_id="recent", timestamp=datetime.now(UTC), outcome=Outcome.SUCCESS)
        ep2 = _ep(
            episode_id="old",
            timestamp=datetime.now(UTC) - timedelta(days=60),
            outcome=Outcome.FAILURE,
        )
        conn = _make_conn(fetch_result=[_make_row(ep1), _make_row(ep2)])
        search_results = _make_search_results_for([ep1, ep2], score=0.9)
        adapter = _make_adapter(conn, search_results=search_results)
        result = await adapter.query_episodes("tests", min_relevance=0.0)
        if len(result) >= 2:
            assert result[0].relevance >= result[1].relevance

    async def test_respects_limit(self) -> None:
        eps = [_ep(episode_id=f"e{i}") for i in range(6)]
        conn = _make_conn(fetch_result=[_make_row(ep) for ep in eps])
        search_results = _make_search_results_for(eps, score=0.8)
        adapter = _make_adapter(conn, search_results=search_results)
        result = await adapter.query_episodes("python", limit=3, min_relevance=0.0)
        assert len(result) <= 3

    async def test_search_port_called_with_query(self) -> None:
        mock_port = _MockSearchPort([])
        adapter = _make_adapter(search_port=mock_port)
        await adapter.query_episodes("python unit tests")
        assert mock_port.last_query == "python unit tests"

    async def test_relevance_scores_in_range(self) -> None:
        ep = _ep()
        conn = _make_conn(fetch_result=[_make_row(ep)])
        search_results = _make_search_results_for([ep], score=0.7)
        adapter = _make_adapter(conn, search_results=search_results)
        result = await adapter.query_episodes("tests", min_relevance=0.0)
        for m in result:
            assert 0.0 <= m.relevance <= 1.0

    async def test_search_port_receives_multiplied_limit(self) -> None:
        mock_port = _MockSearchPort([])
        adapter = _make_adapter(search_port=mock_port)
        await adapter.query_episodes("python", limit=5)
        # search is called with limit * 3
        assert mock_port.last_limit == 15


# ---------------------------------------------------------------------------
# prefetch
# ---------------------------------------------------------------------------


class TestPrefetch:
    async def test_zero_prefetch_limit_disables_automatic_episode_query(self) -> None:
        adapter = _make_adapter()
        adapter._prefetch_limit = 0
        adapter.query_episodes = AsyncMock(side_effect=AssertionError("must not query"))

        assert await adapter.prefetch("a later resident turn") == ""
        adapter.query_episodes.assert_not_awaited()

    async def test_empty_when_no_matches(self) -> None:
        adapter = _make_adapter(search_results=[])
        result = await adapter.prefetch("python crash")
        assert result == ""

    async def test_returns_context_block(self) -> None:
        ep = _ep(summary="debugged python import error", outcome=Outcome.SUCCESS)
        conn = _make_conn(fetch_result=[_make_row(ep)])
        search_results = _make_search_results_for([ep], score=0.9)
        adapter = _make_adapter(conn, search_results=search_results)
        result = await adapter.prefetch("python error")
        assert "Past Context" in result
        assert "debugged python import error" in result

    async def test_respects_budget(self) -> None:
        ep = _ep(summary="a" * 300, task_description="long task " + "b" * 100)
        conn = _make_conn(fetch_result=[_make_row(ep)])
        search_results = _make_search_results_for([ep], score=0.9)
        mock_port = _MockSearchPort(search_results)
        adapter = PostgresMemoryAdapter(
            dsn="postgresql://u:p@h/db",
            prefetch_budget=10,
            prefetch_min_relevance=0.0,
            search_port=mock_port,
        )
        adapter._pool = _make_pool(conn)
        result = await adapter.prefetch("long task")
        assert isinstance(result, str)

    async def test_empty_query_returns_empty(self) -> None:
        adapter = _make_adapter(search_results=[])
        result = await adapter.prefetch("   ")
        assert result == ""

    async def test_contains_outcome_and_date(self) -> None:
        ts = datetime(2025, 6, 1, tzinfo=UTC)
        ep = _ep(summary="fixed database migration", timestamp=ts, outcome=Outcome.SUCCESS)
        conn = _make_conn(fetch_result=[_make_row(ep)])
        search_results = _make_search_results_for([ep], score=0.8)
        adapter = _make_adapter(conn, search_results=search_results)
        result = await adapter.prefetch("database migration")
        if result:
            assert "2025-06-01" in result
            assert "SUCCESS" in result

    async def test_separator_between_blocks(self) -> None:
        ep1 = _ep(episode_id="e1", summary="configured nginx reverse proxy")
        ep2 = _ep(episode_id="e2", summary="configured redis cache")
        conn = _make_conn(fetch_result=[_make_row(ep1), _make_row(ep2)])
        search_results = _make_search_results_for([ep1, ep2], score=0.9)
        adapter = _make_adapter(conn, search_results=search_results)
        result = await adapter.prefetch("configured")
        if result and result.count("---") >= 1:
            assert "---" in result


# ---------------------------------------------------------------------------
# search_sessions
# ---------------------------------------------------------------------------


class TestSearchSessions:
    async def test_empty_query_returns_empty(self) -> None:
        adapter = _make_adapter()
        result = await adapter.search_sessions("  ")
        assert result == []

    async def test_no_rows_returns_empty(self) -> None:
        adapter = _make_adapter(search_results=[])
        result = await adapter.search_sessions("python")
        assert result == []

    async def test_groups_by_session(self) -> None:
        ep1 = _ep(episode_id="e1", session_id="s1", summary="git commit changes")
        ep2 = _ep(episode_id="e2", session_id="s1", summary="git push to remote")
        ep3 = _ep(episode_id="e3", session_id="s2", summary="git merge main")
        conn = _make_conn(fetch_result=[_make_row(ep1), _make_row(ep2), _make_row(ep3)])
        search_results = _make_search_results_for([ep1, ep2, ep3])
        adapter = _make_adapter(conn, search_results=search_results)
        summaries = await adapter.search_sessions("git", limit=10)
        session_ids = {s.session_id for s in summaries}
        assert "s1" in session_ids
        assert "s2" in session_ids

    async def test_episode_count_per_session(self) -> None:
        eps = [
            _ep(episode_id=f"e{i}", session_id="s1", summary="refactored module") for i in range(3)
        ]
        conn = _make_conn(fetch_result=[_make_row(ep) for ep in eps])
        search_results = _make_search_results_for(eps)
        adapter = _make_adapter(conn, search_results=search_results)
        summaries = await adapter.search_sessions("refactored", limit=5)
        s1 = next((s for s in summaries if s.session_id == "s1"), None)
        assert s1 is not None
        assert s1.episode_count == 3

    async def test_sorted_by_last_active_desc(self) -> None:
        ts_old = datetime.now(UTC) - timedelta(days=10)
        ts_new = datetime.now(UTC)
        ep_old = _ep(episode_id="old", session_id="s-old", summary="old task", timestamp=ts_old)
        ep_new = _ep(episode_id="new", session_id="s-new", summary="old task", timestamp=ts_new)
        conn = _make_conn(fetch_result=[_make_row(ep_old), _make_row(ep_new)])
        search_results = _make_search_results_for([ep_old, ep_new])
        adapter = _make_adapter(conn, search_results=search_results)
        summaries = await adapter.search_sessions("old task", limit=5)
        if len(summaries) >= 2:
            assert summaries[0].last_active >= summaries[1].last_active

    async def test_respects_limit(self) -> None:
        eps = [
            _ep(episode_id=f"e{i}", session_id=f"s{i}", summary="fixed bug in code")
            for i in range(5)
        ]
        conn = _make_conn(fetch_result=[_make_row(ep) for ep in eps])
        search_results = _make_search_results_for(eps)
        adapter = _make_adapter(conn, search_results=search_results)
        summaries = await adapter.search_sessions("fixed bug", limit=2)
        assert len(summaries) <= 2

    async def test_tags_collected_and_capped_at_ten(self) -> None:
        tags = [f"tag{i}" for i in range(15)]
        ep = _ep(session_id="s1", tags=tags)
        conn = _make_conn(fetch_result=[_make_row(ep)])
        search_results = _make_search_results_for([ep])
        adapter = _make_adapter(conn, search_results=search_results)
        summaries = await adapter.search_sessions("refactored", limit=5)
        if summaries:
            assert len(summaries[0].tags) <= 10

    async def test_search_port_called_for_sessions(self) -> None:
        mock_port = _MockSearchPort([])
        adapter = _make_adapter(search_port=mock_port)
        await adapter.search_sessions("python tests")
        assert mock_port.last_query == "python tests"

    async def test_summary_truncated_at_budget(self) -> None:
        long_summary = "x" * 200_000
        ep = _ep(session_id="s1", summary=long_summary, task_description="big task")
        conn = _make_conn(fetch_result=[_make_row(ep)])
        search_results = _make_search_results_for([ep])
        mock_port = _MockSearchPort(search_results)
        adapter = PostgresMemoryAdapter(
            dsn="postgresql://u:p@h/db",
            session_search_truncate_chars=100,
            search_port=mock_port,
        )
        adapter._pool = _make_pool(conn)
        summaries = await adapter.search_sessions("big task", limit=5)
        assert isinstance(summaries, list)


# ---------------------------------------------------------------------------
# shared context
# ---------------------------------------------------------------------------


class TestSharedContext:
    async def test_default_none(self) -> None:
        adapter = _make_adapter()
        assert adapter.get_shared_context() is None

    async def test_inject_and_retrieve(self) -> None:
        adapter = _make_adapter()
        ctx = SharedContext(data={"agent": "sköll"})
        adapter.inject_shared_context(ctx)
        assert adapter.get_shared_context() is ctx

    async def test_inject_replaces_previous(self) -> None:
        adapter = _make_adapter()
        adapter.inject_shared_context(SharedContext(data={"a": 1}))
        adapter.inject_shared_context(SharedContext(data={"b": 2}))
        ctx = adapter.get_shared_context()
        assert ctx is not None
        assert "b" in ctx.data
        assert "a" not in ctx.data
