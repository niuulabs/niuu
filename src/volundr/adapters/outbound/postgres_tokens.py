"""PostgreSQL adapter for token tracking."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg

from volundr.domain.models import ModelProvider, TokenUsageRecord
from volundr.domain.ports import TokenTracker


class PostgresTokenTracker(TokenTracker):
    """PostgreSQL implementation of TokenTracker using raw SQL."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def record_usage(
        self,
        session_id: UUID,
        tokens: int,
        provider: ModelProvider,
        model: str,
        cost: float | None = None,
    ) -> TokenUsageRecord:
        """Record token usage for a session."""
        record_id = uuid4()
        recorded_at = datetime.now(UTC)
        cost_decimal = Decimal(str(cost)) if cost is not None else None

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO token_usage (id, session_id, recorded_at, tokens, provider, model, cost)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                record_id,
                session_id,
                recorded_at,
                tokens,
                provider.value,
                model,
                cost_decimal,
            )

        return TokenUsageRecord(
            id=record_id,
            session_id=session_id,
            recorded_at=recorded_at,
            tokens=tokens,
            provider=provider,
            model=model,
            cost=cost_decimal,
        )

    async def get_session_usage(self, session_id: UUID) -> int:
        """Get total tokens used by a session."""
        async with self._pool.acquire() as conn:
            result = await conn.fetchval(
                """
                SELECT COALESCE(SUM(tokens), 0)
                FROM token_usage
                WHERE session_id = $1
                """,
                session_id,
            )
            return int(result)
