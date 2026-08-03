"""Translate OpenAI Chat Completions responses → Anthropic Messages API format."""

from __future__ import annotations

import json
import re
from typing import Any

from bifrost.translation.models import (
    FINISH_REASON_MAP,
    AnthropicResponse,
    ContentBlock,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    UsageInfo,
)

_THINKING_RE = re.compile(r"<thinking>(.*?)</thinking>", re.DOTALL)


def _openai_tool_calls_to_blocks(tool_calls: list[dict[str, Any]]) -> list[ContentBlock]:
    """Convert OpenAI tool_calls to Anthropic tool_use content blocks."""
    blocks: list[ContentBlock] = []
    for tc in tool_calls:
        fn = tc.get("function", {})
        arguments_raw = fn.get("arguments", "{}")
        try:
            tool_input = json.loads(arguments_raw)
        except json.JSONDecodeError:
            tool_input = {"raw": arguments_raw}
        blocks.append(
            ToolUseBlock(
                id=tc.get("id", ""),
                name=fn.get("name", ""),
                input=tool_input,
            )
        )
    return blocks


def openai_to_anthropic(response: dict[str, Any], original_model: str) -> AnthropicResponse:
    """Convert an OpenAI Chat Completions response to Anthropic Messages API format.

    Args:
        response: Parsed JSON from an OpenAI-compatible endpoint.
        original_model: The model name to report in the response (e.g. the alias).

    Returns:
        An ``AnthropicResponse`` ready to serialise back to the caller.
    """
    response_id = response.get("id", "msg_openai")
    model = response.get("model", original_model)

    choices = response.get("choices", [])
    choice = choices[0] if choices else {}
    message = choice.get("message", {})
    finish_reason = choice.get("finish_reason", "stop")

    content: list[ContentBlock] = []

    reasoning = message.get("reasoning") or message.get("reasoning_content") or ""
    if reasoning:
        content.append(ThinkingBlock(thinking=reasoning))

    # Text content — extract <thinking> tags into ThinkingBlocks.
    text = message.get("content") or ""
    if text:
        thinking_matches = _THINKING_RE.findall(text)
        for thinking_text in thinking_matches:
            content.append(ThinkingBlock(thinking=thinking_text.strip()))
        remaining = _THINKING_RE.sub("", text).strip()
        if remaining:
            content.append(TextBlock(text=remaining))

    # Tool calls.
    tool_calls = message.get("tool_calls") or []
    content.extend(_openai_tool_calls_to_blocks(tool_calls))

    # Usage.
    usage_raw = response.get("usage", {})
    usage = UsageInfo(
        input_tokens=usage_raw.get("prompt_tokens", 0),
        output_tokens=usage_raw.get("completion_tokens", 0),
    )

    stop_reason = FINISH_REASON_MAP.get(finish_reason or "stop", "end_turn")

    return AnthropicResponse(
        id=response_id,
        content=content,
        model=model,
        stop_reason=stop_reason,
        usage=usage,
    )
