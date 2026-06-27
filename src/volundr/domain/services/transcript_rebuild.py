"""Transcript rebuild — fold the durable event log into renderable conversation turns.

BUG-2 fix: a tmux-interactive Forge session that crashes mid-turn (WS dies while the
agent is blocked on AskUserQuestion) reloads as an empty "dead" session even though
hundreds of frames are durably persisted in ``session_event_log``. The conversation read
path historically rebuilt turns ONLY from ``kind == "conversation.turn"`` rows (the SDK
happy path), so a tmux/crash session — whose work survives as raw ``terminal_frame`` /
``assistant`` / ``content_block_delta`` / ``result`` frames — returned nothing.

SRD FR-3 / INV-4: this module is the BATCH driver of the ONE shared reducer
(``niuu.domain.transcript_reducer``). The live broker fold and this rebuild both apply the
SAME per-frame transitions — the live path incrementally as frames stream, this path across
the whole ordered list — so a log replay folds byte-identically to what streamed live (under
one turn-id policy + one metadata schema). There is no second folding implementation: the
divergence in turn-id policy, reasoning ordering, and metadata key names is gone.

This stays a PURE domain helper — no I/O, no ``skuld`` imports — called only by
``SessionArchiveService._load_event_log_transcript``.

Data-safety contract (see the Bug-2 spec §8):
  * Fallback-only: the caller runs this only after live/archive sources are empty.
  * No write-back: never mutates ``message_count`` (the /usage path is the single writer).
  * No double-count: ``conversation.turn`` rows are authoritative; raw frames that share a
    saved turn's ``request_id`` (or a human turn's ``uuid``) are skipped — this neutralises
    the seed double-log (the same human turn written as BOTH a conversation.turn AND a raw
    ``user`` frame with the same uuid), AND the Epic-A ``user`` + ``user_confirmed`` pair.
  * Ordering: strictly by ``entry.seq`` (the outer log seq), never ``payload["seq"]``.
  * Idempotent: rebuilt/interrupted turn ids are ``uuid5(session_id, seq, role)`` so
    repeated reloads produce byte-identical turns (no flicker for polling clients).
  * Partial turns: assistant work with no terminating ``result`` (the incident) is flushed
    at end-of-stream as one assistant turn flagged ``metadata.status="interrupted"``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from niuu.domain.transcript_reducer import reduce_frames

if TYPE_CHECKING:
    from volundr.domain.models import SessionLogEntry


@dataclass
class RebuildResult:
    """Outcome of a log replay. ``partial`` is True if any turn was interrupted/errored."""

    turns: list[dict[str, Any]]
    partial: bool = False


def rebuild_turns(entries: list[SessionLogEntry]) -> RebuildResult:
    """Fold ordered ``SessionLogEntry`` rows into renderable conversation turns."""
    rows = sorted(entries, key=lambda e: e.seq)
    if not rows:
        return RebuildResult(turns=[], partial=False)

    sdk_turn_rows = [r for r in rows if r.kind == "conversation.turn"]
    folded_request_ids = {r.request_id for r in sdk_turn_rows if r.request_id}

    # ---- PASS 1: conversation.turn rows are AUTHORITATIVE (SDK happy path + seed user) ----
    sdk_turns: list[dict] = []
    seen_ids: set[str] = set()
    for r in sdk_turn_rows:
        payload = r.payload if isinstance(r.payload, dict) else {}
        turn = payload.get("turn")
        if not isinstance(turn, dict):
            continue
        tid = str(turn.get("id") or "").strip()
        if tid and tid in seen_ids:
            continue
        if tid:
            seen_ids.add(tid)
        # Remember a human conversation.turn's uuid so the raw `user`/`user_confirmed`
        # seed double-log dedups in PASS 2.
        uid = str(turn.get("uuid") or turn.get("id") or "").strip()
        if turn.get("role") == "user" and uid:
            seen_ids.add(uid)
        # Append VERBATIM — conversation.turn rows are already serialized turns; the SDK
        # transcript must reload byte-identical (do NOT re-normalize / add fields here).
        sdk_turns.append(turn)

    # ---- PASS 2: raw-frame / tmux reduction via the SHARED reducer (the crash tail, or a
    # pure tmux session). Same per-frame transitions the live broker drives. ----
    result = reduce_frames(
        rows,
        sdk_turns=sdk_turns,
        folded_request_ids=folded_request_ids,
        seen_ids=seen_ids,
        scrape=_extract_assistant_text,
    )
    return RebuildResult(turns=result.turns, partial=result.partial)


# --------------------------------------------------------------------------- tmux pane scrape


# Box-drawing / TUI chrome that must never appear in a scraped assistant turn.
_CHROME_PREFIXES = ("╭", "╰", "│", "┌", "└", "├", "┤", "─", ">", "?")
_CHROME_SUBSTRINGS = (
    "? for shortcuts",
    "esc to interrupt",
    "auto-accept edits",
    "bypassing permissions",
)


def _extract_assistant_text(rows: list[str]) -> str:
    """LAST-RESORT pane scrape — vendored from the tmux transport's heuristic (kept
    skuld-free to respect the hexagonal boundary; covered by a fixture-parity test).

    Strips the input box, box-drawing chrome, and status lines, returning the agent's
    visible prose. Used ONLY when a tmux turn produced no delta/assistant frames.
    """
    out: list[str] = []
    for raw in rows:
        line = (raw or "").rstrip()
        stripped = line.strip()
        if not stripped:
            if out and out[-1] != "":
                out.append("")  # collapse runs of blanks, keep paragraph breaks
            continue
        if stripped[0] in _CHROME_PREFIXES:
            continue
        low = stripped.lower()
        if any(s in low for s in _CHROME_SUBSTRINGS):
            continue
        out.append(line)
    return "\n".join(out).strip()
