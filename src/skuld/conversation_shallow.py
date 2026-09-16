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

from niuu.domain.transcript_reducer import TOOL_ENDED_AT

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


def _coerce_int(value: Any) -> int | None:
    """A JSON number → int (drops bools/None/non-numeric), for image dimensions."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _content_image_info(content: Any) -> tuple[bool, str | None, int | None, int | None]:
    """Detect an image ``tool_result`` and return ``(is_image, mime, width, height)``.

    A Skuld ``Read`` of an image returns ``content`` as a JSON STRING encoding the
    envelope ``{"type":"image","file":{"base64":…,"type":"image/png","dimensions":
    {"displayWidth":…,"displayHeight":…}}}``. We JSON-decode a string first. The
    Anthropic content-array form ``[{"type":"image","source":{"media_type":…}}]`` is
    tolerated defensively. Anything else → ``(False, None, None, None)``.

    Pure + cheap (no image bytes decoded) — this is what lets an elided placeholder
    carry an image hint so the client can render a thumbnail chip WITHOUT fetching the
    (always-elided, >1 KB) base64 payload.
    """
    parsed = content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError):
            return (False, None, None, None)
    if isinstance(parsed, dict) and parsed.get("type") == "image":
        file = parsed.get("file")
        if isinstance(file, dict):
            mime = file.get("type")
            dims = file.get("dimensions")
            width = height = None
            if isinstance(dims, dict):
                width = _coerce_int(dims.get("displayWidth"))
                height = _coerce_int(dims.get("displayHeight"))
            return (True, mime if isinstance(mime, str) else None, width, height)
        return (True, None, None, None)
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict) and item.get("type") == "image":
                source = item.get("source")
                mime = source.get("media_type") if isinstance(source, dict) else None
                return (True, mime if isinstance(mime, str) else None, None, None)
    return (False, None, None, None)


def is_elided_block(block: Any) -> bool:
    """True when ``block`` is a shallow tool_result placeholder (content dropped)."""
    return (
        isinstance(block, dict)
        and block.get("type") == "tool_result"
        and bool(block.get("truncated"))
        and "content" not in block
    )


INPUT_PREVIEW_CHAR_LIMIT = 180
"""Max characters of a tool_use input kept in the placeholder ``preview`` — matches the
ONE bounded argument line the collapsed tool card renders."""

_INPUT_PRIMARY_KEYS = (
    "command",
    "file_path",
    "path",
    "pattern",
    "query",
    "url",
    "prompt",
    "description",
    "content",
)
"""Preview precedence — the argument a human recognizes the call by (mirrors the clients'
bounded-argument pick: Bash→command, Read/Edit/Write→file_path, Grep→pattern, …)."""


def _input_preview(input_obj: Any) -> str:
    """The bounded one-line preview of a tool_use ``input`` (primary arg, else JSON prefix)."""
    if isinstance(input_obj, dict):
        for key in _INPUT_PRIMARY_KEYS:
            value = input_obj.get(key)
            if isinstance(value, str) and value.strip():
                return value[:INPUT_PREVIEW_CHAR_LIMIT]
        try:
            return json.dumps(input_obj, ensure_ascii=False)[:INPUT_PREVIEW_CHAR_LIMIT]
        except (TypeError, ValueError):
            return ""
    return str(input_obj)[:INPUT_PREVIEW_CHAR_LIMIT]


def is_elided_input(input_obj: Any) -> bool:
    """True when a tool_use ``input`` is the shallow placeholder (full input dropped)."""
    return isinstance(input_obj, dict) and bool(input_obj.get("_elided_input"))


NEVER_ELIDED_INPUT_TOOLS = frozenset({"askuserquestion", "ask_user_question"})
"""Tools whose ``input`` IS the user-facing payload, never mere detail.

``AskUserQuestion`` is the one tool where the input is not an argument the client
summarises to one line — it is the entire thing the user has to read in order to
answer. Eliding it is not a payload saving, it is deleting the UI: the client is
left with a placeholder it cannot render a card from, and the perverse property
is that the BETTER the question (several questions, four options each, a real
description per option) the more certainly it blows the 1 KB limit and vanishes.

Observed live (session ``agents-diet``, 2026-08-11): a four-question ask with
per-option descriptions serialised to 3387 B, was elided, and the app had nothing
to render while the agent sat blocked on the answer.

