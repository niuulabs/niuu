"""OpenAI Chat Completions inbound interface for Bifröst.

Accepts POST /v1/chat/completions in OpenAI format, normalises the request
to the internal Anthropic canonical form, routes via the shared ModelRouter,
then translates the response (and SSE stream) back to OpenAI format.

This enables any OpenAI-SDK-based tool (Continue, Cursor, openai-python,
etc.) to point at Bifröst without code changes.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from bifrost.translation.models import (
    STOP_REASON_TO_OPENAI,
    AnthropicRequest,
    AnthropicResponse,
    ContentBlock,
    Message,
    TextBlock,
    ThinkingBlock,
    ToolChoiceAny,
    ToolChoiceAuto,
    ToolChoiceTool,
    ToolDefinition,
    ToolResultBlock,
)
from bifrost.translation.to_anthropic import _openai_tool_calls_to_blocks
from bifrost.translation.to_openai import _extract_tool_calls

# ---------------------------------------------------------------------------
# OpenAI request Pydantic models
# ---------------------------------------------------------------------------


class _OpenAIFunction(BaseModel):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class _OpenAITool(BaseModel):
    type: Literal["function"] = "function"
    function: _OpenAIFunction


class _OpenAIToolCall(BaseModel):
    id: str = ""
    type: str = "function"
    function: dict[str, Any] = Field(default_factory=dict)


class _OpenAIMessage(BaseModel):
    role: str
    content: str | list[dict[str, Any]] | None = None
    reasoning: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[_OpenAIToolCall] | None = None
    tool_call_id: str | None = None


class OpenAIChatRequest(BaseModel):
    """Pydantic model for an OpenAI Chat Completions request."""

    model: str
    messages: list[_OpenAIMessage]
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: str | list[str] | None = None
    stream: bool = False
    tools: list[_OpenAITool] | None = None
    tool_choice: str | dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    chat_template_kwargs: dict[str, Any] | None = None
    # DeepSeek-dialect thinking controls (deepseek-harness, deepseek-python).
    # Threaded through the canonical request instead of being silently dropped;
    # backends that do not understand them decide loudly for themselves.
    thinking: dict[str, Any] | None = None
    reasoning_effort: str | None = None


# ---------------------------------------------------------------------------
# Error helper: produce OpenAI-shaped error responses
# ---------------------------------------------------------------------------


def openai_error_response(status: int, message: str, error_type: str) -> JSONResponse:
    """Return a JSONResponse whose body matches the OpenAI error schema.

    OpenAI SDK clients expect ``{"error": {"message": ..., "type": ..., ...}}``.
    """
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": error_type, "param": None, "code": None}},
    )


# ---------------------------------------------------------------------------
# Request translation: OpenAI → Anthropic canonical
# ---------------------------------------------------------------------------


def _extract_text_content(content: str | list[dict[str, Any]] | None) -> str:
    """Flatten an OpenAI message content value to a plain string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for part in content:
        if part.get("type") == "text":
            parts.append(part.get("text", ""))
    return "".join(parts)


def _build_assistant_content(msg: _OpenAIMessage) -> str | list[ContentBlock]:
    """Build Anthropic content for an assistant-role message."""
    text = _extract_text_content(msg.content)
    reasoning = msg.reasoning or msg.reasoning_content or ""
    tool_calls = msg.tool_calls or []

    if not reasoning and not text and not tool_calls:
        return ""

    if not reasoning and not tool_calls:
        return text

    blocks: list[ContentBlock] = []
    if reasoning:
        blocks.append(ThinkingBlock(thinking=reasoning))
    if text:
        blocks.append(TextBlock(text=text))

    tool_call_dicts = [{"id": tc.id, "function": tc.function} for tc in tool_calls]
    blocks.extend(_openai_tool_calls_to_blocks(tool_call_dicts))

    return blocks


def _openai_tool_choice_to_anthropic(
    tool_choice: str | dict[str, Any] | None,
) -> ToolChoiceAuto | ToolChoiceAny | ToolChoiceTool | None:
    """Translate an OpenAI tool_choice value to the Anthropic equivalent."""
    if tool_choice is None or tool_choice == "none":
        return None
    if tool_choice == "auto":
        return ToolChoiceAuto()
    if tool_choice == "required":
        return ToolChoiceAny()
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        name = tool_choice.get("function", {}).get("name", "")
        return ToolChoiceTool(name=name)
    return ToolChoiceAuto()


