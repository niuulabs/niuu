"""Shallow (elided) transcript of a session, folded incrementally from its event log.

A session whose broker does not answer is read from its durable log. The whole-log rebuild
decodes up to 50k frames (hundreds of MB of Python objects) and only then elides and pages
the turns, so one ``?detail=shallow&limit=1`` poll cost as much as a full read, and every
new frame of a running session made the next poll pay again. After a Forge restart every
session is in that state at once; the rebuilds ran together and the pod was OOMKilled
(forge-hosts, 2026-11-22).

``ShallowTranscriptFold`` keeps the rows the rebuild can still read, with their heavy
content already elided, and nothing else:

* Once an assistant ``conversation.turn`` row exists, the rebuild never reduces the raw
  frames before it (``niuu.domain.transcript_rebuild``). Up to that **boundary** only the
  turn rows and the small delivery/gap markers (``SETTLED_KINDS``) are read, page by page,
  and each turn is elided as its page arrives.
* Frames after the boundary are the live tail. They are read per request and not kept.

``turns`` is ``rebuild_turns`` over those rows, so it is the same transcript as the whole
rebuild (``tests/test_volundr/test_shallow_transcript.py`` compares the two), with the same
absolute indices, and a refresh costs the frames since the last turn ended.

Two kinds of log need the original prose and tool blocks of settled turns: one with a stored
text repair, and one whose old aggregate-only turns have native frames to repair them from.
The fold says so (``needs_whole_log``) and the caller reads those through the whole rebuild.

Pure: no I/O, no ``skuld`` imports. The elision is the caller's (``elide_turn``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from niuu.domain.conversation_timeline import TIMELINE_KEY
from niuu.domain.text_projection import REPAIR_KIND, is_legacy_text_turn
from niuu.domain.transcript_rebuild import rebuild_turns
from niuu.domain.transcript_reducer import (
    CONVERSATIONAL_KINDS,
    GAP_KINDS,
    STEERING_FRAME_KINDS,
    assistant_turn_id,
    reduce_frames,
)
from volundr.domain.services.attachments_index import HISTORY_IMPORT_KIND, TURN_ROW_KIND

if TYPE_CHECKING:
    from volundr.domain.models import SessionLogEntry

ElideTurn = Callable[[Any], Any]

_RESULT_KIND = "result"
_MARKER_KINDS = STEERING_FRAME_KINDS | GAP_KINDS | {HISTORY_IMPORT_KIND, REPAIR_KIND}

SETTLED_KINDS = _MARKER_KINDS | {TURN_ROW_KIND, _RESULT_KIND}
"""The rows before the last settled turn that can still change the transcript. ``result``
rows are only looked at (see :meth:`ShallowTranscriptFold.needs_whole_log`), never kept."""

REBUILD_KINDS = CONVERSATIONAL_KINDS | _MARKER_KINDS | {TURN_ROW_KIND}
"""Every kind the rebuild reads. All others (hooks, session chrome, telemetry) leave its
output unchanged, so the live tail is read without them."""

_STEERING_KEYS = ("id", "uuid", "steering_state", "accepted_at")


@dataclass
class ShallowTranscriptFold:
    """The elided rows of one session's log up to its last settled turn.

    ``scanned_to`` is the last ``SETTLED_KINDS`` row taken in; ``resume_after`` the last
    settled one, so the live tail starts after it.
    """

    session_id: str
    elide_turn: ElideTurn
    resume_after: int = 0
    scanned_to: int = 0
    import_after: int = 0
    truncated: bool = False
    _rows: list[SessionLogEntry] = field(default_factory=list)
    _pending: list[SessionLogEntry] = field(default_factory=list)
    _imported: list[dict[str, Any]] = field(default_factory=list)
    _repaired: bool = False
    _legacy_ids: set[str] = field(default_factory=set)
    _native_turn_ids: set[str] = field(default_factory=set)

    @property
    def needs_whole_log(self) -> bool:
        """True when a settled turn's text is repaired from evidence the fold does not keep.

        A native (thread- and turn-tagged) ``result`` closes the turn whose id is derived
        from its seq, so a legacy turn with that id is one the rebuild would repair.
        """
        return self._repaired or not self._legacy_ids.isdisjoint(self._native_turn_ids)

    def absorb_import(self, history: list[SessionLogEntry], marker_seq: int) -> None:
        """Settle an imported native history: ``history`` is every row before its marker."""
        self._imported += [self.elide_turn(turn) for turn in reduce_frames(history).turns]
        self._rows = []
        self._pending = []
        self._legacy_ids.clear()
        self._native_turn_ids.clear()
        self.resume_after = self.import_after = self.scanned_to = marker_seq

    def settle(self, page: list[SessionLogEntry]) -> None:
        """Take in the ``SETTLED_KINDS`` rows after ``scanned_to``.

        Everything up to the newest assistant turn row is settled; the rest waits.
        """
        if not page:
            return
        rows = [
            *self._pending,
            *(kept for row in page if (kept := self._compact(row)) is not None),
        ]
        self.scanned_to = page[-1].seq
        boundary = next(
            (i for i in range(len(rows) - 1, -1, -1) if _is_assistant_turn(rows[i])), -1
        )
        self._pending = rows[boundary + 1 :]
        if boundary < 0:
            return
        self._rows += rows[: boundary + 1]
        self.resume_after = rows[boundary].seq

    def turns(self, tail: list[SessionLogEntry]) -> list[dict[str, Any]]:
        """The whole elided transcript; ``tail`` is every row after ``resume_after``."""
        live = rebuild_turns([*self._rows, *tail]).turns
        return [*self._imported, *(_unstamp(self.elide_turn(turn)) for turn in live)]

    def _compact(self, row: SessionLogEntry) -> SessionLogEntry | None:
        payload = row.payload if isinstance(row.payload, dict) else {}
        if row.kind == TURN_ROW_KIND:
            return self._compact_turn(row, payload.get("turn"))
        if row.kind in STEERING_FRAME_KINDS:
            return replace(row, payload={k: payload[k] for k in _STEERING_KEYS if k in payload})
        if row.kind in GAP_KINDS:
            return replace(row, payload={})
        if row.kind == REPAIR_KIND:
            self._repaired = True
            return None
        if row.kind == _RESULT_KIND and _is_native(payload):
            self._native_turn_ids.add(assistant_turn_id(self.session_id, int(row.seq)))
        return None

    def _compact_turn(self, row: SessionLogEntry, turn: Any) -> SessionLogEntry:
        if not isinstance(turn, dict):
            return replace(row, payload={})
        if is_legacy_text_turn(turn):
            self._legacy_ids.add(turn["id"])
        return replace(row, payload={"turn": _elide_stamped(turn, self.elide_turn)})


def _is_assistant_turn(row: SessionLogEntry) -> bool:
    if row.kind != TURN_ROW_KIND:
        return False
    turn = row.payload.get("turn")
    return isinstance(turn, dict) and turn.get("role") == "assistant"


def _is_native(payload: dict[str, Any]) -> bool:
    return all(
        isinstance(payload.get(key), str) and payload[key] for key in ("thread_id", "turn_id")
    )


def _elide_stamped(turn: dict[str, Any], elide_turn: ElideTurn) -> dict[str, Any]:
    """Elide ``turn`` but keep each part's timeline stamp: the rebuild positions by it."""
    elided = elide_turn(turn)
    parts = turn.get("parts")
    if not isinstance(parts, list):
        return elided
    kept = [
        {**after, TIMELINE_KEY: before[TIMELINE_KEY]}
        if isinstance(before, dict) and TIMELINE_KEY in before and TIMELINE_KEY not in after
        else after
        for before, after in zip(parts, elided["parts"], strict=True)
    ]
    return {**elided, "parts": kept}


def _unstamp(turn: dict[str, Any]) -> dict[str, Any]:
    """Drop the stamps :func:`_elide_stamped` kept on placeholders, as elision does."""
    parts = turn.get("parts")
    if not isinstance(parts, list) or not any(_is_stamped_placeholder(part) for part in parts):
        return turn
    return {
        **turn,
        "parts": [
            {key: value for key, value in part.items() if key != TIMELINE_KEY}
            if _is_stamped_placeholder(part)
            else part
            for part in parts
        ],
    }


def _is_stamped_placeholder(part: Any) -> bool:
    return (
        isinstance(part, dict)
        and part.get("type") == "tool_result"
        and bool(part.get("truncated"))
        and "content" not in part
        and TIMELINE_KEY in part
    )
