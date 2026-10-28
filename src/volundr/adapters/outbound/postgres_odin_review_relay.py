"""PostgreSQL store for what the ODIN review relay already announced."""

from __future__ import annotations

from datetime import datetime

import asyncpg

from volundr.domain.odin_review import OwnerRelayState
from volundr.domain.odin_review_ports import OdinReviewRelayStateStore


class PostgresOdinReviewRelayStateStore(OdinReviewRelayStateStore):
    """Raw-SQL implementation of :class:`OdinReviewRelayStateStore`."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def begin_owner(self, owner_id: str, watermark_at: datetime) -> OwnerRelayState:
        # The first replica to get here fixes the watermark; every later call (and
        # every other replica) reads that one back.
        await self._pool.execute(
            """
            INSERT INTO odin_review_relay_owners (owner_id, watermark_at)
            VALUES ($1, $2)
            ON CONFLICT (owner_id) DO NOTHING
            """,
            owner_id,
            watermark_at,
        )
        row = await self._pool.fetchrow(
            """
            SELECT watermark_at, digest_recorded_at
            FROM odin_review_relay_owners
            WHERE owner_id = $1
            """,
            owner_id,
        )
        if row is None:
            raise RuntimeError(f"ODIN review relay state for {owner_id!r} vanished after insert")
        return OwnerRelayState(
            watermark_at=row["watermark_at"],
            digest_recorded=row["digest_recorded_at"] is not None,
        )

    async def mark_digest_recorded(self, owner_id: str) -> None:
        await self._pool.execute(
            """
            UPDATE odin_review_relay_owners
            SET digest_recorded_at = now()
            WHERE owner_id = $1 AND digest_recorded_at IS NULL
            """,
            owner_id,
        )

    async def recorded_among(self, owner_id: str, review_ids: set[str]) -> set[str]:
        if not review_ids:
            return set()
        rows = await self._pool.fetch(
            """
            SELECT review_id FROM odin_review_relay_items
            WHERE owner_id = $1 AND review_id = ANY($2::text[])
            """,
            owner_id,
            sorted(review_ids),
        )
        return {row["review_id"] for row in rows}

    async def mark_recorded(self, owner_id: str, review_id: str) -> None:
        await self._pool.execute(
            """
            INSERT INTO odin_review_relay_items (owner_id, review_id)
            VALUES ($1, $2)
            ON CONFLICT (owner_id, review_id) DO NOTHING
            """,
            owner_id,
            review_id,
        )

    async def open_ids(self, owner_id: str) -> set[str]:
        rows = await self._pool.fetch(
            """
            SELECT review_id FROM odin_review_relay_items
            WHERE owner_id = $1 AND retired_at IS NULL
            """,
            owner_id,
        )
        return {row["review_id"] for row in rows}

    async def mark_retired(self, owner_id: str, review_id: str) -> None:
        await self._pool.execute(
            """
            UPDATE odin_review_relay_items
            SET retired_at = now()
            WHERE owner_id = $1 AND review_id = $2 AND retired_at IS NULL
            """,
            owner_id,
            review_id,
        )
