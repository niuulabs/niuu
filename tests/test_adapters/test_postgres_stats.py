"""Tests for PostgresStatsRepository adapter."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from volundr.adapters.outbound.postgres_stats import PostgresStatsRepository
from volundr.domain.models import Stats


@pytest.fixture
def mock_pool() -> MagicMock:
    """Create a mock asyncpg pool."""
    pool = MagicMock()
    pool.acquire = MagicMock()
    return pool


@pytest.fixture
def stats_repo(mock_pool: MagicMock) -> PostgresStatsRepository:
    """Create a stats repository with mock pool."""
    return PostgresStatsRepository(mock_pool)


class TestPostgresStatsRepository:
    """Tests for PostgresStatsRepository."""

    async def test_get_stats_returns_stats(
        self, stats_repo: PostgresStatsRepository, mock_pool: MagicMock
    ) -> None:
        """Test that get_stats returns Stats object with correct values."""
        # Mock connection context manager
        mock_conn = AsyncMock()
        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_conn
        mock_context.__aexit__.return_value = None
        mock_pool.acquire.return_value = mock_context

        # Mock session count query result
        session_counts_row = {
            "active_sessions": 5,
            "total_sessions": 20,
            "sessions_today": 2,
        }

        # Mock token usage query result
        token_stats_row = {
            "tokens_today": 100000,
            "local_tokens": 40000,
            "cloud_tokens": 60000,
            "cost_today": Decimal("3.75"),
        }

        chronicle_stats_row = {
            "tokens_today": 0,
            "cost_today": Decimal("0"),
        }

        mock_conn.fetchrow = AsyncMock(
            side_effect=[session_counts_row, token_stats_row, chronicle_stats_row]
        )
        mock_conn.fetch = AsyncMock(return_value=[{"count": 0.0}, {"count": 2.0}])

        stats = await stats_repo.get_stats(tenant_id=None, owner_id=None)

        assert isinstance(stats, Stats)
        assert stats.active_sessions == 5
        assert stats.total_sessions == 20
        assert stats.sessions_today == 2
        assert stats.tokens_today == 100000
        assert stats.local_tokens == 40000
        assert stats.cloud_tokens == 60000
        assert stats.cost_today == Decimal("3.75")
        assert stats.sparklines == {"sessionsToday": [0.0, 2.0]}

    async def test_get_stats_with_zero_values(
        self, stats_repo: PostgresStatsRepository, mock_pool: MagicMock
    ) -> None:
        """Test that get_stats handles zero values correctly."""
        mock_conn = AsyncMock()
        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_conn
        mock_context.__aexit__.return_value = None
        mock_pool.acquire.return_value = mock_context

        session_counts_row = {
            "active_sessions": 0,
            "total_sessions": 0,
            "sessions_today": 0,
        }

        token_stats_row = {
            "tokens_today": 0,
            "local_tokens": 0,
            "cloud_tokens": 0,
            "cost_today": Decimal("0"),
        }

        chronicle_stats_row = {
            "tokens_today": 0,
            "cost_today": Decimal("0"),
        }

        mock_conn.fetchrow = AsyncMock(
            side_effect=[session_counts_row, token_stats_row, chronicle_stats_row]
        )
        mock_conn.fetch = AsyncMock(return_value=[])

        stats = await stats_repo.get_stats(tenant_id=None, owner_id=None)

        assert stats.active_sessions == 0
        assert stats.total_sessions == 0
        assert stats.sessions_today == 0
        assert stats.tokens_today == 0
        assert stats.local_tokens == 0
        assert stats.cloud_tokens == 0
        assert stats.cost_today == Decimal("0")
        assert stats.sparklines == {"sessionsToday": []}

    async def test_get_stats_queries_executed(
        self, stats_repo: PostgresStatsRepository, mock_pool: MagicMock
    ) -> None:
        """Test that correct SQL queries are executed."""
        mock_conn = AsyncMock()
        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_conn
        mock_context.__aexit__.return_value = None
        mock_pool.acquire.return_value = mock_context

        mock_conn.fetchrow = AsyncMock(
            side_effect=[
                {"active_sessions": 0, "total_sessions": 0, "sessions_today": 0},
                {
                    "tokens_today": 0,
                    "local_tokens": 0,
                    "cloud_tokens": 0,
                    "cost_today": Decimal("0"),
                },
                {
                    "tokens_today": 0,
                    "cost_today": Decimal("0"),
                },
            ]
        )
        mock_conn.fetch = AsyncMock(return_value=[])

        await stats_repo.get_stats(tenant_id=None, owner_id=None)

        # Verify fetchrow was called for session counts, token stats, and chronicle fallback.
        assert mock_conn.fetchrow.call_count == 3
        assert mock_conn.fetch.call_count == 1

        # Verify session counts query contains expected elements.
        first_call_sql = mock_conn.fetchrow.call_args_list[0][0][0]
        assert "sessions" in first_call_sql.lower()
        assert "chronicles" in first_call_sql.lower()
        assert "session_starts" in first_call_sql.lower()
        assert "running" in first_call_sql.lower()
        assert "count" in first_call_sql.lower()

        # Verify token stats query contains expected elements
        second_call_sql = mock_conn.fetchrow.call_args_list[1][0][0]
        assert "token_usage" in second_call_sql.lower()
        assert "sum" in second_call_sql.lower()

        third_call_sql = mock_conn.fetchrow.call_args_list[2][0][0]
        assert "chronicles" in third_call_sql.lower()
        assert "token_usage" in third_call_sql.lower()

        sparkline_sql = mock_conn.fetch.call_args_list[0][0][0]
        assert "chronicles" in sparkline_sql.lower()
        assert "session_starts" in sparkline_sql.lower()
        assert "generate_series" in sparkline_sql.lower()

    async def test_get_stats_adds_chronicle_usage_for_sessions_without_token_rows(
        self, stats_repo: PostgresStatsRepository, mock_pool: MagicMock
    ) -> None:
        """Chronicle usage fills the dashboard when live token rows are absent."""
        mock_conn = AsyncMock()
        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_conn
        mock_context.__aexit__.return_value = None
        mock_pool.acquire.return_value = mock_context

        mock_conn.fetchrow = AsyncMock(
            side_effect=[
                {"active_sessions": 1, "total_sessions": 3, "sessions_today": 1},
                {
                    "tokens_today": 100,
                    "local_tokens": 40,
                    "cloud_tokens": 60,
                    "cost_today": Decimal("0.02"),
                },
                {
                    "tokens_today": 900,
                    "cost_today": Decimal("0.18"),
                },
            ]
        )
        mock_conn.fetch = AsyncMock(return_value=[])

        stats = await stats_repo.get_stats(tenant_id=None, owner_id=None)

        assert stats.active_sessions == 1
        assert stats.total_sessions == 3
        assert stats.sessions_today == 1
        assert stats.tokens_today == 1000
        assert stats.local_tokens == 40
        assert stats.cloud_tokens == 960
        assert stats.cost_today == Decimal("0.20")

    async def test_every_query_is_bounded_by_tenant_and_owner(
        self, stats_repo: PostgresStatsRepository, mock_pool: MagicMock
    ) -> None:
        """The caller's bounds reach every query as $1 (tenant) and $2 (owner)."""
        mock_conn = AsyncMock()
        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_conn
        mock_context.__aexit__.return_value = None
        mock_pool.acquire.return_value = mock_context
        mock_conn.fetchrow = AsyncMock(
            side_effect=[
                {"active_sessions": 0, "total_sessions": 0, "sessions_today": 0},
                {
                    "tokens_today": 0,
                    "local_tokens": 0,
                    "cloud_tokens": 0,
                    "cost_today": Decimal("0"),
                },
                {"tokens_today": 0, "cost_today": Decimal("0")},
            ]
        )
        mock_conn.fetch = AsyncMock(return_value=[])

        await stats_repo.get_stats(tenant_id="t1", owner_id="alice")

        calls = [*mock_conn.fetchrow.call_args_list, *mock_conn.fetch.call_args_list]
        assert len(calls) == 4
        session_bound = "($1::text IS NULL OR s.tenant_id = $1) AND "
        session_bound += "($2::text IS NULL OR s.owner_id = $2)"
        chronicle_bound = session_bound.replace("s.", "c.")
        for call in calls:
            sql, tenant_id, owner_id, _day = call.args
            assert (tenant_id, owner_id) == ("t1", "alice")
            assert session_bound in sql or chronicle_bound in sql
        counts_sql, tokens_sql, fallback_sql = (c.args[0] for c in calls[:3])
        sparkline_sql = calls[3].args[0]
        # Session starts span live sessions and durable chronicles, both bounded.
        for sql in (counts_sql, sparkline_sql):
            assert session_bound in sql and chronicle_bound in sql
        # Token rows are attributed through their session.
        assert "JOIN sessions s ON s.id = t.session_id" in tokens_sql
        assert session_bound in tokens_sql
        assert chronicle_bound in fallback_sql

    async def test_every_query_counts_one_utc_day(
        self, stats_repo: PostgresStatsRepository, mock_pool: MagicMock
    ) -> None:
        """Every query takes the same UTC date as $3 instead of the session's clock."""
        mock_conn = AsyncMock()
        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_conn
        mock_context.__aexit__.return_value = None
        mock_pool.acquire.return_value = mock_context
        mock_conn.fetchrow = AsyncMock(
            side_effect=[
                {"active_sessions": 0, "total_sessions": 0, "sessions_today": 0},
                {
                    "tokens_today": 0,
                    "local_tokens": 0,
                    "cloud_tokens": 0,
                    "cost_today": Decimal("0"),
                },
                {"tokens_today": 0, "cost_today": Decimal("0")},
            ]
        )
        mock_conn.fetch = AsyncMock(return_value=[])

        before = datetime.now(UTC).date()
        await stats_repo.get_stats(tenant_id=None, owner_id=None)
        after = datetime.now(UTC).date()

        calls = [*mock_conn.fetchrow.call_args_list, *mock_conn.fetch.call_args_list]
        days = {call.args[3] for call in calls}
        assert len(days) == 1
        assert days <= {before, after}
        today_start = "($3::date::timestamp AT TIME ZONE 'UTC')"
        for call in calls:
            sql = call.args[0]
            assert "CURRENT_DATE" not in sql
            assert "now()" not in sql.lower()
        counts_sql, tokens_sql, fallback_sql = (c.args[0] for c in calls[:3])
        assert f"started_at >= {today_start}" in counts_sql
        assert f"t.recorded_at >= {today_start}" in tokens_sql
        assert f"WHERE recorded_at >= {today_start}" in fallback_sql
        assert f"c.updated_at >= {today_start}" in fallback_sql
        # The sparkline's days and buckets are UTC dates ending on $3.
        sparkline_sql = calls[3].args[0]
        assert "$3::date - days_ago" in sparkline_sql
        assert "(started_at AT TIME ZONE 'UTC')::date" in sparkline_sql
