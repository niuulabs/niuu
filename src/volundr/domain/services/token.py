"""Domain service for token usage tracking."""

from __future__ import annotations

import logging
from uuid import UUID

from volundr.domain.models import (
    ModelProvider,
    SessionStatus,
    TokenUsageRecord,
)
from volundr.domain.ports import (
    EventBroadcaster,
    PricingProvider,
    SessionRepository,
    TokenTracker,
)

from .session import SessionNotFoundError

logger = logging.getLogger(__name__)


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


class SessionNotRunningError(Exception):
    """Raised when trying to report usage for a non-running session."""

    def __init__(self, session_id: UUID, current_status: SessionStatus):
        self.session_id = session_id
        self.current_status = current_status
        super().__init__(
            f"Cannot report usage for session {session_id}: "
            f"session is {current_status.value}, not running"
        )


class TokenService:
    """Service for tracking token usage."""

    def __init__(
        self,
        token_tracker: TokenTracker,
        session_repository: SessionRepository,
        pricing_provider: PricingProvider | None = None,
        broadcaster: EventBroadcaster | None = None,
    ):
        self._token_tracker = token_tracker
        self._session_repository = session_repository
        self._pricing_provider = pricing_provider
        self._broadcaster = broadcaster

    def _calculate_cost(self, tokens: int, provider: ModelProvider, model: str) -> float | None:
        """Calculate cost for token usage.

        Args:
            tokens: Number of tokens used.
            provider: The model provider.
            model: The model identifier.

        Returns:
            Cost in USD, or None if not calculable.
        """
        if provider != ModelProvider.CLOUD:
            return None

        if self._pricing_provider is None:
            return None

        price_per_million = self._pricing_provider.get_price(model)
        if price_per_million is None:
            return None

        return (tokens / 1_000_000) * price_per_million

    async def record_usage(
        self,
        session_id: UUID,
        tokens: int,
        provider: ModelProvider,
        model: str,
        message_count: int = 1,
        cost: float | None = None,
    ) -> TokenUsageRecord:
        """Record token usage for a session.

        Args:
            session_id: The session ID.
            tokens: Number of tokens used.
            provider: The model provider (cloud or local).
            model: The model identifier.
            message_count: Number of messages (default 1).
            cost: Pre-calculated cost in USD. When provided (e.g. from the
                Claude CLI ``costUSD`` field) this value is used directly
                instead of deriving cost from the pricing table.

        Returns:
            The created TokenUsageRecord.

        Raises:
            SessionNotFoundError: If session doesn't exist.
            SessionNotRunningError: If session is not running.
        """
        session = await self._session_repository.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)

        # Accept usage during any *live* phase, not just RUNNING. The broker
        # reports the first turn's tokens within ~2s of spawn — while the session
        # is still PROVISIONING (the readiness poll flips it to RUNNING at ~5s).
        # Rejecting those reports with 409 silently dropped the opening turn's
        # tokens (the long-standing tokens_used=0 symptom). Only terminal/idle-
        # before-start states should refuse usage.
        live_statuses = (
            SessionStatus.STARTING,
            SessionStatus.PROVISIONING,
            SessionStatus.RUNNING,
        )
        if session.status not in live_statuses:
            raise SessionNotRunningError(session_id, session.status)

        # Use pre-calculated cost when provided, fall back to pricing table
        final_cost = cost if cost is not None else self._calculate_cost(tokens, provider, model)

        # Record the usage
        record = await self._token_tracker.record_usage(
            session_id=session_id,
            tokens=tokens,
            provider=provider,
            model=model,
            cost=final_cost,
        )
        logger.info(
            "Token usage recorded: session=%s, tokens=%d, provider=%s, model=%s, cost=%s",
            _sanitize_log(session_id),
            tokens,
            provider.value,
            _sanitize_log(model),
            final_cost,
        )

        # Update session activity
        updated_session = session.with_activity(
            message_count=session.message_count + message_count,
            tokens=session.tokens_used + tokens,
        )
        await self._session_repository.update(updated_session)

        if self._broadcaster is not None:
            logger.info(
                "SSE: publishing session_updated for session=%s, tokens_used=%d",
                _sanitize_log(session_id),
                updated_session.tokens_used,
            )
            await self._broadcaster.publish_session_updated(updated_session)
        else:
            logger.warning(
                "SSE: no broadcaster configured, session_updated not sent for session=%s",
                _sanitize_log(session_id),
            )

        return record

    async def get_session_usage(self, session_id: UUID) -> int:
        """Get total tokens used by a session.

        Args:
            session_id: The session ID.

        Returns:
            Total tokens used by the session.

        Raises:
            SessionNotFoundError: If session doesn't exist.
        """
        session = await self._session_repository.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)

        return await self._token_tracker.get_session_usage(session_id)
