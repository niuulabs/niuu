"""OpenAI Responses API inbound interface for Bifröst.

Accepts ``POST /v1/responses`` in the OpenAI Responses format (what the Codex
CLI speaks; its chat-completions wire format was retired), normalises the
request to the internal Anthropic canonical form, and translates the
response and the SSE stream back to Responses events.

The gateway is stateless: every request must carry its full ``input``.
``previous_response_id`` is refused rather than silently ignored, since a
client relying on it would otherwise lose its conversation without noticing.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bifrost.translation.models import (
    AnthropicRequest,
    AnthropicResponse,
    ContentBlock,
    ImageBlock,
    ImageSource,
    Message,
    TextBlock,
    ToolChoice,
    ToolChoiceAny,
    ToolChoiceAuto,
    ToolChoiceTool,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)

logger = logging.getLogger(__name__)

# Responses clients usually leave the output budget to the server; coding
# agents need room for a patch, so the default is wider than chat's.
DEFAULT_MAX_OUTPUT_TOKENS = 4096

# Anthropic stop reasons → Responses status / incomplete reason.
_INCOMPLETE_REASONS: dict[str, str] = {"max_tokens": "max_output_tokens"}


class ResponsesRequest(BaseModel):
    """The subset of the Responses API request the gateway translates.

    Unknown fields (``include``, ``prompt_cache_key``, ``text``, ``store`` …)
    are accepted and ignored: they steer OpenAI-side behaviour the gateway
    does not have.
    """

    model_config = ConfigDict(extra="allow")

    model: str
    input: str | list[dict[str, Any]] = ""
    instructions: str | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    stream: bool = False
    max_output_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    reasoning: dict[str, Any] | None = None
    previous_response_id: str | None = None
    metadata: dict[str, Any] | None = Field(default=None)


class UnsupportedResponsesInputError(ValueError):
    """The request uses a Responses feature the gateway cannot honour."""


# ---------------------------------------------------------------------------
# Request translation: Responses → Anthropic canonical
# ---------------------------------------------------------------------------


def _text_of(content: Any) -> str:
    """The text of a message content: a string, or the text parts of a list."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if isinstance(part, dict) and part.get("type") in ("input_text", "output_text", "text"):
            parts.append(str(part.get("text", "")))
    return "".join(parts)


def _image_block(part: dict[str, Any]) -> ImageBlock:
    """An ``input_image`` part as an Anthropic image block (data URL or URL)."""
    url = str(part.get("image_url") or "")
    if url.startswith("data:"):
        header, _, data = url.partition(",")
        media_type = header[len("data:") :].split(";", 1)[0] or "image/png"
        # Validate the payload once here; a bad data URL fails the request, not the model.
        base64.b64decode(data, validate=True)
        return ImageBlock(source=ImageSource(type="base64", media_type=media_type, data=data))
    if url.startswith(("http://", "https://")):
        return ImageBlock(source=ImageSource(type="url", url=url))
    raise UnsupportedResponsesInputError("input_image needs a data: or http(s) image_url")


def _user_blocks(content: Any) -> list[ContentBlock]:
    """A user message's content parts as Anthropic blocks."""
    if isinstance(content, str):
        return [TextBlock(text=content)]
    blocks: list[ContentBlock] = []
    for part in content if isinstance(content, list) else []:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind in ("input_text", "text"):
            blocks.append(TextBlock(text=str(part.get("text", ""))))
        elif kind == "input_image":
            blocks.append(_image_block(part))
        else:
            raise UnsupportedResponsesInputError(f"unsupported user content part type {kind!r}")
    return blocks


