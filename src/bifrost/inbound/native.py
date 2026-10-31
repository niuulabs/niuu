"""Protocol helpers for the native passthrough path.

On the passthrough path the payload and the stream are the provider's own, so
the things Bifröst still does itself have to read them in that protocol:

- **metering** — a :class:`StreamUsageReader` per protocol pulls token usage
  out of the native stream, and :func:`native_response_usage` out of a native
  response body, into the same :class:`TokenUsage` the canonical path records;
- **rules** — a ``strip_images`` rule fires on the canonical view and is
  applied to the native payload by :func:`strip_native_images`;
- **mid-stream failures** — reported in the caller's protocol rather than by
  closing an already-200 stream.

The canonical path's own reader (Anthropic SSE) lives here too, so both paths
meter through one interface.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Protocol

from bifrost.domain.models import TokenUsage
from bifrost.domain.usage import (
    add_usage,
    anthropic_compaction_usage,
    chat_completions_usage,
    responses_usage,
)
from bifrost.ports.native import NativeProtocol

logger = logging.getLogger(__name__)

IMAGE_REMOVED_TEXT = "[image removed]"

# Responses input item types the canonical translation turns into messages.
# Anything else (compaction, compaction_trigger, item_reference, …) is state
# only the provider can interpret.
CANONICAL_RESPONSES_ITEM_TYPES = frozenset(
    {"message", "function_call", "function_call_output", "reasoning"}
)

_RESPONSES_TERMINAL_EVENTS = frozenset(
    {"response.completed", "response.incomplete", "response.failed"}
)


def _data_payloads(chunk: str) -> list[dict[str, Any]]:
    """The JSON objects on the ``data:`` lines of an SSE chunk."""
    payloads: list[dict[str, Any]] = []
    for line in chunk.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            payload = json.loads(line[6:])
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


class StreamUsageReader(Protocol):
    """Reads token usage out of one protocol's SSE stream."""

    def feed(self, chunk: str, usage: TokenUsage) -> None:
        """Update *usage* in place from the events in *chunk*."""

    def is_terminal(self, chunk: str) -> bool:
        """Whether *chunk* ends the response (so usage is final)."""


class AnthropicStreamUsage:
    """Usage from Bifröst's canonical (Anthropic Messages) SSE stream."""

    def feed(self, chunk: str, usage: TokenUsage) -> None:
        for payload in _data_payloads(chunk):
            event_type = payload.get("type", "")
            if event_type == "message_start":
                msg_usage = payload.get("message", {}).get("usage", {})
                usage.input_tokens += msg_usage.get("input_tokens", 0)
                usage.cache_creation_input_tokens += msg_usage.get("cache_creation_input_tokens", 0)
                usage.cache_read_input_tokens += msg_usage.get("cache_read_input_tokens", 0)
                usage.reasoning_tokens += msg_usage.get("reasoning_tokens", 0)
            elif event_type == "message_delta":
                delta_usage = payload.get("usage", {})
                usage.input_tokens += delta_usage.get("input_tokens", 0)
                usage.output_tokens += delta_usage.get("output_tokens", 0)
                usage.reasoning_tokens += delta_usage.get("reasoning_tokens", 0)
                add_usage(usage, anthropic_compaction_usage(delta_usage))

    def is_terminal(self, chunk: str) -> bool:
        for line in chunk.splitlines():
            if line == "event: message_stop" or line == "data: [DONE]":
                return True
        return any(payload.get("type") == "message_stop" for payload in _data_payloads(chunk))


class ResponsesStreamUsage:
    """Usage from a native OpenAI Responses SSE stream (the terminal event)."""

    def feed(self, chunk: str, usage: TokenUsage) -> None:
        for payload in _data_payloads(chunk):
            if payload.get("type") not in _RESPONSES_TERMINAL_EVENTS:
                continue
            add_usage(usage, responses_usage((payload.get("response") or {}).get("usage")))

    def is_terminal(self, chunk: str) -> bool:
        return any(
            payload.get("type") in _RESPONSES_TERMINAL_EVENTS for payload in _data_payloads(chunk)
        )


class ChatCompletionsStreamUsage:
    """Usage from a native Chat Completions SSE stream (the ``include_usage`` chunk)."""

    def feed(self, chunk: str, usage: TokenUsage) -> None:
        for payload in _data_payloads(chunk):
            if isinstance(payload.get("usage"), dict):
                add_usage(usage, chat_completions_usage(payload["usage"]))

    def is_terminal(self, chunk: str) -> bool:
        return any(line.strip() == "data: [DONE]" for line in chunk.splitlines())


