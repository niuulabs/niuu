"""PostgreSQL adapter for statistics repository."""

from decimal import Decimal

import asyncpg

from volundr.domain.models import Stats
from volundr.domain.ports import StatsRepository

# Every query binds $1 to the tenant bound and $2 to the owner bound; NULL is
# unbounded. A bound compares with ``=``, so it never matches a NULL column:
# untenanted or unowned rows count only for an unbounded caller.
_SESSION_IN_SCOPE = (
    "($1::text IS NULL OR s.tenant_id = $1) AND ($2::text IS NULL OR s.owner_id = $2)"
)
_CHRONICLE_IN_SCOPE = (
    "($1::text IS NULL OR c.tenant_id = $1) AND ($2::text IS NULL OR c.owner_id = $2)"
)

# Chronicles are durable after session deletion and carry their session's
# owner and tenant, so include them for history-oriented totals and sparklines.
_SESSION_STARTS = f"""
    session_starts AS (
        SELECT
            session_key,
            MIN(created_at) AS started_at
        FROM (
            SELECT s.id::text AS session_key, s.created_at
            FROM sessions s
            WHERE {_SESSION_IN_SCOPE}

            UNION ALL

            SELECT COALESCE(c.session_id::text, c.id::text) AS session_key, c.created_at
            FROM chronicles c
            WHERE {_CHRONICLE_IN_SCOPE}
        ) raw_starts
        GROUP BY session_key
    )
"""


class PostgresStatsRepository(StatsRepository):
    """PostgreSQL implementation of StatsRepository using raw SQL."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def get_stats(self, *, tenant_id: str | None, owner_id: str | None) -> Stats:
        """Retrieve aggregate statistics for the dashboard within the given bounds."""
        async with self._pool.acquire() as conn:
            session_counts = await conn.fetchrow(
                f"""
                WITH {_SESSION_STARTS}
                SELECT
                    (
                        SELECT COUNT(*)
                        FROM sessions s
                        WHERE s.status = 'running' AND {_SESSION_IN_SCOPE}
                    ) AS active_sessions,
                    COUNT(*) AS total_sessions,
                    COUNT(*) FILTER (
                        WHERE started_at >= CURRENT_DATE AT TIME ZONE 'UTC'
                    ) AS sessions_today
                FROM session_starts
                """,
                tenant_id,
                owner_id,
            )

            # Get token usage for today (UTC). Token rows are deleted with their
            # session, so the session always attributes them.
            token_stats = await conn.fetchrow(
                f"""
                SELECT
                    COALESCE(SUM(t.tokens), 0) AS tokens_today,
                    COALESCE(SUM(t.tokens) FILTER (WHERE t.provider = 'local'), 0)
                        AS local_tokens,
                    COALESCE(SUM(t.tokens) FILTER (WHERE t.provider = 'cloud'), 0)
                        AS cloud_tokens,
                    COALESCE(SUM(t.cost), 0) AS cost_today
                FROM token_usage t
                JOIN sessions s ON s.id = t.session_id
                WHERE t.recorded_at >= CURRENT_DATE AT TIME ZONE 'UTC'
                    AND {_SESSION_IN_SCOPE}
                """,
                tenant_id,
                owner_id,
            )

            # Some live sessions only persist final usage on their chronicle. Treat
            # that as a fallback for sessions without token_usage rows today.
            chronicle_stats = await conn.fetchrow(
                f"""
                WITH today_token_sessions AS (
                    SELECT DISTINCT session_id
                    FROM token_usage
                    WHERE recorded_at >= CURRENT_DATE AT TIME ZONE 'UTC'
                ),
                latest_chronicles AS (
                    SELECT DISTINCT ON (c.session_id)
                        c.session_id,
                        c.token_usage,
                        c.cost
                    FROM chronicles c
                    WHERE
                        c.session_id IS NOT NULL
                        AND c.updated_at >= CURRENT_DATE AT TIME ZONE 'UTC'
                        AND {_CHRONICLE_IN_SCOPE}
                    ORDER BY c.session_id, c.updated_at DESC
                )
                SELECT
                    COALESCE(SUM(token_usage), 0) AS tokens_today,
                    COALESCE(SUM(cost), 0) AS cost_today
                FROM latest_chronicles
                WHERE session_id NOT IN (SELECT session_id FROM today_token_sessions)
                """,
                tenant_id,
                owner_id,
            )

            token_usage_tokens = int(token_stats["tokens_today"])
            chronicle_tokens = int(chronicle_stats["tokens_today"])
            token_usage_cost = Decimal(str(token_stats["cost_today"]))
            chronicle_cost = Decimal(str(chronicle_stats["cost_today"]))
            sessions_by_day = await conn.fetch(
                f"""
                WITH days AS (
                    SELECT generate_series(
                        CURRENT_DATE - INTERVAL '29 days',
                        CURRENT_DATE,
                        INTERVAL '1 day'
                    )::date AS day
                ),
                {_SESSION_STARTS},
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
                """,
                tenant_id,
                owner_id,
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