def openai_request_to_anthropic(req: OpenAIChatRequest) -> AnthropicRequest:
    """Convert an OpenAI Chat Completions request to Anthropic canonical format.

    Args:
        req: Parsed OpenAI Chat Completions request.

    Returns:
        An ``AnthropicRequest`` ready for the routing layer.
    """
    system_parts: list[str] = []
    anthropic_messages: list[Message] = []
    pending_tool_results: list[ToolResultBlock] = []

    for msg in req.messages:
        if msg.role == "system":
            text = _extract_text_content(msg.content)
            if text:
                system_parts.append(text)
            continue

        # Flush accumulated tool results when a non-tool message appears.
        if pending_tool_results and msg.role != "tool":
            anthropic_messages.append(Message(role="user", content=list(pending_tool_results)))
            pending_tool_results = []

        if msg.role == "tool":
            content_text = _extract_text_content(msg.content)
            pending_tool_results.append(
                ToolResultBlock(
                    tool_use_id=msg.tool_call_id or "",
                    content=content_text,
                )
            )
            continue

        if msg.role == "user":
            anthropic_messages.append(
                Message(role="user", content=_extract_text_content(msg.content))
            )
            continue

        if msg.role == "assistant":
            content = _build_assistant_content(msg)
            anthropic_messages.append(Message(role="assistant", content=content))

    # Flush any remaining tool results at end of message list.
    if pending_tool_results:
        anthropic_messages.append(Message(role="user", content=list(pending_tool_results)))

    system: str | None = "\n\n".join(system_parts) if system_parts else None

    # Tool definitions.
    tools: list[ToolDefinition] | None = None
    if req.tools and req.tool_choice != "none":
        tools = [
            ToolDefinition(
                name=t.function.name,
                description=t.function.description,
                input_schema=t.function.parameters,
            )
            for t in req.tools
        ]

    # Tool choice — only meaningful when tools are present.
    tool_choice = _openai_tool_choice_to_anthropic(req.tool_choice) if tools else None

    # Stop sequences.
    stop_sequences: list[str] | None = None
    if req.stop:
        stop_sequences = [req.stop] if isinstance(req.stop, str) else list(req.stop)

    return AnthropicRequest(
        model=req.model,
        max_tokens=req.max_tokens or 1024,
        messages=anthropic_messages,
        system=system,
        tools=tools,
        tool_choice=tool_choice,
        temperature=req.temperature,
        top_p=req.top_p,
        stop_sequences=stop_sequences,
        stream=req.stream,
        chat_template_kwargs=req.chat_template_kwargs,
        thinking=req.thinking,
        reasoning_effort=req.reasoning_effort,
    )


# ---------------------------------------------------------------------------
# Response translation: Anthropic canonical → OpenAI
# ---------------------------------------------------------------------------


def anthropic_response_to_openai(response: AnthropicResponse) -> dict[str, Any]:
    """Convert an Anthropic Messages API response to OpenAI Chat Completions format.

    Args:
        response: The Anthropic canonical response from the routing layer.

    Returns:
        A dict ready to be JSON-serialised and returned to the caller.
    """
    text = "".join(block.text for block in response.content if isinstance(block, TextBlock))
    reasoning = "".join(
        block.thinking for block in response.content if isinstance(block, ThinkingBlock)
    )
    tool_calls = _extract_tool_calls(response.content)
    finish_reason = STOP_REASON_TO_OPENAI.get(response.stop_reason or "end_turn", "stop")

    message: dict[str, Any] = {"role": "assistant", "content": text or None}
    if reasoning:
        message["reasoning_content"] = reasoning
    if tool_calls:
        message["tool_calls"] = tool_calls

    return {
        "id": response.id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": response.model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
                "logprobs": None,
            }
        ],
        "usage": {
            "prompt_tokens": response.usage.input_tokens,
            "completion_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
        },
    }


# ---------------------------------------------------------------------------
# Streaming translation: Anthropic SSE → OpenAI delta SSE
# ---------------------------------------------------------------------------


def _openai_chunk(
    message_id: str,
    model: str,
    created: int,
    delta: dict[str, Any],
    finish_reason: str | None = None,
    usage: dict[str, Any] | None = None,
) -> str:
    """Format a single OpenAI SSE chunk."""
    payload: dict[str, Any] = {
        "id": message_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
                "logprobs": None,
            }
        ],
    }
    if usage is not None:
        payload["usage"] = usage
    return f"data: {json.dumps(payload)}\n\n"


