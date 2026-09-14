"""Fit legacy complete snapshots and explicitly negotiated recent-history windows."""

import json

from niuu.domain.history_paging import history_page_metadata, select_history_window
from niuu.domain.json_text import json_text_safe
from niuu.domain.text_projection import projection_revision
from skuld.conversation_shallow import SHALLOW_DETAIL, elide_turns


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


def prepare_recent_snapshot(frame: dict, *, max_bytes: int, max_turns: int) -> dict:
    """Project a bounded recent window; the full transcript remains the paging source.

    Offsets refer to the original conversation, including an in-progress trailing
    turn. An unusually large single turn is an explicit preview, never presented
    as a complete replacement for its stored source.
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
    while len(recent["turns"]) > 1 and snapshot_byte_size(recent) > max_bytes:
        recent["turns"] = recent["turns"][1:]
        recent["window_offset"] += 1
    if snapshot_byte_size(recent) <= max_bytes:
        return recent
    if not recent["turns"]:
        raise ConversationSnapshotTooLargeError("Recent snapshot metadata exceeds byte budget")

    turn = dict(recent["turns"][0])
    recent["turns"] = [turn]
    recent["history_preview"] = True
    turn["history_preview"] = True
    text_budget = max_bytes // 4
    for key in ("content", "reasoning"):
        if isinstance(turn.get(key), str):
            turn[key] = _text_tail(turn[key], text_budget)
    parts = [dict(part) for part in (turn.get("parts") or [])]
    original_part_count = len(parts)
    turn["parts"] = parts
    if len(parts) > 1 and snapshot_byte_size(recent) > max_bytes:
        # Find the largest fitting suffix without serializing successively
        # shorter 100k-part arrays (quadratic work on a pathological one-row
        # history). Keep at least one part for the text-trimming step below.
        low, high, keep = 1, len(parts) - 1, 1
        while low <= high:
            count = (low + high) // 2
            turn["parts"] = parts[-count:]
            if snapshot_byte_size(recent) <= max_bytes:
                keep = count
                low = count + 1
            else:
                high = count - 1
        parts = parts[-keep:]
        turn["parts"] = parts
    if parts and snapshot_byte_size(recent) > max_bytes:
        last = dict(parts[-1])
        if last.get("type") in ("text", "thinking", "reasoning"):
            for key in ("text", "thinking", "content"):
                if isinstance(last.get(key), str):
                    last[key] = _text_tail(last[key], text_budget)
            turn["parts"] = [last]
        if snapshot_byte_size(recent) > max_bytes:
            # The original tool/attachment remains available through REST. Do not
            # fabricate a successful result or an actionable partial question.
            turn["parts"] = []
    turn["preview_omitted_parts"] = original_part_count - len(turn["parts"])
    # JSON escaping can expand a control character to six wire bytes. Measure
    # the actual envelope after each trim rather than assuming UTF-8 length fits.
    while snapshot_byte_size(recent) > max_bytes and text_budget > 0:
        text_budget //= 2
        for key in ("content", "reasoning"):
            if isinstance(turn.get(key), str):
                turn[key] = _text_tail(turn[key], text_budget)
        for part in turn["parts"]:
            if part.get("type") in ("text", "thinking", "reasoning"):
                for key in ("text", "thinking", "content"):
                    if isinstance(part.get(key), str):
                        part[key] = _text_tail(part[key], text_budget)
    if snapshot_byte_size(recent) > max_bytes:
        raise ConversationSnapshotTooLargeError("Recent turn metadata exceeds byte budget")
    return recent


def _text_tail(text: str, max_bytes: int) -> str:
    """Keep the newest text without splitting a UTF-8 character."""
    if max_bytes <= 0:
        return ""
    return text.encode("utf-8")[-max_bytes:].decode("utf-8", errors="ignore")


def prepare_history_page(
    frame: dict, *, session_id: str, max_bytes: int, max_turns: int, cursor: str | None = None
) -> dict:
    """Source-bounded wire projection with immutable seek/refresh boundaries.

    Only selected rows are elided/copied/JSON-encoded; identity scans are cheap
    references, not serialization of the unselected tool payloads. A refresh can
    require several bounded responses after growth: continuation_cursor names
    its remaining prefix, never a moving end-relative count.
    """
    source = frame["turns"]
    revision = frame.get("projection_revision") or projection_revision(source)
    window = select_history_window(
        source, session_id=session_id, revision=revision, limit=max_turns, cursor=cursor
    )

    def metadata(start: int) -> dict:
        return history_page_metadata(
            source,
            session_id=session_id,
            revision=revision,
            start=start,
            end=window.end,
            requested=window,
        )

    start = max(window.start, window.end - max_turns)
    page = {**frame, **metadata(start), "turns": source[start : window.end]}
    # Reserve room for a continuation token if byte trimming creates one, plus
    # the tiny change in encoded cursor lengths at digit boundaries.
    reserve = 1024
    budget = max_bytes - reserve
    if budget <= 0:
        raise ConversationSnapshotTooLargeError("History budget cannot fit the page envelope")
    try:
        page = prepare_recent_snapshot(page, max_bytes=budget, max_turns=max_turns)
    except ConversationSnapshotTooLargeError:
        if not page["turns"]:
            raise
        # A single item can have enormous metadata as well as prose/tools.
        # Preserve its identity but expose NONE of a misleading partial tool
        # group. Exact full content remains available by explicit item lookup.
        original = page["turns"][-1]
        page.update(metadata(window.end - 1))
        page["turns"] = [
            {
                **{
                    k: original[k]
                    for k in ("id", "role", "created_at", "in_progress")
                    if k in original
                },
                "content": "",
                "parts": [],
                "history_preview": True,
                "preview_omitted_parts": len(original.get("parts") or []),
            }
        ]
        page["history_preview"] = True
    if page.get("history_preview"):
        for turn in page["turns"]:
            if turn.get("history_preview"):
                turn["history_ref"] = {"turn_id": turn["id"]}
                # Existing single-row fitting can keep a result without its call.
                # An item preview must not claim a partial tool group is complete.
                turn["preview_omitted_parts"] = len(
                    source[page["window_offset"]].get("parts") or []
                )
                turn["parts"] = []
    page.update(metadata(page["window_offset"]))
    if snapshot_byte_size(page) > max_bytes:
        raise ConversationSnapshotTooLargeError("History identity/envelope exceeds byte budget")
    return page
