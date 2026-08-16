"""Translate Anthropic Messages API requests → OpenAI Chat Completions format."""

from __future__ import annotations

import json
from typing import Any

from bifrost.translation.models import (
    AnthropicRequest,
    ContentBlock,
    Message,
    TextBlock,
    ThinkingBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)


def _content_to_openai_text(content: str | list[ContentBlock]) -> str:
    """Flatten Anthropic message content to a plain string for OpenAI."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, TextBlock):
            parts.append(block.text)
    return "".join(parts)


def _extract_tool_calls(content: list[ContentBlock]) -> list[dict[str, Any]]:
    """Extract tool_use blocks and convert to OpenAI tool_calls format."""
    tool_calls = []
    for block in content:
        if isinstance(block, ToolUseBlock):
            tool_calls.append(
                {
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": json.dumps(block.input),
                    },
                }
            )
    return tool_calls


def _message_to_openai(msg: Message) -> list[dict[str, Any]]:
    """Convert a single Anthropic message to one or more OpenAI messages.

    Tool-result blocks in a user message become separate ``tool`` role messages.
    """
    if isinstance(msg.content, str):
        return [{"role": msg.role, "content": msg.content}]

    tool_result_messages: list[dict[str, Any]] = []
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    for block in msg.content:
        if isinstance(block, ToolResultBlock):
            result_content = (
                block.content
                if isinstance(block.content, str)
                else _content_to_openai_text(block.content)
            )
            tool_result_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": block.tool_use_id,
                    "content": result_content,
                }
            )
        elif isinstance(block, ToolUseBlock):
            tool_calls.extend(_extract_tool_calls([block]))
        elif isinstance(block, TextBlock):
            text_parts.append(block.text)
        elif isinstance(block, ThinkingBlock):
            reasoning_parts.append(block.thinking)

    if tool_result_messages:
        result: list[dict[str, Any]] = []
        text = "".join(text_parts)
        if text:
            result.append({"role": msg.role, "content": text})
        result.extend(tool_result_messages)
        return result

    out: dict[str, Any] = {"role": msg.role}
    text = "".join(text_parts)
    if text:
        out["content"] = text
    if reasoning_parts:
        out["reasoning_content"] = "".join(reasoning_parts)
    if tool_calls:
        out["tool_calls"] = tool_calls
    if not text and not tool_calls:
        out["content"] = ""
    return [out]


def _tool_definition_to_openai(tool: ToolDefinition) -> dict[str, Any]:
    """Convert an Anthropic tool definition to OpenAI format."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def _tool_choice_to_openai(choice: Any) -> Any:
    """Convert Anthropic tool_choice to OpenAI format."""
    if choice is None:
        return None
    choice_type = getattr(choice, "type", None)
    if choice_type == "auto":
        return "auto"
    if choice_type == "any":
        return "required"
    if choice_type == "tool":
        return {"type": "function", "function": {"name": choice.name}}
    return "auto"


def anthropic_to_openai(request: AnthropicRequest, model: str) -> dict[str, Any]:
    """Convert an Anthropic Messages API request to OpenAI Chat Completions format.

    Args:
        request: The inbound Anthropic request.
        model: The resolved model name for the target provider.

    Returns:
        A dict ready to be JSON-serialised and sent to an OpenAI-compatible endpoint.
    """
    messages: list[dict[str, Any]] = []

    # System prompt: Anthropic top-level → OpenAI system message.
    if request.system is not None:
        if isinstance(request.system, str):
            system_text = request.system
        else:
            system_text = "".join(b.text for b in request.system)
        if system_text:
            messages.append({"role": "system", "content": system_text})

    for msg in request.messages:
        messages.extend(_message_to_openai(msg))

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": request.max_tokens,
        "stream": request.stream,
    }

    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.top_p is not None:
        payload["top_p"] = request.top_p
    if request.stop_sequences:
        payload["stop"] = request.stop_sequences
    if request.chat_template_kwargs is not None:
        payload["chat_template_kwargs"] = request.chat_template_kwargs
    if request.thinking is not None:
        payload["thinking"] = request.thinking
    if request.reasoning_effort is not None:
        payload["reasoning_effort"] = request.reasoning_effort

    if request.tools:
        payload["tools"] = [_tool_definition_to_openai(t) for t in request.tools]
        tool_choice = _tool_choice_to_openai(request.tool_choice)
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

    return payload
