"""PostgreSQL adapter for statistics repository."""

from decimal import Decimal

import asyncpg

from volundr.domain.models import Stats
from volundr.domain.ports import StatsRepository


class PostgresStatsRepository(StatsRepository):
    """PostgreSQL implementation of StatsRepository using raw SQL."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def get_stats(self) -> Stats:
        """Retrieve aggregate statistics for the dashboard."""
        async with self._pool.acquire() as conn:
            # Get session counts
            session_counts = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status = 'running') AS active_sessions,
                    COUNT(*) AS total_sessions
                FROM sessions
                """
            )

            # Get token usage for today (UTC)
            token_stats = await conn.fetchrow(
                """
                SELECT
                    COALESCE(SUM(tokens), 0) AS tokens_today,
                    COALESCE(SUM(tokens) FILTER (WHERE provider = 'local'), 0) AS local_tokens,
                    COALESCE(SUM(tokens) FILTER (WHERE provider = 'cloud'), 0) AS cloud_tokens,
                    COALESCE(SUM(cost), 0) AS cost_today
                FROM token_usage
                WHERE recorded_at >= CURRENT_DATE AT TIME ZONE 'UTC'
                """
            )

            # Some live sessions only persist final usage on their chronicle. Treat
            # that as a fallback for sessions without token_usage rows today.
            chronicle_stats = await conn.fetchrow(
                """
                WITH today_token_sessions AS (
                    SELECT DISTINCT session_id
                    FROM token_usage
                    WHERE recorded_at >= CURRENT_DATE AT TIME ZONE 'UTC'
                ),
                latest_chronicles AS (
                    SELECT DISTINCT ON (session_id)
                        session_id,
                        token_usage,
                        cost
                    FROM chronicles
                    WHERE
                        session_id IS NOT NULL
                        AND updated_at >= CURRENT_DATE AT TIME ZONE 'UTC'
                    ORDER BY session_id, updated_at DESC
                )
                SELECT
                    COALESCE(SUM(token_usage), 0) AS tokens_today,
                    COALESCE(SUM(cost), 0) AS cost_today
                FROM latest_chronicles
                WHERE session_id NOT IN (SELECT session_id FROM today_token_sessions)
                """
            )

            token_usage_tokens = int(token_stats["tokens_today"])
            chronicle_tokens = int(chronicle_stats["tokens_today"])
            token_usage_cost = Decimal(str(token_stats["cost_today"]))
            chronicle_cost = Decimal(str(chronicle_stats["cost_today"]))

            return Stats(
                active_sessions=session_counts["active_sessions"],
                total_sessions=session_counts["total_sessions"],
                tokens_today=token_usage_tokens + chronicle_tokens,
                local_tokens=token_stats["local_tokens"],
                cloud_tokens=token_stats["cloud_tokens"] + chronicle_tokens,
                cost_today=token_usage_cost + chronicle_cost,
            )
