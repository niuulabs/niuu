"""Streaming translation: OpenAI SSE chunks → Anthropic SSE events.

Bifröst exposes an Anthropic-compatible streaming interface.  When the
upstream provider uses OpenAI-compatible SSE, we translate on the fly.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from bifrost.translation.models import FINISH_REASON_MAP


def _sse_event(event_type: str, data: dict) -> str:
    """Format a single Anthropic SSE event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


async def openai_stream_to_anthropic(
    openai_chunks: AsyncIterator[str],
    *,
    message_id: str,
    model: str,
    force_nonempty_content: bool = False,
) -> AsyncIterator[str]:
    """Translate an OpenAI SSE stream to Anthropic SSE event format.

    Args:
        openai_chunks: Raw SSE lines from an OpenAI-compatible endpoint.
        message_id: The message ID to embed in Anthropic events.
        model: The model name to report.
        force_nonempty_content: Promote reasoning to text when the provider
            finishes with only empty or whitespace text content.

    Yields:
        Anthropic-formatted SSE strings.
    """
    emitted_start = False
    active_block_type = ""
    block_index = 0
    input_tokens = 0
    output_tokens = 0  # Updated from upstream usage if available.
    stop_reason = "end_turn"
    tool_call_accumulator: dict[str, dict] = {}  # index → partial tool call
    reasoning_parts: list[str] = []
    has_non_whitespace_text = False

    async for raw_line in openai_chunks:
        line = raw_line.strip()
        if not line or not line.startswith("data: "):
            continue

        payload = line[6:]
        if payload == "[DONE]":
            break

        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue

        if not emitted_start:
            emitted_start = True
            yield _sse_event(
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
                        "usage": {"input_tokens": 0, "output_tokens": 1},
                    },
                },
            )
            yield _sse_event("ping", {"type": "ping"})

        usage = chunk.get("usage")
        if usage and isinstance(usage, dict):
            input_tokens = usage.get("prompt_tokens", input_tokens)
            output_tokens = usage.get("completion_tokens", output_tokens)

        choices = chunk.get("choices", [])
        if not choices:
            continue

        choice = choices[0]
        delta = choice.get("delta", {})
        finish_reason = choice.get("finish_reason")

        # Reasoning delta.
        reasoning = delta.get("reasoning") or delta.get("reasoning_content")
        if reasoning:
            reasoning_parts.append(reasoning)
            if active_block_type != "thinking":
                if active_block_type:
                    yield _sse_event(
                        "content_block_stop",
                        {"type": "content_block_stop", "index": block_index},
                    )
                    block_index += 1
                active_block_type = "thinking"
                yield _sse_event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": block_index,
                        "content_block": {"type": "thinking", "thinking": ""},
                    },
                )
            yield _sse_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": block_index,
                    "delta": {"type": "thinking_delta", "thinking": reasoning},
                },
            )

        # Text delta.
        text = delta.get("content")
        if text:
            has_non_whitespace_text = has_non_whitespace_text or bool(text.strip())
            if active_block_type != "text":
                if active_block_type:
                    yield _sse_event(
                        "content_block_stop",
                        {"type": "content_block_stop", "index": block_index},
                    )
                    block_index += 1
                active_block_type = "text"
                yield _sse_event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": block_index,
                        "content_block": {"type": "text", "text": ""},
                    },
                )
            yield _sse_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": block_index,
                    "delta": {"type": "text_delta", "text": text},
                },
            )

        # Tool call deltas.
        tool_calls = delta.get("tool_calls") or []
        for tc_delta in tool_calls:
            tc_index = tc_delta.get("index", 0)
            if tc_index not in tool_call_accumulator:
                tool_call_accumulator[tc_index] = {
                    "id": "",
                    "name": "",
                    "arguments": "",
                }
            acc = tool_call_accumulator[tc_index]
            fn = tc_delta.get("function", {})
            if tc_delta.get("id"):
                acc["id"] = tc_delta["id"]
            if fn.get("name"):
                acc["name"] = fn["name"]
            if fn.get("arguments"):
                acc["arguments"] += fn["arguments"]

        if finish_reason:
            stop_reason = FINISH_REASON_MAP.get(finish_reason, "end_turn")

    if (
        force_nonempty_content
        and not tool_call_accumulator
        and not has_non_whitespace_text
        and "".join(reasoning_parts).strip()
    ):
        if active_block_type:
            yield _sse_event(
                "content_block_stop",
                {"type": "content_block_stop", "index": block_index},
            )
            block_index += 1
        active_block_type = "text"
        yield _sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": block_index,
                "content_block": {"type": "text", "text": ""},
            },
        )
        yield _sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": block_index,
                "delta": {"type": "text_delta", "text": "".join(reasoning_parts)},
            },
        )

    # Flush tool call accumulator as content blocks.
    if tool_call_accumulator:
        if active_block_type:
            yield _sse_event(
                "content_block_stop",
                {"type": "content_block_stop", "index": block_index},
            )
            block_index += 1
            active_block_type = ""

        for acc in tool_call_accumulator.values():
            try:
                tool_input = json.loads(acc["arguments"] or "{}")
            except json.JSONDecodeError:
                tool_input = {"raw": acc["arguments"]}

            yield _sse_event(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": block_index,
                    "content_block": {
                        "type": "tool_use",
                        "id": acc["id"],
                        "name": acc["name"],
                        "input": {},
                    },
                },
            )
            yield _sse_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": block_index,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(tool_input),
                    },
                },
            )
            yield _sse_event(
                "content_block_stop",
                {"type": "content_block_stop", "index": block_index},
            )
            block_index += 1
        stop_reason = "tool_use"
    elif active_block_type:
        yield _sse_event(
            "content_block_stop",
            {"type": "content_block_stop", "index": block_index},
        )

    if not emitted_start:
        # Empty response — emit minimal events.
        yield _sse_event(
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

    yield _sse_event(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        },
    )
    yield _sse_event("message_stop", {"type": "message_stop"})
    yield "data: [DONE]\n\n"
