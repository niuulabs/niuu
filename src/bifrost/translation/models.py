"""Pydantic models representing the Anthropic Messages API wire format.

These are the canonical inbound/outbound types for Bifröst.  All provider
adapters accept an ``AnthropicRequest`` and return an ``AnthropicResponse``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr, model_validator

# ---------------------------------------------------------------------------
# Content blocks
# ---------------------------------------------------------------------------


class CacheControl(BaseModel):
    type: Literal["ephemeral"] = "ephemeral"


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str
    cache_control: CacheControl | None = None


class ThinkingBlock(BaseModel):
    type: Literal["thinking"] = "thinking"
    thinking: str


class ToolUseBlock(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ToolResultBlock(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str | list[TextBlock] = ""
    is_error: bool = False
    cache_control: CacheControl | None = None


class ImageSource(BaseModel):
    type: Literal["base64", "url"] = "base64"
    media_type: str = ""
    data: str = ""
    url: str = ""


class ImageBlock(BaseModel):
    type: Literal["image"] = "image"
    source: ImageSource
    cache_control: CacheControl | None = None


ContentBlock = TextBlock | ThinkingBlock | ImageBlock | ToolUseBlock | ToolResultBlock

# A message content value is either a plain string or a list of blocks.
MessageContent = str | list[ContentBlock]


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: MessageContent


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


class ToolDefinition(BaseModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Tool choice
# ---------------------------------------------------------------------------


class ToolChoiceAuto(BaseModel):
    type: Literal["auto"] = "auto"


class ToolChoiceAny(BaseModel):
    type: Literal["any"] = "any"


class ToolChoiceTool(BaseModel):
    type: Literal["tool"] = "tool"
    name: str


ToolChoice = ToolChoiceAuto | ToolChoiceAny | ToolChoiceTool


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


def _has_role(message: Any, role: str) -> bool:
    return isinstance(message, dict) and message.get("role") == role


def _is_user_turn(message: Any) -> bool:
    return _has_role(message, "user") and isinstance(message.get("content"), str | list)


def _system_block_text(block: Any) -> str:
    if not (isinstance(block, dict) and block.get("type") == "text"):
        raise ValueError("a role: system message may hold only text blocks")
    text = block.get("text")
    if not isinstance(text, str):
        raise ValueError("a text block in a role: system message needs a string text")
    return text


def _system_text_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the non-empty texts of a ``role: system`` message as plain text blocks.

    Raises:
        ValueError: The content is neither a string nor a list of text blocks.
    """
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if not isinstance(content, list):
        raise ValueError("a role: system message's content must be a string or a list of blocks")
    texts = [_system_block_text(block) for block in content]
    return [{"type": "text", "text": text} for text in texts if text]


def _content_blocks(content: str | list[Any]) -> list[Any]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    return list(content)


def _block_type(block: Any) -> Any:
    if isinstance(block, dict):
        return block.get("type")
    return getattr(block, "type", None)


def _insert_after_tool_results(
    turn: dict[str, Any], blocks: list[dict[str, Any]]
) -> dict[str, Any]:
    """Put *blocks* at the head of a user turn, behind the ``tool_result`` blocks that lead it."""
    content = _content_blocks(turn["content"])
    at = 0
    while at < len(content) and _block_type(content[at]) == "tool_result":
        at += 1
    return {**turn, "content": [*content[:at], *blocks, *content[at:]]}