The lazy per-result endpoint is not a sufficient escape hatch here either: it is
keyed on the tool_use_id of a *result*, and a question that is still waiting for
its answer has no result by definition. (It does serve input-only hits — see
``get_tool_result`` — but that is a recovery path, not a reason to break the
common case.) Keeping these inline costs a few KB once per ask.
"""


def _is_never_elided_tool(name: Any) -> bool:
    """True for tools exempt from input elision (see ``NEVER_ELIDED_INPUT_TOOLS``)."""
    return isinstance(name, str) and name.strip().lower() in NEVER_ELIDED_INPUT_TOOLS


DESCRIPTION_LIMIT = 200
"""Max characters of a tool_use ``input.description`` carried through elision — the model writes a
handful of words, and this is only a defence against a pathological one."""


def elide_tool_use_block(block: Any, *, inline_limit: int = INLINE_BYTE_LIMIT) -> Any:
    """Return a tool_use block with a heavy ``input`` replaced by a placeholder.

    P1 of the payload diet (2026-07-12): measured on the lexi-frontend-presentation
    session, ``tool_use.input`` was 59% of a 1.5 MB shallow window (908 KB — every
    Edit's old/new strings, Write bodies), while the transcript renders exactly one
    bounded argument line per collapsed card. Inputs above ``inline_limit`` collapse
    to ``{"_elided_input": true, "byte_size": N, "preview": <primary arg line>}``
    — STILL A DICT, so clients that decode ``input`` as a string-keyed object are
    untouched. The full input rides the existing per-result lazy endpoint
    (``/tool-result/{tool_use_id}`` response gains ``input``), fetched on expand.
    Non-tool_use blocks and already-elided inputs pass through unchanged.
    """
    if not isinstance(block, dict) or block.get("type") != "tool_use":
        return block
    if _is_never_elided_tool(block.get("name")):
        return block
    input_obj = block.get("input")
    if input_obj is None or is_elided_input(input_obj):
        return block
    byte_size = _content_byte_size(input_obj)
    if byte_size <= inline_limit:
        return block
    placeholder = {
        "_elided_input": True,
        "byte_size": byte_size,
        "preview": _input_preview(input_obj),
    }
    # KEEP THE MODEL'S OWN `description` (2026-08-07). Claude Code's Bash schema asks the model for
    # a short human-readable description of what a command does ("Read AGENTS.md", "Find manipulator
    # files"), and that string — not the raw command — is what a reader wants as the title of the
    # row. Dropping it here lost the title on exactly the HEAVIEST calls, which are the ones whose
    # raw command is least readable. It is bounded and tiny (a handful of words), so it costs
    # nothing next to the KBs this elision saves.
    if isinstance(input_obj, dict):
        description = input_obj.get("description")
        if isinstance(description, str) and description.strip():
            placeholder["description"] = description[:DESCRIPTION_LIMIT]
    return {**block, "input": placeholder}


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
    placeholder = {
        "type": "tool_result",
        "tool_use_id": block.get("tool_use_id", ""),
        "is_error": bool(block.get("is_error", False)),
        "truncated": True,
        "byte_size": byte_size,
        "preview": _content_preview(content),
    }
    # D1 TIMING: this placeholder is REBUILT from scratch (unlike elide_tool_use_block, which
    # spreads ``**block``), so any additive key must be copied across explicitly or shallow
    # clients silently lose it. Carry the per-tool ``ended_at`` stamp. Omitted when absent, so
    # a pre-D1 transcript elides to the exact same placeholder it did before.
    ended_at = block.get(TOOL_ENDED_AT)
    if ended_at:
        placeholder[TOOL_ENDED_AT] = ended_at
    # IMAGE HINT: an image Read is always > 1 KB (base64) so it is ALWAYS elided, and the
    # placeholder above carries no type signal (the preview is a truncated base64 blob). Stamp
    # is_image / mime_type / dimensions so the client can hoist a thumbnail chip and size it by
    # aspect ratio WITHOUT fetching the payload. Additive — omitted entirely for non-images.
    is_image, mime, width, height = _content_image_info(content)
    if is_image:
        placeholder["is_image"] = True
        if mime:
            placeholder["mime_type"] = mime
        if width is not None:
            placeholder["img_w"] = width
        if height is not None:
            placeholder["img_h"] = height
    return placeholder


def elide_parts(parts: Any, *, inline_limit: int = INLINE_BYTE_LIMIT) -> Any:
    """Elide every heavy tool_result AND tool_use input in a turn's ``parts`` list."""
    if not isinstance(parts, list):
        return parts
    return [
        elide_tool_use_block(
            elide_tool_result_block(b, inline_limit=inline_limit), inline_limit=inline_limit
        )
        for b in parts
    ]


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
