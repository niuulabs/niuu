"""PostgreSQL claims that survive socket, broker and platform restarts."""

from uuid import UUID

import asyncpg

from volundr.adapters.outbound._jsonb import scrub_text
from volundr.domain.message_delivery import (
    DeliveryStatus,
    MessageDelivery,
    MessageDeliveryConflictError,
    MessageDeliveryRepository,
)


class PostgresMessageDelivery(MessageDeliveryRepository):
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def claim(
        self, session_id: UUID, request_id: str, payload_hash: str, claim_token: UUID
    ) -> MessageDelivery:
        # A transaction and row lock serialize competing claims, including a
        # duplicate arriving on another platform worker or after a broker restart.
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """INSERT INTO session_message_deliveries
                       (session_id, request_id, payload_hash, claim_token)
                       VALUES ($1, $2, $3, $4)
                       ON CONFLICT (session_id, request_id) DO NOTHING""",
                    session_id,
                    request_id,
                    payload_hash,
                    claim_token,
                )
                row = await conn.fetchrow(
                    """SELECT * FROM session_message_deliveries
                       WHERE session_id = $1 AND request_id = $2 FOR UPDATE""",
                    session_id,
                    request_id,
                )
                if row["payload_hash"] != payload_hash:
                    raise MessageDeliveryConflictError(
                        "request_id already belongs to different content"
                    )
                return MessageDelivery(
                    request_id=request_id,
                    status=row["status"],
                    claimed=row["claim_token"] == claim_token and row["status"] == "pending",
                    error=row["error"],
                )

    async def settle(
        self,
        session_id: UUID,
        request_id: str,
        claim_token: UUID,
        status: DeliveryStatus,
        error: str | None = None,
    ) -> MessageDelivery:
        if status not in ("delivered", "failed"):
            raise ValueError("Only terminal delivery states can be settled")
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """SELECT * FROM session_message_deliveries
                       WHERE session_id = $1 AND request_id = $2 FOR UPDATE""",
                    session_id,
                    request_id,
                )
                if row is None:
                    raise LookupError("Message delivery claim does not exist")
                if row["claim_token"] != claim_token:
                    raise MessageDeliveryConflictError(
                        "Only the original dispatcher can settle delivery"
                    )
                if row["status"] != "pending":
                    if row["status"] != status:
                        raise MessageDeliveryConflictError(
                            "A terminal delivery state cannot change"
                        )
                    return MessageDelivery(request_id, row["status"], error=row["error"])
                clean_error = scrub_text(error) if error else None
                await conn.execute(
                    """UPDATE session_message_deliveries
                       SET status = $3, error = $4, updated_at = NOW()
                       WHERE session_id = $1 AND request_id = $2""",
                    session_id,
                    request_id,
                    status,
                    clean_error,
                )
                return MessageDelivery(request_id, status, error=clean_error)

    async def get(self, session_id: UUID, request_id: str) -> MessageDelivery | None:
        row = await self._pool.fetchrow(
            """SELECT request_id, status, error FROM session_message_deliveries
               WHERE session_id = $1 AND request_id = $2""",
            session_id,
            request_id,
        )
        if row is None:
            return None
        return MessageDelivery(row["request_id"], row["status"], error=row["error"])
