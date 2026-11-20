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
    """Turns ``[start, end)``; ``part_start``/``part_end`` bound the parts of turn ``end - 1``.

    Part bounds are present only for within-turn paging (``part_paging``), when a
    single turn is larger than the page budget. ``None`` means the whole turn.
    """

    start: int
    end: int
    kind: str = "recent"
    part_start: int | None = None
    part_end: int | None = None

    @property
    def parts(self) -> tuple[int, int] | None:
        if self.part_end is None:
            return None
        return (self.part_start or 0, self.part_end)


def _encode(
    turns: list[dict],
    session_id: str,
    revision: str,
    start: int,
    end: int,
    parts: tuple[int, int] | None = None,
) -> str:
    payload: list = [2, session_id, revision, start, end, _prefix(turns, end)]
    if parts is not None:
        # Within-turn bounds for turn end-1. Older cursors without them keep
        # their original six-element shape, so every existing cursor decodes.
        payload.extend(parts)
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def _decode(cursor: str) -> tuple:
    try:
        if len(cursor) > 4096:
            raise ValueError("Cursor too long")
        data = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if not isinstance(data, list) or len(data) not in (6, 8):
            raise ValueError("Malformed history cursor")
        version, sid, rev, start, end, prefix = data[:6]
        parts = tuple(data[6:]) or None
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
        if parts is not None and (
            not all(type(v) is int for v in parts)
            or end < 1
            or not 0 <= parts[0] < parts[1]
            # An older cursor names the parts BEFORE a boundary: from the first.
            or (start < 0 and parts[0] != 0)
            # A part-bounded interval must still contain its bounded turn.
            or (start >= 0 and start == end)
        ):
            raise ValueError("Malformed history cursor")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError("Malformed history cursor") from exc
    return sid, rev, start, end, prefix, parts


def select_history_window(
    turns: list[dict], *, session_id: str, revision: str, limit: int, cursor: str | None
) -> HistoryWindow:
    if not cursor:
        return HistoryWindow(max(0, len(turns) - limit), len(turns))
    sid, rev, start, end, prefix, parts = _decode(cursor)
    if sid != session_id or rev != revision or end > len(turns) or prefix != _prefix(turns, end):
        raise InvalidHistoryCursorError("History cursor no longer matches the projection")
    if parts is not None and parts[1] > len(turns[end - 1].get("parts") or []):
        # Parts of a turn only ever append; fewer parts than the boundary means
        # the row was rewritten, not that the boundary moved.
        raise InvalidHistoryCursorError("History cursor names parts this row no longer has")
    part_start, part_end = parts if parts is not None else (None, None)
    # -1 means an older page ending at this fixed boundary; otherwise refresh
    # the exact returned interval (possibly narrowed by a smaller request limit).
    return HistoryWindow(
        max(0, end - limit) if start < 0 else start,
        end,
        "older" if start < 0 else "refresh",
        part_start,
        part_end,
    )


def history_page_metadata(
    turns: list[dict],
    *,
    session_id: str,
    revision: str,
    start: int,
    end: int,
    requested: HistoryWindow,
    parts: tuple[int, int] | None = None,
) -> dict:
    """Boundaries of a served page; ``parts`` is the served range of turn ``end - 1``.

    ``parts`` is ``None`` when that turn was served whole. When it starts after
    part 0, the turn continues in an older page: ``older_cursor`` names its
    preceding parts before any earlier turn.
    """
    part_head = parts[0] if parts is not None else 0
    requested_head = requested.parts[0] if requested.parts is not None else 0
    if part_head > 0:
        older = _encode(turns, session_id, revision, -1, end, (0, part_head))
    else:
        older = _encode(turns, session_id, revision, -1, start) if start else None
    continuation = None
    if requested.kind == "refresh" and part_head > requested_head:
        continuation = _encode(
            turns, session_id, revision, requested.start, end, (requested_head, part_head)
        )
    elif requested.kind == "refresh" and start > requested.start:
        continuation = _encode(turns, session_id, revision, requested.start, start)
    return {
        "history_protocol": 2,
        "total_turns": len(turns),
        "window_offset": start,
        "window_end": end,
        "page_kind": requested.kind,
        "requested_window_offset": requested.start,
        "requested_window_end": requested.end,
        "page_complete": start == requested.start
        and end == requested.end
        and parts == requested.parts,
        "continuation_cursor": continuation,
        "projection_revision": revision,
        "has_more_before": start > 0 or part_head > 0,
        "older_cursor": older,
        "refresh_cursor": (
            _encode(turns, session_id, revision, start, end, parts) if start < end else None
        ),
    }
