"""Replay watermarks for host-key signed VM guest metadata requests."""

from __future__ import annotations

from uuid import UUID

import asyncpg


class PostgresGuestRequestWatermarks:
    """Per-allocation highest accepted request timestamp, advanced atomically."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def try_advance(self, allocation_id: UUID, timestamp_ms: int) -> bool:
        # One atomic statement: the WHERE on the conflict branch re-checks the stored
        # watermark at write time under the row lock, so two concurrent requests with
        # the same or non-increasing timestamps cannot both succeed. A first request
        # inserts the row. An allocation deleted meanwhile violates the foreign key,
        # which is a rejection like any other unknown allocation.
        try:
            row = await self._pool.fetchrow(
                """
                INSERT INTO compute_guest_request_watermarks (allocation_id, last_request_at)
                VALUES ($1, $2)
                ON CONFLICT (allocation_id) DO UPDATE
                    SET last_request_at = EXCLUDED.last_request_at
                    WHERE compute_guest_request_watermarks.last_request_at
                          < EXCLUDED.last_request_at
                RETURNING allocation_id
                """,
                allocation_id,
                timestamp_ms,
            )
        except asyncpg.ForeignKeyViolationError:
            return False
        return row is not None