ANTHROPIC_STREAM_USAGE = AnthropicStreamUsage()

NATIVE_STREAM_USAGE: dict[NativeProtocol, StreamUsageReader] = {
    NativeProtocol.OPENAI_RESPONSES: ResponsesStreamUsage(),
    NativeProtocol.OPENAI_CHAT_COMPLETIONS: ChatCompletionsStreamUsage(),
}


def native_response_usage(protocol: NativeProtocol, body: dict[str, Any]) -> TokenUsage:
    """The usage of a complete native response body."""
    if protocol == NativeProtocol.OPENAI_RESPONSES:
        return responses_usage(body.get("usage"))
    return chat_completions_usage(body.get("usage"))


# ---------------------------------------------------------------------------
# Native-only features
# ---------------------------------------------------------------------------


def responses_native_only_features(body: dict[str, Any]) -> list[str]:
    """What a Responses request asks for that only the provider itself can honour.

    These cannot cross the canonical translation; a request that uses them must
    be served by a passthrough provider or refused.
    """
    features: list[str] = []
    if body.get("previous_response_id"):
        features.append("previous_response_id")
    tools = body.get("tools")
    if isinstance(tools, list):
        hosted = sorted(
            {str(tool.get("type")) for tool in tools if isinstance(tool, dict)} - {"function"}
        )
        features.extend(f"tool type {kind!r}" for kind in hosted)
    if body.get("context_management"):
        features.append("context_management")
    items = body.get("input")
    if isinstance(items, list):
        kinds = {
            item.get("type") or ("message" if "role" in item else "")
            for item in items
            if isinstance(item, dict)
        }
        features.extend(
            f"input item type {kind!r}"
            for kind in sorted(kinds - CANONICAL_RESPONSES_ITEM_TYPES)
            if kind
        )
    return features


def responses_requests_compaction(body: dict[str, Any]) -> bool:
    """Whether a Responses request asks the provider to compact its context."""
    if body.get("context_management"):
        return True
    items = body.get("input")
    return isinstance(items, list) and any(
        isinstance(item, dict) and item.get("type") == "compaction_trigger" for item in items
    )


# ---------------------------------------------------------------------------
# strip_images on a native payload
# ---------------------------------------------------------------------------


def _without_images(parts: list[Any], image_type: str, text_part: dict[str, Any]) -> list[Any]:
    kept = [
        part for part in parts if not (isinstance(part, dict) and part.get("type") == image_type)
    ]
    return kept or [text_part]


def strip_native_images(protocol: NativeProtocol, payload: dict[str, Any]) -> dict[str, Any]:
    """*payload* with every image part removed, as a ``strip_images`` rule asks.

    A message left with no content gets the same ``[image removed]`` text the
    canonical rule inserts, so the provider never receives an empty message.
    """
    if protocol == NativeProtocol.OPENAI_RESPONSES:
        items = payload.get("input")
        if not isinstance(items, list):
            return payload
        stripped = [
            {
                **item,
                "content": _without_images(
                    item["content"],
                    "input_image",
                    {"type": "input_text", "text": IMAGE_REMOVED_TEXT},
                ),
            }
            if isinstance(item, dict) and isinstance(item.get("content"), list)
            else item
            for item in items
        ]
        return {**payload, "input": stripped}
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return payload
    stripped_messages = [
        {
            **message,
            "content": _without_images(
                message["content"], "image_url", {"type": "text", "text": IMAGE_REMOVED_TEXT}
            ),
        }
        if isinstance(message, dict) and isinstance(message.get("content"), list)
        else message
        for message in messages
    ]
    return {**payload, "messages": stripped_messages}


# ---------------------------------------------------------------------------
# Mid-stream failures
# ---------------------------------------------------------------------------


async def responses_stream_errors_reported(
    source: AsyncIterator[str], *, response_id: str, model: str
) -> AsyncIterator[str]:
    """Report a failure after the 200 as a Responses ``response.failed`` event.

    The canonical Responses stream does this inside its translator; the native
    stream has no translator, so a provider that drops the connection mid-way
    would otherwise reach the client as a stream that simply stops.
    """
    try:
        async for chunk in source:
            yield chunk
    except Exception as exc:
        logger.error("Native Responses stream failed after the response started: %s", exc)
        failed = {
            "type": "response.failed",
            "response": {
                "id": response_id,
                "object": "response",
                "status": "failed",
                "model": model,
                "output": [],
                "error": {"code": "server_error", "message": str(exc)[:500]},
            },
        }
        yield f"event: response.failed\ndata: {json.dumps(failed)}\n\n"
