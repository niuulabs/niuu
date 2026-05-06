"""OpenAICompatibleAdapter — LLMPort for OpenAI-compatible APIs.

Supports OpenAI, Azure OpenAI, DeepSeek, Ollama (/v1), and any other
server that implements the OpenAI Chat Completions API.

Features:
- Streaming via SSE (``data: {...}`` events)
- Tool calling in OpenAI function-calling format
- Token usage normalisation: ``prompt_tokens`` → ``input_tokens``,
  ``completion_tokens`` → ``output_tokens``
- Token estimation fallback when the API response does not include usage data
- Reasoning-tag stripping: ``<think>…</think>`` / ``<reasoning>…</reasoning>`` /
  ``<REASONING_SCRATCHPAD>…</REASONING_SCRATCHPAD>`` blocks are stripped from final text
- Developer role swap: models in the GPT-5/o-series/Codex family receive the
  system prompt as a ``"developer"`` role message instead of ``"system"``
- Model-specific steering injection via optional ``system_prefix`` kwarg
- Transient-error retries (429, 500, 502, 503) with exponential back-off

Tool format conversion
~~~~~~~~~~~~~~~~~~~~~~
The caller passes Anthropic-format tool dicts::

    {"name": "...", "description": "...", "input_schema": {...}}

The adapter converts these to OpenAI function-calling format::

    {"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}

System prompt
~~~~~~~~~~~~~
If ``system`` is a string it is used directly.  If it is a list of Anthropic
text blocks the text values are concatenated (cache_control entries are
ignored — OpenAI does not support prompt caching in the same way).

Developer role
~~~~~~~~~~~~~~
OpenAI's o1/o3/GPT-5/Codex model series uses a ``"developer"`` role in place of
``"system"`` for the instruction message.  The adapter detects these model
families and substitutes the role automatically so callers do not need to know.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator

import httpx

from ravn.budget import TokenEstimator
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

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503})
_DEFAULT_BASE_URL = "https://api.openai.com"

# Regex to strip reasoning tags produced by some open-source models.
# Uses a named backreference so open and close tags must match exactly.
# Handles: <think>, <reasoning>, <REASONING_SCRATCHPAD> (all case-insensitive).
_REASONING_TAG_RE = re.compile(
    r"<(?P<tag>think|reasoning|REASONING_SCRATCHPAD)>.*?</(?P=tag)>",
    re.DOTALL | re.IGNORECASE,
)

# Model name prefixes that use "developer" role instead of "system".
# Covers: o1-*, o3-*, gpt-5*, codex-*
_DEVELOPER_ROLE_PREFIXES = ("o1-", "o3-", "gpt-5", "codex-")


def _strip_reasoning_tags(text: str) -> str:
    """Remove <think>, <reasoning>, and <REASONING_SCRATCHPAD> blocks.

    Only strips leading/trailing whitespace when a tag was actually removed,
    so that partial streaming deltas (e.g. ``"Hello, "``) are not trimmed.
    Open and close tags must match exactly (backreference guard).
    """
    result = _REASONING_TAG_RE.sub("", text)
    if result == text:
        return text
    return result.strip()


def _uses_developer_role(model: str) -> bool:
    """Return True when *model* expects a ``developer`` role instead of ``system``."""
    return any(model.startswith(prefix) for prefix in _DEVELOPER_ROLE_PREFIXES)


def _convert_tools(tools: list[dict]) -> list[dict]:
    """Convert Anthropic-format tool dicts to OpenAI function-calling format."""
    converted: list[dict] = []
    for tool in tools:
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                },
            }
        )
    return converted


def _system_to_string(system: SystemPrompt) -> str:
    """Flatten system prompt to a plain string."""
    if isinstance(system, str):
        return system
    # List of Anthropic text blocks — concatenate text values.
    return "\n\n".join(block.get("text", "") for block in system if block.get("text"))


def _normalise_usage(raw: dict, *, input_text: str = "", output_text: str = "") -> TokenUsage:
    """Normalise an OpenAI usage dict to TokenUsage.

    When the API response does not include usage data (e.g. local Ollama
    models with ``stream: false``), falls back to a character-based
    token estimate using *input_text* and *output_text* if provided.
    """
    cache_read = 0
    details = raw.get("prompt_tokens_details") or {}
    if isinstance(details, dict):
        cache_read = details.get("cached_tokens", 0) or 0

    input_tokens = raw.get("prompt_tokens", 0)
    output_tokens = raw.get("completion_tokens", 0)

    # Estimation fallback: when the API sends no usage numbers, estimate from text length.
    if input_tokens == 0 and input_text:
        input_tokens = max(1, TokenEstimator.rough(input_text))
    if output_tokens == 0 and output_text:
        output_tokens = max(1, TokenEstimator.rough(output_text))

    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=0,  # OpenAI does not expose cache writes
    )


def _convert_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Convert Anthropic-format tool messages to OpenAI-format.

    Anthropic stores tool calls as ``{"type": "tool_use", ...}`` content blocks
    in assistant messages, and tool results as ``{"type": "tool_result", ...}``
    content blocks in user messages.  OpenAI expects tool calls in the
    ``tool_calls`` field of assistant messages, and tool results as separate
    ``role: "tool"`` messages.
    """
    converted: list[dict] = []
    for msg in messages:
        content = msg.get("content")

        # Plain string content — pass through unchanged.
        if not isinstance(content, list):
            converted.append(msg)
            continue

        if msg.get("role") == "assistant":
            # Split content blocks into text + tool_use entries.
            text_parts: list[str] = []
            tool_calls_out: list[dict] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_calls_out.append(
                        {
                            "id": block.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(block.get("input", {})),
                            },
                        }
                    )
                elif isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    text_parts.append(block)

            out: dict = {"role": "assistant", "content": "\n".join(text_parts) or None}
            if tool_calls_out:
                out["tool_calls"] = tool_calls_out
            converted.append(out)

        elif msg.get("role") == "user":
            # Check if content blocks are tool_result entries.
            tool_results: list[dict] = []
            text_parts_u: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_results.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    text_parts_u.append(block.get("text", ""))
                elif isinstance(block, str):
                    text_parts_u.append(block)

            if tool_results:
                # Emit each tool result as a separate role=tool message.
                for tr in tool_results:
                    converted.append(
                        {
                            "role": "tool",
                            "tool_call_id": tr.get("tool_use_id", ""),
                            "content": tr.get("content", ""),
                        }
                    )
            else:
                # Non-tool list content — join text parts.
                converted.append({"role": "user", "content": "\n".join(text_parts_u)})
        else:
            converted.append(msg)

    return converted


