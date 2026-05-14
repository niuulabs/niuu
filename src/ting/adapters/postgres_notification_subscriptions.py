"""PostgreSQL adapter for notification subscription lookups."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ting.ports.notification_subscriptions import NotificationSubscriptionRepository

if TYPE_CHECKING:
    import asyncpg


class PostgresNotificationSubscriptionRepository(NotificationSubscriptionRepository):
    """Queries notification_subscriptions via asyncpg."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def find_owner_by_telegram_chat_id(self, chat_id: str) -> str | None:
        row = await self._pool.fetchrow(
            """
            SELECT owner_id
            FROM notification_subscriptions
            WHERE channel = 'telegram'
              AND config->>'chat_id' = $1
              AND enabled = true
            LIMIT 1
            """,
            chat_id,
        )
        if row is None:
            return None
        return row["owner_id"]