async def anthropic_stream_to_openai(
    source: AsyncIterator[str],
    *,
    message_id: str,
    model: str,
) -> AsyncIterator[str]:
    """Translate a Bifröst Anthropic SSE stream to OpenAI delta SSE format.

    The source is the output of ``router.stream()``, which may produce either
    individual SSE field lines (AnthropicAdapter) or complete multi-line SSE
    event strings (OpenAICompatAdapter via ``openai_stream_to_anthropic``).
    Both formats are handled by splitting on newlines and buffering the current
    event type.

    Args:
        source: Async iterator of raw SSE strings from the routing layer.
        message_id: ID to embed in every chunk (taken from the ``message_start``
            event if available, otherwise the caller-supplied fallback).
        model: Model name to embed in every chunk.

    Yields:
        OpenAI-formatted SSE strings (``data: {...}\\n\\n`` and ``data: [DONE]\\n\\n``).
    """
    created = int(time.time())
    current_event_type = ""
    input_tokens = 0
    output_tokens = 0
    emitted_role = False

    # Anthropic block-index → OpenAI tool_call index mapping.
    tool_block_index_map: dict[int, int] = {}
    tool_call_counter = 0

    async for raw in source:
        for raw_line in raw.split("\n"):
            line = raw_line.strip()

            if not line:
                current_event_type = ""
                continue

            if line.startswith("event: "):
                current_event_type = line[7:]
                continue

            if not line.startswith("data: "):
                continue

            payload_str = line[6:]

            if payload_str == "[DONE]":
                yield "data: [DONE]\n\n"
                return

            try:
                payload = json.loads(payload_str)
            except json.JSONDecodeError:
                continue

            event_type = current_event_type or payload.get("type", "")

            if event_type in ("ping",):
                continue

            if event_type == "message_start":
                msg = payload.get("message", {})
                # Use the actual message ID from the upstream event when available.
                upstream_id = msg.get("id")
                if upstream_id:
                    message_id = upstream_id
                usage = msg.get("usage", {})
                input_tokens = usage.get("input_tokens", 0)

                if not emitted_role:
                    emitted_role = True
                    yield _openai_chunk(
                        message_id,
                        model,
                        created,
                        {"role": "assistant", "content": ""},
                    )

            elif event_type == "content_block_start":
                index = payload.get("index", 0)
                cb = payload.get("content_block", {})
                cb_type = cb.get("type", "text")

                if cb_type == "tool_use":
                    tc_index = tool_call_counter
                    tool_block_index_map[index] = tc_index
                    tool_call_counter += 1

                    yield _openai_chunk(
                        message_id,
                        model,
                        created,
                        {
                            "tool_calls": [
                                {
                                    "index": tc_index,
                                    "id": cb.get("id", ""),
                                    "type": "function",
                                    "function": {
                                        "name": cb.get("name", ""),
                                        "arguments": "",
                                    },
                                }
                            ]
                        },
                    )

            elif event_type == "content_block_delta":
                index = payload.get("index", 0)
                delta = payload.get("delta", {})
                delta_type = delta.get("type", "")

                if delta_type == "text_delta":
                    text = delta.get("text", "")
                    yield _openai_chunk(
                        message_id,
                        model,
                        created,
                        {"content": text},
                    )

                elif delta_type == "thinking_delta":
                    reasoning = delta.get("thinking", "")
                    yield _openai_chunk(
                        message_id,
                        model,
                        created,
                        {"reasoning_content": reasoning},
                    )

                elif delta_type == "input_json_delta":
                    partial_json = delta.get("partial_json", "")
                    tc_index = tool_block_index_map.get(index, 0)
                    yield _openai_chunk(
                        message_id,
                        model,
                        created,
                        {
                            "tool_calls": [
                                {
                                    "index": tc_index,
                                    "function": {"arguments": partial_json},
                                }
                            ]
                        },
                    )

            elif event_type == "message_delta":
                delta = payload.get("delta", {})
                stop_reason = delta.get("stop_reason", "end_turn")
                usage = payload.get("usage", {})
                # OpenAI-compat backends only learn usage at stream end, so the
                # translated message_start carried input_tokens=0; the final
                # message_delta carries the real count for both directions.
                input_tokens = usage.get("input_tokens", input_tokens)
                output_tokens = usage.get("output_tokens", output_tokens)
                finish_reason = STOP_REASON_TO_OPENAI.get(stop_reason, "stop")

                yield _openai_chunk(
                    message_id,
                    model,
                    created,
                    {},
                    finish_reason=finish_reason,
                    usage={
                        "prompt_tokens": input_tokens,
                        "completion_tokens": output_tokens,
                        "total_tokens": input_tokens + output_tokens,
                    },
                )

            elif event_type == "message_stop":
                yield "data: [DONE]\n\n"
                return

    # Fallback: if stream ended without message_stop, emit [DONE].
    yield "data: [DONE]\n\n"
