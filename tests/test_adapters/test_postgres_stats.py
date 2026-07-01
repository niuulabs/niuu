"""Tests for PostgresStatsRepository adapter."""

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

        stats = await stats_repo.get_stats()

        assert isinstance(stats, Stats)
        assert stats.active_sessions == 5
        assert stats.total_sessions == 20
        assert stats.tokens_today == 100000
        assert stats.local_tokens == 40000
        assert stats.cloud_tokens == 60000
        assert stats.cost_today == Decimal("3.75")

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

        stats = await stats_repo.get_stats()

        assert stats.active_sessions == 0
        assert stats.total_sessions == 0
        assert stats.tokens_today == 0
        assert stats.local_tokens == 0
        assert stats.cloud_tokens == 0
        assert stats.cost_today == Decimal("0")

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
                {"active_sessions": 0, "total_sessions": 0},
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

        await stats_repo.get_stats()

        # Verify fetchrow was called for session counts, token stats, and chronicle fallback.
        assert mock_conn.fetchrow.call_count == 3

        # Verify session counts query contains expected elements
        first_call_sql = mock_conn.fetchrow.call_args_list[0][0][0]
        assert "sessions" in first_call_sql.lower()
        assert "running" in first_call_sql.lower()
        assert "count" in first_call_sql.lower()

        # Verify token stats query contains expected elements
        second_call_sql = mock_conn.fetchrow.call_args_list[1][0][0]
        assert "token_usage" in second_call_sql.lower()
        assert "sum" in second_call_sql.lower()

        third_call_sql = mock_conn.fetchrow.call_args_list[2][0][0]
        assert "chronicles" in third_call_sql.lower()
        assert "token_usage" in third_call_sql.lower()

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
                {"active_sessions": 1, "total_sessions": 3},
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

        stats = await stats_repo.get_stats()

        assert stats.active_sessions == 1
        assert stats.total_sessions == 3
        assert stats.tokens_today == 1000
        assert stats.local_tokens == 40
        assert stats.cloud_tokens == 960
        assert stats.cost_today == Decimal("0.20")
