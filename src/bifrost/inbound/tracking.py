"""SSE token tracking helpers for the Bifröst inbound layer.

Provides utilities for extracting token usage from Anthropic SSE events,
wrapping async streams with per-request usage recording, and emitting cost
events to downstream Valkyrie consumers.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime

try:
    from sleipnir.domain.catalog import bifrost_budget_degraded as _catalog_budget_degraded
except ImportError:
    _catalog_budget_degraded = None  # type: ignore[assignment]

import bifrost.metrics as _metrics
from bifrost.auth import AgentIdentity
from bifrost.domain.models import RequestLog, TokenUsage
from bifrost.ports.events import BudgetWarningEvent, CostEventEmitter, RequestCompletedEvent
from bifrost.ports.usage_store import UsageRecord, UsageStore
from bifrost.pricing import ModelPricing, calculate_cost

logger = logging.getLogger(__name__)

# Header names for quota warnings.
_HEADER_QUOTA_WARNING = "X-Quota-Warning"
_HEADER_QUOTA_REMAINING = "X-Quota-Remaining"


def _extract_usage_from_sse_line(line: str, usage: TokenUsage) -> None:
    """Parse one SSE data line and update *usage* in-place."""
    if not line.startswith("data: "):
        return
    try:
        payload = json.loads(line[6:])
    except (json.JSONDecodeError, ValueError):
        return

    event_type = payload.get("type", "")

    if event_type == "message_start":
        msg_usage = payload.get("message", {}).get("usage", {})
        usage.input_tokens += msg_usage.get("input_tokens", 0)
        usage.cache_creation_input_tokens += msg_usage.get("cache_creation_input_tokens", 0)
        usage.cache_read_input_tokens += msg_usage.get("cache_read_input_tokens", 0)
        usage.reasoning_tokens += msg_usage.get("reasoning_tokens", 0)
    elif event_type == "message_delta":
        delta_usage = payload.get("usage", {})
        usage.output_tokens += delta_usage.get("output_tokens", 0)
        usage.reasoning_tokens += delta_usage.get("reasoning_tokens", 0)


def _log_request(log: RequestLog) -> None:
    logger.info(
        "request ts=%s model=%s input=%d output=%d cache_read=%d cache_write=%d "
        "reasoning=%d latency=%.1fms stream=%s",
        log.timestamp.isoformat(),
        log.model,
        log.usage.input_tokens,
        log.usage.output_tokens,
        log.usage.cache_read_input_tokens,
        log.usage.cache_creation_input_tokens,
        log.usage.reasoning_tokens,
        log.latency_ms,
        log.stream,
    )


def _compute_budget_pct(spent_usd: float, limit_usd: float) -> float:
    """Return remaining budget as a percentage (0–100).

    Returns 100.0 when *limit_usd* is zero (unlimited budget).
    """
    if limit_usd <= 0.0:
        return 100.0
    remaining = max(0.0, limit_usd - spent_usd)
    return (remaining / limit_usd) * 100.0


async def emit_cost_events(
    emitter: CostEventEmitter,
    store: UsageStore,
    identity: AgentIdentity,
    cost: float,
    tokens_used: int,
    model: str,
    agent_budget_limit: float,
    budget_warning_threshold_pct: float,
    sleipnir_publisher: object | None = None,
) -> None:
    """Emit request_completed (always) and budget_warning (when threshold crossed).

    Queries *store* for the agent's current daily cost to compute the
    remaining budget percentage after recording the current request.
    """
    cost_today = await store.agent_cost_today(identity.agent_id)
    budget_pct = _compute_budget_pct(cost_today, agent_budget_limit)

    await emitter.emit_request_completed(
        RequestCompletedEvent(
            agent_id=identity.agent_id,
            session_id=identity.session_id,
            cost_usd=cost,
            tokens_used=tokens_used,
            budget_pct_remaining=budget_pct,
            model=model,
            timestamp=datetime.now(UTC).isoformat(),
        )
    )

    if agent_budget_limit > 0.0 and budget_pct <= budget_warning_threshold_pct:
        await emitter.emit_budget_warning(
            BudgetWarningEvent(
                agent_id=identity.agent_id,
                budget_pct_remaining=budget_pct,
                daily_limit_usd=agent_budget_limit,
                spent_usd=cost_today,
            )
        )
        # NIU-582: emit bifrost.budget.degraded to Sleipnir catalog (best-effort)
        if sleipnir_publisher is not None and _catalog_budget_degraded is not None:
            try:
                _event = _catalog_budget_degraded(
                    tenant_id=identity.tenant_id,
                    current_spend=cost_today,
                    cap=agent_budget_limit,
                    downgraded_to=model,
                    source=f"bifrost:{identity.agent_id}",
                    correlation_id=identity.session_id or identity.tenant_id,
                )
                await sleipnir_publisher.publish(_event)
            except Exception:
                logger.warning("Failed to emit bifrost.budget.degraded; continuing.", exc_info=True)


async def _stream_with_tracking(
    source: AsyncIterator[str],
    model: str,
    start: float,
    identity: AgentIdentity,
    store: UsageStore,
    pricing_overrides: dict[str, ModelPricing],
    request_id: str,
    emitter: CostEventEmitter,
    provider: str = "",
    agent_budget_limit: float = 0.0,
    budget_warning_threshold_pct: float = 20.0,
    sleipnir_publisher: object | None = None,
) -> AsyncIterator[str]:
    """Yield SSE lines from *source* while tracking token usage."""
    usage = TokenUsage()

    async for line in source:
        _extract_usage_from_sse_line(line, usage)
        yield line

    latency_ms = (time.monotonic() - start) * 1000
    _log_request(
        RequestLog(
            timestamp=datetime.now(UTC),
            model=model,
            usage=usage,
            latency_ms=latency_ms,
            stream=True,
        )
    )

    cost = calculate_cost(model, usage, pricing_overrides)
    _metrics.record_request(
        provider=provider,
        model=model,
        status="200",
        duration_seconds=latency_ms / 1000.0,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_input_tokens,
        cache_write_tokens=usage.cache_creation_input_tokens,
        cost_usd=cost,
    )
    await store.record(
        UsageRecord(
            request_id=request_id,
            agent_id=identity.agent_id,
            tenant_id=identity.tenant_id,
            session_id=identity.session_id,
            saga_id=identity.saga_id,
            model=model,
            provider=provider,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_input_tokens,
            cache_write_tokens=usage.cache_creation_input_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            streaming=True,
            timestamp=datetime.now(UTC),
        )
    )

    await emit_cost_events(
        emitter=emitter,
        store=store,
        identity=identity,
        cost=cost,
        tokens_used=usage.input_tokens + usage.output_tokens,
        model=model,
        agent_budget_limit=agent_budget_limit,
        budget_warning_threshold_pct=budget_warning_threshold_pct,
        sleipnir_publisher=sleipnir_publisher,
    )
