"""Streaming translation: OpenAI Responses SSE -> Anthropic SSE events.

The event shapes handled here were recorded from the ChatGPT-subscription
Codex backend (``chatgpt.com/backend-api/codex/responses``):

``response.created`` / ``response.in_progress`` open the stream;
``response.output_item.added`` / ``.done`` bracket each output item, which is a
``message``, a ``function_call`` or a ``reasoning`` item, in sequence;
``response.output_text.delta`` carries text; ``response.function_call_arguments
.delta`` / ``.done`` carry tool arguments; ``response.reasoning_summary_*``
carries the reasoning summary; ``response.metadata`` is moderation bookkeeping
and is ignored. The stream ends with ``response.completed`` (or
``response.incomplete`` when ``max_output_tokens`` cut the answer short, or
``response.failed``) carrying the usage.

Each ``reasoning`` output item becomes one Anthropic ``thinking`` block:

- its summary text (only requested when the caller asked for thinking) streams
  as ``thinking_delta``, several summary parts joined by a blank line;
- its ``encrypted_content`` is sent as a ``signature_delta`` just before the
  block stops, whether or not a summary was requested, because it is what lets a
  later request continue the same reasoning (see ``to_responses``). The value on
  ``response.output_item.done`` is the one to use: the copy on
  ``response.output_item.added`` is shorter and differs, verified live.

An item that arrives without ``encrypted_content`` is not signed; it still shows
its summary as an unsigned block when one was requested.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from bifrost.domain.usage import responses_usage
from bifrost.ports.provider import ProviderError
from bifrost.translation.models import (
    AnthropicResponse,
    ContentBlock,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    UsageInfo,
)

# Responses incomplete_details.reason -> Anthropic stop_reason.
_INCOMPLETE_STOP_REASONS: dict[str, str] = {
    "max_output_tokens": "max_tokens",
    "content_filter": "end_turn",
}

_PART_TYPES_AS_TEXT = frozenset({"output_text", "refusal"})

# Joins the summary parts of one reasoning item into a single thinking block.
_SUMMARY_PART_SEPARATOR = "\n\n"


def new_message_id() -> str:
    """A fresh Anthropic-style message id."""
    return f"msg_{uuid.uuid4().hex[:24]}"


def sse_event(event_type: str, data: dict[str, Any]) -> str:
    """Format one Anthropic SSE event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


async def iter_responses_events(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    """Parse a Responses SSE line stream into event payloads.

    Only ``data:`` lines carry information (each payload repeats its ``type``);
    ``event:`` lines, comments and blank separators are skipped.

    Raises:
        ProviderError: A ``data:`` payload is not a JSON object.
    """
    async for raw in lines:
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        body = line[len("data:") :].strip()
        if body == "[DONE]":
            return
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Responses stream sent malformed JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ProviderError("Responses stream sent a data payload that is not an object")
        yield payload


def _failure_message(payload: dict[str, Any]) -> str:
    """``code: message`` of a ``response.failed`` or ``error`` event, in either shape.

    ``response.failed`` nests the error under ``response.error``; a bare ``error``
    event carries it flat or under ``error``.
    """
    if "response" in payload:
        error = payload["response"].get("error")
    else:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else payload
    error = error or {}
    code = error.get("code") or error.get("type") or "unknown"
    message = error.get("message") or "no message"
    return f"{code}: {message}"


def _usage_of(response: dict[str, Any]) -> dict[str, int]:
    """The response's usage as Anthropic ``message_delta`` usage.

    Mapped by :func:`bifrost.domain.usage.responses_usage`, the same function
    the native passthrough path meters with, so a request costs the same on
    either path.
    """
    usage = responses_usage(response.get("usage"))
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
    }