def _tool_input(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    text = str(arguments or "")
    try:
        parsed = json.loads(text) if text else {}
    except json.JSONDecodeError:
        return {"raw": text}
    return parsed if isinstance(parsed, dict) else {"raw": text}


class _Conversation:
    """Builds the alternating Anthropic message list from Responses items."""

    def __init__(self) -> None:
        self.messages: list[Message] = []

    def _append(self, role: str, blocks: list[ContentBlock]) -> None:
        if not blocks:
            return
        last = self.messages[-1] if self.messages else None
        if last is not None and last.role == role and isinstance(last.content, list):
            last.content.extend(blocks)
            return
        self.messages.append(Message(role=role, content=blocks))  # type: ignore[arg-type]

    def user(self, blocks: list[ContentBlock]) -> None:
        self._append("user", blocks)

    def assistant(self, blocks: list[ContentBlock]) -> None:
        self._append("assistant", blocks)


def responses_request_to_anthropic(req: ResponsesRequest) -> AnthropicRequest:
    """Convert a Responses API request to the Anthropic canonical format."""
    if req.previous_response_id:
        raise UnsupportedResponsesInputError(
            "previous_response_id is not supported: the gateway keeps no response state; "
            "send the full conversation in input"
        )

    system_parts: list[str] = []
    if req.instructions:
        system_parts.append(req.instructions)
    conversation = _Conversation()

    items: list[dict[str, Any]]
    if isinstance(req.input, str):
        items = [{"type": "message", "role": "user", "content": req.input}]
    else:
        items = req.input

    for item in items:
        if not isinstance(item, dict):
            raise UnsupportedResponsesInputError(
                f"input items must be objects, got {type(item).__name__}"
            )
        kind = item.get("type") or ("message" if "role" in item else "")
        if kind == "message":
            role = item.get("role", "user")
            if role in ("system", "developer"):
                text = _text_of(item.get("content"))
                if text:
                    system_parts.append(text)
            elif role == "user":
                conversation.user(_user_blocks(item.get("content")))
            elif role == "assistant":
                text = _text_of(item.get("content"))
                conversation.assistant([TextBlock(text=text)] if text else [])
            else:
                raise UnsupportedResponsesInputError(f"unsupported message role {role!r}")
        elif kind == "function_call":
            conversation.assistant(
                [
                    ToolUseBlock(
                        id=str(item.get("call_id") or item.get("id") or ""),
                        name=str(item.get("name", "")),
                        input=_tool_input(item.get("arguments")),
                    )
                ]
            )
        elif kind == "function_call_output":
            output = item.get("output")
            conversation.user(
                [
                    ToolResultBlock(
                        tool_use_id=str(item.get("call_id", "")),
                        content=output if isinstance(output, str) else _text_of(output),
                    )
                ]
            )
        elif kind == "reasoning":
            # Reasoning items round-trip OpenAI-side state; there is none here.
            continue
        else:
            raise UnsupportedResponsesInputError(f"unsupported input item type {kind!r}")

    tools: list[ToolDefinition] | None = None
    if req.tools and req.tool_choice != "none":
        function_tools = [tool for tool in req.tools if tool.get("type") == "function"]
        skipped = [tool.get("type") for tool in req.tools if tool.get("type") != "function"]
        if skipped:
            logger.warning(
                "Responses request asks for tools the gateway cannot provide, dropped: %s",
                ", ".join(str(kind) for kind in skipped),
            )
        if function_tools:
            tools = [
                ToolDefinition(
                    name=str(tool.get("name", "")),
                    description=str(tool.get("description") or ""),
                    input_schema=dict(tool.get("parameters") or {}),
                )
                for tool in function_tools
            ]

    tool_choice: ToolChoice | None = None
    if tools:
        tool_choice = _tool_choice(req.tool_choice)

    # ``reasoning.effort`` rides along as the DeepSeek-dialect ``reasoning_effort``
    # the chat surface also threads; the router drops it for models the catalog
    # marks as having no thinking mode.
    reasoning_effort: str | None = None
    if isinstance(req.reasoning, dict) and req.reasoning.get("effort"):
        reasoning_effort = str(req.reasoning["effort"])

    return AnthropicRequest(
        model=req.model,
        max_tokens=req.max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS,
        messages=conversation.messages,
        system="\n\n".join(system_parts) if system_parts else None,
        tools=tools,
        tool_choice=tool_choice,
        temperature=req.temperature,
        top_p=req.top_p,
        stream=req.stream,
        metadata=req.metadata,
        reasoning_effort=reasoning_effort,
    )


def _tool_choice(choice: str | dict[str, Any] | None) -> ToolChoice:
    if isinstance(choice, dict):
        if choice.get("type") == "function" and choice.get("name"):
            return ToolChoiceTool(name=str(choice["name"]))
        return ToolChoiceAuto()
    if choice == "required":
        return ToolChoiceAny()
    return ToolChoiceAuto()


# ---------------------------------------------------------------------------
# Response translation: Anthropic canonical → Responses
# ---------------------------------------------------------------------------


def _usage(input_tokens: int, output_tokens: int) -> dict[str, Any]:
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": input_tokens + output_tokens,
    }