def _append_to_turn(turn: dict[str, Any], blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {**turn, "content": [*_content_blocks(turn["content"]), *blocks]}


class AnthropicRequest(BaseModel):
    _routed_provider: str | None = PrivateAttr(default=None)
    _routing_prepared: bool = PrivateAttr(default=False)
    _response_format: dict[str, Any] | None = PrivateAttr(default=None)

    @model_validator(mode="before")
    @classmethod
    def _fold_system_messages(cls, data: Any) -> Any:
        """Accept ``role: system`` messages, which the Anthropic API keeps out of ``messages``.

        Clients written against OpenAI-style APIs, and Claude Code once pointed
        at a custom base URL, put instructions there; rejecting the turn would
        break every such client. A system message must hold a string or a list
        of text blocks, or the request fails validation. Its text stays where
        the message stood, as plain text blocks without ``cache_control``:

        - before any other message: appended to ``system``;
        - after a user turn: appended to that turn;
        - after an assistant turn: put at the head of the next user turn, behind
          its ``tool_result`` blocks, or, when an assistant turn or the end of
          the conversation comes next, sent as a user turn of its own.

        A system message with empty text is dropped. Only those that open the
        conversation join ``system``: Claude Code sends one after every tool
        result, and hoisting those would change the head of every request as
        the conversation grows, so no provider's prefix cache could reuse the
        turns before it.
        """
        if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
            return data
        if not any(_has_role(message, "system") for message in data["messages"]):
            return data
        leading: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        kept: list[Any] = []
        for message in data["messages"]:
            if not _has_role(message, "system"):
                if pending and _is_user_turn(message):
                    message = _insert_after_tool_results(message, pending)
                elif pending:
                    kept.append({"role": "user", "content": pending})
                pending = []
                kept.append(message)
                continue
            blocks = _system_text_blocks(message)
            if not blocks:
                continue
            if not kept:
                leading.extend(blocks)
            elif _is_user_turn(kept[-1]):
                kept[-1] = _append_to_turn(kept[-1], blocks)
            else:
                pending.extend(blocks)
        if pending:
            kept.append({"role": "user", "content": pending})
        if not leading:
            return {**data, "messages": kept}
        system: list[Any] = []
        existing = data.get("system")
        if isinstance(existing, str) and existing:
            system.append({"type": "text", "text": existing})
        elif isinstance(existing, list):
            system.extend(existing)
        return {**data, "messages": kept, "system": [*system, *leading]}

    model: str
    max_tokens: int = 1024
    messages: list[Message]
    system: str | list[TextBlock] | None = None
    tools: list[ToolDefinition] | None = None
    tool_choice: ToolChoice | None = None
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    stop_sequences: list[str] | None = None
    stream: bool = False
    metadata: dict[str, Any] | None = None
    thinking: dict[str, Any] | None = None
    # DeepSeek-dialect effort knob ('high' | 'max'); OpenAI-compatible backends
    # receive it verbatim, the Anthropic adapter excludes it from its payload.
    reasoning_effort: str | None = None
    chat_template_kwargs: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class UsageInfo(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class AnthropicResponse(BaseModel):
    id: str
    type: Literal["message"] = "message"
    role: Literal["assistant"] = "assistant"
    content: list[ContentBlock]
    model: str
    stop_reason: str | None = None
    stop_sequence: str | None = None
    usage: UsageInfo = Field(default_factory=UsageInfo)


# ---------------------------------------------------------------------------
# Streaming events
# ---------------------------------------------------------------------------


class MessageStartEvent(BaseModel):
    type: Literal["message_start"] = "message_start"
    message: dict[str, Any]


class ContentBlockStartEvent(BaseModel):
    type: Literal["content_block_start"] = "content_block_start"
    index: int
    content_block: dict[str, Any]


class ContentBlockDeltaEvent(BaseModel):
    type: Literal["content_block_delta"] = "content_block_delta"
    index: int
    delta: dict[str, Any]


class ContentBlockStopEvent(BaseModel):
    type: Literal["content_block_stop"] = "content_block_stop"
    index: int


class MessageDeltaEvent(BaseModel):
    type: Literal["message_delta"] = "message_delta"
    delta: dict[str, Any]
    usage: dict[str, Any] = Field(default_factory=dict)


class MessageStopEvent(BaseModel):
    type: Literal["message_stop"] = "message_stop"


class PingEvent(BaseModel):
    type: Literal["ping"] = "ping"


# Mapping from OpenAI finish_reason → Anthropic stop_reason.
FINISH_REASON_MAP: dict[str, str] = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
    "content_filter": "end_turn",
    "function_call": "tool_use",
}

# Mapping from Anthropic stop_reason → OpenAI finish_reason (inverse of FINISH_REASON_MAP).
STOP_REASON_TO_OPENAI: dict[str, str] = {
    "end_turn": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "stop_sequence": "stop",
}


def extract_text_from_response(response: AnthropicResponse) -> str:
    """Flatten Anthropic content blocks to a plain text string.

    ``TextBlock`` content is concatenated directly; ``ThinkingBlock`` content
    is wrapped in ``<thinking>`` tags.  All other block types are ignored.

    Args:
        response: An ``AnthropicResponse`` from the routing layer.

    Returns:
        A single string suitable for embedding in any text-based response format.
    """
    parts: list[str] = []
    for block in response.content:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        elif isinstance(block, ThinkingBlock):
            parts.append(f"<thinking>{block.thinking}</thinking>")
    return "".join(parts)
