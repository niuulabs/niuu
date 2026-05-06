"""Route handlers for the Bifröst inbound HTTP layer.

Contains all FastAPI route handler functions and quota/access enforcement
helpers. ``create_router()`` returns a configured ``APIRouter`` ready to be
mounted on the main ``FastAPI`` application.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

import bifrost.metrics as _metrics
from bifrost.auth import AgentIdentity
from bifrost.config import AgentPermissions, AuditDetailLevel, BifrostConfig, BudgetGuardrailConfig
from bifrost.domain.models import RequestLog, TokenUsage
from bifrost.domain.routing import RuleRejectError
from bifrost.inbound.chat_completions import (
    OpenAIChatRequest,
    anthropic_response_to_openai,
    anthropic_stream_to_openai,
    openai_error_response,
    openai_request_to_anthropic,
)
from bifrost.inbound.ollama import (
    OllamaChatRequest,
    OllamaGenerateRequest,
    anthropic_response_to_ollama_chat,
    anthropic_response_to_ollama_generate,
    anthropic_stream_to_ollama_chat,
    anthropic_stream_to_ollama_generate,
    ollama_chat_to_anthropic,
    ollama_error_response,
    ollama_generate_to_anthropic,
)
from bifrost.inbound.tracking import (
    _HEADER_QUOTA_WARNING,
    _log_request,
    _stream_with_tracking,
    emit_cost_events,
)
from bifrost.ports.audit import AuditEvent, AuditPort
from bifrost.ports.auth import AuthPort
from bifrost.ports.cache import CachePort
from bifrost.ports.events import BudgetDegradedEvent, CostEventEmitter
from bifrost.ports.rules import RoutingContext
from bifrost.ports.usage_store import UsageRecord, UsageStore
from bifrost.pricing import ModelPricing, calculate_cost
from bifrost.router import ModelRouter, RouterError
from bifrost.translation.models import AnthropicRequest, AnthropicResponse

logger = logging.getLogger(__name__)

# Header injected on responses when the agent's budget is approaching or at the
# warn threshold.  Callers can inspect this header to adjust their behaviour.
_HEADER_BUDGET_WARNING = "X-Bifrost-Budget-Warning"


def _seconds_until_utc_midnight() -> int:
    """Return the number of seconds remaining until midnight UTC today."""
    now = datetime.now(UTC)
    tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    return max(0, int((tomorrow - now).total_seconds()))


# ---------------------------------------------------------------------------
# Cache key computation
# ---------------------------------------------------------------------------


def _compute_cache_key(tenant_id: str, request: AnthropicRequest) -> str:
    """Return a per-tenant SHA-256 cache key for *request*.

    The key covers all generation-affecting fields so that two requests that
    would produce different provider responses always get distinct entries.
    ``stream`` and ``metadata`` are excluded — ``stream`` does not affect
    content and ``metadata`` is not semantically relevant.

    Args:
        tenant_id: Caller's tenant identifier (prevents cross-tenant leakage).
        request:   The inbound Anthropic-format request.

    Returns:
        A lowercase hex SHA-256 digest (64 characters).
    """
    key_data = request.model_dump(exclude={"stream", "metadata"}, exclude_none=True)
    key_data["tenant_id"] = tenant_id
    payload = json.dumps(key_data, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _cache_hit_record(
    request_id: str,
    identity,
    model: str,
    provider: str,
    latency_ms: float,
) -> UsageRecord:
    """Build a zero-cost ``UsageRecord`` for a cache hit."""
    return UsageRecord(
        request_id=request_id,
        agent_id=identity.agent_id,
        tenant_id=identity.tenant_id,
        session_id=identity.session_id,
        saga_id=identity.saga_id,
        model=model,
        provider=provider,
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        reasoning_tokens=0,
        cost_usd=0.0,
        latency_ms=latency_ms,
        streaming=False,
        cache_hit=True,
        timestamp=datetime.now(UTC),
    )


async def _try_cache_hit(
    cache: CachePort,
    key: str,
    identity,
    request_id: str,
    model: str,
    provider: str,
    start: float,
    store: UsageStore,
    response_transform=None,
) -> JSONResponse | None:
    """Return a ``JSONResponse`` on cache hit, or ``None`` on miss.

    Args:
        cache:              Cache adapter.
        key:                SHA-256 cache key.
        identity:           Caller identity (for usage recording).
        request_id:         Correlation ID for the request.
        model:              Resolved model name.
        provider:           Resolved provider name.
        start:              ``time.monotonic()`` captured at request entry.
        store:              Usage store for recording the zero-cost hit.
        response_transform: Optional callable to convert the cached
                            ``AnthropicResponse`` into a response dict.
                            When ``None``, ``response.model_dump()`` is used.

    Returns:
        A ``JSONResponse`` when the cache contained an entry, else ``None``.
    """
    cached = await cache.get(key)
    if cached is None:
        return None
    latency_ms = (time.monotonic() - start) * 1000
    await store.record(_cache_hit_record(request_id, identity, model, provider, latency_ms))
    content = response_transform(cached) if response_transform else cached.model_dump()
    return JSONResponse(content=content)


def _cache_hit_record(
    request_id: str,
    identity,
    model: str,
    provider: str,
    latency_ms: float,
) -> UsageRecord:
    """Build a zero-cost ``UsageRecord`` for a cache hit."""
    return UsageRecord(
        request_id=request_id,
        agent_id=identity.agent_id,
        tenant_id=identity.tenant_id,
        session_id=identity.session_id,
        saga_id=identity.saga_id,
        model=model,
        provider=provider,
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        reasoning_tokens=0,
        cost_usd=0.0,
        latency_ms=latency_ms,
        streaming=False,
        cache_hit=True,
        timestamp=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Quota enforcement
# ---------------------------------------------------------------------------


async def _check_quotas(
    identity: AgentIdentity,
    config: BifrostConfig,
    store: UsageStore,
    agent_perms: AgentPermissions,
) -> tuple[list[str], float | None]:
    """Check quota limits and return warnings plus the agent cost already fetched.

    Args:
        agent_perms: Pre-resolved permissions for the caller (avoids a second
                     ``config.permissions_for_agent()`` lookup per request).

    Returns:
        A 2-tuple of:
        - ``warnings``         — list of human-readable soft-limit warning strings.
        - ``agent_cost_today`` — the agent's total cost today (USD) when a per-agent
                                 budget is configured, else ``None``.  Returned so
                                 callers can pass it to ``_evaluate_guardrails``
                                 without issuing a second store round-trip.

    Raises:
        HTTPException(429): If any hard limit is exceeded.
    """
    warnings: list[str] = []
    agent_cost_today: float | None = None

    tenant_quota = config.quota_for_tenant(identity.tenant_id)
    agent_quota = agent_perms.quota

    # Tenant: tokens per day
    if tenant_quota.max_tokens_per_day > 0:
        used = await store.tokens_today(identity.tenant_id)
        limit = tenant_quota.max_tokens_per_day
        fraction = used / limit
        if fraction >= 1.0:
            _metrics.record_quota_rejection(agent_id=identity.agent_id)
            raise HTTPException(
                status_code=429,
                detail=f"Tenant daily token quota exceeded ({used}/{limit}).",
            )
        if fraction >= tenant_quota.soft_limit_fraction:
            warnings.append(f"tenant_tokens_per_day={used}/{limit} ({fraction:.0%})")

    # Tenant: cost per day
    if tenant_quota.max_cost_per_day > 0.0:
        used_cost = await store.cost_today(identity.tenant_id)
        limit_cost = tenant_quota.max_cost_per_day
        fraction = used_cost / limit_cost
        if fraction >= 1.0:
            _metrics.record_quota_rejection(agent_id=identity.agent_id)
            raise HTTPException(
                status_code=429,
                detail=f"Tenant daily cost quota exceeded (${used_cost:.4f}/${limit_cost:.4f}).",
            )
        if fraction >= tenant_quota.soft_limit_fraction:
            warnings.append(
                f"tenant_cost_per_day=${used_cost:.4f}/${limit_cost:.4f} ({fraction:.0%})"
            )

    # Tenant: requests per hour
    if tenant_quota.max_requests_per_hour > 0:
        used_req = await store.requests_this_hour(identity.tenant_id)
        limit_req = tenant_quota.max_requests_per_hour
        fraction = used_req / limit_req
        if fraction >= 1.0:
            _metrics.record_quota_rejection(agent_id=identity.agent_id)
            raise HTTPException(
                status_code=429,
                detail=f"Tenant hourly request quota exceeded ({used_req}/{limit_req}).",
            )
        if fraction >= tenant_quota.soft_limit_fraction:
            warnings.append(f"tenant_requests_per_hour={used_req}/{limit_req} ({fraction:.0%})")

    # Agent: cost per day (only when a per-agent budget is configured).
    # The fetched cost is returned so _evaluate_guardrails can reuse it
    # without issuing a duplicate store query.
    if agent_quota.max_cost_per_day > 0.0:
        agent_cost_today = await store.agent_cost_today(identity.agent_id)
        limit_cost = agent_quota.max_cost_per_day
        fraction = agent_cost_today / limit_cost
        if fraction >= 1.0:
            _metrics.record_quota_rejection(agent_id=identity.agent_id)
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Agent daily cost quota exceeded (${agent_cost_today:.4f}/${limit_cost:.4f})."
                ),
            )
        if fraction >= agent_quota.soft_limit_fraction:
            warnings.append(
                f"agent_cost_per_day=${agent_cost_today:.4f}/${limit_cost:.4f} ({fraction:.0%})"
            )

    return warnings, agent_cost_today


def _check_model_access(
    identity: AgentIdentity,
    model: str,
    agent_perms: AgentPermissions,
) -> None:
    """Raise 403 if the agent does not have permission to use *model*.

    Rules:
    - An empty ``allowed_models`` list means all models are permitted.
    - ``'*'`` in the list means all models are permitted (unrestricted).
    - Other entries are matched exactly against *model*.

    Args:
        agent_perms: Pre-resolved permissions for the caller.
    """
    if not agent_perms.allowed_models:
        return
    if "*" in agent_perms.allowed_models:
        return
    if model not in agent_perms.allowed_models:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Agent '{identity.agent_id}' is not permitted to use model '{model}'. "
                f"Allowed: {agent_perms.allowed_models}"
            ),
        )


# ---------------------------------------------------------------------------
# Guardrail evaluation (budget + context-window)
# ---------------------------------------------------------------------------


def _resolve_degraded_model(current_model: str, budget_cfg: BudgetGuardrailConfig) -> str:
    """Return the next cheaper model in the degradation chain, or warn_target if chain is empty."""
    chain = budget_cfg.degradation_chain
    if not chain:
        return budget_cfg.warn_target

    try:
        idx = chain.index(current_model)
    except ValueError:
        # Model not in chain — use final (cheapest) entry as safe fallback.
        return chain[-1]

    next_idx = min(idx + 1, len(chain) - 1)
    return chain[next_idx]


async def _evaluate_guardrails(
    request: AnthropicRequest,
    identity: AgentIdentity,
    config: BifrostConfig,
    store: UsageStore,
    agent_perms: AgentPermissions,
    event_emitter: CostEventEmitter | None = None,
    agent_cost_today: float | None = None,
) -> tuple[AnthropicRequest, RoutingContext, str | None]:
    """Evaluate budget and context-window guardrails before routing.

    This runs *after* ``_check_quotas`` so the hard-limit 429 (agent budget
    exhausted) is always raised there first.  This function handles the
    *soft* budget path: routing to a cheaper model, emitting a
    ``bifrost.budget.degraded`` event, and injecting the
    ``X-Bifrost-Budget-Warning`` response header.

    Args:
        request:          Inbound Anthropic-format request (may be mutated).
        identity:         Authenticated agent identity.
        config:           Gateway configuration containing guardrail policies.
        store:            Usage store (queried only when ``agent_cost_today``
                          is not supplied).
        agent_perms:      Pre-resolved permissions for the caller.
        event_emitter:    Optional emitter for budget degradation events.
        agent_cost_today: Agent cost already fetched by ``_check_quotas``
                          (avoids a duplicate store round-trip).  When
                          ``None``, the store is queried directly.

    Returns:
        A 3-tuple of:
        - ``request``      — possibly with model overridden (budget warn_action).
        - ``context``      — ``RoutingContext`` with ``agent_budget_pct`` and
                             ``agent_id`` set so that declarative rules fire.
        - ``budget_warn``  — header value for ``X-Bifrost-Budget-Warning``, or
                             ``None`` when no warning is applicable.

    Raises:
        HTTPException(400): When the request exceeds the context-window message limit.
    """
    budget_warn: str | None = None
    agent_budget_pct: float | None = None

    # ── Context-window guardrail ─────────────────────────────────────────────
    # max_messages is inclusive: a request with exactly max_messages messages
    # is allowed; only strictly more than max_messages is rejected.
    cw_cfg = config.guardrails.context_window
    if cw_cfg is not None and len(request.messages) > cw_cfg.max_messages:
        raise HTTPException(status_code=400, detail=cw_cfg.reason)

    # ── Budget guardrail ─────────────────────────────────────────────────────
    # Note: the >= 100% hard limit is enforced upstream by _check_quotas (which
    # also raises 429 + sets Retry-After).  By the time we reach this point the
    # agent is guaranteed to be below 100%.  We only handle the warn threshold
    # here (route to a cheaper model, inject X-Bifrost-Budget-Warning).
    budget_cfg = config.guardrails.budget
    agent_quota = agent_perms.quota

    if budget_cfg is not None and agent_quota.max_cost_per_day > 0.0:
        if agent_cost_today is None:
            agent_cost_today = await store.agent_cost_today(identity.agent_id)
        agent_cost = agent_cost_today
        limit = agent_quota.max_cost_per_day
        pct_consumed = (agent_cost / limit) * 100.0
        agent_budget_pct = pct_consumed

        if pct_consumed >= budget_cfg.warn_at_pct:
            original_model = request.model
            degraded_model = _resolve_degraded_model(original_model, budget_cfg)
            if budget_cfg.warn_action == "route_to":
                request = request.model_copy(update={"model": degraded_model})
            budget_warn = (
                f"budget_consumed={pct_consumed:.1f}% "
                f"(${agent_cost:.4f}/${limit:.4f}); "
                f"routed_to={degraded_model}"
            )
            logger.info(
                "Budget degradation: agent=%s pct=%.1f%% model %s -> %s (spent=$%.4f limit=$%.4f)",
                identity.agent_id,
                pct_consumed,
                original_model,
                degraded_model,
                agent_cost,
                limit,
            )
            if event_emitter is not None:
                await event_emitter.emit_budget_degraded(
                    BudgetDegradedEvent(
                        agent_id=identity.agent_id,
                        session_id=identity.session_id,
                        original_model=original_model,
                        degraded_model=degraded_model,
                        budget_pct_consumed=pct_consumed,
                        daily_limit_usd=limit,
                        spent_usd=agent_cost,
                    )
                )

    context = RoutingContext(
        agent_budget_pct=agent_budget_pct,
        agent_id=identity.agent_id,
    )
    return request, context, budget_warn


# ---------------------------------------------------------------------------
# Model enumeration helper (shared between /v1/models and /api/tags)
# ---------------------------------------------------------------------------


def _enumerate_models(config: BifrostConfig) -> list[tuple[str, str]]:
    """Return ``(model_id, provider_name)`` pairs for all models and aliases.

    Iterates configured providers first, then aliases, using a seen-set to
    deduplicate entries that appear under multiple names.

    Args:
        config: The gateway configuration.

    Returns:
        An ordered list of ``(model_id, provider_name)`` 2-tuples.
    """
    result: list[tuple[str, str]] = []
    seen: set[str] = set()

    for provider_name, provider_cfg in config.providers.items():
        for model_id in provider_cfg.models:
            if model_id in seen:
                continue
            seen.add(model_id)
            result.append((model_id, provider_name))

    for alias, canonical in config.aliases.items():
        if alias in seen:
            continue
        seen.add(alias)
        provider_name = config.provider_for_model(canonical) or "unknown"
        result.append((alias, provider_name))

    return result


# ---------------------------------------------------------------------------
# Audit event builder
# ---------------------------------------------------------------------------


def _build_audit_event(
    *,
    config: BifrostConfig,
    request_id: str,
    identity: AgentIdentity,
    model: str,
    provider: str,
    outcome: str,
    status_code: int,
    latency_ms: float,
    tokens_input: int = 0,
    tokens_output: int = 0,
    cost_usd: float = 0.0,
    cache_hit: bool = False,
    error_message: str = "",
    rule_name: str = "",
    rule_action: str = "",
    tags: dict | None = None,
    request: AnthropicRequest | None = None,
    response: AnthropicResponse | None = None,
) -> AuditEvent:
    """Build an ``AuditEvent`` populated according to the configured detail level.

    At ``minimal`` level only the core fields (tokens, cost, latency) are set.
    At ``standard`` level provider, session/saga IDs, outcome, and rule metadata
    are also populated.  At ``full`` level prompt and response content are
    included as well.
    """
    level = config.audit.level

    # Minimal: always populated.
    event = AuditEvent(
        request_id=request_id,
        agent_id=identity.agent_id,
        tenant_id=identity.tenant_id,
        model=model,
        timestamp=datetime.now(UTC),
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        cache_hit=cache_hit,
    )

    if level == AuditDetailLevel.MINIMAL:
        return event

    # Standard: add provider, session metadata, outcome, status code, rules.
    event.provider = provider
    event.session_id = identity.session_id
    event.saga_id = identity.saga_id
    event.outcome = outcome
    event.status_code = status_code
    event.rule_name = rule_name
    event.rule_action = rule_action
    event.tags = tags or {}
    event.error_message = error_message

    if level == AuditDetailLevel.STANDARD:
        return event

    # Full: also add prompt/response content.
    if request is not None:
        event.prompt_content = json.dumps([m.model_dump() for m in request.messages], default=str)
    if response is not None:
        event.response_content = json.dumps(response.model_dump(), default=str)

    return event


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_router(
    config: BifrostConfig,
    router: ModelRouter,
    store: UsageStore,
    pricing_overrides: dict[str, ModelPricing],
    auth_adapter: AuthPort,
    event_emitter: CostEventEmitter,
    cache: CachePort | None = None,
    audit: AuditPort | None = None,
) -> APIRouter:
    """Build and return a configured ``APIRouter`` with all Bifröst routes.

    Args:
        config:           Gateway configuration.
        router:           Routing layer that dispatches to LLM providers.
        store:            Usage store for recording and querying usage records.
        pricing_overrides: Per-model pricing overrides from config.
        auth_adapter:     Authentication adapter (open / pat / mesh).
        cache:            Optional response cache (disabled by default).

    Returns:
        A ``fastapi.APIRouter`` with all routes registered.
    """
    _cache: CachePort
    if cache is None:
        from bifrost.adapters.cache.disabled import DisabledCache

        _cache = DisabledCache()
    else:
        _cache = cache

    _audit: AuditPort
    if audit is None:
        from bifrost.adapters.audit.null import NullAuditAdapter

        _audit = NullAuditAdapter()
    else:
        _audit = audit

    api_router = APIRouter()

    async def _emit_events(
        identity: AgentIdentity,
        cost: float,
        tokens: int,
        model: str,
        budget_limit: float,
        raw_request: Request | None = None,
    ) -> None:
        publisher = (
            getattr(raw_request.app.state, "sleipnir_publisher", None)
            if raw_request is not None
            else None
        )
        await emit_cost_events(
            emitter=event_emitter,
            store=store,
            identity=identity,
            cost=cost,
            tokens_used=tokens,
            model=model,
            agent_budget_limit=budget_limit,
            budget_warning_threshold_pct=config.events.budget_warning_threshold_pct,
            sleipnir_publisher=publisher,
        )

    _audit_tasks: set[asyncio.Task] = set()

    def _schedule_audit(event: AuditEvent) -> None:
        """Schedule audit logging as a fire-and-forget task."""
        task = asyncio.create_task(_audit.log(event))
        _audit_tasks.add(task)
        task.add_done_callback(_audit_tasks.discard)

    @api_router.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @api_router.get("/v1/cache/stats")
    async def cache_stats(raw_request: Request) -> dict:
        """Return aggregate cache statistics.

        Returns hit/miss counts, hit rate, and saved token counts since the
        process started.  Statistics are per-instance and reset on restart.
        """
        auth_adapter.extract(raw_request)
        s = _cache.stats()
        return {
            "hits": s.hits,
            "misses": s.misses,
            "hit_rate": round(s.hit_rate, 4),
            "saved_tokens": s.saved_tokens,
            "saved_input_tokens": s.saved_input_tokens,
            "saved_output_tokens": s.saved_output_tokens,
            "entries": s.entries,
        }

    @api_router.post("/admin/reload-keys")
    async def admin_reload_keys(raw_request: Request) -> dict:
        """Reload provider API keys from their source without restarting.

        Triggers the same key-rotation logic as a SIGHUP signal.  Useful
        in environments where sending UNIX signals is inconvenient (e.g.
        containers without a shell, or Windows).

        Authentication is enforced according to the configured auth mode —
        in PAT or mesh mode a valid credential is required to call this
        endpoint, preventing unauthenticated disruption of the adapter cache.

        After this call, all cached provider adapters are discarded and
        will be rebuilt with the freshly loaded keys on the next request.

        Returns:
            ``{"status": "ok"}``
        """
        auth_adapter.extract(raw_request)
        router.reload_keys()
        return {"status": "ok"}

    @api_router.get("/v1/models")
    async def list_models() -> dict:
        """List models available across all configured providers.

        Returns an OpenAI-compatible list response including both canonical
        model IDs and any configured aliases.
        """
        return {
            "object": "list",
            "data": [
                {
                    "id": model_id,
                    "object": "model",
                    "owned_by": provider_name,
                    "display_name": config.aliases.get(model_id, model_id),
                }
                for model_id, provider_name in _enumerate_models(config)
            ],
        }

    @api_router.post("/v1/messages", response_model=None)
    async def messages(raw_request: Request) -> JSONResponse | StreamingResponse:
        """Anthropic-compatible Messages endpoint.

        Accepts an Anthropic Messages API request body, routes it to the
        configured provider, and returns the response in Anthropic format.
        Token usage is tracked per-request and attributed to the caller.
        """
        # --- Authentication ---
        identity = auth_adapter.extract(raw_request)

        try:
            body = await raw_request.json()
            request = AnthropicRequest.model_validate(body)
        except Exception as exc:
            logger.debug("Request validation failed: %s", exc)
            raise HTTPException(status_code=422, detail="Invalid request body.") from exc

        # Resolve permissions once — used by both access control and quota checks.
        agent_perms = config.permissions_for_agent(identity.agent_id)

        # --- Model access control ---
        _check_model_access(identity, request.model, agent_perms)

        # --- Quota check (before routing) ---
        warnings, agent_cost_today = await _check_quotas(identity, config, store, agent_perms)

        # --- Guardrail evaluation (budget + context-window) ---
        # Pass agent_cost_today to avoid a duplicate store query.
        request, routing_ctx, budget_warn = await _evaluate_guardrails(
            request, identity, config, store, agent_perms, event_emitter, agent_cost_today
        )

        request_id = str(raw_request.state.correlation_id)
        start = time.monotonic()

        provider = config.provider_for_model(request.model) or ""

        agent_budget_limit = agent_perms.quota.max_cost_per_day

        try:
            if request.stream:
                stream_resp = StreamingResponse(
                    _stream_with_tracking(
                        router.stream(request, routing_ctx),
                        request.model,
                        start,
                        identity,
                        store,
                        pricing_overrides,
                        request_id,
                        provider=provider,
                        emitter=event_emitter,
                        agent_budget_limit=agent_budget_limit,
                        budget_warning_threshold_pct=config.events.budget_warning_threshold_pct,
                        sleipnir_publisher=getattr(
                            raw_request.app.state, "sleipnir_publisher", None
                        ),
                    ),
                    media_type="text/event-stream",
                    headers={
                        "cache-control": "no-cache",
                        "x-accel-buffering": "no",
                        "connection": "keep-alive",
                    },
                )
                if warnings:
                    stream_resp.headers[_HEADER_QUOTA_WARNING] = "; ".join(warnings)
                if budget_warn:
                    stream_resp.headers[_HEADER_BUDGET_WARNING] = budget_warn
                return stream_resp

            # --- Exact response cache (non-streaming only) ---
            cache_key = _compute_cache_key(identity.tenant_id, request)
            hit_resp = await _try_cache_hit(
                _cache,
                cache_key,
                identity,
                request_id,
                request.model,
                provider,
                start,
                store,
            )
            if hit_resp is not None:
                _schedule_audit(
                    _build_audit_event(
                        config=config,
                        request_id=request_id,
                        identity=identity,
                        model=request.model,
                        provider=provider,
                        outcome="cache_hit",
                        status_code=200,
                        latency_ms=(time.monotonic() - start) * 1000,
                        cache_hit=True,
                        request=request,
                    )
                )
                _metrics.record_cache_hit(provider=provider, model=request.model)
                if warnings:
                    hit_resp.headers[_HEADER_QUOTA_WARNING] = "; ".join(warnings)
                return hit_resp

            _metrics.record_cache_miss(provider=provider, model=request.model)
            response = await router.complete(request, routing_ctx)
            latency_ms = (time.monotonic() - start) * 1000
            data = response.model_dump()
            raw_usage = data.get("usage", {})
            usage = TokenUsage(
                input_tokens=raw_usage.get("input_tokens", 0),
                output_tokens=raw_usage.get("output_tokens", 0),
                cache_creation_input_tokens=raw_usage.get("cache_creation_input_tokens", 0),
                cache_read_input_tokens=raw_usage.get("cache_read_input_tokens", 0),
                reasoning_tokens=raw_usage.get("reasoning_tokens", 0),
            )
            _log_request(
                RequestLog(
                    timestamp=datetime.now(UTC),
                    model=request.model,
                    usage=usage,
                    latency_ms=latency_ms,
                    stream=False,
                )
            )

            cost = calculate_cost(request.model, usage, pricing_overrides)
            _metrics.record_request(
                provider=provider,
                model=request.model,
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
                    model=request.model,
                    provider=provider,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read_tokens=usage.cache_read_input_tokens,
                    cache_write_tokens=usage.cache_creation_input_tokens,
                    reasoning_tokens=usage.reasoning_tokens,
                    cost_usd=cost,
                    latency_ms=latency_ms,
                    streaming=False,
                    timestamp=datetime.now(UTC),
                )
            )
            _schedule_audit(
                _build_audit_event(
                    config=config,
                    request_id=request_id,
                    identity=identity,
                    model=request.model,
                    provider=provider,
                    outcome="success",
                    status_code=200,
                    latency_ms=latency_ms,
                    tokens_input=usage.input_tokens,
                    tokens_output=usage.output_tokens,
                    cost_usd=cost,
                    request=request,
                    response=response,
                )
            )
            await _emit_events(
                identity,
                cost,
                usage.input_tokens + usage.output_tokens,
                request.model,
                agent_budget_limit,
                raw_request=raw_request,
            )
            await _cache.set(cache_key, response, config.cache.default_ttl)

            json_resp = JSONResponse(content=data)
            if warnings:
                json_resp.headers[_HEADER_QUOTA_WARNING] = "; ".join(warnings)
            if budget_warn:
                json_resp.headers[_HEADER_BUDGET_WARNING] = budget_warn
            return json_resp

        except RuleRejectError as exc:
            _schedule_audit(
                _build_audit_event(
                    config=config,
                    request_id=request_id,
                    identity=identity,
                    model=request.model,
                    provider=provider,
                    outcome="rejected",
                    status_code=400,
                    latency_ms=(time.monotonic() - start) * 1000,
                    error_message=exc.message,
                    request=request,
                )
            )
            _metrics.record_request(
                provider=provider,
                model=request.model,
                status="400",
                duration_seconds=(time.monotonic() - start),
            )
            raise HTTPException(status_code=400, detail=exc.message) from exc
        except RouterError as exc:
            _metrics.record_request(
                provider=provider,
                model=request.model,
                status="502",
                duration_seconds=(time.monotonic() - start),
            )
            logger.error("Routing failed: %s", exc)
            _schedule_audit(
                _build_audit_event(
                    config=config,
                    request_id=request_id,
                    identity=identity,
                    model=request.model,
                    provider=provider,
                    outcome="error",
                    status_code=502,
                    latency_ms=(time.monotonic() - start) * 1000,
                    error_message=str(exc),
                    request=request,
                )
            )
            raise HTTPException(status_code=502, detail="Upstream routing failed.") from exc

    @api_router.get("/v1/usage")
    async def usage_endpoint(
        agent_id: str | None = None,
        tenant_id: str | None = None,
        model: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 1000,
        granularity: str | None = None,
    ) -> dict:
        """Return aggregated usage statistics with optional filters.

        Query parameters:
            agent_id:    Filter by agent identifier.
            tenant_id:   Filter by tenant identifier.
            model:       Filter by model name.
            since:       ISO-8601 datetime (inclusive lower bound).
            until:       ISO-8601 datetime (inclusive upper bound).
            limit:       Maximum number of raw records returned (default 1000).
            granularity: Time-series bucket size — 'hour' (default) or 'day'.
                         When provided, the response includes a ``timeseries``
                         array with per-bucket aggregates.

        Returns a summary (totals + per-model/provider breakdown), an optional
        time-series breakdown, and the raw record list.
        """
        since_dt: datetime | None = None
        until_dt: datetime | None = None

        if since is not None:
            try:
                _dt = datetime.fromisoformat(since)
                since_dt = _dt.replace(tzinfo=UTC) if _dt.tzinfo is None else _dt.astimezone(UTC)
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid 'since' datetime: {exc}",
                ) from exc

        if until is not None:
            try:
                _dt = datetime.fromisoformat(until)
                until_dt = _dt.replace(tzinfo=UTC) if _dt.tzinfo is None else _dt.astimezone(UTC)
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid 'until' datetime: {exc}",
                ) from exc

        if granularity is not None and granularity not in ("hour", "day"):
            raise HTTPException(
                status_code=422,
                detail="'granularity' must be 'hour' or 'day'.",
            )

        summary = await store.summarise(
            agent_id=agent_id,
            tenant_id=tenant_id,
            model=model,
            since=since_dt,
            until=until_dt,
        )
        records = await store.query(
            agent_id=agent_id,
            tenant_id=tenant_id,
            model=model,
            since=since_dt,
            until=until_dt,
            limit=limit,
        )

        response_body: dict = {
            "summary": {
                "total_requests": summary.total_requests,
                "total_input_tokens": summary.total_input_tokens,
                "total_output_tokens": summary.total_output_tokens,
                "total_cost_usd": round(summary.total_cost_usd, 6),
                "by_model": summary.by_model,
                "by_provider": summary.by_provider,
            },
            "records": [
                {
                    "request_id": r.request_id,
                    "agent_id": r.agent_id,
                    "tenant_id": r.tenant_id,
                    "session_id": r.session_id,
                    "saga_id": r.saga_id,
                    "model": r.model,
                    "provider": r.provider,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "cache_read_tokens": r.cache_read_tokens,
                    "cache_write_tokens": r.cache_write_tokens,
                    "reasoning_tokens": r.reasoning_tokens,
                    "cost_usd": round(r.cost_usd, 6),
                    "latency_ms": round(r.latency_ms, 2),
                    "streaming": r.streaming,
                    "timestamp": r.timestamp.isoformat(),
                }
                for r in records
            ],
        }

        if granularity is not None:
            ts_entries = await store.time_series(
                granularity=granularity,
                agent_id=agent_id,
                tenant_id=tenant_id,
                model=model,
                since=since_dt,
                until=until_dt,
            )
            response_body["timeseries"] = [
                {
                    "bucket": e.bucket,
                    "requests": e.requests,
                    "input_tokens": e.input_tokens,
                    "output_tokens": e.output_tokens,
                    "cost_usd": round(e.cost_usd, 6),
                }
                for e in ts_entries
            ]

        return response_body

    @api_router.post("/v1/chat/completions", response_model=None)
    async def chat_completions(raw_request: Request) -> JSONResponse | StreamingResponse:
        """OpenAI Chat Completions-compatible endpoint.

        Accepts an OpenAI Chat Completions request, translates it to the
        internal Anthropic canonical format, routes via the shared ModelRouter,
        then translates the response back to OpenAI format.  The full streaming
        path is supported; token usage is extracted and logged on each request.
        """
        # --- Authentication ---
        identity = auth_adapter.extract(raw_request)

        try:
            body = await raw_request.json()
            oai_request = OpenAIChatRequest.model_validate(body)
        except Exception as exc:
            logger.warning("Invalid OpenAI chat completion request: %s", exc)
            return openai_error_response(422, "Invalid request body.", "invalid_request_error")

        request = openai_request_to_anthropic(oai_request)

        # --- Model access control ---
        agent_perms = config.permissions_for_agent(identity.agent_id)
        try:
            _check_model_access(identity, request.model, agent_perms)
        except HTTPException as exc:
            return openai_error_response(exc.status_code, exc.detail, "invalid_request_error")

        # --- Quota check (before routing) ---
        try:
            warnings, agent_cost_today = await _check_quotas(identity, config, store, agent_perms)
        except HTTPException as exc:
            return openai_error_response(exc.status_code, exc.detail, "rate_limit_error")

        # --- Guardrail evaluation (budget + context-window) ---
        # Pass agent_cost_today to avoid a duplicate store query.
        try:
            request, routing_ctx, budget_warn = await _evaluate_guardrails(
                request, identity, config, store, agent_perms, event_emitter, agent_cost_today
            )
        except HTTPException as exc:
            return openai_error_response(exc.status_code, exc.detail, "rate_limit_error")

        request_id = str(raw_request.state.correlation_id)
        start = time.monotonic()
        provider = config.provider_for_model(request.model) or ""
        agent_budget_limit = agent_perms.quota.max_cost_per_day

        try:
            if request.stream:
                message_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
                stream_resp = StreamingResponse(
                    anthropic_stream_to_openai(
                        _stream_with_tracking(
                            router.stream(request, routing_ctx),
                            request.model,
                            start,
                            identity,
                            store,
                            pricing_overrides,
                            request_id,
                            provider=provider,
                            emitter=event_emitter,
                            agent_budget_limit=agent_budget_limit,
                            budget_warning_threshold_pct=config.events.budget_warning_threshold_pct,
                            sleipnir_publisher=getattr(
                                raw_request.app.state, "sleipnir_publisher", None
                            ),
                        ),
                        message_id=message_id,
                        model=request.model,
                    ),
                    media_type="text/event-stream",
                    headers={
                        "cache-control": "no-cache",
                        "x-accel-buffering": "no",
                        "connection": "keep-alive",
                    },
                )
                if warnings:
                    stream_resp.headers[_HEADER_QUOTA_WARNING] = "; ".join(warnings)
                if budget_warn:
                    stream_resp.headers[_HEADER_BUDGET_WARNING] = budget_warn
                return stream_resp

            # --- Exact response cache (non-streaming only) ---
            cache_key = _compute_cache_key(identity.tenant_id, request)
            hit_resp = await _try_cache_hit(
                _cache,
                cache_key,
                identity,
                request_id,
                request.model,
                provider,
                start,
                store,
                response_transform=anthropic_response_to_openai,
            )
            if hit_resp is not None:
                _schedule_audit(
                    _build_audit_event(
                        config=config,
                        request_id=request_id,
                        identity=identity,
                        model=request.model,
                        provider=provider,
                        outcome="cache_hit",
                        status_code=200,
                        latency_ms=(time.monotonic() - start) * 1000,
                        cache_hit=True,
                        request=request,
                    )
                )
                _metrics.record_cache_hit(provider=provider, model=request.model)
                if warnings:
                    hit_resp.headers[_HEADER_QUOTA_WARNING] = "; ".join(warnings)
                return hit_resp

            _metrics.record_cache_miss(provider=provider, model=request.model)
            response = await router.complete(request, routing_ctx)
            latency_ms = (time.monotonic() - start) * 1000
            usage = TokenUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
                reasoning_tokens=0,
            )
            _log_request(
                RequestLog(
                    timestamp=datetime.now(UTC),
                    model=request.model,
                    usage=usage,
                    latency_ms=latency_ms,
                    stream=False,
                )
            )

            cost = calculate_cost(request.model, usage, pricing_overrides)
            _metrics.record_request(
                provider=provider,
                model=request.model,
                status="200",
                duration_seconds=latency_ms / 1000.0,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=0,
                cache_write_tokens=0,
                cost_usd=cost,
            )
            await store.record(
                UsageRecord(
                    request_id=request_id,
                    agent_id=identity.agent_id,
                    tenant_id=identity.tenant_id,
                    session_id=identity.session_id,
                    saga_id=identity.saga_id,
                    model=request.model,
                    provider=provider,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                    reasoning_tokens=0,
                    cost_usd=cost,
                    latency_ms=latency_ms,
                    streaming=False,
                    timestamp=datetime.now(UTC),
                )
            )
            _schedule_audit(
                _build_audit_event(
                    config=config,
                    request_id=request_id,
                    identity=identity,
                    model=request.model,
                    provider=provider,
                    outcome="success",
                    status_code=200,
                    latency_ms=latency_ms,
                    tokens_input=usage.input_tokens,
                    tokens_output=usage.output_tokens,
                    cost_usd=cost,
                    request=request,
                    response=response,
                )
            )
            await _emit_events(
                identity,
                cost,
                usage.input_tokens + usage.output_tokens,
                request.model,
                agent_budget_limit,
                raw_request=raw_request,
            )
            await _cache.set(cache_key, response, config.cache.default_ttl)

            json_resp = JSONResponse(content=anthropic_response_to_openai(response))
            if warnings:
                json_resp.headers[_HEADER_QUOTA_WARNING] = "; ".join(warnings)
            if budget_warn:
                json_resp.headers[_HEADER_BUDGET_WARNING] = budget_warn
            return json_resp

        except RuleRejectError as exc:
            _schedule_audit(
                _build_audit_event(
                    config=config,
                    request_id=request_id,
                    identity=identity,
                    model=request.model,
                    provider=provider,
                    outcome="rejected",
                    status_code=400,
                    latency_ms=(time.monotonic() - start) * 1000,
                    error_message=exc.message,
                    request=request,
                )
            )
            _metrics.record_request(
                provider=provider,
                model=request.model,
                status="400",
                duration_seconds=(time.monotonic() - start),
            )
            return openai_error_response(400, exc.message, "invalid_request_error")
        except RouterError as exc:
            _metrics.record_request(
                provider=provider,
                model=request.model,
                status="502",
                duration_seconds=(time.monotonic() - start),
            )
            logger.error("Routing failed: %s", exc)
            _schedule_audit(
                _build_audit_event(
                    config=config,
                    request_id=request_id,
                    identity=identity,
                    model=request.model,
                    provider=provider,
                    outcome="error",
                    status_code=502,
                    latency_ms=(time.monotonic() - start) * 1000,
                    error_message=str(exc),
                    request=request,
                )
            )
            return openai_error_response(502, "Upstream routing failed.", "server_error")

    # -----------------------------------------------------------------------
    # Ollama-compatible endpoints
    # -----------------------------------------------------------------------

    async def _handle_ollama_request(
        raw_request: Request,
        parse_fn: Callable,
        translate_fn: Callable,
        stream_translate_fn: Callable,
        response_translate_fn: Callable,
    ) -> JSONResponse | StreamingResponse:
        """Shared handler for Ollama /api/generate and /api/chat.

        Handles auth, request parsing + translation, quota enforcement,
        routing, usage tracking, and response translation.  The four
        callables let callers plug in endpoint-specific logic without
        duplicating the common plumbing.

        Args:
            raw_request: The incoming FastAPI request.
            parse_fn: ``body_dict → OllamaXxxRequest`` — validates the raw body.
            translate_fn: ``OllamaXxxRequest → AnthropicRequest`` — converts to
                the internal canonical format.
            stream_translate_fn: ``(source, *, model, start) → AsyncIterator[str]``
                — translates Anthropic SSE to Ollama NDJSON for streaming responses.
            response_translate_fn: ``(response, *, created_at, total_duration_ns)
                → dict`` — translates a non-streaming Anthropic response to Ollama
                format.
        """
        identity = auth_adapter.extract(raw_request)

        try:
            body = await raw_request.json()
            ollama_req = parse_fn(body)
        except Exception as exc:
            logger.warning("Ollama request parse error: %s", exc)
            return ollama_error_response(422, "Invalid request body")

        request = translate_fn(ollama_req)

        agent_perms = config.permissions_for_agent(identity.agent_id)
        try:
            _check_model_access(identity, request.model, agent_perms)
        except HTTPException as exc:
            return ollama_error_response(exc.status_code, exc.detail)

        try:
            warnings, agent_cost_today = await _check_quotas(identity, config, store, agent_perms)
        except HTTPException as exc:
            return ollama_error_response(exc.status_code, exc.detail)

        # --- Guardrail evaluation (budget + context-window) ---
        # Pass agent_cost_today to avoid a duplicate store query.
        try:
            request, routing_ctx, budget_warn = await _evaluate_guardrails(
                request, identity, config, store, agent_perms, event_emitter, agent_cost_today
            )
        except HTTPException as exc:
            return ollama_error_response(exc.status_code, exc.detail)

        request_id = str(raw_request.state.correlation_id)
        start = time.monotonic()
        provider = config.provider_for_model(request.model) or ""
        agent_budget_limit = agent_perms.quota.max_cost_per_day

        try:
            if request.stream:
                stream_resp = StreamingResponse(
                    stream_translate_fn(
                        _stream_with_tracking(
                            router.stream(request, routing_ctx),
                            request.model,
                            start,
                            identity,
                            store,
                            pricing_overrides,
                            request_id,
                            provider=provider,
                            emitter=event_emitter,
                            agent_budget_limit=agent_budget_limit,
                            budget_warning_threshold_pct=config.events.budget_warning_threshold_pct,
                            sleipnir_publisher=getattr(
                                raw_request.app.state, "sleipnir_publisher", None
                            ),
                        ),
                        model=request.model,
                        start=start,
                    ),
                    media_type="application/x-ndjson",
                )
                if warnings:
                    stream_resp.headers[_HEADER_QUOTA_WARNING] = "; ".join(warnings)
                if budget_warn:
                    stream_resp.headers[_HEADER_BUDGET_WARNING] = budget_warn
                return stream_resp

            # --- Exact response cache (non-streaming only) ---
            cache_key = _compute_cache_key(identity.tenant_id, request)
            hit_resp = await _try_cache_hit(
                _cache,
                cache_key,
                identity,
                request_id,
                request.model,
                provider,
                start,
                store,
                response_transform=lambda cached: response_translate_fn(
                    cached,
                    created_at=datetime.now(UTC).isoformat(),
                    total_duration_ns=int((time.monotonic() - start) * 1e9),
                ),
            )
            if hit_resp is not None:
                _schedule_audit(
                    _build_audit_event(
                        config=config,
                        request_id=request_id,
                        identity=identity,
                        model=request.model,
                        provider=provider,
                        outcome="cache_hit",
                        status_code=200,
                        latency_ms=(time.monotonic() - start) * 1000,
                        cache_hit=True,
                        request=request,
                    )
                )
                _metrics.record_cache_hit(provider=provider, model=request.model)
                if warnings:
                    hit_resp.headers[_HEADER_QUOTA_WARNING] = "; ".join(warnings)
                return hit_resp

            _metrics.record_cache_miss(provider=provider, model=request.model)
            response = await router.complete(request, routing_ctx)
            latency_ms = (time.monotonic() - start) * 1000
            usage = TokenUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
                reasoning_tokens=0,
            )
            _log_request(
                RequestLog(
                    timestamp=datetime.now(UTC),
                    model=request.model,
                    usage=usage,
                    latency_ms=latency_ms,
                    stream=False,
                )
            )

            cost = calculate_cost(request.model, usage, pricing_overrides)
            _metrics.record_request(
                provider=provider,
                model=request.model,
                status="200",
                duration_seconds=latency_ms / 1000.0,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=0,
                cache_write_tokens=0,
                cost_usd=cost,
            )
            await store.record(
                UsageRecord(
                    request_id=request_id,
                    agent_id=identity.agent_id,
                    tenant_id=identity.tenant_id,
                    session_id=identity.session_id,
                    saga_id=identity.saga_id,
                    model=request.model,
                    provider=provider,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                    reasoning_tokens=0,
                    cost_usd=cost,
                    latency_ms=latency_ms,
                    streaming=False,
                    timestamp=datetime.now(UTC),
                )
            )
            _schedule_audit(
                _build_audit_event(
                    config=config,
                    request_id=request_id,
                    identity=identity,
                    model=request.model,
                    provider=provider,
                    outcome="success",
                    status_code=200,
                    latency_ms=latency_ms,
                    tokens_input=usage.input_tokens,
                    tokens_output=usage.output_tokens,
                    cost_usd=cost,
                    request=request,
                    response=response,
                )
            )
            await _emit_events(
                identity,
                cost,
                usage.input_tokens + usage.output_tokens,
                request.model,
                agent_budget_limit,
                raw_request=raw_request,
            )
            await _cache.set(cache_key, response, config.cache.default_ttl)

            created_at = datetime.now(UTC).isoformat()
            total_ns = int(latency_ms * 1e6)
            json_resp = JSONResponse(
                content=response_translate_fn(
                    response, created_at=created_at, total_duration_ns=total_ns
                )
            )
            if warnings:
                json_resp.headers[_HEADER_QUOTA_WARNING] = "; ".join(warnings)
            if budget_warn:
                json_resp.headers[_HEADER_BUDGET_WARNING] = budget_warn
            return json_resp

        except RuleRejectError as exc:
            _schedule_audit(
                _build_audit_event(
                    config=config,
                    request_id=request_id,
                    identity=identity,
                    model=request.model,
                    provider=provider,
                    outcome="rejected",
                    status_code=400,
                    latency_ms=(time.monotonic() - start) * 1000,
                    error_message=exc.message,
                    request=request,
                )
            )
            return ollama_error_response(400, exc.message)
        except RouterError as exc:
            _metrics.record_request(
                provider=provider,
                model=request.model,
                status="502",
                duration_seconds=(time.monotonic() - start),
            )
            logger.error("Routing failed: %s", exc)
            _schedule_audit(
                _build_audit_event(
                    config=config,
                    request_id=request_id,
                    identity=identity,
                    model=request.model,
                    provider=provider,
                    outcome="error",
                    status_code=502,
                    latency_ms=(time.monotonic() - start) * 1000,
                    error_message=str(exc),
                    request=request,
                )
            )
            return ollama_error_response(502, "Upstream provider error")

    @api_router.get("/api/tags")
    async def ollama_tags() -> dict:
        """List available models in Ollama /api/tags format.

        Maps the internal model registry to the Ollama model list shape so
        that tools like Open WebUI can discover available models automatically.
        """
        return {
            "models": [
                {
                    "name": model_id,
                    "model": model_id,
                    "modified_at": "2024-01-01T00:00:00Z",
                    "size": 0,
                    "digest": "",
                    "details": {
                        "format": "unknown",
                        "family": provider_name,
                        "families": None,
                        "parameter_size": "unknown",
                        "quantization_level": "unknown",
                    },
                }
                for model_id, provider_name in _enumerate_models(config)
            ]
        }

    @api_router.post("/api/generate", response_model=None)
    async def ollama_generate(raw_request: Request) -> JSONResponse | StreamingResponse:
        """Ollama /api/generate endpoint (prompt-based completion).

        Accepts a native Ollama generate request, translates it to the
        internal Anthropic canonical format, routes via the shared
        ModelRouter, and returns the response in Ollama format.
        Streaming uses newline-delimited JSON (NDJSON), not SSE.
        """
        return await _handle_ollama_request(
            raw_request,
            OllamaGenerateRequest.model_validate,
            ollama_generate_to_anthropic,
            anthropic_stream_to_ollama_generate,
            anthropic_response_to_ollama_generate,
        )

    @api_router.post("/api/chat", response_model=None)
    async def ollama_chat(raw_request: Request) -> JSONResponse | StreamingResponse:
        """Ollama /api/chat endpoint (messages-based chat).

        Accepts a native Ollama chat request, translates it to the internal
        Anthropic canonical format, routes via the shared ModelRouter, and
        returns the response in Ollama format.
        Streaming uses newline-delimited JSON (NDJSON), not SSE.
        """
        return await _handle_ollama_request(
            raw_request,
            OllamaChatRequest.model_validate,
            ollama_chat_to_anthropic,
            anthropic_stream_to_ollama_chat,
            anthropic_response_to_ollama_chat,
        )

    return api_router
