"""Shallow conversation serialization — elide heavy tool_result payloads.

A coding session's conversation is dominated by ``tool_result`` blocks: in a
crash-hunt session ~95% of a 20 MB transcript was tool-return content. A client
that only needs the conversation *structure* (turns, tool calls, assistant text)
to render the timeline does not need every tool return up front — it can fetch
an individual result on demand when the user expands that block.

Shallow mode rewrites each large ``tool_result`` block to a small placeholder
that keeps the join key (``tool_use_id``), the error flag, a short preview, and
the original byte size, and drops the heavy ``content``. The client renders a
stub and lazy-loads the full content via the per-result endpoint
(``/api/conversation/tool-result/{tool_use_id}``) only when needed.

Small tool results (``<= INLINE_BYTE_LIMIT``) are left INLINE: eliding them would
save nothing and force a needless round-trip on expand. Only blocks above the
threshold become placeholders. This module is pure (no I/O, no broker state) so
it is shared by the broker's live conversation path and volundr's durable-log
rebuild fallback — one definition of the placeholder shape for both.
"""

from __future__ import annotations

import json
from typing import Any

SHALLOW_DETAIL = "shallow"
"""``?detail=shallow`` query-param value that selects elided serialization."""

PREVIEW_CHAR_LIMIT = 200
"""Max characters of a tool result kept in the placeholder ``preview``."""

INLINE_BYTE_LIMIT = 1024
"""Tool results at or below this byte size are kept inline (not elided).

Tuned against a real 20 MB crash-hunt transcript: at ~256-512 B a placeholder
(tool_use_id + 200-char preview + byte_size) costs as much as the content it
replaces, so eliding tiny results saves nothing and only adds an expand
round-trip. 1 KB keeps the small "pillow" results inline (no round-trip) while
still eliding the heavy ones — the shallow payload lands near its floor (~1.4 MB,
a 14x reduction) since that floor is dominated by the tool *calls*, not returns.
"""


def _content_byte_size(content: Any) -> int:
    """Return the UTF-8 byte size of a tool_result ``content`` (str or block list)."""
    if isinstance(content, str):
        return len(content.encode("utf-8"))
    try:
        return len(json.dumps(content, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return len(str(content).encode("utf-8"))


def _content_preview(content: Any) -> str:
    """Return a compact text preview for a tool_result ``content``.

    Mirrors ``Broker._extract_tool_result_preview``: a string is truncated; a
    list of content blocks has its ``text`` fields joined; anything else yields "".
    """
    if isinstance(content, str):
        return content[:PREVIEW_CHAR_LIMIT]
    if isinstance(content, list):
        text_parts = [
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("text")
        ]
        return " ".join(text_parts)[:PREVIEW_CHAR_LIMIT]
    return ""


def is_elided_block(block: Any) -> bool:
    """True when ``block`` is a shallow tool_result placeholder (content dropped)."""
    return (
        isinstance(block, dict)
        and block.get("type") == "tool_result"
        and bool(block.get("truncated"))
        and "content" not in block
    )


def elide_tool_result_block(block: Any, *, inline_limit: int = INLINE_BYTE_LIMIT) -> Any:
    """Return a placeholder for a heavy tool_result block, else the block unchanged.

    Non-tool_result blocks (tool_use, text, thinking, …) pass through untouched.
    A tool_result whose content is at or below ``inline_limit`` bytes also passes
    through untouched (cheap to ship, no round-trip worth saving)."""
    if not isinstance(block, dict) or block.get("type") != "tool_result":
        return block
    content = block.get("content", "")
    byte_size = _content_byte_size(content)
    if byte_size <= inline_limit:
        return block
    return {
        "type": "tool_result",
        "tool_use_id": block.get("tool_use_id", ""),
        "is_error": bool(block.get("is_error", False)),
        "truncated": True,
        "byte_size": byte_size,
        "preview": _content_preview(content),
    }


def elide_parts(parts: Any, *, inline_limit: int = INLINE_BYTE_LIMIT) -> Any:
    """Elide every heavy tool_result in a turn's ``parts`` list (others unchanged)."""
    if not isinstance(parts, list):
        return parts
    return [elide_tool_result_block(b, inline_limit=inline_limit) for b in parts]


def elide_turn(turn: Any, *, inline_limit: int = INLINE_BYTE_LIMIT) -> Any:
    """Return a copy of a serialized turn dict with its ``parts`` elided."""
    if not isinstance(turn, dict) or not isinstance(turn.get("parts"), list):
        return turn
    return {**turn, "parts": elide_parts(turn["parts"], inline_limit=inline_limit)}


def elide_turns(turns: Any, *, inline_limit: int = INLINE_BYTE_LIMIT) -> Any:
    """Elide tool_result content across a list of serialized turns.

    Returns a new list; input dicts are shallow-copied where modified so the
    caller's turn objects are never mutated.
    """
    if not isinstance(turns, list):
        return turns
    return [elide_turn(t, inline_limit=inline_limit) for t in turns]
