"""Translate Anthropic Messages API requests -> OpenAI Responses API requests.

This is the outbound direction of the Responses API (the inbound direction
lives in ``bifrost.inbound.responses``). It targets the ChatGPT-subscription
Codex backend, whose behaviour was verified live:

- ``stream`` must be ``true`` (the backend refuses ``false``);
- ``temperature`` is refused ("Unsupported parameter"), so sampling hints
  (``temperature``, ``top_p``, ``top_k``) are not forwarded;
- ``max_output_tokens`` is honoured and ends a cut-off answer with
  ``response.incomplete``;
- ``store`` is sent as ``false``: nothing is kept server-side, so every
  request carries its whole conversation;
- ``include: ["reasoning.encrypted_content"]`` makes the backend return each
  reasoning item's opaque ``encrypted_content``, which is the only way to carry
  reasoning from one request to the next when nothing is stored.

Reasoning is replayed through the Anthropic ``thinking`` block's ``signature``:
an assistant ``thinking`` block that has one becomes a ``reasoning`` input item
(``summary`` from the block's text, ``encrypted_content`` from the signature)
in the block's position, so each step continues its own reasoning instead of
re-deriving it from the visible history. The backend requires ``type``,
``summary`` (an array, possibly empty; omitting it is a 400) and
``encrypted_content`` on such an item; ``id`` is neither needed nor sent. A
``thinking`` block without a signature has nothing the backend can resume and is
dropped.
"""

from __future__ import annotations

import json
from typing import Any

from bifrost.ports.provider import ProviderError
from bifrost.translation.models import (
    AnthropicRequest,
    ContentBlock,
    ImageBlock,
    Message,
    TextBlock,
    ThinkingBlock,
    ToolChoice,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)

# What the Responses request asks the backend to add to its output; the one
# value needed to receive reasoning that can be replayed with ``store: false``.
REASONING_ENCRYPTED_INCLUDE = "reasoning.encrypted_content"


class ResponsesTranslationError(ProviderError):
    """The request uses a feature the Responses API cannot express.

    The message names the feature. It is refused rather than dropped so a
    caller never receives an answer to a different request than it sent.
    """


def _join_text(parts: list[str]) -> str:
    return "\n\n".join(part for part in parts if part)


def _system_text(request: AnthropicRequest) -> str:
    system = request.system
    if system is None:
        return ""
    if isinstance(system, str):
        return system
    return _join_text([block.text for block in system])


def _image_part(block: ImageBlock) -> dict[str, Any]:
    source = block.source
    if source.type == "url":
        if not source.url:
            raise ResponsesTranslationError("image block of type 'url' has no url")
        return {"type": "input_image", "image_url": source.url}
    if not source.data or not source.media_type:
        raise ResponsesTranslationError("image block of type 'base64' needs media_type and data")
    return {"type": "input_image", "image_url": f"data:{source.media_type};base64,{source.data}"}


def _tool_result_output(block: ToolResultBlock) -> str:
    if isinstance(block.content, str):
        return block.content
    return _join_text([part.text for part in block.content])


def _message_item(role: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "message", "role": role, "content": parts}


def _user_items(content: str | list[ContentBlock]) -> list[dict[str, Any]]:
    """A user turn as Responses input items, keeping the blocks' order.

    ``tool_result`` blocks become ``function_call_output`` items; text and
    images between them are grouped into ``message`` items.
    """
    if isinstance(content, str):
        return [_message_item("user", [{"type": "input_text", "text": content}])] if content else []

    items: list[dict[str, Any]] = []
    parts: list[dict[str, Any]] = []

    def flush() -> None:
        if parts:
            items.append(_message_item("user", list(parts)))
            parts.clear()

    for block in content:
        if isinstance(block, TextBlock):
            if block.text:
                parts.append({"type": "input_text", "text": block.text})
        elif isinstance(block, ImageBlock):
            parts.append(_image_part(block))
        elif isinstance(block, ToolResultBlock):
            flush()
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": block.tool_use_id,
                    "output": _tool_result_output(block),
                }
            )
        elif isinstance(block, ThinkingBlock | ToolUseBlock):
            raise ResponsesTranslationError(
                f"a user message cannot carry a {block.type!r} block for the Responses API"
            )
    flush()
    return items


def _reasoning_item(block: ThinkingBlock) -> dict[str, Any]:
    """A signed ``thinking`` block as a replayable Responses ``reasoning`` item."""
    summary = [{"type": "summary_text", "text": block.thinking}] if block.thinking else []
    return {"type": "reasoning", "summary": summary, "encrypted_content": block.signature}