class OpenAICompatibleAdapter(LLMPort):
    """Calls any OpenAI-compatible Chat Completions endpoint.

    Constructor kwargs are forwarded from config via the dynamic adapter pattern.
    """

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = _DEFAULT_BASE_URL,
        model: str = "gpt-4o",
        max_tokens: int = 8192,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        timeout: float = 120.0,
        system_prefix: str = "",
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._default_model = model
        self._default_max_tokens = max_tokens
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._timeout = timeout
        self._system_prefix = system_prefix

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {
            "content-type": "application/json",
        }
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        return headers

    def _build_messages(
        self, messages: list[dict], system: SystemPrompt, model: str = ""
    ) -> list[dict]:
        """Prepend a system (or developer) message when *system* is non-empty.

        GPT-5/o1/o3/Codex models use a ``"developer"`` role for instruction
        messages.  Other models use the standard ``"system"`` role.

        Anthropic-format tool_use / tool_result content blocks are converted
        to OpenAI-format tool_calls and role=tool messages.
        """
        converted = _convert_anthropic_messages(messages)

        system_text = _system_to_string(system)
        if self._system_prefix:
            system_text = f"{self._system_prefix}\n\n{system_text}".strip()

        if not system_text:
            return converted

        role = "developer" if _uses_developer_role(model or self._default_model) else "system"
        return [{"role": role, "content": system_text}, *converted]

    def _build_request(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system: SystemPrompt,
        model: str,
        max_tokens: int,
        stream: bool,
    ) -> dict:
        effective_model = model or self._default_model
        body: dict = {
            "model": effective_model,
            "max_tokens": max_tokens or self._default_max_tokens,
            "messages": self._build_messages(messages, system, effective_model),
            "stream": stream,
        }
        if stream:
            # Request usage data in the final stream chunk.
            body["stream_options"] = {"include_usage": True}
        if tools:
            body["tools"] = _convert_tools(tools)
        return body

    async def _post_with_retry(
        self,
        client: httpx.AsyncClient,
        payload: dict,
        *,
        stream: bool,
    ) -> httpx.Response:
        url = f"{self._base_url}/v1/chat/completions"
        last_exc: Exception | None = None
        last_status_code: int | None = None
        model_name = str(payload.get("model") or self._default_model)

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

                last_status_code = response.status_code
                if stream:
                    await response.aclose()

                if attempt < self._max_retries:
                    delay = self._retry_base_delay * (2**attempt)
                    logger.warning(
                        "OpenAI-compatible API returned %s, retrying in %.1fs (attempt %d/%d)",
                        response.status_code,
                        delay,
                        attempt + 1,
                        self._max_retries,
                    )
                    await asyncio.sleep(delay)
                    last_exc = LLMError(
                        f"OpenAI-compatible API error {response.status_code}",
                        status_code=response.status_code,
                    )

            except httpx.TransportError as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    delay = self._retry_base_delay * (2**attempt)
                    await asyncio.sleep(delay)

        details = [
            f"model={model_name}",
            f"endpoint={url}",
        ]
        if last_status_code is not None:
            details.append(f"status={last_status_code}")
        raise LLMError(
            "OpenAI-compatible API failed after "
            f"{self._max_retries} retries ({', '.join(details)})",
            status_code=last_status_code,
        ) from last_exc

    # ------------------------------------------------------------------
    # LLMPort implementation
    # ------------------------------------------------------------------

    async def stream(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system: SystemPrompt,
        model: str,
        max_tokens: int,
        thinking: dict | None = None,  # noqa: ARG002 — Anthropic-only, ignored here
    ) -> AsyncIterator[StreamEvent]:
        payload = self._build_request(
            messages,
            tools=tools,
            system=system,
            model=model,
            max_tokens=max_tokens,
            stream=True,
        )

        async with httpx.AsyncClient(headers=self._headers(), timeout=self._timeout) as client:
            response = await self._post_with_retry(client, payload, stream=True)

            if response.status_code != 200:
                body = await response.aread()
                raise LLMError(
                    f"OpenAI-compatible API error {response.status_code}: {body.decode()}",
                    status_code=response.status_code,
                )

            # Accumulate partial tool arguments keyed by tool index.
            partial_args: dict[int, str] = {}
            tool_ids: dict[int, str] = {}
            tool_names: dict[int, str] = {}

            # Accumulate output text for estimation fallback when usage is absent.
            accumulated_text: list[str] = []
            usage_emitted = False

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

                # Usage-only chunk (stream_options: include_usage)
                usage_raw = event_data.get("usage")
                if usage_raw:
                    usage_emitted = True
                    yield StreamEvent(
                        type=StreamEventType.MESSAGE_DONE,
                        usage=_normalise_usage(usage_raw),
                    )
                    continue

                choices = event_data.get("choices") or []
                if not choices:
                    continue

                choice = choices[0]
                delta = choice.get("delta") or {}
                finish_reason = choice.get("finish_reason")

                # Text content delta.
                content = delta.get("content")
                if content:
                    cleaned = _strip_reasoning_tags(content)
                    if cleaned:
                        accumulated_text.append(cleaned)
                        yield StreamEvent(type=StreamEventType.TEXT_DELTA, text=cleaned)

                # Tool call deltas.
                for tc_delta in delta.get("tool_calls") or []:
                    idx = tc_delta.get("index", 0)
                    tc_id = tc_delta.get("id", "")
                    func = tc_delta.get("function") or {}

                    if tc_id:
                        tool_ids[idx] = tc_id
                    if func.get("name"):
                        tool_names[idx] = func["name"]
                    partial_args.setdefault(idx, "")
                    partial_args[idx] += func.get("arguments", "")

                # When a tool call finishes, emit the complete TOOL_CALL event.
                if finish_reason == "tool_calls":
                    for idx in sorted(partial_args):
                        raw_args = partial_args.get(idx, "")
                        try:
                            parsed = json.loads(raw_args) if raw_args else {}
                        except json.JSONDecodeError:
                            parsed = {}
                        yield StreamEvent(
                            type=StreamEventType.TOOL_CALL,
                            tool_call=ToolCall(
                                id=tool_ids.get(idx, ""),
                                name=tool_names.get(idx, ""),
                                input=parsed,
                            ),
                        )

            # Emit a MESSAGE_DONE with estimated usage when the API did not send one.
            if not usage_emitted:
                input_text = (
                    _system_to_string(system)
                    + " "
                    + " ".join(str(m.get("content", "")) for m in messages)
                )
                output_text = "".join(accumulated_text)
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_DONE,
                    usage=_normalise_usage({}, input_text=input_text, output_text=output_text),
                )

    async def generate(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system: SystemPrompt,
        model: str,
        max_tokens: int,
        thinking: dict | None = None,  # noqa: ARG002 — Anthropic-only, ignored here
    ) -> LLMResponse:
        payload = self._build_request(
            messages,
            tools=tools,
            system=system,
            model=model,
            max_tokens=max_tokens,
            stream=False,
        )

        async with httpx.AsyncClient(headers=self._headers(), timeout=self._timeout) as client:
            response = await self._post_with_retry(client, payload, stream=False)

        if response.status_code != 200:
            raise LLMError(
                f"OpenAI-compatible API error {response.status_code}: {response.text}",
                status_code=response.status_code,
            )

        data = response.json()
        choices = data.get("choices") or []
        message = choices[0].get("message", {}) if choices else {}
        finish_reason = choices[0].get("finish_reason", "stop") if choices else "stop"

        content_text = _strip_reasoning_tags(message.get("content") or "")
        tool_calls: list[ToolCall] = []

        # First, check for API-returned tool calls
        for tc in message.get("tool_calls") or []:
            func = tc.get("function") or {}
            raw_args = func.get("arguments", "")
            try:
                parsed_args = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                parsed_args = {}
            tool_calls.append(
                ToolCall(
                    id=tc.get("id", ""),
                    name=func.get("name", ""),
                    input=parsed_args,
                )
            )

        stop_reason = StopReason.TOOL_USE if tool_calls else StopReason.END_TURN
        if finish_reason == "length":
            stop_reason = StopReason.MAX_TOKENS

        # Build an approximate input text for estimation fallback.
        input_text = (
            _system_to_string(system) + " " + " ".join(str(m.get("content", "")) for m in messages)
        )
        usage = _normalise_usage(
            data.get("usage") or {},
            input_text=input_text,
            output_text=content_text,
        )

        return LLMResponse(
            content=content_text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=usage,
        )
