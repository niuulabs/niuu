"""AnthropicAdapter — LLMPort implementation using the Anthropic Messages API.

Uses httpx for HTTP, following the project pattern established in
ting/adapters/bifrost.py.  Implements streaming (SSE), tool calling,
prompt caching (cache_control: ephemeral on system prompt), and
transient-error retries.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

import httpx

from niuu.observability import get_observability
from ravn.adapters.llm.provider_items import (
    ANTHROPIC_MESSAGES,
    COMPACTION_TYPE,
    PROVIDER_KEYS,
    latest_compaction_index,
    provider_items_of,
    refuse_foreign_compaction,
)
from ravn.domain.exceptions import ConfigurationError, LLMError
from ravn.domain.models import (
    LLMResponse,
    StopReason,
    StreamEvent,
    StreamEventType,
    TokenUsage,
    ToolCall,
)
from ravn.ports.llm import LLMPort, SystemPrompt
from ravn.ports.native_compaction import NativeCompactionPort

logger = logging.getLogger(__name__)

ANTHROPIC_API_VERSION = "2023-06-01"
_BETA_PROMPT_CACHING = "prompt-caching-2024-07-31"
_BETA_INTERLEAVED_THINKING = "interleaved-thinking-2025-05-14"
# Server-side compaction at a token threshold (Messages API beta).
_BETA_COMPACTION = "compact-2026-01-12"
_COMPACTION_EDIT = "compact_20260112"
# The API refuses a compaction trigger below this many input tokens.
_MIN_COMPACTION_TRIGGER_TOKENS = 50_000

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503})
# Stop reasons that mean the answer ran out of room: the output cap, or the
# context window filling up before the model finished.
_TRUNCATED_STOP_REASONS = frozenset({"max_tokens", "model_context_window_exceeded"})
_DEFAULT_BASE_URL = "https://api.anthropic.com"

# Ravn's transport-neutral message keys that the Anthropic API does not accept.
_INTERNAL_MESSAGE_KEYS = frozenset({"reasoning", "reasoning_details"}) | PROVIDER_KEYS

# Everything the Messages API accepts inside a tool_result block. Any other key
# is rejected outright ("tool_result.name: Extra inputs are not permitted"), so
# history carrying one — an old checkpoint, another adapter's shape — has to be
# pruned before it goes back on the wire.
_TOOL_RESULT_KEYS = frozenset(
    {"type", "tool_use_id", "content", "is_error", "cache_control", "citations"}
)


# Models that still take the fixed-budget thinking shape. Everything else —
# including anything released after this list was written — gets adaptive
# thinking. That default direction is deliberate: `budget_tokens` is a hard 400
# from Opus 4.7 onward, so treating an unknown model as legacy would break it on
# the day it ships, while treating a legacy model as adaptive degrades to a
# model that simply thinks less.
_LEGACY_THINKING_MODELS: tuple[str, ...] = (
    "claude-3",
    "claude-sonnet-4-0",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
    "claude-opus-4-0",
    "claude-opus-4-1",
    "claude-opus-4-5",
)


def _uses_legacy_thinking(model: str) -> bool:
    """True when *model* wants {"type": "enabled", "budget_tokens": N}."""
    return model.startswith(_LEGACY_THINKING_MODELS)


def _thinking_for_model(thinking: dict, model: str) -> dict:
    """Translate a fixed-budget thinking request into the shape *model* accepts.

    Callers ask for thinking in one vocabulary — a token budget — because that
    is what the config exposes. Newer models replaced the budget with adaptive
    thinking plus output_config.effort and reject the old field outright.
    """
    if thinking.get("type") != "enabled":
        return thinking
    if _uses_legacy_thinking(model):
        return thinking
    return {"type": "adaptive"}


def _pruned_block(block: object) -> object:
    """Drop non-schema keys from a tool_result content block."""
    if not isinstance(block, dict):
        return block
    if block.get("type") != "tool_result":
        return block
    return {key: value for key, value in block.items() if key in _TOOL_RESULT_KEYS}


def _accumulate_block(block: dict, delta: dict) -> None:
    """Grow a verbatim content block by one stream delta."""
    match delta.get("type"):
        case "text_delta":
            block["text"] = block.get("text", "") + delta.get("text", "")
        case "thinking_delta":
            block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")
        case "signature_delta":
            block["signature"] = block.get("signature", "") + delta.get("signature", "")
        case "compaction_delta":
            block["content"] = (block.get("content") or "") + delta.get("content", "")


def _stop_reason(raw: object) -> StopReason | None:
    """Map an Anthropic ``stop_reason`` onto Ravn's, failing on one that is no answer.

    ``None`` is "not reported yet" (a stream's earlier events). A refusal, a
    paused turn or a reason this adapter does not know is not something the
    agent can act on as if the model had finished, so it raises.
    """
    if raw is None:
        return None
    if raw in _TRUNCATED_STOP_REASONS:
        return StopReason.MAX_TOKENS
    try:
        return StopReason(str(raw))
    except ValueError:
        raise LLMError(
            f"Anthropic response stopped with {raw!r}, which is not an answer the agent "
            "can act on (expected end_turn, tool_use, max_tokens or stop_sequence)"
        ) from None


def _compaction_usage(usage: dict) -> tuple[int, int]:
    """Input and output tokens of the compaction iterations in *usage*.

    With the compaction beta the top-level counts exclude compaction; the
    billed total is the sum over every ``usage.iterations`` entry.
    """
    iterations = [
        item
        for item in usage.get("iterations") or []
        if isinstance(item, dict) and item.get("type") == COMPACTION_TYPE
    ]
    return (
        sum(int(item.get("input_tokens") or 0) for item in iterations),
        sum(int(item.get("output_tokens") or 0) for item in iterations),
    )


def _tool_use_ids(content: object) -> list[str]:
    if not isinstance(content, list):
        return []
    return [
        str(block.get("id", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]


def _verbatim_content(message: dict) -> list[dict] | None:
    """The assistant turn's own content blocks, when the provider returned them.

    Kept for models on native compaction: the ``compaction`` block (and the
    signed thinking around it) must go back exactly as it came.

    Raises:
        LLMError: The blocks name other tool calls than the message's content.
    """
    blocks = provider_items_of(message, ANTHROPIC_MESSAGES)
    if not blocks:
        return None
    if _tool_use_ids(blocks) != _tool_use_ids(message.get("content")):
        raise LLMError(
            "assistant message's Anthropic content blocks name different tool calls than "
            "its content; the history was changed after the provider answered and cannot "
            "be replayed"
        )
    return blocks


def _for_api(messages: list[dict]) -> list[dict]:
    """Strip internal reasoning and non-schema tool_result keys.

    ``reasoning_details`` is another provider's opaque payload; the Anthropic
    API has its own thinking blocks and would reject the field. An assistant
    turn kept verbatim (``provider_items`` in this protocol) is sent as those
    blocks.
    """
    cleaned: list[dict] = []
    for message in messages:
        out = {key: value for key, value in message.items() if key not in _INTERNAL_MESSAGE_KEYS}
        verbatim = _verbatim_content(message) if message.get("role") == "assistant" else None
        if verbatim is not None:
            out["content"] = verbatim
        content = out.get("content")
        if isinstance(content, list):
            out["content"] = [_pruned_block(block) for block in content]
        cleaned.append(out)
    return cleaned


class AnthropicAdapter(LLMPort, NativeCompactionPort):
    """Calls the Anthropic Messages API with streaming and tool support.

    Constructor kwargs are forwarded from config via the dynamic adapter pattern.

    Extended thinking is activated by passing
    ``thinking={"type": "enabled", "budget_tokens": N}`` to ``stream()`` or
    ``generate()``.  When enabled, the ``interleaved-thinking-2025-05-14`` beta
    header is added automatically.  Thinking blocks are yielded as
    ``StreamEvent(type=StreamEventType.THINKING, text=…)`` events and their
    approximate token count is tracked in ``TokenUsage.thinking_tokens``.

    Native compaction (``context_management.compaction: native``): requests for
    the enabled model carry the ``compact_20260112`` edit with the configured
    trigger and the ``compact-2026-01-12`` beta. The turn's content blocks —
    including the ``compaction`` block — are kept verbatim on the message and
    sent back unchanged, and history before the latest compaction block is
    dropped (the API ignores it). Compaction iterations are counted in usage.
    """

    @property
    def supports_thinking(self) -> bool:
        return True

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = _DEFAULT_BASE_URL,
        model: str = "claude-sonnet-4-6",
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        timeout: float = 120.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._default_model = model
        # Model → maximum output tokens, as the Models API reports it.
        self._output_limits: dict[str, int] = {}
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._timeout = timeout
        # Model → compaction trigger, for the models set to native compaction.
        self._compaction_triggers: dict[str, int] = {}

    def enable_native_compaction(self, *, model: str, threshold_tokens: int) -> None:
        if threshold_tokens < _MIN_COMPACTION_TRIGGER_TOKENS:
            raise ConfigurationError(
                f"native compaction for {model!r} would trigger at {threshold_tokens} "
                f"tokens, but Anthropic accepts no trigger below "
                f"{_MIN_COMPACTION_TRIGGER_TOKENS}; raise context_management.max_prompt_tokens "
                "(the trigger is max_prompt_tokens x compression_threshold / "
                "token_estimate_safety_factor), or use compaction 'ravn' for this model"
            )
        self._compaction_triggers[model] = threshold_tokens

    def compaction_window_start(self, messages: list[dict]) -> int:
        return latest_compaction_index(messages, ANTHROPIC_MESSAGES)

    def _native_compaction_for(self, model: str) -> int | None:
        return self._compaction_triggers.get(model or self._default_model)

    def _headers(self, *, thinking_enabled: bool = False, model: str = "") -> dict[str, str]:
        betas = [_BETA_PROMPT_CACHING]
        # Adaptive thinking interleaves on its own; the beta is only meaningful
        # for the models that still take a fixed budget.
        if thinking_enabled and _uses_legacy_thinking(model or self._default_model):
            betas.append(_BETA_INTERLEAVED_THINKING)
        if self._native_compaction_for(model) is not None:
            betas.append(_BETA_COMPACTION)
        return {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
            "anthropic-beta": ",".join(betas),
            "content-type": "application/json",
            # W3C traceparent/tracestate for the active span, when observability
            # is enabled and a span is active. Empty dict (no keys) otherwise —
            # this call is the leaf of the trace that reaches Bifröst.
            **get_observability().inject(),
        }

    def _build_system(self, system: SystemPrompt) -> list[dict]:
        """Return Anthropic-format system blocks from a string or block list.

        - If *system* is already a ``list[dict]``, it is returned as-is (the
          caller is responsible for setting ``cache_control`` on each block).
        - If *system* is a plain string, it is wrapped in a single text block
          with ``cache_control: ephemeral`` for prompt-caching.
        """
        if isinstance(system, list):
            return system
        if not system:
            return []
        return [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def _build_request(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system: SystemPrompt,
        model: str,
        max_tokens: int | None,
        stream: bool,
        thinking: dict | None = None,
    ) -> dict:
        refuse_foreign_compaction(messages, ANTHROPIC_MESSAGES, type(self).__name__)
        body: dict = {
            "model": model or self._default_model,
            "messages": _for_api(messages),
            "stream": stream,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        system_blocks = self._build_system(system)
        if system_blocks:
            body["system"] = system_blocks
        if tools:
            body["tools"] = tools
        if thinking is not None:
            body["thinking"] = _thinking_for_model(thinking, body["model"])
        trigger = self._native_compaction_for(body["model"])
        if trigger is not None:
            edit = {"type": _COMPACTION_EDIT, "trigger": {"type": "input_tokens", "value": trigger}}
            body["context_management"] = {"edits": [edit]}
        return body

    async def _output_cap(self, model: str, max_tokens: int | None) -> int | None:
        """The ``max_tokens`` to send for *model*.

        The Messages API requires the parameter, so "no cap" is the model's own
        maximum output, read from the Models API once per model — never a
        number chosen here.
        """
        if max_tokens is not None:
            return max_tokens
        effective_model = model or self._default_model
        cached = self._output_limits.get(effective_model)
        if cached is not None:
            return cached
        url = f"{self._base_url}/v1/models/{effective_model}"
        async with httpx.AsyncClient(headers=self._headers(), timeout=self._timeout) as client:
            response = await client.get(url)
        if response.status_code != httpx.codes.OK:
            raise LLMError(
                f"could not read the output limit of {effective_model!r} from {url} "
                f"({response.status_code}: {response.text[:500]}); set llm.max_tokens "
                "(or the calling component's max_tokens) to send an explicit cap",
                status_code=response.status_code,
            )
        limit = response.json().get("max_tokens")
        if not isinstance(limit, int) or limit <= 0:
            raise LLMError(
                f"the Models API reported no max_tokens for {effective_model!r} at {url}; "
                "set llm.max_tokens (or the calling component's max_tokens) to send an "
                "explicit cap"
            )
        self._output_limits[effective_model] = limit
        return limit

    async def _post_with_retry(
        self,
        client: httpx.AsyncClient,
        payload: dict,
        *,
        stream: bool,
    ) -> httpx.Response:
        url = f"{self._base_url}/v1/messages"
        last_exc: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                if stream:
                    response = await client.send(
                        client.build_request("POST", url, json=payload),
                        stream=True,
                    )
                else:
                    response = await client.post(url, json=payload)

                if response.status_code not in _RETRYABLE_STATUS_CODES:
                    return response

                if stream:
                    await response.aclose()

                if attempt < self._max_retries:
                    delay = self._retry_base_delay * (2**attempt)
                    logger.warning(
                        "Anthropic API returned %s, retrying in %.1fs (attempt %d/%d)",
                        response.status_code,
                        delay,
                        attempt + 1,
                        self._max_retries,
                    )
                    await asyncio.sleep(delay)
                    last_exc = LLMError(
                        f"Anthropic API error {response.status_code}",
                        status_code=response.status_code,
                    )

            except httpx.TransportError as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    delay = self._retry_base_delay * (2**attempt)
                    await asyncio.sleep(delay)

        raise LLMError(f"Anthropic API failed after {self._max_retries} retries") from last_exc

    async def stream(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system: SystemPrompt,
        model: str,
        max_tokens: int | None,
        thinking: dict | None = None,
    ) -> AsyncIterator[StreamEvent]:
        thinking_enabled = thinking is not None
        payload = self._build_request(
            messages,
            tools=tools,
            system=system,
            model=model,
            max_tokens=await self._output_cap(model, max_tokens),
            stream=True,
            thinking=thinking,
        )

        async with httpx.AsyncClient(
            headers=self._headers(thinking_enabled=thinking_enabled, model=model),
            timeout=self._timeout,
        ) as client:
            response = await self._post_with_retry(client, payload, stream=True)

            if response.status_code != 200:
                body = await response.aread()
                raise LLMError(
                    f"Anthropic API error {response.status_code}: {body.decode()}",
                    status_code=response.status_code,
                )

            # With native compaction the turn's blocks are kept verbatim, by index.
            capture = self._native_compaction_for(model) is not None
            blocks: dict[int, dict] = {}
            # Accumulate partial tool inputs keyed by tool_use block index.
            partial_inputs: dict[int, dict] = {}
            tool_ids: dict[int, str] = {}
            tool_names: dict[int, str] = {}
            # Accumulate thinking block text keyed by block index.
            partial_thinking: dict[int, str] = {}
            current_block_index = -1
            accumulated_thinking_chars = 0

            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                raw = line[len("data: ") :]
                if raw == "[DONE]":
                    break

                try:
                    event_data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                event_type = event_data.get("type", "")

                match event_type:
                    case "content_block_start":
                        block = event_data.get("content_block", {})
                        current_block_index = event_data.get("index", current_block_index)
                        if capture:
                            blocks[current_block_index] = dict(block)
                        if block.get("type") == "tool_use":
                            tool_ids[current_block_index] = block.get("id", "")
                            tool_names[current_block_index] = block.get("name", "")
                            partial_inputs[current_block_index] = {}
                        elif block.get("type") == "thinking":
                            partial_thinking[current_block_index] = ""

                    case "content_block_delta":
                        delta = event_data.get("delta", {})
                        idx = event_data.get("index", current_block_index)
                        if capture and idx in blocks:
                            _accumulate_block(blocks[idx], delta)
                        match delta.get("type"):
                            case "text_delta":
                                yield StreamEvent(
                                    type=StreamEventType.TEXT_DELTA,
                                    text=delta.get("text", ""),
                                )
                            case "thinking_delta":
                                chunk = delta.get("thinking", "")
                                if idx not in partial_thinking:
                                    partial_thinking[idx] = ""
                                partial_thinking[idx] += chunk
                                accumulated_thinking_chars += len(chunk)
                                yield StreamEvent(
                                    type=StreamEventType.THINKING,
                                    text=chunk,
                                )
                            case "input_json_delta":
                                # Accumulate partial JSON — emit when block stops.
                                if idx not in partial_inputs:
                                    partial_inputs[idx] = {}
                                chunk = delta.get("partial_json", "")
                                partial_inputs[idx]["_raw"] = (
                                    partial_inputs[idx].get("_raw", "") + chunk
                                )

                    case "content_block_stop":
                        idx = event_data.get("index", current_block_index)
                        if idx in partial_inputs:
                            raw_json = partial_inputs[idx].get("_raw", "")
                            try:
                                parsed_input = json.loads(raw_json) if raw_json else {}
                            except json.JSONDecodeError:
                                parsed_input = {}
                            if capture and idx in blocks:
                                blocks[idx]["input"] = parsed_input
                            yield StreamEvent(
                                type=StreamEventType.TOOL_CALL,
                                tool_call=ToolCall(
                                    id=tool_ids.get(idx, ""),
                                    name=tool_names.get(idx, ""),
                                    input=parsed_input,
                                ),
                            )
                            del partial_inputs[idx]
                        if idx in partial_thinking:
                            del partial_thinking[idx]

                    case "message_delta":
                        usage_data = event_data.get("usage", {})
                        stop_reason = _stop_reason(
                            (event_data.get("delta") or {}).get("stop_reason")
                        )
                        # Approximate thinking tokens from accumulated char count.
                        thinking_tokens = accumulated_thinking_chars // 4
                        if capture:
                            yield StreamEvent(
                                type=StreamEventType.PROVIDER_ITEMS,
                                provider_items=[blocks[index] for index in sorted(blocks)],
                                provider_protocol=ANTHROPIC_MESSAGES,
                            )
                        compaction_in, compaction_out = _compaction_usage(usage_data)
                        yield StreamEvent(
                            type=StreamEventType.MESSAGE_DONE,
                            usage=TokenUsage(
                                input_tokens=usage_data.get("input_tokens", 0) + compaction_in,
                                output_tokens=usage_data.get("output_tokens", 0) + compaction_out,
                                cache_read_tokens=usage_data.get("cache_read_input_tokens", 0),
                                cache_write_tokens=usage_data.get("cache_creation_input_tokens", 0),
                                thinking_tokens=thinking_tokens,
                            ),
                            stop_reason=stop_reason,
                        )

    async def generate(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system: SystemPrompt,
        model: str,
        max_tokens: int | None,
        thinking: dict | None = None,
    ) -> LLMResponse:
        thinking_enabled = thinking is not None
        payload = self._build_request(
            messages,
            tools=tools,
            system=system,
            model=model,
            max_tokens=await self._output_cap(model, max_tokens),
            stream=False,
            thinking=thinking,
        )

        async with httpx.AsyncClient(
            headers=self._headers(thinking_enabled=thinking_enabled, model=model),
            timeout=self._timeout,
        ) as client:
            response = await self._post_with_retry(client, payload, stream=False)

        if response.status_code != 200:
            raise LLMError(
                f"Anthropic API error {response.status_code}: {response.text}",
                status_code=response.status_code,
            )

        data = response.json()
        content_text = ""
        tool_calls: list[ToolCall] = []
        thinking_parts: list[str] = []

        for block in data.get("content", []):
            match block.get("type"):
                case "thinking":
                    thinking_parts.append(block.get("thinking", ""))
                case "text":
                    content_text += block.get("text", "")
                case "tool_use":
                    tool_calls.append(
                        ToolCall(
                            id=block.get("id", ""),
                            name=block.get("name", ""),
                            input=block.get("input", {}),
                        )
                    )

        usage_data = data.get("usage", {})
        compaction_in, compaction_out = _compaction_usage(usage_data)
        reasoning = "".join(thinking_parts)
        native = self._native_compaction_for(model) is not None
        stop_reason = _stop_reason(data.get("stop_reason", "end_turn")) or StopReason.END_TURN

        return LLMResponse(
            content=content_text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=TokenUsage(
                input_tokens=usage_data.get("input_tokens", 0) + compaction_in,
                output_tokens=usage_data.get("output_tokens", 0) + compaction_out,
                cache_read_tokens=usage_data.get("cache_read_input_tokens", 0),
                cache_write_tokens=usage_data.get("cache_creation_input_tokens", 0),
                thinking_tokens=len(reasoning) // 4,
            ),
            reasoning=reasoning,
            provider_items=list(data.get("content", [])) if native else [],
            provider_protocol=ANTHROPIC_MESSAGES if native else "",
        )
