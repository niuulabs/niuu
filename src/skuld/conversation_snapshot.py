"""Fit legacy complete snapshots and explicitly negotiated recent-history windows.

A byte budget only ever drops OLDER WHOLE turns. A returned turn is complete:
its content, reasoning and every part (under the shallow tool_result/input
elision, whose full bodies stay lazily loadable). Nothing here cuts text or
trims parts (LEX-1937). A single turn larger than the budget is paged WITHIN by
part ranges, each page carrying complete parts, when the client negotiated
``part_paging``. Otherwise it is elided deeper — every tool body whose placeholder
is smaller becomes one, every part and tool call kept (LEX-2017) — and served
whole as it is only if even that does not fit. Wire contract: "Bounded history
pages" in ``docs/forge/openclaw-session-orchestrator-guide.md``.
"""

import json
import logging

from niuu.domain.history_paging import history_page_metadata, select_history_window
from niuu.domain.json_text import json_text_safe
from niuu.domain.text_projection import projection_revision
from skuld.conversation_shallow import SHALLOW_DETAIL, elide_turn_deep, elide_turns

logger = logging.getLogger(__name__)

PART_WINDOW_KEY = "part_window"
"""Turn field naming the served part range ``{offset, end, total}`` of a paged turn."""

NEWEST_FRAGMENT_KEYS = ("content", "reasoning")
"""Whole-turn text carried only by the fragment holding the turn's last part."""


class ConversationSnapshotTooLargeError(ValueError):
    """Even a complete shallow snapshot exceeds the client's receive budget."""


