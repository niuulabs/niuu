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

from ravn.domain.exceptions import LLMError
from ravn.domain.models import (
    LLMResponse,
    StopReason,
    StreamEvent,
    StreamEventType,
    TokenUsage,
    ToolCall,
)
from ravn.ports.llm import LLMPort, SystemPrompt

logger = logging.getLogger(__name__)

ANTHROPIC_API_VERSION = "2023-06-01"
_BETA_PROMPT_CACHING = "prompt-caching-2024-07-31"
_BETA_INTERLEAVED_THINKING = "interleaved-thinking-2025-05-14"

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503})
_DEFAULT_BASE_URL = "https://api.anthropic.com"


def _without_reasoning(messages: list[dict]) -> list[dict]:
    """Remove prior internal reasoning from Anthropic input messages."""
    return [
        {key: value for key, value in message.items() if key != "reasoning"} for message in messages
    ]


class AnthropicAdapter(LLMPort):
    """Calls the Anthropic Messages API with streaming and tool support.

    Constructor kwargs are forwarded from config via the dynamic adapter pattern.

    Extended thinking is activated by passing
    ``thinking={"type": "enabled", "budget_tokens": N}`` to ``stream()`` or
    ``generate()``.  When enabled, the ``interleaved-thinking-2025-05-14`` beta
    header is added automatically.  Thinking blocks are yielded as
    ``StreamEvent(type=StreamEventType.THINKING, text=…)`` events and their
    approximate token count is tracked in ``TokenUsage.thinking_tokens``.
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
        max_tokens: int = 8192,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        timeout: float = 120.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._default_model = model
        self._default_max_tokens = max_tokens
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._timeout = timeout

    def _headers(self, *, thinking_enabled: bool = False) -> dict[str, str]:
        betas = [_BETA_PROMPT_CACHING]
        if thinking_enabled:
            betas.append(_BETA_INTERLEAVED_THINKING)
        return {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
            "anthropic-beta": ",".join(betas),
            "content-type": "application/json",
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
        max_tokens: int,
        stream: bool,
        thinking: dict | None = None,
    ) -> dict:
        effective_max_tokens = max_tokens or self._default_max_tokens
        body: dict = {
            "model": model or self._default_model,
            "max_tokens": effective_max_tokens,
            "messages": _without_reasoning(messages),
            "stream": stream,
        }
        system_blocks = self._build_system(system)
        if system_blocks:
            body["system"] = system_blocks
        if tools:
            body["tools"] = tools
        if thinking is not None:
            body["thinking"] = thinking
        return body

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
        max_tokens: int,
        thinking: dict | None = None,
    ) -> AsyncIterator[StreamEvent]:
        thinking_enabled = thinking is not None
        payload = self._build_request(
            messages,
            tools=tools,
            system=system,
            model=model,
            max_tokens=max_tokens,
            stream=True,
            thinking=thinking,
        )

        async with httpx.AsyncClient(
            headers=self._headers(thinking_enabled=thinking_enabled),
            timeout=self._timeout,
        ) as client:
            response = await self._post_with_retry(client, payload, stream=True)

            if response.status_code != 200:
                body = await response.aread()
                raise LLMError(
                    f"Anthropic API error {response.status_code}: {body.decode()}",
                    status_code=response.status_code,
                )

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
                        if block.get("type") == "tool_use":
                            tool_ids[current_block_index] = block.get("id", "")
                            tool_names[current_block_index] = block.get("name", "")
                            partial_inputs[current_block_index] = {}
                        elif block.get("type") == "thinking":
                            partial_thinking[current_block_index] = ""

                    case "content_block_delta":
                        delta = event_data.get("delta", {})
                        idx = event_data.get("index", current_block_index)
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
                        # Approximate thinking tokens from accumulated char count.
                        thinking_tokens = accumulated_thinking_chars // 4
                        yield StreamEvent(
                            type=StreamEventType.MESSAGE_DONE,
                            usage=TokenUsage(
                                input_tokens=usage_data.get("input_tokens", 0),
                                output_tokens=usage_data.get("output_tokens", 0),
                                cache_read_tokens=usage_data.get("cache_read_input_tokens", 0),
                                cache_write_tokens=usage_data.get("cache_creation_input_tokens", 0),
                                thinking_tokens=thinking_tokens,
                            ),
                        )

    async def generate(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system: SystemPrompt,
        model: str,
        max_tokens: int,
        thinking: dict | None = None,
    ) -> LLMResponse:
        thinking_enabled = thinking is not None
        payload = self._build_request(
            messages,
            tools=tools,
            system=system,
            model=model,
            max_tokens=max_tokens,
            stream=False,
            thinking=thinking,
        )

        async with httpx.AsyncClient(
            headers=self._headers(thinking_enabled=thinking_enabled),
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
        reasoning = "".join(thinking_parts)
        stop_reason_raw = data.get("stop_reason", "end_turn")
        try:
            stop_reason = StopReason(stop_reason_raw)
        except ValueError:
            stop_reason = StopReason.END_TURN

        return LLMResponse(
            content=content_text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=TokenUsage(
                input_tokens=usage_data.get("input_tokens", 0),
                output_tokens=usage_data.get("output_tokens", 0),
                cache_read_tokens=usage_data.get("cache_read_input_tokens", 0),
                cache_write_tokens=usage_data.get("cache_creation_input_tokens", 0),
                thinking_tokens=len(reasoning) // 4,
            ),
            reasoning=reasoning,
        )
