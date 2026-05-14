"""BifrostPublisher — convenience façade for Bifrost → Sleipnir event emission.

Wraps a :class:`~sleipnir.ports.events.SleipnirPublisher` and provides
one method per Bifrost event type, handling :class:`~sleipnir.domain.events.SleipnirEvent`
construction and urgency assignment so callers never build events manually.

Event type → urgency mapping (per NIU-526 spec)
-----------------------------------------------
``bifrost.request.complete``  → 0.0
``bifrost.quota.warning``     → 0.5
``bifrost.quota.exceeded``    → 0.7
``bifrost.provider.down``     → 0.8
``bifrost.provider.recovered``→ 0.3
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sleipnir.domain.events import SleipnirEvent
from sleipnir.domain.registry import (
    BIFROST_PROVIDER_DOWN,
    BIFROST_PROVIDER_RECOVERED,
    BIFROST_QUOTA_EXCEEDED,
    BIFROST_QUOTA_WARNING,
    BIFROST_REQUEST_COMPLETE,
)
from sleipnir.ports.events import SleipnirPublisher

logger = logging.getLogger(__name__)

_SOURCE = "bifrost:llm-adapter"
_DOMAIN = "infrastructure"


class BifrostPublisher:
    """Façade that maps Bifrost lifecycle events onto Sleipnir.

    All publish calls are fire-and-forget: errors are logged and swallowed
    so that a Sleipnir outage never disrupts the LLM call path.

    Args:
        publisher: Underlying Sleipnir publisher (transport-agnostic).
        agent_id:  Optional identifier for the agent/saga making LLM calls
                   (used as ``correlation_id`` on events).
        tenant_id: Optional tenant scope forwarded to every event.
    """

    def __init__(
        self,
        publisher: SleipnirPublisher,
        *,
        agent_id: str = "",
        tenant_id: str | None = None,
    ) -> None:
        self._publisher = publisher
        self._agent_id = agent_id
        self._tenant_id = tenant_id

    # ------------------------------------------------------------------
    # Public publish methods
    # ------------------------------------------------------------------

    async def request_complete(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
    ) -> None:
        """Publish ``bifrost.request.complete`` (urgency 0.0)."""
        await self._emit(
            event_type=BIFROST_REQUEST_COMPLETE,
            urgency=0.0,
            summary=f"LLM call complete: {model} ({input_tokens + output_tokens} tokens)",
            payload={
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "latency_ms": latency_ms,
                "agent_id": self._agent_id,
            },
        )

    async def quota_warning(
        self,
        *,
        tokens_used: int,
        budget_tokens: int,
    ) -> None:
        """Publish ``bifrost.quota.warning`` (urgency 0.5)."""
        pct = tokens_used / budget_tokens if budget_tokens > 0 else 0.0
        await self._emit(
            event_type=BIFROST_QUOTA_WARNING,
            urgency=0.5,
            summary=(
                f"Agent {self._agent_id or 'unknown'} at "
                f"{pct:.0%} of token budget ({tokens_used}/{budget_tokens})"
            ),
            payload={
                "agent_id": self._agent_id,
                "tokens_used": tokens_used,
                "budget_tokens": budget_tokens,
                "pct_used": round(pct, 4),
            },
        )

    async def quota_exceeded(
        self,
        *,
        tokens_used: int,
        budget_tokens: int,
    ) -> None:
        """Publish ``bifrost.quota.exceeded`` (urgency 0.7)."""
        await self._emit(
            event_type=BIFROST_QUOTA_EXCEEDED,
            urgency=0.7,
            summary=(
                f"Agent {self._agent_id or 'unknown'} exceeded token budget "
                f"({tokens_used}/{budget_tokens})"
            ),
            payload={
                "agent_id": self._agent_id,
                "tokens_used": tokens_used,
                "budget_tokens": budget_tokens,
            },
        )

    async def provider_down(self, *, provider: str, status_code: int, error: str) -> None:
        """Publish ``bifrost.provider.down`` (urgency 0.8)."""
        await self._emit(
            event_type=BIFROST_PROVIDER_DOWN,
            urgency=0.8,
            summary=f"LLM provider unreachable: {provider} (HTTP {status_code})",
            payload={
                "provider": provider,
                "status_code": status_code,
                "error": error,
            },
        )

    async def provider_recovered(self, *, provider: str) -> None:
        """Publish ``bifrost.provider.recovered`` (urgency 0.3)."""
        await self._emit(
            event_type=BIFROST_PROVIDER_RECOVERED,
            urgency=0.3,
            summary=f"LLM provider recovered: {provider}",
            payload={"provider": provider},
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _emit(
        self,
        *,
        event_type: str,
        urgency: float,
        summary: str,
        payload: dict,
    ) -> None:
        event = SleipnirEvent(
            event_type=event_type,
            source=_SOURCE,
            payload=payload,
            summary=summary,
            urgency=urgency,
            domain=_DOMAIN,
            timestamp=datetime.now(UTC),
            correlation_id=self._agent_id or None,
            tenant_id=self._tenant_id,
        )
        try:
            await self._publisher.publish(event)
        except Exception:
            logger.error(
                "BifrostPublisher: failed to publish %s to Sleipnir",
                event_type,
                exc_info=True,
            )