def _message_item(item_id: str, text: str, status: str = "completed") -> dict[str, Any]:
    return {
        "id": item_id,
        "type": "message",
        "status": status,
        "role": "assistant",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _function_call_item(
    item_id: str, call_id: str, name: str, arguments: str, status: str = "completed"
) -> dict[str, Any]:
    return {
        "id": item_id,
        "type": "function_call",
        "status": status,
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
    }


def _response_envelope(
    response_id: str,
    model: str,
    created_at: int,
    *,
    status: str,
    output: list[dict[str, Any]],
    usage: dict[str, Any] | None,
    stop_reason: str | None = None,
) -> dict[str, Any]:
    incomplete = _INCOMPLETE_REASONS.get(stop_reason or "")
    return {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": "incomplete" if incomplete else status,
        "incomplete_details": {"reason": incomplete} if incomplete else None,
        "error": None,
        "model": model,
        "output": output,
        "usage": usage,
    }


def anthropic_response_to_responses(
    response: AnthropicResponse, *, response_id: str, created_at: int
) -> dict[str, Any]:
    """Convert a non-streaming Anthropic response to a Responses object."""
    output: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for index, block in enumerate(response.content):
        if isinstance(block, TextBlock):
            text_parts.append(block.text)
        elif isinstance(block, ToolUseBlock):
            output.append(
                _function_call_item(
                    f"fc_{response_id}_{index}", block.id, block.name, json.dumps(block.input)
                )
            )
    if text_parts:
        output.insert(0, _message_item(f"msg_{response_id}", "".join(text_parts)))
    return _response_envelope(
        response_id,
        response.model,
        created_at,
        status="completed",
        output=output,
        usage=_usage(response.usage.input_tokens, response.usage.output_tokens),
        stop_reason=response.stop_reason,
    )


# ---------------------------------------------------------------------------
# Streaming translation: Anthropic SSE → Responses SSE
# ---------------------------------------------------------------------------


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


async def anthropic_stream_to_responses(
    source: AsyncIterator[str],
    *,
    response_id: str,
    model: str,
) -> AsyncIterator[str]:
    """Translate a Bifröst Anthropic SSE stream to Responses API SSE events.

    Text blocks become a ``message`` output item with ``output_text`` deltas;
    tool-use blocks become ``function_call`` items with argument deltas;
    thinking blocks are not forwarded. The stream ends with
    ``response.completed`` (or ``response.incomplete`` when the model hit its
    output budget) carrying every item and the usage.
    """
    created_at = int(time.time())
    sequence = 0
    current_event_type = ""
    input_tokens = 0
    output_tokens = 0
    stop_reason: str | None = None
    output_index = -1
    # Anthropic block index → kind, item id, output index, accumulated text or
    # arguments, and for tool calls the call id and name.
    open_blocks: dict[int, dict[str, Any]] = {}
    items: list[dict[str, Any]] = []

    def event(name: str, payload: dict[str, Any]) -> str:
        nonlocal sequence
        payload = {"type": name, "sequence_number": sequence, **payload}
        sequence += 1
        return _sse(name, payload)

    def envelope(status: str, usage: dict[str, Any] | None) -> dict[str, Any]:
        return _response_envelope(
            response_id,
            model,
            created_at,
            status=status,
            output=list(items),
            usage=usage,
            stop_reason=stop_reason if status == "completed" else None,
        )

    yield event("response.created", {"response": envelope("in_progress", None)})
    yield event("response.in_progress", {"response": envelope("in_progress", None)})

    try:
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
                    break
                try:
                    payload = json.loads(payload_str)
                except json.JSONDecodeError:
                    continue
                event_type = current_event_type or payload.get("type", "")

                if event_type == "message_start":
                    usage = payload.get("message", {}).get("usage", {})
                    input_tokens = usage.get("input_tokens", input_tokens)

                elif event_type == "content_block_start":
                    index = payload.get("index", 0)
                    block = payload.get("content_block", {})
                    kind = block.get("type", "text")
                    if kind == "text":
                        output_index += 1
                        item_id = f"msg_{response_id}_{output_index}"
                        open_blocks[index] = {
                            "kind": "text",
                            "id": item_id,
                            "output_index": output_index,
                            "text": "",
                        }
                        yield event(
                            "response.output_item.added",
                            {
                                "output_index": output_index,
                                "item": {
                                    **_message_item(item_id, "", "in_progress"),
                                    "content": [],
                                },
                            },
                        )
                        yield event(
                            "response.content_part.added",
                            {
                                "item_id": item_id,
                                "output_index": output_index,
                                "content_index": 0,
                                "part": {"type": "output_text", "text": "", "annotations": []},
                            },
                        )
                    elif kind == "tool_use":
                        output_index += 1
                        item_id = f"fc_{response_id}_{output_index}"
                        open_blocks[index] = {
                            "kind": "tool_use",
                            "id": item_id,
                            "output_index": output_index,
                            "arguments": "",
                            "call_id": block.get("id", ""),
                            "name": block.get("name", ""),
                        }
                        yield event(
                            "response.output_item.added",
                            {
                                "output_index": output_index,
                                "item": _function_call_item(
                                    item_id,
                                    block.get("id", ""),
                                    block.get("name", ""),
                                    "",
                                    "in_progress",
                                ),
                            },
                        )
                    else:
                        open_blocks[index] = {"kind": kind}

                elif event_type == "content_block_delta":
                    index = payload.get("index", 0)
                    delta = payload.get("delta", {})
                    block = open_blocks.get(index)
                    if block is None:
                        continue
                    if block["kind"] == "text" and delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        block["text"] += text
                        yield event(
                            "response.output_text.delta",
                            {
                                "item_id": block["id"],
                                "output_index": block["output_index"],
                                "content_index": 0,
                                "delta": text,
                            },
                        )
                    elif block["kind"] == "tool_use" and delta.get("type") == "input_json_delta":
                        partial = delta.get("partial_json", "")
                        block["arguments"] += partial
                        yield event(
                            "response.function_call_arguments.delta",
                            {
                                "item_id": block["id"],
                                "output_index": block["output_index"],
                                "delta": partial,
                            },
                        )

                elif event_type == "content_block_stop":
                    index = payload.get("index", 0)
                    block = open_blocks.pop(index, None)
                    if block is None:
                        continue
                    if block["kind"] == "text":
                        item = _message_item(block["id"], block["text"])
                        items.append(item)
                        base = {"item_id": block["id"], "output_index": block["output_index"]}
                        yield event(
                            "response.output_text.done",
                            {**base, "content_index": 0, "text": block["text"]},
                        )
                        yield event(
                            "response.content_part.done",
                            {**base, "content_index": 0, "part": item["content"][0]},
                        )
                        yield event(
                            "response.output_item.done",
                            {"output_index": block["output_index"], "item": item},
                        )
                    elif block["kind"] == "tool_use":
                        arguments = block["arguments"] or "{}"
                        item = _function_call_item(
                            block["id"], block["call_id"], block["name"], arguments
                        )
                        items.append(item)
                        yield event(
                            "response.function_call_arguments.done",
                            {
                                "item_id": block["id"],
                                "output_index": block["output_index"],
                                "arguments": arguments,
                            },
                        )
                        yield event(
                            "response.output_item.done",
                            {"output_index": block["output_index"], "item": item},
                        )

                elif event_type == "message_delta":
                    delta = payload.get("delta", {})
                    stop_reason = delta.get("stop_reason", stop_reason)
                    usage = payload.get("usage", {})
                    input_tokens = usage.get("input_tokens", input_tokens)
                    output_tokens = usage.get("output_tokens", output_tokens)

                elif event_type == "message_stop":
                    break
            else:
                continue
            break

    except Exception as exc:
        # The upstream failed mid-stream: say so in the protocol so the client
        # shows the reason instead of a dropped connection.
        logger.error("Responses stream failed upstream: %s", exc)
        failed = envelope("failed", None)
        failed["error"] = {"code": "server_error", "message": str(exc)[:500]}
        yield event("response.failed", {"response": failed})
        return

    final = envelope("completed", _usage(input_tokens, output_tokens))
    yield event(
        "response.incomplete" if final["status"] == "incomplete" else "response.completed",
        {"response": final},
    )
