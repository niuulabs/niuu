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
            # Get session counts. Chronicles are durable after session deletion,
            # so include them for history-oriented totals and sparklines.
            session_counts = await conn.fetchrow(
                """
                WITH session_starts AS (
                    SELECT
                        session_key,
                        MIN(created_at) AS started_at
                    FROM (
                        SELECT id::text AS session_key, created_at
                        FROM sessions

                        UNION ALL

                        SELECT COALESCE(session_id::text, id::text) AS session_key, created_at
                        FROM chronicles
                    ) raw_starts
                    GROUP BY session_key
                )
                SELECT
                    (SELECT COUNT(*) FROM sessions WHERE status = 'running') AS active_sessions,
                    COUNT(*) AS total_sessions,
                    COUNT(*) FILTER (
                        WHERE started_at >= CURRENT_DATE AT TIME ZONE 'UTC'
                    ) AS sessions_today
                FROM session_starts
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
            sessions_by_day = await conn.fetch(
                """
                WITH days AS (
                    SELECT generate_series(
                        CURRENT_DATE - INTERVAL '29 days',
                        CURRENT_DATE,
                        INTERVAL '1 day'
                    )::date AS day
                ),
                session_starts AS (
                    SELECT
                        session_key,
                        MIN(created_at) AS started_at
                    FROM (
                        SELECT id::text AS session_key, created_at
                        FROM sessions

                        UNION ALL

                        SELECT COALESCE(session_id::text, id::text) AS session_key, created_at
                        FROM chronicles
                    ) raw_starts
                    GROUP BY session_key
                ),
                session_counts AS (
                    SELECT
                        (started_at AT TIME ZONE 'UTC')::date AS day,
                        COUNT(*)::float AS count
                    FROM session_starts
                    WHERE started_at >= (CURRENT_DATE - INTERVAL '29 days') AT TIME ZONE 'UTC'
                    GROUP BY 1
                )
                SELECT COALESCE(session_counts.count, 0) AS count
                FROM days
                LEFT JOIN session_counts USING (day)
                ORDER BY days.day
                """
            )

            return Stats(
                active_sessions=session_counts["active_sessions"],
                total_sessions=session_counts["total_sessions"],
                tokens_today=token_usage_tokens + chronicle_tokens,
                local_tokens=token_stats["local_tokens"],
                cloud_tokens=token_stats["cloud_tokens"] + chronicle_tokens,
                cost_today=token_usage_cost + chronicle_cost,
                sessions_today=session_counts["sessions_today"],
                sparklines={
                    "sessionsToday": [float(row["count"]) for row in sessions_by_day],
                },
            )
