"""ModelRouter — resolve model names, aliases, and route between providers.

The router maps a model name (possibly an alias) to the correct provider adapter
and selects/orders candidates according to the configured RoutingStrategy.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from bifrost.config import BifrostConfig, CacheMode, ProviderConfig, RoutingStrategy
from bifrost.domain.routing import UnsupportedFeatureError, apply_rules
from bifrost.ports.key_vault import KeyVaultPort
from bifrost.ports.native import NativePassthroughPort, NativeProtocol
from bifrost.ports.provider import ProviderError, ProviderPort
from bifrost.ports.rules import RoutingContext, RuleEnginePort
from bifrost.ports.selection import SelectionPort
from bifrost.translation.models import AnthropicRequest, AnthropicResponse
from niuu.observability import get_observability
from niuu.utils import import_class

logger = logging.getLogger(__name__)


class _UsageLike(Protocol):
    """Structural type for the two usage shapes callers pass in.

    ``bifrost.translation.models.UsageInfo`` (non-streaming) and
    ``bifrost.domain.models.TokenUsage`` (streaming, accumulated from SSE
    deltas) both carry these two fields but aren't related types.
    """

    input_tokens: int
    output_tokens: int


def record_genai_span_attributes(
    *,
    requested_model: str,
    provider: str,
    failover_attempts: int,
    cache_hit: bool,
    response_model: str = "",
    usage: _UsageLike | None = None,
) -> None:
    """Enrich the active server span with GenAI semantic-convention attributes.

    Bifröst does not open its own span per inference: once the composition
    root instruments the app (``niuu.observability.instrument_fastapi_app``),
    every inbound completion request already has a FastAPI server span open
    for its whole duration — opening a second, nested span here would just
    duplicate it. This attaches the GenAI attributes (requested model,
    routed model, chosen provider, failover attempts, cache hit, token usage)
    to that already-active span instead. A no-op when observability is
    disabled.
    """
    attributes: dict[str, object] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": requested_model,
        "bifrost.failover_attempts": failover_attempts,
        "bifrost.cache_hit": cache_hit,
    }
    if provider:
        attributes["gen_ai.provider.name"] = provider
    if response_model:
        attributes["gen_ai.response.model"] = response_model
    if usage is not None:
        attributes["gen_ai.usage.input_tokens"] = usage.input_tokens
        attributes["gen_ai.usage.output_tokens"] = usage.output_tokens
    get_observability().set_attributes(attributes)


# HTTP status codes that trigger failover to an alternative provider.
_FAILOVER_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

# Map provider name → adapter class dotted path.
_PROVIDER_ADAPTER_MAP: dict[str, str] = {
    "anthropic": "bifrost.adapters.anthropic.AnthropicAdapter",
    "openai": "bifrost.adapters.openai_compat.OpenAICompatAdapter",
    "ollama": "bifrost.adapters.ollama.OllamaAdapter",
}

_DEFAULT_ADAPTER = "bifrost.adapters.openai_compat.OpenAICompatAdapter"

# ProviderConfig.timeout's default: adapters keep their own default unless the
# operator changed it.
_DEFAULT_PROVIDER_TIMEOUT = ProviderConfig.model_fields["timeout"].default


def _dynamic_adapter_kwargs(provider_name: str, cfg: ProviderConfig, base_url: str) -> dict:
    """Constructor kwargs for an adapter named by ``cfg.adapter``.

    The provider's ``kwargs`` are the adapter's own configuration. ``base_url``
    and ``timeout`` set on the provider are passed too; naming either in both
    places is ambiguous and refused rather than resolved by silently preferring
    one.
    """
    kwargs = dict(cfg.kwargs)
    inherited: dict = {}
    if base_url:
        inherited["base_url"] = base_url
    if cfg.timeout != _DEFAULT_PROVIDER_TIMEOUT:
        inherited["timeout"] = cfg.timeout
    clashes = sorted(set(inherited) & set(kwargs))
    if clashes:
        raise ValueError(
            f"provider '{provider_name}' sets {', '.join(clashes)} both on the provider "
            "and in its adapter kwargs: keep it in one place"
        )
    return {**inherited, **kwargs}


def _adapter_class(provider_name: str, cfg: ProviderConfig) -> type:
    """The adapter class for *provider_name*: ``cfg.adapter``, else by provider name."""
    if cfg.adapter:
        return import_class(cfg.adapter)
    return import_class(_PROVIDER_ADAPTER_MAP.get(provider_name, _DEFAULT_ADAPTER))


def _native_protocol_of(provider_name: str, cfg: ProviderConfig) -> NativeProtocol | None:
    """The protocol *provider_name* is served in natively, or ``None`` when it is not.

    Raises:
        ValueError: ``passthrough`` is set on a provider whose adapter cannot serve
            any protocol natively.
    """
    if not cfg.passthrough:
        return None
    cls = _adapter_class(provider_name, cfg)
    if not issubclass(cls, NativePassthroughPort):
        raise ValueError(
            f"provider '{provider_name}' sets passthrough: true, but its adapter "
            f"{cls.__module__}.{cls.__qualname__} cannot serve requests in a native "
            "protocol (it does not implement bifrost.ports.native.NativePassthroughPort); "
            "remove passthrough or use an adapter that does"
        )
    return cls.native_protocol


def _validate_passthrough(config: BifrostConfig) -> dict[str, NativeProtocol]:
    """Check every passthrough setting at startup and return provider → protocol.

    Raises:
        ValueError: A passthrough adapter cannot serve natively; the providers of
            one model disagree on the path (failover cannot switch protocols
            mid-request); or the response cache is enabled, which stores only
            canonical responses.
    """
    native = {
        name: protocol
        for name, cfg in config.providers.items()
        if (protocol := _native_protocol_of(name, cfg)) is not None
    }
    if not native:
        return native
    if config.cache.mode != CacheMode.DISABLED:
        raise ValueError(
            f"cache.mode is {config.cache.mode.value!r} and providers "
            f"{sorted(native)} set passthrough: true; the response cache stores canonical "
            "responses only, so passthrough responses cannot be cached. Disable the cache "
            "or passthrough"
        )
    models = {model for cfg in config.providers.values() for model in cfg.models}
    for model in sorted(models):
        paths = {name: native.get(name) for name in config.providers_for_model(model)}
        if len(set(paths.values())) > 1:
            described = ", ".join(
                f"{name} ({protocol.value if protocol else 'canonical'})"
                for name, protocol in paths.items()
            )
            raise ValueError(
                f"model '{model}' is served by providers on different paths: {described}. "
                "Failover cannot change protocol mid-request; set the same passthrough on "
                "every provider of the model"
            )
    return native


@dataclass
class RoutedRequest:
    """A request after rules, model selection and candidate ordering.

    ``request`` is the canonical view the policy ran on; ``candidates`` are the
    (provider, model) pairs to try in order; ``native_protocol`` is the protocol
    the candidates are served in natively, or ``None`` for the canonical path.
    """

    request: AnthropicRequest
    candidates: list[tuple[str, str]]
    native_protocol: NativeProtocol | None


def _load_adapter(
    provider_name: str,
    cfg: ProviderConfig,
    base_url: str,
    key_vault: KeyVaultPort | None = None,
) -> ProviderPort:
    """Instantiate the appropriate adapter for *provider_name*.

    When ``cfg.adapter`` names a class it is imported and built from
    ``cfg.kwargs`` (dynamic adapter configuration). Otherwise the adapter is
    chosen from the provider name. When a *key_vault* is provided, the
    provider's API key is read from the vault (which holds the cached,
    rotatable value); otherwise the key is read directly from ``cfg.api_key``
    for backwards compatibility.
    """
    api_key = key_vault.get_key(provider_name) if key_vault else cfg.api_key
    cls = _adapter_class(provider_name, cfg)
    if cfg.adapter:
        kwargs = _dynamic_adapter_kwargs(provider_name, cfg, base_url)
        if api_key:
            kwargs["api_key"] = api_key
        return cls(**kwargs)
    kwargs: dict = {}
    if base_url:
        kwargs["base_url"] = base_url
    if api_key:
        kwargs["api_key"] = api_key
    if cfg.timeout != _DEFAULT_PROVIDER_TIMEOUT:
        kwargs["timeout"] = cfg.timeout
    if cfg.chat_template_kwargs:
        kwargs["chat_template_kwargs"] = cfg.chat_template_kwargs
    return cls(**kwargs)


class RouterError(Exception):
    """Raised when no provider can fulfil the request."""


class ModelRouter:
    """Routes requests to the right provider using the configured RoutingStrategy.

    Providers are loaded lazily on first use so that no connections are
    opened until an actual request arrives.
    """

    def __init__(
        self,
        config: BifrostConfig,
        rule_engine: RuleEnginePort | None = None,
        key_vault: KeyVaultPort | None = None,
        selection: SelectionPort | None = None,
    ) -> None:
        self._config = config
        self._rule_engine = rule_engine
        self._key_vault = key_vault
        self._selection = selection
        if selection is not None:
            for provider, model in selection.configured_targets():
                if (
                    provider not in config.providers
                    or model not in config.providers[provider].models
                ):
                    raise ValueError(f"Routing target {provider}/{model} is not configured")
        self._native_protocols = _validate_passthrough(config)
        self._adapters: dict[str, ProviderPort] = {}
        # Per-model request counter used by the round_robin strategy.
        self._round_robin_counters: dict[str, int] = {}
        # Per-provider EWMA latency (seconds) used by the latency_optimised strategy.
        self._latency_ewma: dict[str, float] = {}

    def _get_adapter(self, provider_name: str) -> ProviderPort:
        if provider_name not in self._adapters:
            cfg = self._config.providers.get(provider_name, ProviderConfig())
            base_url = self._config.effective_base_url(provider_name)
            self._adapters[provider_name] = _load_adapter(
                provider_name, cfg, base_url, self._key_vault
            )
        return self._adapters[provider_name]

    def reload_keys(self) -> None:
        """Reload provider API keys and discard cached adapters.

        After a call to this method, adapters are re-instantiated on the
        next request with the freshly loaded keys.  This is the hook
        called by the SIGHUP handler and the admin reload endpoint.
        """
        if self._key_vault is not None:
            self._key_vault.reload()
        # Clear cached adapters so they are rebuilt with the new keys.
        self._adapters.clear()
        logger.info("ModelRouter: adapters cleared after key reload")

    def _record_latency(self, provider: str, elapsed: float) -> None:
        """Update the EWMA latency estimate for *provider*."""
        alpha = self._config.latency_ewma_alpha
        current = self._latency_ewma.get(provider)
        if current is None:
            self._latency_ewma[provider] = elapsed
            return
        self._latency_ewma[provider] = alpha * elapsed + (1 - alpha) * current

    def _build_candidates(self, raw_model: str) -> list[tuple[str, str]]:
        """Return an ordered list of (provider, model) pairs to try.

        Order is determined by the configured RoutingStrategy.

        Raises:
            RouterError: If no provider is configured for the resolved model.
        """
        model = self._config.resolve_alias(raw_model)
        providers = self._config.providers_for_model(model)
        if not providers:
            raise RouterError(
                f"No provider configured for model '{model}' "
                f"(requested: '{raw_model}'). "
                f"Configured providers: {list(self._config.providers)}"
            )

        strategy = self._config.routing_strategy_for_model(raw_model)

        match strategy:
            case RoutingStrategy.DIRECT:
                return [(providers[0], model)]

            case RoutingStrategy.FAILOVER:
                return [(p, model) for p in providers]

            case RoutingStrategy.COST_OPTIMISED:
                return self._cost_optimised_candidates(providers, model)

            case RoutingStrategy.ROUND_ROBIN:
                return self._round_robin_candidates(providers, model)

            case RoutingStrategy.LATENCY_OPTIMISED:
                return self._latency_optimised_candidates(providers, model)

            case _:
                raise ValueError(f"Unknown routing strategy: {strategy}")
        raise AssertionError("Unreachable _build_candidates fallthrough")

    def _cost_optimised_candidates(self, providers: list[str], model: str) -> list[tuple[str, str]]:
        """Sort providers cheapest-first by their configured cost_per_token."""

        def cost(name: str) -> float:
            cfg = self._config.providers.get(name)
            return cfg.cost_per_token if cfg else 0.0

        ordered = sorted(providers, key=cost)
        return [(p, model) for p in ordered]

    def _round_robin_candidates(self, providers: list[str], model: str) -> list[tuple[str, str]]:
        """Rotate the provider list so a different provider leads each request."""
        idx = self._round_robin_counters.get(model, 0)
        self._round_robin_counters[model] = (idx + 1) % len(providers)
        rotated = providers[idx:] + providers[:idx]
        return [(p, model) for p in rotated]

    def _latency_optimised_candidates(
        self, providers: list[str], model: str
    ) -> list[tuple[str, str]]:
        """Sort providers fastest-first by EWMA latency.

        Providers with no recorded latency are placed after those with data,
        preserving config order among unknowns.
        """

        def latency_key(name: str) -> tuple[int, float]:
            ewma = self._latency_ewma.get(name)
            if ewma is None:
                return (1, 0.0)
            return (0, ewma)

        ordered = sorted(providers, key=latency_key)
        return [(p, model) for p in ordered]

    async def route(
        self,
        request: AnthropicRequest,
        context: RoutingContext | None = None,
    ) -> RoutedRequest:
        """Apply rules and model selection, and order the candidates to try.

        Idempotent: a request that was routed already keeps its route.

        Args:
            request: Inbound Anthropic-format request (the canonical view).
            context: Optional per-request routing context (e.g. agent budget
                     percentage).  When ``None`` an empty context is used so
                     budget-sensitive rules are skipped silently.
        """
        if request._routed_candidates is not None:
            candidates = request._routed_candidates
            return RoutedRequest(request, candidates, self._native_protocols.get(candidates[0][0]))
        ctx = context if context is not None else RoutingContext()
        request = self._honour_model_thinking(await self.prepare(request, ctx))
        candidates = (
            [(request._routed_provider, request.model)]
            if request._routed_provider
            else await self._select_candidates(request.model)
        )
        # Routed once: rules, selection and round-robin state are not applied
        # a second time when the inbound layer passes this request back in.
        # A copy, so the caller's request object can be routed afresh.
        request = request.model_copy()
        request._routing_prepared = True
        request._routed_candidates = candidates
        if request.uses_context_management():
            self._require_context_management(candidates)
        # _validate_passthrough guarantees one model's providers share a path,
        # so the first candidate speaks for all of them.
        native_protocol = self._native_protocols.get(candidates[0][0])
        return RoutedRequest(request, candidates, native_protocol)

    def _require_context_management(self, candidates: list[tuple[str, str]]) -> None:
        """Refuse Anthropic context management for a provider that cannot honour it.

        Raises:
            UnsupportedFeatureError: A candidate's adapter would have to drop the
                ``context_management`` edits or ``compaction`` blocks.
        """
        for name, model in candidates:
            cls = _adapter_class(name, self._config.providers.get(name, ProviderConfig()))
            if not getattr(cls, "accepts_context_management", False):
                raise UnsupportedFeatureError(
                    f"provider '{name}' ({cls.__name__}) serves model '{model}' through a "
                    "translation that cannot carry Anthropic context_management or "
                    "compaction blocks; route the model to an Anthropic provider, or have "
                    "the client compact on its own"
                )

    async def complete(
        self,
        request: AnthropicRequest,
        context: RoutingContext | None = None,
    ) -> AnthropicResponse:
        """Route a non-streaming completion request on the canonical path.

        Tries candidates in order; moves to the next on retryable errors.
        """
        return await self.complete_routed(await self.route(request, context))

    async def stream(
        self,
        request: AnthropicRequest,
        context: RoutingContext | None = None,
    ) -> AsyncIterator[str]:
        """Route a streaming request on the canonical path.

        Failover is attempted on connection or HTTP errors before the first
        byte is yielded.
        """
        routed = await self.route(request, context)
        async for chunk in self.stream_routed(routed):
            yield chunk

    async def complete_routed(self, routed: RoutedRequest) -> AnthropicResponse:
        """Complete an already-routed request through the canonical translation."""
        request = routed.request

        async def call(adapter: ProviderPort, model: str) -> AnthropicResponse:
            return await adapter.complete(request, model)

        result, pname, pmodel, attempt = await self._complete_with_failover(routed, call)
        record_genai_span_attributes(
            requested_model=request.model,
            provider=pname,
            failover_attempts=attempt,
            cache_hit=False,
            response_model=pmodel,
            usage=result.usage,
        )
        return result

    async def stream_routed(self, routed: RoutedRequest) -> AsyncIterator[str]:
        """Stream an already-routed request through the canonical translation."""
        request = routed.request
        async for chunk in self._stream_with_failover(
            routed, lambda adapter, model: adapter.stream(request, model)
        ):
            yield chunk

    async def complete_native(
        self, routed: RoutedRequest, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Complete *payload* verbatim on the routed providers' native protocol."""

        async def call(adapter: ProviderPort, model: str) -> dict[str, Any]:
            return await self._native(adapter).native_complete(payload, model)

        result, pname, pmodel, attempt = await self._complete_with_failover(routed, call)
        record_genai_span_attributes(
            requested_model=routed.request.model,
            provider=pname,
            failover_attempts=attempt,
            cache_hit=False,
            response_model=pmodel,
        )
        return result

    async def stream_native(
        self, routed: RoutedRequest, payload: dict[str, Any]
    ) -> AsyncIterator[str]:
        """Stream *payload* verbatim on the routed providers' native protocol."""
        async for chunk in self._stream_with_failover(
            routed, lambda adapter, model: self._native(adapter).native_stream(payload, model)
        ):
            yield chunk

    def supports_native_compaction(self, routed: RoutedRequest) -> bool:
        """Whether every routed provider compacts context natively on request."""
        return all(
            getattr(
                _adapter_class(name, self._config.providers.get(name, ProviderConfig())),
                "supports_native_compaction",
                False,
            )
            for name, _ in routed.candidates
        )

    @staticmethod
    def _native(adapter: ProviderPort) -> NativePassthroughPort:
        if not isinstance(adapter, NativePassthroughPort):
            raise RouterError(
                f"{type(adapter).__name__} cannot serve a native request; "
                "the passthrough configuration was not validated"
            )
        return adapter

    async def _complete_with_failover(
        self,
        routed: RoutedRequest,
        call: Callable[[ProviderPort, str], Awaitable[Any]],
    ) -> tuple[Any, str, str, int]:
        """Call each candidate in order until one answers.

        Returns the result with the provider, model and attempt that served it.
        """
        last_exc: Exception | None = None
        for attempt, (pname, pmodel) in enumerate(routed.candidates):
            try:
                adapter = self._get_adapter(pname)
                t0 = time.monotonic()
                result = await call(adapter, pmodel)
                self._record_latency(pname, time.monotonic() - t0)
                return result, pname, pmodel, attempt
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in _FAILOVER_STATUS_CODES:
                    raise
                logger.warning(
                    "Provider %s returned HTTP %d for model %s; trying next candidate",
                    pname,
                    exc.response.status_code,
                    pmodel,
                )
                last_exc = exc
            except ProviderError as exc:
                logger.warning("Provider %s error for model %s: %s", pname, pmodel, exc)
                last_exc = exc

        raise RouterError(
            f"All providers failed for model '{routed.candidates[0][1]}': {last_exc}"
        ) from last_exc

    async def _stream_with_failover(
        self,
        routed: RoutedRequest,
        open_stream: Callable[[ProviderPort, str], AsyncIterator[str]],
    ) -> AsyncIterator[str]:
        """Stream from each candidate in order until one opens."""
        last_exc: Exception | None = None
        for attempt, (pname, pmodel) in enumerate(routed.candidates):
            try:
                adapter = self._get_adapter(pname)
                t0 = time.monotonic()
                async for chunk in open_stream(adapter, pmodel):
                    yield chunk
                self._record_latency(pname, time.monotonic() - t0)
                # Token usage isn't known at this layer for streaming responses
                # (it arrives as SSE deltas the inbound layer accumulates), so
                # only the request/provider/failover attributes are recorded
                # here; usage is left for the inbound layer to attach.
                record_genai_span_attributes(
                    requested_model=routed.request.model,
                    provider=pname,
                    failover_attempts=attempt,
                    cache_hit=False,
                )
                return
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in _FAILOVER_STATUS_CODES:
                    raise
                logger.warning(
                    "Provider %s returned HTTP %d; trying next candidate",
                    pname,
                    exc.response.status_code,
                )
                last_exc = exc
            except ProviderError as exc:
                logger.warning("Provider %s error: %s", pname, exc)
                last_exc = exc

        raise RouterError(
            f"All providers failed for model '{routed.candidates[0][1]}': {last_exc}"
        ) from last_exc

    async def _select_candidates(self, model: str) -> list[tuple[str, str]]:
        if self._selection is None or self._selection.configured_targets():
            return self._build_candidates(model)
        resolved = self._config.resolve_alias(model)
        candidates = [
            (provider, resolved) for provider in self._config.providers_for_model(resolved)
        ]
        if not candidates:
            raise RouterError(f"No provider configured for model '{resolved}'")
        selected = await self._selection.select(candidates)
        if selected not in candidates:
            raise RouterError("Selection adapter returned an unconfigured target")
        return [selected]

    def _honour_model_thinking(self, request: AnthropicRequest) -> AnthropicRequest:
        """Drop thinking controls for a model the catalog says has none.

        ``thinking`` and ``reasoning_effort`` are threaded to backends on
        purpose (DeepSeek honours them, vLLM ignores them), but a server that
        knows the model cannot think (Ollama with Llama) rejects the whole
        request over them. The catalog's ``supports_thinking`` is the
        configured answer; an unknown model keeps the controls.
        """
        entry = self._config.model_entry(request.model)
        if entry is None or entry.supports_thinking:
            return request
        if request.thinking is None and request.reasoning_effort is None:
            return request
        logger.info(
            "Model %s has no thinking mode (supports_thinking=false); "
            "dropping thinking/reasoning_effort",
            request.model,
        )
        stripped = request.model_copy(update={"thinking": None, "reasoning_effort": None})
        stripped._routed_provider = request._routed_provider
        stripped._routing_prepared = request._routing_prepared
        stripped._response_format = request._response_format
        stripped._images_stripped = request._images_stripped
        stripped._routed_candidates = request._routed_candidates
        return stripped

    async def prepare(
        self,
        request: AnthropicRequest,
        context: RoutingContext | None = None,
        authorize: Callable[[str], None] | None = None,
        record_call: Callable[[str, str, AnthropicResponse, float], Awaitable[None]] | None = None,
    ) -> AnthropicRequest:
        """Resolve explicit routes before caching, accounting, and answer execution."""
        if request._routing_prepared:
            return request
        request = apply_rules(request, context or RoutingContext(), self._rule_engine)
        if self._selection is None or not self._selection.configured_targets():
            return request
        request = request.model_copy()
        request._routing_prepared = True

        async def call_model(provider: str, model: str, judge: AnthropicRequest):
            if (provider, model) not in self._selection.configured_targets():
                raise RouterError("Classifier requested an unconfigured target")
            if authorize is not None:
                authorize(model)
            started = time.monotonic()
            response = await self._get_adapter(provider).complete(judge, model)
            if record_call is not None:
                await record_call(provider, model, response, time.monotonic() - started)
            return response

        try:
            selected = await self._selection.route(request, call_model)
        except (ValueError, RuntimeError, ProviderError, httpx.HTTPError) as exc:
            raise RouterError(f"Model selection failed: {exc}") from exc
        if selected is None:
            return request
        provider, model = selected
        if (provider, model) not in self._selection.configured_targets():
            raise RouterError("Selection adapter returned an unconfigured target")
        if authorize is not None:
            authorize(model)
        prepared = request.model_copy(update={"model": model})
        prepared._routed_provider = provider
        return prepared

    @property
    def has_model_routes(self) -> bool:
        return self._selection is not None and bool(self._selection.configured_targets())

    async def close(self) -> None:
        """Close all open provider adapters."""
        for adapter in self._adapters.values():
            await adapter.close()
        self._adapters.clear()
