"""PostgreSQL adapter for shared inbound-consumer cursors."""

from __future__ import annotations

import asyncpg

from volundr.domain.ports import CommunicationCursorRepository


class PostgresCommunicationCursorRepository(CommunicationCursorRepository):
    """Persist per-platform consumer cursors for shared communication ingress."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_cursor(self, platform: str, consumer_key: str) -> str | None:
        return await self._pool.fetchval(
            """
            SELECT cursor
            FROM communication_cursors
            WHERE platform = $1
              AND consumer_key = $2
            """,
            platform,
            consumer_key,
        )

    async def upsert_cursor(self, platform: str, consumer_key: str, cursor: str) -> None:
        await self._pool.execute(
            """
            INSERT INTO communication_cursors (platform, consumer_key, cursor, updated_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (platform, consumer_key) DO UPDATE SET
                cursor = EXCLUDED.cursor,
                updated_at = EXCLUDED.updated_at
            """,
            platform,
            consumer_key,
            cursor,
        )