def snapshot_byte_size(frame: dict) -> int:
    """Match Starlette WebSocket.send_json, used by the broker's reconnect path."""
    return len(json.dumps(frame, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def prepare_conversation_snapshot(frame: dict, *, max_bytes: int) -> dict:
    """Keep small snapshots unchanged; use existing lazy tool placeholders for large ones.

    Clients replace their canonical history from this frame. We therefore never
    window away turns: older deployed iOS clients do not read snapshot offsets.
    Oversized prose must use the REST history path instead of an incomplete seed.
    """
    if max_bytes <= 0:
        raise ValueError("Conversation snapshot byte budget must be positive")
    frame = json_text_safe(frame)
    if snapshot_byte_size(frame) <= max_bytes:
        return frame
    shallow = {**frame, "turns": elide_turns(frame["turns"]), "detail": SHALLOW_DETAIL}
    size = snapshot_byte_size(shallow)
    if size > max_bytes:
        raise ConversationSnapshotTooLargeError(
            f"Complete shallow conversation snapshot is {size} bytes; limit is {max_bytes}"
        )
    return shallow


def _turns_fit(envelope_size: int, sizes: list[int], max_bytes: int) -> int:
    """How many NEWEST whole turns fit; at least one (a turn is never cut).

    ``envelope_size`` is the page encoded with an empty ``turns`` list; a list
    of n encoded items adds their sizes plus n-1 commas, exactly.
    """
    total, kept = envelope_size, 0
    for size in reversed(sizes):
        added = size + (1 if kept else 0)
        if kept and total + added > max_bytes:
            break
        total += added
        kept += 1
    return kept


def _envelope_size(frame: dict, max_bytes: int, what: str) -> int:
    size = snapshot_byte_size({**frame, "turns": []})
    if size > max_bytes:
        raise ConversationSnapshotTooLargeError(f"{what} metadata exceeds byte budget")
    return size


def _log_oversize_item(page: dict, max_bytes: int) -> None:
    size = snapshot_byte_size(page)
    if size <= max_bytes:
        return
    turn = page["turns"][-1]
    window = turn.get(PART_WINDOW_KEY)
    # Nothing is lost (the item is delivered whole), so this is not a cap hit;
    # it is logged so oversize pages stay visible to the operator.
    logger.info(
        "History page is %d bytes over its %d-byte budget: %s %s is served whole rather than cut%s",
        size - max_bytes,
        max_bytes,
        "the smallest complete part range of turn" if window else "turn",
        turn.get("id"),
        "" if window else " (clients can request part_paging=1 to page within a turn)",
    )


def prepare_recent_snapshot(frame: dict, *, max_bytes: int, max_turns: int) -> dict:
    """Project a bounded recent window; the full transcript remains the paging source.

    Offsets refer to the original conversation, including an in-progress trailing
    turn. The byte budget drops the oldest whole turns; the newest turn is always
    kept whole, so one turn larger than ``max_bytes`` makes the frame larger than
    ``max_bytes``. A transport with a hard frame limit checks the result.
    """
    if max_bytes <= 0 or max_turns <= 0:
        raise ValueError("Recent snapshot budgets must be positive")
    source = frame["turns"]
    skipped = max(0, len(source) - max_turns)
    recent = {
        **frame,
        "turns": source[skipped:],
        "total_turns": frame.get("total_turns", len(source)),
        "window_offset": frame.get("window_offset", 0) + skipped,
        "recent_window": True,
        "detail": SHALLOW_DETAIL,
    }
    recent["turns"] = elide_turns(recent["turns"])
    recent = json_text_safe(recent)
    envelope = _envelope_size(recent, max_bytes, "Recent snapshot")
    turns = recent["turns"]
    kept = _turns_fit(envelope, [snapshot_byte_size(turn) for turn in turns], max_bytes)
    recent["window_offset"] += len(turns) - kept
    recent["turns"] = turns[len(turns) - kept :]
    if recent["turns"]:
        _log_oversize_item(recent, max_bytes)
    return recent


def _fragment(turn: dict, start: int, end: int) -> dict:
    """Turn identity and metadata with only its complete parts ``[start, end)``."""
    parts = turn.get("parts") or []
    fragment = {key: value for key, value in turn.items() if key != "parts"}
    fragment["parts"] = parts[start:end]
    if end < len(parts):
        for key in NEWEST_FRAGMENT_KEYS:
            fragment.pop(key, None)
    fragment[PART_WINDOW_KEY] = {"offset": start, "end": end, "total": len(parts)}
    return fragment


def _parts_fit(page: dict, turn: dict, part_start: int, part_end: int, max_bytes: int) -> int:
    """First part of the largest suffix of ``[part_start, part_end)`` that fits; >= 1 part.

    A page never starts on a tool_result whose tool_use is in the older range,
    so a call and its result travel together: the boundary moves to a newer
    part, or — when no newer one is a valid start — to the nearest valid older
    one, whatever its size.
    """
    parts = turn.get("parts") or []
    base = snapshot_byte_size({**page, "turns": [_fragment(turn, part_end, part_end)]})
    kept = _turns_fit(
        base, [snapshot_byte_size(part) for part in parts[part_start:part_end]], max_bytes
    )
    head = part_end - kept
    # earliest[i]: the lowest index of a tool_use in range whose result lies at
    # or after i. Starting at i is valid iff no such call precedes i.
    call_at = {
        part["id"]: index
        for index, part in enumerate(parts[part_start:part_end], part_start)
        if isinstance(part, dict) and part.get("type") == "tool_use" and part.get("id")
    }
    earliest = [part_end] * (part_end - part_start + 1)
    for index in range(part_end - 1, part_start - 1, -1):
        part = parts[index]
        call = part_end
        if isinstance(part, dict) and part.get("type") == "tool_result":
            call = call_at.get(part.get("tool_use_id"), part_end)
        earliest[index - part_start] = min(call, earliest[index - part_start + 1])
    candidates = [*range(head, part_end), *range(head - 1, part_start, -1)]
    return next((c for c in candidates if earliest[c - part_start] >= c), part_start)


def prepare_history_page(
    frame: dict,
    *,
    session_id: str,
    max_bytes: int,
    max_turns: int,
    cursor: str | None = None,
    part_paging: bool = False,
) -> dict:
    """Source-bounded wire projection with immutable seek/refresh boundaries.

    Only selected rows are elided/copied/JSON-encoded; identity scans are cheap
    references, not serialization of the unselected tool payloads. A refresh can
    require several bounded responses after growth: continuation_cursor names
    its remaining prefix, never a moving end-relative count.

    The budget drops older whole turns. When one turn alone exceeds it and
    ``part_paging`` was negotiated (or the cursor already names a part range), the
    page holds the largest suffix of its complete parts that fits — always at least
    one — with ``part_window`` on the turn and an ``older_cursor`` to its preceding
    parts. Without part paging the turn is elided deeper (``elide_turn_deep``: all
    its parts and tool calls kept, each body lazily loadable) when that fits, and
    served whole as it is otherwise (LEX-2017: clients reject an oversize page, so
    "Show earlier" stopped at such a turn).
    """
    source = frame["turns"]
    revision = frame.get("projection_revision") or projection_revision(source)
    window = select_history_window(
        source, session_id=session_id, revision=revision, limit=max_turns, cursor=cursor
    )
    part_paging = part_paging or window.parts is not None

    def metadata(start: int, parts: tuple[int, int] | None = None) -> dict:
        return history_page_metadata(
            source,
            session_id=session_id,
            revision=revision,
            start=start,
            end=window.end,
            requested=window,
            parts=parts,
        )

    start = max(window.start, window.end - max_turns)
    # Reserve room for a continuation token if byte trimming creates one, plus
    # the tiny change in encoded cursor lengths at digit boundaries.
    reserve = 1024
    budget = max_bytes - reserve
    if budget <= 0:
        raise ConversationSnapshotTooLargeError("History budget cannot fit the page envelope")
    page = json_text_safe(
        {
            **frame,
            **metadata(start, window.parts),
            "turns": [],
            "recent_window": True,
            "detail": SHALLOW_DETAIL,
        }
    )
    selected = json_text_safe(elide_turns(source[start : window.end]))
    last = selected[-1] if selected else None
    if window.parts is not None:
        selected[-1] = _fragment(last, *window.parts)
    envelope = _envelope_size(page, budget, "History page")
    kept = _turns_fit(envelope, [snapshot_byte_size(turn) for turn in selected], budget)
    start = window.end - kept
    turns = selected[len(selected) - kept :]
    parts = window.parts
    if kept == 1 and not part_paging and envelope + snapshot_byte_size(turns[0]) > budget:
        deep = json_text_safe(elide_turn_deep(source[window.end - 1]))
        if envelope + snapshot_byte_size(deep) <= budget:
            logger.info(
                "History page: turn %s is over its %d-byte budget at the shallow detail; "
                "served with every tool body elided (%d bytes)",
                deep.get("id"),
                budget,
                snapshot_byte_size(deep),
            )
            turns = [deep]
    if kept == 1 and part_paging and envelope + snapshot_byte_size(turns[0]) > budget:
        part_start, part_end = parts or (0, len(last.get("parts") or []))
        if part_end - part_start > 1:
            sized = {**page, **metadata(start, (part_start, part_end))}
            head = _parts_fit(sized, last, part_start, part_end, budget)
            if parts is not None or head > 0:
                # Otherwise no valid boundary exists: the whole turn is the atom.
                parts = (head, part_end)
                turns = [_fragment(last, head, part_end)]
    page.update(metadata(start, parts), turns=turns)
    if turns:
        _log_oversize_item(page, max_bytes)
    return page