async def responses_to_anthropic_events(
    events: AsyncIterator[dict[str, Any]],
    *,
    message_id: str,
    model: str,
    include_thinking: bool,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Translate parsed Responses events into Anthropic ``(event_type, data)`` pairs.

    Responses output items arrive one after another, so at most one Anthropic
    content block is open at a time.

    Raises:
        ProviderError: The backend reported ``response.failed`` or an ``error``
            event, sent an unknown incomplete reason, or the stream ended
            without a terminal event.
    """
    started = False
    block_index = -1
    open_kind = ""  # "", "text", "thinking" or "tool_use"
    arguments_streamed = False
    saw_tool_call = False

    def start_block(block: dict[str, Any], kind: str) -> tuple[str, dict[str, Any]]:
        nonlocal block_index, open_kind
        block_index += 1
        open_kind = kind
        return (
            "content_block_start",
            {"type": "content_block_start", "index": block_index, "content_block": block},
        )

    def stop_block() -> list[tuple[str, dict[str, Any]]]:
        nonlocal open_kind
        if not open_kind:
            return []
        open_kind = ""
        return [("content_block_stop", {"type": "content_block_stop", "index": block_index})]

    def delta(delta_body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        return (
            "content_block_delta",
            {"type": "content_block_delta", "index": block_index, "delta": delta_body},
        )

    async for event in events:
        kind = event.get("type", "")

        if kind == "response.created":
            started = True
            yield (
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": message_id,
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": model,
                        "stop_reason": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                },
            )
            continue

        if kind in ("response.failed", "error"):
            raise ProviderError(f"ChatGPT Responses backend failed: {_failure_message(event)}")

        if kind in ("response.completed", "response.incomplete"):
            response = event.get("response", {})
            for stopped in stop_block():
                yield stopped
            if kind == "response.incomplete":
                reason = (response.get("incomplete_details") or {}).get("reason", "")
                stop_reason = _INCOMPLETE_STOP_REASONS.get(reason)
                if stop_reason is None:
                    raise ProviderError(
                        "ChatGPT Responses backend ended the response early: "
                        f"{reason or 'no reason'}"
                    )
            else:
                stop_reason = "tool_use" if saw_tool_call else "end_turn"
            yield (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                    "usage": _usage_of(response),
                },
            )
            yield ("message_stop", {"type": "message_stop"})
            return

        if not started:
            raise ProviderError(f"Responses stream sent {kind!r} before response.created")

        if kind == "response.output_item.added":
            item = event.get("item", {})
            if item.get("type") == "reasoning":
                for stopped in stop_block():
                    yield stopped
            elif item.get("type") == "function_call":
                arguments_streamed = False
                saw_tool_call = True
                yield start_block(
                    {
                        "type": "tool_use",
                        "id": item.get("call_id", ""),
                        "name": item.get("name", ""),
                        "input": {},
                    },
                    "tool_use",
                )

        elif kind == "response.content_part.added":
            if event.get("part", {}).get("type") in _PART_TYPES_AS_TEXT:
                for stopped in stop_block():
                    yield stopped
                yield start_block({"type": "text", "text": ""}, "text")

        elif kind in ("response.output_text.delta", "response.refusal.delta"):
            if open_kind != "text":
                for stopped in stop_block():
                    yield stopped
                yield start_block({"type": "text", "text": ""}, "text")
            yield delta({"type": "text_delta", "text": event.get("delta", "")})

        elif kind == "response.function_call_arguments.delta":
            arguments_streamed = True
            yield delta({"type": "input_json_delta", "partial_json": event.get("delta", "")})

        elif kind == "response.function_call_arguments.done":
            if not arguments_streamed and event.get("arguments"):
                yield delta({"type": "input_json_delta", "partial_json": event["arguments"]})

        elif kind == "response.reasoning_summary_part.added":
            if include_thinking and open_kind == "thinking":
                yield delta({"type": "thinking_delta", "thinking": _SUMMARY_PART_SEPARATOR})
            elif include_thinking:
                for stopped in stop_block():
                    yield stopped
                yield start_block({"type": "thinking", "thinking": ""}, "thinking")

        elif kind == "response.reasoning_summary_text.delta":
            if include_thinking and open_kind == "thinking":
                yield delta({"type": "thinking_delta", "thinking": event.get("delta", "")})

        elif kind == "response.output_item.done":
            item = event.get("item", {})
            encrypted = item.get("encrypted_content") if item.get("type") == "reasoning" else ""
            if encrypted:
                if open_kind != "thinking":
                    for stopped in stop_block():
                        yield stopped
                    yield start_block({"type": "thinking", "thinking": ""}, "thinking")
                yield delta({"type": "signature_delta", "signature": encrypted})
            for stopped in stop_block():
                yield stopped

        elif kind == "response.content_part.done":
            for stopped in stop_block():
                yield stopped

    raise ProviderError(
        "ChatGPT Responses stream ended without response.completed; "
        "the connection was cut before the answer finished"
    )


async def responses_stream_to_anthropic(
    lines: AsyncIterator[str],
    *,
    message_id: str,
    model: str,
    include_thinking: bool,
) -> AsyncIterator[str]:
    """Translate Responses SSE lines to Anthropic SSE strings (ending in ``[DONE]``)."""
    async for event_type, data in responses_to_anthropic_events(
        iter_responses_events(lines),
        message_id=message_id,
        model=model,
        include_thinking=include_thinking,
    ):
        yield sse_event(event_type, data)
    yield "data: [DONE]\n\n"


async def responses_stream_to_message(
    lines: AsyncIterator[str],
    *,
    message_id: str,
    model: str,
    include_thinking: bool,
) -> AnthropicResponse:
    """Consume a Responses SSE stream and assemble the final Anthropic message."""
    blocks: list[ContentBlock] = []
    tool_arguments: dict[int, str] = {}
    stop_reason: str | None = None
    usage = UsageInfo()

    async for event_type, data in responses_to_anthropic_events(
        iter_responses_events(lines),
        message_id=message_id,
        model=model,
        include_thinking=include_thinking,
    ):
        if event_type == "content_block_start":
            block = data["content_block"]
            if block["type"] == "text":
                blocks.append(TextBlock(text=""))
            elif block["type"] == "thinking":
                blocks.append(ThinkingBlock(thinking=""))
            else:
                blocks.append(ToolUseBlock(id=block["id"], name=block["name"]))
                tool_arguments[data["index"]] = ""
        elif event_type == "content_block_delta":
            index, body = data["index"], data["delta"]
            if body["type"] == "text_delta":
                blocks[index].text += body["text"]  # type: ignore[union-attr]
            elif body["type"] == "thinking_delta":
                blocks[index].thinking += body["thinking"]  # type: ignore[union-attr]
            elif body["type"] == "signature_delta":
                blocks[index].signature += body["signature"]  # type: ignore[union-attr]
            else:
                tool_arguments[index] += body["partial_json"]
        elif event_type == "message_delta":
            stop_reason = data["delta"]["stop_reason"]
            usage = UsageInfo(
                input_tokens=data["usage"]["input_tokens"],
                output_tokens=data["usage"]["output_tokens"],
            )

    for index, raw in tool_arguments.items():
        blocks[index].input = _tool_input(raw)  # type: ignore[union-attr]
    return AnthropicResponse(
        id=message_id,
        content=blocks,
        model=model,
        stop_reason=stop_reason,
        usage=usage,
    )


def _tool_input(raw: str) -> dict[str, Any]:
    """Parse a function call's arguments; an unparsable payload is an error, not ``{}``."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderError(
            f"model produced tool arguments that are not valid JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ProviderError("model produced tool arguments that are not a JSON object")
    return parsed