def _assistant_items(content: str | list[ContentBlock]) -> list[dict[str, Any]]:
    """An assistant turn as Responses input items.

    Text becomes ``message`` items, ``tool_use`` becomes ``function_call`` and a
    signed ``thinking`` block becomes a ``reasoning`` item, all in block order.
    An unsigned ``thinking`` block is dropped: the backend cannot resume it.
    """
    if isinstance(content, str):
        return (
            [_message_item("assistant", [{"type": "output_text", "text": content}])]
            if content
            else []
        )

    items: list[dict[str, Any]] = []
    parts: list[dict[str, Any]] = []

    def flush() -> None:
        if parts:
            items.append(_message_item("assistant", list(parts)))
            parts.clear()

    for block in content:
        if isinstance(block, TextBlock):
            if block.text:
                parts.append({"type": "output_text", "text": block.text})
        elif isinstance(block, ToolUseBlock):
            flush()
            items.append(
                {
                    "type": "function_call",
                    "call_id": block.id,
                    "name": block.name,
                    "arguments": json.dumps(block.input),
                }
            )
        elif isinstance(block, ThinkingBlock):
            if not block.signature:
                continue
            flush()
            items.append(_reasoning_item(block))
        else:
            raise ResponsesTranslationError(
                f"an assistant message cannot carry a {block.type!r} block for the Responses API"
            )
    flush()
    return items


def _message_items(message: Message) -> list[dict[str, Any]]:
    if message.role == "user":
        return _user_items(message.content)
    return _assistant_items(message.content)


def _tool_definition(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.input_schema or {"type": "object", "properties": {}},
        "strict": False,
    }


def _tool_choice(choice: ToolChoice | None) -> str | dict[str, Any]:
    if choice is None or choice.type == "auto":
        return "auto"
    if choice.type == "any":
        return "required"
    return {"type": "function", "name": choice.name}


def _text_format(response_format: dict[str, Any]) -> dict[str, Any]:
    """The Responses ``text.format`` for a chat-completions ``response_format``."""
    if response_format.get("type") != "json_schema":
        raise ResponsesTranslationError(
            f"response_format type {response_format.get('type')!r} is not supported; "
            "only 'json_schema' is"
        )
    schema = response_format.get("json_schema") or {}
    if not schema.get("name") or not isinstance(schema.get("schema"), dict):
        raise ResponsesTranslationError(
            "response_format json_schema needs a 'name' and a 'schema' object"
        )
    return {
        "type": "json_schema",
        "name": schema["name"],
        "schema": schema["schema"],
        "strict": bool(schema.get("strict", True)),
    }


def _reasoning(
    request: AnthropicRequest, *, default_effort: str, summary: str, wants_summary: bool
) -> dict[str, str] | None:
    reasoning: dict[str, str] = {}
    effort = request.reasoning_effort or default_effort
    if effort:
        reasoning["effort"] = effort
    if wants_summary and summary:
        reasoning["summary"] = summary
    return reasoning or None


def thinking_requested(request: AnthropicRequest) -> bool:
    """Whether the caller asked for thinking (so reasoning summaries are wanted)."""
    thinking = request.thinking
    return bool(thinking) and thinking.get("type") != "disabled"


def anthropic_to_responses(
    request: AnthropicRequest,
    model: str,
    *,
    default_reasoning_effort: str = "",
    reasoning_summary: str = "auto",
    parallel_tool_calls: bool = True,
) -> dict[str, Any]:
    """Convert an Anthropic request to a streaming Responses API request body.

    Args:
        request: The inbound Anthropic request.
        model: The model name at the target provider.
        default_reasoning_effort: Effort used when the request names none;
            empty leaves the choice to the backend.
        reasoning_summary: Summary mode requested when the caller enabled
            thinking (``auto`` / ``concise`` / ``detailed``); empty disables it.
        parallel_tool_calls: Whether the model may issue several tool calls in
            one turn.

    Raises:
        ResponsesTranslationError: The request carries something the Responses
            API cannot express (stop sequences, an unsupported block, an
            unsupported ``response_format``).
    """
    if request.stop_sequences:
        raise ResponsesTranslationError(
            "stop_sequences are not supported by the ChatGPT Responses backend; "
            "remove them from the request"
        )

    items: list[dict[str, Any]] = []
    for message in request.messages:
        items.extend(_message_items(message))

    payload: dict[str, Any] = {
        "model": model,
        "input": items,
        "store": False,
        "stream": True,
        "include": [REASONING_ENCRYPTED_INCLUDE],
    }
    if request.max_tokens is not None:
        payload["max_output_tokens"] = request.max_tokens
    instructions = _system_text(request)
    if instructions:
        payload["instructions"] = instructions

    if request.tools:
        payload["tools"] = [_tool_definition(tool) for tool in request.tools]
        payload["tool_choice"] = _tool_choice(request.tool_choice)
        payload["parallel_tool_calls"] = parallel_tool_calls

    reasoning = _reasoning(
        request,
        default_effort=default_reasoning_effort,
        summary=reasoning_summary,
        wants_summary=thinking_requested(request),
    )
    if reasoning:
        payload["reasoning"] = reasoning

    if request._response_format is not None:
        payload["text"] = {"format": _text_format(request._response_format)}
    return payload
