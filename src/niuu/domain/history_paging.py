"""Opaque, session-scoped page boundaries over stable conversation identities.

These are seek tokens, NOT credentials. Authorization belongs to the route.
Content/status changes and appends do not move a boundary. Changes to the
identity prefix or text projection explicitly invalidate it instead of serving
a different interval as though it were the requested page.
"""

import base64
import hashlib
import json
from dataclasses import dataclass


class InvalidHistoryCursorError(ValueError):
    """A well-formed cursor no longer names this projection."""


def _prefix(turns: list[dict], end: int) -> str:
    digest = hashlib.sha256()
    for index in range(end):
        turn = turns[index]
        identity = turn.get("id")
        if not isinstance(identity, str) or not identity:
            raise InvalidHistoryCursorError("History has no stable row identity")
        digest.update(json.dumps(identity, ensure_ascii=True).encode())
        digest.update(b"\n")
    return digest.hexdigest()


@dataclass(frozen=True)
class HistoryWindow:
    start: int
    end: int
    kind: str = "recent"


def _encode(turns: list[dict], session_id: str, revision: str, start: int, end: int) -> str:
    payload = [2, session_id, revision, start, end, _prefix(turns, end)]
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def select_history_window(
    turns: list[dict], *, session_id: str, revision: str, limit: int, cursor: str | None
) -> HistoryWindow:
    if not cursor:
        return HistoryWindow(max(0, len(turns) - limit), len(turns))
    try:
        if len(cursor) > 4096:
            raise ValueError("Cursor too long")
        data = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        version, sid, rev, start, end, prefix = data
        if (
            version != 2
            or type(start) is not int
            or type(end) is not int
            or start < -1
            or end < 0
            or start > end
            or not all(isinstance(v, str) for v in (sid, rev, prefix))
        ):
            raise ValueError("Malformed history cursor")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError("Malformed history cursor") from exc
    if sid != session_id or rev != revision or end > len(turns) or prefix != _prefix(turns, end):
        raise InvalidHistoryCursorError("History cursor no longer matches the projection")
    # -1 means an older page ending at this fixed boundary; otherwise refresh
    # the exact returned interval (possibly narrowed by a smaller request limit).
    return HistoryWindow(
        max(0, end - limit) if start < 0 else start, end, "older" if start < 0 else "refresh"
    )


def history_page_metadata(
    turns: list[dict],
    *,
    session_id: str,
    revision: str,
    start: int,
    end: int,
    requested: HistoryWindow,
) -> dict:
    return {
        "history_protocol": 2,
        "total_turns": len(turns),
        "window_offset": start,
        "window_end": end,
        "page_kind": requested.kind,
        "requested_window_offset": requested.start,
        "requested_window_end": requested.end,
        "page_complete": start == requested.start and end == requested.end,
        "continuation_cursor": (
            _encode(turns, session_id, revision, requested.start, start)
            if requested.kind == "refresh" and start > requested.start
            else None
        ),
        "projection_revision": revision,
        "has_more_before": start > 0,
        "older_cursor": _encode(turns, session_id, revision, -1, start) if start else None,
        "refresh_cursor": _encode(turns, session_id, revision, start, end) if start < end else None,
    }
