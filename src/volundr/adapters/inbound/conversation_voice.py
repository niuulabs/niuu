"""The voice projection of a conversation page (LEX-1677).

A voice read wants what was said, not how: the newest user and agent messages as
plain text. :func:`voice_page` turns a conversation tail page (already read from the
end of its source, shallow) into ``{role, text, created_at, turn_id}`` messages,
dropping tool calls, tool results, attachments and turn metadata.
"""

from __future__ import annotations

from typing import Any

VOICE_DETAIL = "voice"
_VOICE_ROLES = frozenset({"user", "assistant"})


def _turn_text(turn: dict[str, Any]) -> str:
    content = turn.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    texts = [
        part["text"].strip()
        for part in turn.get("parts") or []
        if isinstance(part, dict)
        and part.get("type") == "text"
        and isinstance(part.get("text"), str)
        and part["text"].strip()
    ]
    return "\n\n".join(texts)


def _in_progress(turn: dict[str, Any]) -> bool:
    metadata = turn.get("metadata")
    status = metadata.get("status") if isinstance(metadata, dict) else None
    return bool(turn.get("in_progress")) or status == "in_progress"


def voice_messages(turns: list[Any]) -> list[dict[str, Any]]:
    """The user/agent turns that carry text, oldest first, as voice messages."""
    messages: list[dict[str, Any]] = []
    for turn in turns:
        if not isinstance(turn, dict) or turn.get("role") not in _VOICE_ROLES:
            continue
        if turn.get("visibility") not in (None, "public"):
            continue
        text = _turn_text(turn)
        if not text:
            continue
        message = {
            "role": "agent" if turn["role"] == "assistant" else "user",
            "text": text,
            "created_at": turn.get("created_at"),
            "turn_id": turn.get("id"),
        }
        if _in_progress(turn):
            message["in_progress"] = True
        messages.append(message)
    return messages


def voice_page(
    page: dict[str, Any],
    *,
    session_id: str,
    limit: int,
    since_turn: str | None,
) -> dict[str, Any]:
    """Project a tail page of ``limit`` turns to its voice messages.

    ``since_turn`` keeps only the messages after that turn. ``since_turn_found``
    says whether the turn was inside the page: when it is false, every message
    returned is newer, but more may lie between that turn and the page.
    """
    turns = page.get("turns") if isinstance(page.get("turns"), list) else []
    found: bool | None = None
    if since_turn is not None:
        ids = [turn.get("id") for turn in turns if isinstance(turn, dict)]
        found = since_turn in ids
        if found:
            turns = turns[ids.index(since_turn) + 1 :]
    out: dict[str, Any] = {
        "session_id": session_id,
        "detail": VOICE_DETAIL,
        "limit": limit,
        "messages": voice_messages(turns),
        "activity_state": page.get("activity_state"),
    }
    if page.get("total_turns") is not None:
        out["total_turns"] = page["total_turns"]
    if since_turn is not None:
        out["since_turn"] = since_turn
        out["since_turn_found"] = found
    return out
