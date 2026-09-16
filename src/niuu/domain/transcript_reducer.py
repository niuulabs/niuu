"""Shared pure transcript reducer — the SINGLE folding contract (SRD FR-3 / INV-4).

There used to be two independent implementations of "fold frames into conversation
turns": the LIVE one in ``skuld.broker._handle_cli_event`` (incremental, frame-by-frame
as deltas stream) and the REBUILD one in ``volundr...transcript_rebuild.rebuild_turns``
(batch, over the whole durable log). They drifted — different turn-id policy, different
reasoning ordering, different metadata key names — and a UI reading ``metadata.usage`` got
nothing back from a rebuilt turn. There was no parity test pinning them together.

This module is the one place the per-frame STATE TRANSITION lives. A single frame mutates a
:class:`TurnAccumulator`; flushing the accumulator produces a turn dict. The two paths drive
the SAME transitions:

  * the LIVE path applies them one frame at a time as frames arrive (broker keeps its own
    streaming/broadcast side-effects, but the turn it *builds* comes from here);
  * the REBUILD path applies them across the full ordered list (``reduce_frames``).

Identical transitions over the same frames ⇒ identical resulting turns ⇒ INV-4 holds by
construction.

niuu is the correct home: both ``skuld`` and ``volundr`` already import ``niuu``, and a pure
reducer here avoids deepening the skuld↔volundr coupling. This module imports NOTHING from
``volundr`` or ``skuld`` (module-boundaries rule) — it takes duck-typed frame objects.

## Unified turn-id policy (chosen ONCE here)

  * assistant / synthesised turns: ``uuid5(_TURN_NAMESPACE, "{session_id}:{seq}:{role}")``
    where ``seq`` is the durable-log seq of the LAST frame folded into the turn. Deterministic
    so repeated rebuilds are byte-identical (no flicker for polling clients), AND the live path
    uses the same seq (its ``_event_log_seq`` at flush time) so live id == rebuilt id for the
    same logical turn.
  * user turns: the human turn's own id/uuid when the frame carries one (the live broker mints
    a uuid4 ``msg_id`` and writes it into BOTH the ``user`` frame's ``uuid`` and the
    ``user_confirmed`` frame's ``id``); otherwise the deterministic seq-based id. Using the
    carried id means live and rebuild agree on the user-turn id without coordination.

## Unified metadata schema (chosen ONCE here)

  ``{usage, cost, model}`` (the names the UI reads), plus optional ``stop_reason``, ``status``
  (``interrupted`` / ``error``) and ``provenance`` (``terminal_scrape``). Failed results also
  retain ``is_error``, ``error`` and ``messageType: "error"`` so a transport failure cannot
  masquerade as a successful completion after reload. The old rebuild-only
  ``modelUsage`` key is gone — it is normalised to ``usage`` here. There is deliberately NO
  ``source`` provenance tag: metadata must be path-IDENTICAL (live == rebuild) for INV-4, and a
  "which path produced this" marker cannot be both meaningful and identical across paths.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from niuu.domain.conversation_timeline import TIMELINE_KEY, project_timeline, stamp_parts, timeline
from niuu.domain.history_control import is_history_control

# Deterministic namespace for folded-turn ids (stable across reloads AND across paths).
_TURN_NAMESPACE = uuid.UUID("6f2d2e2a-7b1c-4e8a-9d3f-0a1b2c3d4e5f")

# Per-reasoning-block summary cap (kept identical to the historical live + rebuild behaviour).
_REASONING_TAIL = 500

# --------------------------------------------------------------------------- D1 tool timing
#
# Per-tool wall-clock, stamped onto the CONVERSATION WIRE so a client can say "ran in 3m 12s"
# about ONE tool (or one burst of tools) instead of deriving it from turn boundaries — which
# over-counts, because a turn's span includes model thinking and prose, and a text→tools→
# text→tools turn would report the SAME whole-run span on every burst inside it.
#
# Placement (the contract iOS/ForgeKit decodes against):
#   * ``tool_use``    part → ``started_at``, ``ended_at``, ``duration_ms``
#   * ``tool_result`` part → ``ended_at``
# ALL THREE client-facing values ride the tool_use part on purpose: it is the block that
# survives shallow elision by ``{**block}`` spread, and the one ForgeKit already decodes into
# a typed struct. The tool_result stamp is the orphan-safe fallback (a result whose call was
# in an already-flushed turn still reports when it landed).
#
# Every key is ADDITIVE and OMITTED ENTIRELY when unknown: a frame with no timestamp produces
# the exact byte-identical part dict it produced before D1, so a stored transcript, an old
# broker, and a client that never learned these keys are all unaffected.
#
# Honest semantics: "started" = when the broker OBSERVED the tool_use frame, "ended" = when it
# observed the tool_result. On the tmux/Claude plane those come from PreToolUse/PostToolUse
# hooks (millisecond-accurate boundaries); on SDK/streaming transports a message's tool_use
# blocks arrive together, so parallel calls report overlapping windows. This is wall-clock
# observation latency, NOT CPU time. A transport that knows better may stamp ``started_at`` /
# ``ended_at`` on the raw block itself — the reducer prefers a block-carried stamp over the
# frame ts, so hook-exact timing is a one-line transport change later.
TOOL_STARTED_AT = "started_at"
TOOL_ENDED_AT = "ended_at"
TOOL_DURATION_MS = "duration_ms"

# Synthetic / non-wire kinds: derived reducer seeds and atomic import markers,
# never broadcast to a live channel (SRD FR-3 / FR-10). The broker
# appends ``conversation.turn`` rows via ``Broker._append_turn`` so a fold (live OR
# crash-rebuild) can consume them as authoritative ``sdk_turns`` without
# re-deriving turns from raw frames — but the live broadcast NEVER emits them. The
# RAW-streaming read paths (cold-read GET /log, replay-as-live tail) therefore MUST
# exclude these so literal frame-for-frame equality holds (live == replay == cold).
# This is NOT a visibility concern (independent of ``show_internal``); it is the set
# of kinds that simply do not belong on the wire. The reduce/rebuild path STILL
# reads them as authoritative seeds or snapshot boundaries; only the verbatim
# wire stream drops them.
NON_BROADCAST_KINDS: frozenset[str] = frozenset(
    {"conversation.turn", "conversation.projection", "history_import"}
)

# Per-connect handshake/preamble frames: addressed to ONE freshly-connecting
# socket, NOT the canonical shared stream (SRD FR-7 / INV-5). On every browser
# connect the broker emits a ``system`` "Connected to session …" welcome and a
# ``capabilities`` catalog to THAT socket only (via ``_safe_send_broker_frame_to``).
# A LIVE client sees exactly ONE such pair — its own. But the durable log
# accumulates one pair PER historical connect (each logged with fresh seqs, INV-1
# superset preserved), so a cold-read or replay-as-live tail would re-stream every
# historical handshake and a reconnected session's read paths would surface
# system+capabilities frames a live viewer never saw — breaking INV-5.
#
# These frames ARE broadcast (to one socket), so they are NOT in
# ``NON_BROADCAST_KINDS`` (whose docstring pins it to "never broadcast live").
# Instead the RAW read paths (cold-read GET /log, replay-as-live tail) drop them
# the same mechanism as ``conversation.turn``: a connecting client always gets its
# OWN fresh handshake/preamble, so surfacing historical ones is pure duplication.
#
# ``capabilities`` is unconditionally a per-connect frame (its only producer is the
# first-connect handshake). The ``system`` welcome shares its kind with genuine CLI
# ``system`` frames (e.g. ``system``/``init``), so it CANNOT be excluded by kind
# alone — the broker stamps it with the ``PER_CONNECT_MARKER`` payload key, which
# :func:`is_per_connect_ephemeral` keys on. Genuine CLI ``system`` frames carry no
# such marker and pass through untouched.
PER_CONNECT_EPHEMERAL_KINDS: frozenset[str] = frozenset({"capabilities"})

# Inert payload marker the broker stamps onto a per-connect handshake frame whose
# kind is NOT uniquely ephemeral (the ``system`` welcome). The read paths key on it
# via :func:`is_per_connect_ephemeral`. It is inert on the wire — a live client that
# receives its own fresh handshake ignores the extra key.
PER_CONNECT_MARKER: str = "_per_connect_handshake"


def is_per_connect_ephemeral(kind: str, payload: dict | None) -> bool:
    """True if a durable-log frame is a per-connect handshake/preamble (SRD INV-5).

    Per-connect handshakes are addressed to ONE freshly-connecting socket, never the
    canonical shared stream, so the RAW read paths must not surface HISTORICAL ones
    (a connecting client always gets its own fresh pair). ``capabilities`` is always
    such a frame; a ``system`` welcome is identified by the ``PER_CONNECT_MARKER``
    payload key the broker stamps (so genuine CLI ``system`` frames are unaffected).
    """
    if kind in PER_CONNECT_EPHEMERAL_KINDS or is_history_control(kind, payload):
        return True
    return isinstance(payload, dict) and bool(payload.get(PER_CONNECT_MARKER))


def is_read_path_excluded(kind: str, payload: dict | None) -> bool:
    """True if a durable-log frame must be dropped from the RAW read-path streams.

    The ONE place that decides what the cold-read (GET /log) and replay-as-live tail
    exclude so literal frame-for-frame live==replay==cold equality holds (SRD INV-5),
    independent of ``show_internal`` (a separate visibility concern). Two rationales,
    both "this frame was never on the canonical shared wire":

      * ``NON_BROADCAST_KINDS`` — derived ``conversation.turn`` seeds and atomic
        ``history_import`` markers, never broadcast to ANY channel; the
        reduce/rebuild path still reads them.
      * per-connect handshakes (:func:`is_per_connect_ephemeral`) — broadcast to ONE
        socket only; a connecting client gets its own fresh handshake.

    The frames remain in the durable log (INV-1 superset); only the verbatim wire
    projection drops them.
    """
    return kind in NON_BROADCAST_KINDS or is_per_connect_ephemeral(kind, payload)


@runtime_checkable
class Frame(Protocol):
    """The minimal shape the reducer needs from a durable-log entry.

    Both ``volundr.domain.models.SessionLogEntry`` and the live broker's buffered dict-frames
    satisfy this structurally — the reducer never imports either type.
    """

    seq: int
    kind: str
    payload: dict
    request_id: str | None


@dataclass
class TurnAccumulator:
    """Mutable per-turn span. The SAME object the live path threads frame-to-frame and the
    batch path rebuilds; flushing it yields one turn dict.
    """

    content: str = ""
    parts: list[dict] = field(default_factory=list)
    reasoning: str = ""
    last_ts: datetime | None = None
    last_seq: int = 0
    # tmux LAST-RESORT pane rows, used only when no delta/assistant content exists.
    pending_tmux_rows: list[str] | None = None
    native_import: dict | None = None

    def touch(self, ts: datetime | None, seq: int) -> None:
        if ts is not None:
            self.last_ts = ts
        self.last_seq = max(self.last_seq, seq)

    def is_empty(self) -> bool:
        return (
            not self.content
            and not self.reasoning
            and not any(part.get("type") != "text" or part.get("text") for part in self.parts)
        )

    def reset(self) -> None:
        self.content = ""
        self.parts = []
        self.reasoning = ""
        self.pending_tmux_rows = None
        self.native_import = None
        # last_ts / last_seq intentionally retained as a floor for the next span.


# --------------------------------------------------------------------------- id policy


def assistant_turn_id(session_id: str, seq: int, role: str = "assistant") -> str:
    """Deterministic id for a folded turn (same formula on both paths)."""
    return str(uuid.uuid5(_TURN_NAMESPACE, f"{session_id}:{seq}:{role}"))


# --------------------------------------------------------------------------- steering state
#
# Delivery lifecycle of a HUMAN turn: the broker accepts a message ("pending"), the agent
# consumes it ("active"), or delivery terminally fails ("failed"). The live broker stamps this
# onto the in-memory user turn's ``metadata.steering_state``; the SAME transition must be
# reconstructable from the durable log (SRD INV-4 fold parity + INV-7 delivery integrity), or a
# rebuilt turn would silently lose the delivery state a live viewer saw.
#
# Every transition is already a logged broker frame keyed by the user-turn id:
#   * ``user_confirmed``        carries an explicit ``steering_state`` (the accept = "pending");
#   * ``user_active``           == the agent consumed it ("active");
#   * ``user_delivery_failed``  == terminal failure ("failed").
# (``user_delivered`` means "typed into the pane", NOT yet consumed — it deliberately does NOT
# change steering_state, matching the live path, where the active flip rides ``user_active``.)
#
# This is the ONE policy: both the live broker (stamping its in-memory turn as it logs these
# frames) and the rebuild (below) call ``steering_state_from_frame`` so they cannot diverge.

# Frame kinds that carry a user-turn delivery-state transition, keyed by the user-turn id.
_STEERING_FRAME_KINDS = frozenset({"user_confirmed", "user_active", "user_delivery_failed"})


def steering_state_from_frame(kind: str, payload: dict) -> str | None:
    """The steering_state a delivery-ACK frame implies for its user turn, or None.

    Single source of truth for the pending/active/failed transition so the live fold and the
    log rebuild stamp byte-identical metadata. ``user_confirmed`` honours the explicit
    ``steering_state`` it carries (defaulting to "pending"); ``user_active`` is "active";
    ``user_delivery_failed`` is "failed". Any other frame yields None (no transition)."""
    if kind == "user_confirmed":
        state = payload.get("steering_state")
        if isinstance(state, str) and state:
            return state
        return "pending"
    if kind == "user_active":
        return "active"
    if kind == "user_delivery_failed":
        return "failed"
    return None


def steering_target_id(kind: str, payload: dict) -> str:
    """The user-turn id a steering-ACK frame targets (its ``id``/``uuid``)."""
    return str(payload.get("id") or payload.get("uuid") or "").strip()


# --------------------------------------------------------------------------- transitions
#
# Each function is a PURE state transition: it mutates ``acc`` for one frame's worth of input.
# The live path calls them as frames stream; the batch path calls them while walking the list.


def apply_assistant_blocks(
    acc: TurnAccumulator, content_blocks: list, *, ts: datetime | str | None = None
) -> None:
    """Fold an ``assistant`` frame's content blocks (text / thinking / tool_use) into ``acc``.

    Mirrors the live broker's accumulation EXACTLY: text blocks append to both ``parts`` and
    ``content`` (newline-joined), thinking blocks append a capped reasoning part, tool_use
    blocks append a tool card carrying subagent attribution when present.

    ``ts`` is the frame's timestamp (D1 per-tool timing): when given, every tool_use part is
    stamped ``started_at``. Optional and omitted-when-absent so every historical caller and
    every stored transcript keeps its exact pre-D1 shape (see ``_stamp_iso``).
    """
    if not isinstance(content_blocks, list):
        return
    started_at = _stamp_iso(ts)
    for block in content_blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text" and isinstance(block.get("text"), str):
            apply_text_message(acc, block)
        elif btype == "thinking" and block.get("thinking"):
            summary = str(block["thinking"])[-_REASONING_TAIL:]
            acc.parts.append({"type": "reasoning", "text": summary})
        elif btype == "tool_use" and block.get("id"):
            existing = next(
                (
                    part
                    for part in reversed(acc.parts)
                    if part.get("type") == "tool_use" and part.get("id") == block["id"]
                ),
                None,
            )
            if existing is not None:
                # Native tools can reveal their arguments only at completion.
                # A same-ID refresh enriches the original card; its original
                # timing and attribution remain authoritative when omitted here.
                for key in ("name", "input", "parent_tool_use_id", "agent_id"):
                    if key in block:
                        existing[key] = block[key]
                continue
            part: dict[str, Any] = {
                "type": "tool_use",
                "id": block.get("id"),
                "name": block.get("name"),
                "input": block.get("input") or {},
            }
            if block.get("parent_tool_use_id") is not None:
                part["parent_tool_use_id"] = block.get("parent_tool_use_id")
            if block.get("agent_id") is not None:
                part["agent_id"] = block.get("agent_id")
            # D1: prefer a transport-stamped start (hook-exact) over the frame ts.
            block_started = _stamp_iso(block.get(TOOL_STARTED_AT)) or started_at
            if block_started:
                part[TOOL_STARTED_AT] = block_started
            acc.parts.append(part)


_TEXT_METADATA = ("phase", "turn_id", "thread_id", "index", "id_source")


def _text_identity(frame: dict, block: dict | None = None) -> str | None:
    value = (block or {}).get("id") or frame.get("item_id")
    return value if isinstance(value, str) and value else None


def _text_metadata(part: dict, source: dict) -> None:
    for key in _TEXT_METADATA:
        value = source.get(key)
        if key == "index":
            valid = type(value) is int and value >= 0
        elif key == "phase":
            valid = value in ("commentary", "final_answer")
        else:
            valid = isinstance(value, str) and bool(value)
        if valid:
            part[key] = value


def _find_text_part(acc: TurnAccumulator, frame: dict) -> dict | None:
    identity = _text_identity(frame)
    for part in reversed(acc.parts):
        if part.get("type") != "text":
            continue
        if identity is not None:
            if part.get("id") != identity:
                continue
            if any(
                frame.get(key) and part.get(key) and frame[key] != part[key]
                for key in ("turn_id", "thread_id")
            ):
                continue
            return part
        if part.get("complete") is False and (
            frame.get("index") is None or frame.get("index") == part.get("index")
        ):
            return part
    return None


def _sync_text_content(acc: TurnAccumulator) -> None:
    """Compatibility prose; exact per-item bytes live in ordered parts.

    Legacy whole-message transcripts keep their historical single-newline join.
    Identified messages have a paragraph boundary, never a separator per delta.
    """
    text = ""
    previous: dict | None = None
    for part in acc.parts:
        value = part.get("text")
        if part.get("type") != "text" or not isinstance(value, str) or not value:
            continue
        if previous is not None:
            text += "\n\n" if previous.get("id") or part.get("id") else "\n"
        text += value
        previous = part
    acc.content = text


def _preserve_legacy_content(acc: TurnAccumulator) -> None:
    # Older callers/cache seeds can supply aggregate-only prose. Preserve it as
    # an opaque legacy block; this cannot reconstruct its original chronology.
    if acc.content and not any(p.get("type") == "text" and p.get("text") for p in acc.parts):
        acc.parts.append({"type": "text", "text": acc.content})


def apply_text_start(acc: TurnAccumulator, frame: dict) -> None:
    """Reserve the message's first-seen position before tools or later text arrive."""
    block = frame.get("content_block")
    if not isinstance(block, dict) or block.get("type") != "text":
        return
    if block.get("phase", frame.get("phase")) == "analysis":
        return
    identity = _text_identity(frame, block)
    lookup = {**frame, "item_id": identity}
    part = _find_text_part(acc, lookup) if identity else None
    if part is not None and part.get("complete") is True:
        return  # Replay of an old start cannot reopen a completed message.
    if part is None:
        _preserve_legacy_content(acc)
        part = {
            "type": "text",
            "id": identity or f"legacy-text-{len(acc.parts)}",
            "text": "",
            "complete": False,
        }
        if identity is None:
            part["id_source"] = "synthetic"
        acc.parts.append(part)
    _text_metadata(part, frame)
    _text_metadata(part, block)
    if not part["text"] and isinstance(block.get("text"), str) and block["text"]:
        part["text"] = block["text"]
        _sync_text_content(acc)


def apply_text_delta(acc: TurnAccumulator, text: str, *, frame: dict | None = None) -> None:
    """Append exact chunk bytes to one open text item, never to the whole turn."""
    if not isinstance(text, str) or not text:
        return
    source = frame or {}
    if source.get("phase") == "analysis":
        return
    part = _find_text_part(acc, source)
    if part is None:
        apply_text_start(acc, {**source, "content_block": {"type": "text"}})
        part = _find_text_part(acc, source)
    if part is None or part.get("complete") is True:
        return
    _text_metadata(part, source)
    part["text"] += text
    _sync_text_content(acc)


def apply_content_block_start(acc: TurnAccumulator, block: dict) -> None:
    """Apply a text boundary from callers using the block-only interface."""
    apply_text_start(acc, {"content_block": block})


def apply_text_stop(acc: TurnAccumulator, frame: dict) -> None:
    part = _find_text_part(acc, frame)
    if part is not None:
        _text_metadata(part, frame)
        part["complete"] = True


def apply_text_message(acc: TurnAccumulator, block: dict) -> None:
    """Upsert authoritative full text by item identity without moving its anchor."""
    if block.get("phase") == "analysis":
        return
    text = block.get("text")
    if not isinstance(text, str):
        return
    identity = _text_identity({}, block)
    if identity is None:
        if text:
            _preserve_legacy_content(acc)
            acc.parts.append({"type": "text", "text": text})
            _sync_text_content(acc)
        return
    source = {**block, "item_id": identity}
    part = _find_text_part(acc, source)
    if part is None:
        apply_text_start(acc, {**source, "content_block": {"type": "text", "id": identity}})
        part = _find_text_part(acc, source)
    if part is None or (part.get("complete") is True and block.get("complete") is False):
        return
    _text_metadata(part, block)
    part["text"] = text
    part["complete"] = block.get("complete") is not False
    _sync_text_content(acc)


def apply_thinking_delta(acc: TurnAccumulator, thinking: str) -> None:
    """Fold a streaming thinking delta into ``acc`` (HTTP streaming format)."""
    if thinking:
        acc.reasoning += thinking


def apply_tool_result_blocks(
    acc: TurnAccumulator, blocks: list, *, ts: datetime | str | None = None
) -> None:
    """Enrich the OPEN assistant turn with tool_result blocks (a tool_result-only user event
    is NOT a new turn — it carries the output of the calls already in ``acc``).

    ``ts`` is the frame's timestamp (D1 per-tool timing): when given, the tool_result part is
    stamped ``ended_at`` AND the matching ``tool_use`` part already in ``acc.parts`` is
    back-filled with ``ended_at`` + ``duration_ms``. The back-fill mutates the part dict IN
    PLACE, which is what makes the live plane work: the broker's ``_pending_accumulator()``
    hands the reducer the very same ``parts`` list object it serves from, so an in-progress
    poll and the eventual flushed turn both see the completed timing.
    """
    ended_at = _stamp_iso(ts)
    for block in blocks or []:
        if not (isinstance(block, dict) and block.get("type") == "tool_result"):
            continue
        tool_use_id = block.get("tool_use_id")
        if not tool_use_id:
            continue
        part: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": block.get("content"),
            "is_error": bool(block.get("is_error")),
        }
        # D1: prefer a transport-stamped end (hook-exact) over the frame ts.
        block_ended = _stamp_iso(block.get(TOOL_ENDED_AT)) or ended_at
        if block_ended:
            part[TOOL_ENDED_AT] = block_ended
        acc.parts.append(part)
        if block_ended:
            _close_tool_use(acc.parts, str(tool_use_id), block_ended)


def _close_tool_use(parts: list[dict], tool_use_id: str, ended_at: str) -> None:
    """Back-fill ``ended_at`` / ``duration_ms`` onto the tool_use part this result closes.

    Scans ``parts`` in REVERSE for the most recent matching, still-open call. Deliberately
    index-free: the state lives entirely in the accumulator's own parts list, so there is no
    second structure that the live plane (which rebuilds a throw-away ``TurnAccumulator`` view
    per frame) and the batch rebuild could get out of step on — the seam is parity-safe by
    construction. A LATE / ORPHAN result (its call was in an already-flushed turn, or never
    seen) simply finds nothing and is a no-op; the result part still carries its own
    ``ended_at``.
    """
    for part in reversed(parts):
        if part.get("type") != "tool_use" or str(part.get("id") or "") != tool_use_id:
            continue
        if TOOL_ENDED_AT in part:
            continue  # already closed — a duplicate/replayed result must not re-stamp
        part[TOOL_ENDED_AT] = ended_at
        duration = _duration_ms(part.get(TOOL_STARTED_AT), ended_at)
        if duration is not None:
            part[TOOL_DURATION_MS] = duration
        return


def apply_tmux_rows(acc: TurnAccumulator, rows: list[str]) -> None:
    """Stash terminal pane rows as the last-resort scrape source for the open turn."""
    acc.pending_tmux_rows = rows


def apply_result_content(acc: TurnAccumulator, payload: dict) -> None:
    """Inject a ``result`` frame's text into the OPEN turn when no assistant text streamed yet.

    The ONE policy shared by the live broker's result-close and the batch rebuild's result
    branch (SRD INV-4 / FR-3 "one reducer"). A turn that only emitted tool_use blocks (so
    ``acc.content`` is still empty) but is closed by a result carrying text — either the
    top-level ``result`` string or a ``content`` text block — must surface that text as its
    content on BOTH paths. The guard is "no streamed assistant text" (empty ``content``), NOT
    ``is_empty()``: a tool_use-only turn has non-empty ``parts`` yet empty ``content``, and the
    live viewer saw the result text, so a rebuild must too.

    A failed result may carry ONLY ``error`` (Codex socket EOF does this). Preserve the
    failure as visible content when no prose exists. If text already streamed, its content
    stays intact and :func:`result_metadata` carries the diagnostic without duplicating it.
    """
    if acc.content:
        return
    text = str(payload.get("result", "") or "")
    if not text:
        for block in payload.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                text = block["text"]
                break
    if not text and _result_status(payload) == "error":
        text = _result_error_text(payload)
    if text:
        apply_text_message(acc, {"type": "text", "text": text})


# --------------------------------------------------------------------------- turn builders


def finalize_parts(acc: TurnAccumulator) -> list[dict]:
    """The turn's parts at flush time: accumulated parts plus a trailing reasoning summary
    (reasoning is appended AT FLUSH on both paths — this fixes the old ordering divergence).
    """
    parts = [part for part in acc.parts if part.get("type") != "text" or part.get("text")]
    if acc.reasoning:
        parts.append({"type": "reasoning", "text": acc.reasoning[-_REASONING_TAIL:]})
    return parts


def build_assistant_turn(
    session_id: str,
    acc: TurnAccumulator,
    metadata: dict | None = None,
    *,
    status: str | None = None,
    scrape_text: str | None = None,
) -> dict | None:
    """Build a completed assistant turn from ``acc`` (the flush). Returns None when there is
    nothing to flush (so an empty result never creates a phantom turn).

    ``scrape_text`` is the tmux pane scrape, used ONLY when no delta/assistant content exists.
    """
    text = acc.content
    parts = finalize_parts(acc)
    meta = dict(metadata or {})
    if acc.native_import is not None:
        meta["native_import"] = dict(acc.native_import)
    if not text and not parts and scrape_text:
        text = scrape_text
        meta["provenance"] = "terminal_scrape"
    if not text and not parts:
        return None
    if status:
        meta["status"] = status
    return _turn_dict(
        id=assistant_turn_id(session_id, acc.last_seq),
        role="assistant",
        content=text,
        parts=parts,
        metadata=meta,
        ts=acc.last_ts,
    )


def build_user_turn(
    session_id: str,
    seq: int,
    content: str,
    *,
    turn_id: str | None = None,
    metadata: dict | None = None,
    ts: datetime | None = None,
) -> dict:
    """Build a user turn. Uses the carried ``turn_id`` (so live==rebuild) or the seq-based id."""
    tid = turn_id or assistant_turn_id(session_id, seq, "user")
    return _turn_dict(
        id=tid,
        role="user",
        content=content,
        parts=[],
        metadata=dict(metadata or {}),
        ts=ts,
    )


def build_error_turn(session_id: str, seq: int, text: str, ts: datetime | None = None) -> dict:
    return _turn_dict(
        id=assistant_turn_id(session_id, seq),
        role="assistant",
        content=text,
        parts=[{"type": "text", "text": text}] if text else [],
        metadata={"status": "error"},
        ts=ts,
    )


def result_metadata(payload: dict) -> dict:
    """Lift accounting and terminal outcome into the UNIFIED metadata schema.

    Accepts either the live wire key ``modelUsage`` or a pre-normalised ``usage`` and always
    emits ``{usage, cost, model}`` — the names the UI reads — plus ``stop_reason`` when present.
    Errors remain errors even after prose streamed, and intentional interruption remains
    distinguishable from a provider failure. Successful results keep their existing shape.
    """
    usage = payload.get("modelUsage")
    if not isinstance(usage, dict):
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    cost: float | None = None
    model: str | None = None
    for model_id, u in usage.items():
        model = model_id
        if isinstance(u, dict) and u.get("costUSD") is not None:
            cost = (cost or 0) + u["costUSD"]
    md: dict[str, Any] = {"usage": usage, "cost": cost, "model": model}
    stop_reason = payload.get("stop_reason")
    if stop_reason is not None:
        md["stop_reason"] = stop_reason
    status = _result_status(payload)
    if status is not None:
        md["status"] = status
    if payload.get("is_error") is True:
        md["is_error"] = True
    if status == "error":
        md.update(is_error=True, error=_result_error_text(payload), messageType="error")
    return md


def _result_status(payload: dict) -> str | None:
    stop_reason = str(payload.get("stop_reason") or "").lower()
    if stop_reason in {"interrupted", "cancelled", "canceled", "aborted"}:
        return "interrupted"
    if (
        payload.get("is_error") is True
        or stop_reason in {"error", "errored", "failed"}
        or str(payload.get("subtype") or "").startswith("error_")
    ):
        return "error"
    return None


def _result_error_text(payload: dict) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        error = error.get("message")
    if isinstance(error, str) and error.strip():
        return error
    errors = payload.get("errors")
    if isinstance(errors, list):
        messages = [message for message in errors if isinstance(message, str) and message.strip()]
        if messages:
            return "\n".join(messages)
    result = payload.get("result")
    if isinstance(result, str) and result.strip():
        return result
    return "The agent stopped with an error."


# --------------------------------------------------------------------------- batch driver


# Frame kinds that are session chrome / control, never conversational content.
_IGNORED_KINDS = frozenset(
    {
        "system",
        "init",
        "control_request",
        "control_response",
        "ask_user_question",
        "ask_user_resolved",
        "available_commands",
        "session_updated",
        "terminal_input_sent",
        "terminal_key_sent",
        "tool_use",  # surfaced inside assistant frames; standalone tool_use is telemetry
        "tool_result",
        "log_gap",  # Epic-A overflow sentinel: a detectable hole marker, NOT content.
        "log_conflict",  # INV-3c sentinel: a distinct-payload seq collision marker, NOT content.
    }
)

_CONVERSATIONAL_KINDS = frozenset(
    {
        "user",
        "user_confirmed",
        "assistant",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "result",
        "error",
        "terminal_frame",
        "terminal_snapshot",
    }
)


@dataclass
class ReduceResult:
    turns: list[dict[str, Any]]
    partial: bool = False


def reduce_frames(
    frames: list[Frame],
    *,
    sdk_turns: list[dict] | None = None,
    folded_request_ids: set[str] | None = None,
    seen_ids: set[str] | None = None,
    scrape: Any | None = None,
) -> ReduceResult:
    """Batch driver (the REBUILD path): fold an ordered frame list into turns using the SAME
    per-frame transitions the live path drives one at a time.

    ``sdk_turns`` are authoritative ``conversation.turn`` payloads emitted first (verbatim);
    ``folded_request_ids`` / ``seen_ids`` carry their correlation so raw frames are not
    double-folded and the seed double-log (``user`` + ``user_confirmed`` for one human turn)
    dedups to a single user turn. ``scrape`` is an optional ``(rows)->str`` callable for the
    tmux last-resort pane scrape (kept out of niuu to respect the boundary).
    """
    rows = sorted(frames, key=lambda e: e.seq)
    if not rows:
        # No raw frames to reduce — the result is just the authoritative SDK turns (the rebuild
        # passes only the uncovered tail; an empty tail = every message has a conversation.turn).
        return ReduceResult(turns=list(sdk_turns or []), partial=False)

    session_id = _session_id_of(rows[0])
    folded_request_ids = set(folded_request_ids or set())
    seen_ids = set(seen_ids or set())
    # Delivery folding must not mutate seed payloads supplied by a repository
    # or a caller retaining the original ledger rows. Preserve absent metadata.
    turns: list[dict[str, Any]] = [
        {
            **turn,
            **(
                {"metadata": dict(turn["metadata"])}
                if isinstance(turn.get("metadata"), dict)
                else {}
            ),
        }
        for turn in sdk_turns or []
    ]

    acc = TurnAccumulator()
    imported_prefix_open = False
    # A terminal pane keeps repainting after a result. Public replay has no
    # conversation.turn seed to suppress that idle scrollback, so a completed
    # span stays closed until a new human turn or assistant activity opens it.
    # Initial/raw-only history still admits the last-resort pane fallback.
    terminal_span_closed = False
    # Preserve legacy raw-tail delivery folding. Only explicitly timed user seeds
    # opt into the additional cross-seed lifecycle reconciliation below.
    steering: dict[str, tuple[int, str]] = {}

    def flush(status: str | None = None, md: dict | None = None) -> None:
        scrape_text = None
        if scrape is not None and acc.pending_tmux_rows is not None:
            scrape_text = scrape(acc.pending_tmux_rows)
        turn = build_assistant_turn(
            session_id, acc, metadata=md, status=status, scrape_text=scrape_text
        )
        if turn is not None:
            turns.append(turn)
        acc.reset()

    for r in rows:
        k = r.kind
        p = r.payload if isinstance(r.payload, dict) else {}
        # Exclude before any accumulator/import boundary transition. A failed
        # viewer history delivery must never terminate the running agent span.
        if is_per_connect_ephemeral(k, p):
            continue
        metadata = p.get("metadata")
        is_imported = isinstance(metadata, dict) and isinstance(metadata.get("native_import"), dict)
        # An imported snapshot is a distinct historical prefix. If it ended
        # mid-turn, a subsequent live result must never finish that old work.
        # Raw DB reads carry the boundary marker; public replay filters it out,
        # so native provenance supplies the SAME boundary at the next live frame.
        if imported_prefix_open and (
            k == "history_import" or (not is_imported and k in _CONVERSATIONAL_KINDS)
        ):
            flush(status="interrupted")
            imported_prefix_open = False
        if is_imported:
            imported_prefix_open = True
        if k in NON_BROADCAST_KINDS or k in _IGNORED_KINDS:
            continue
        if r.request_id and r.request_id in folded_request_ids:
            continue
        seq = int(r.seq)
        ts = _ts_of(r)

        if k in _STEERING_FRAME_KINDS:
            _record_steering(steering, k, p, seq)
            # user_confirmed also seeds/dedups the user turn (handled below); user_active /
            # user_delivery_failed carry no content, so they are pure state transitions.
            if k != "user_confirmed":
                continue

        if k in ("user", "user_confirmed"):
            # Tool-result frames belong to the open assistant span. Human user
            # frames flush that span first and carry their own provenance below.
            if is_imported and _user_string_content(p) is None:
                acc.native_import = acc.native_import or dict(metadata["native_import"])
            if _apply_user_frame(acc, turns, seen_ids, session_id, seq, ts, k, p, flush):
                terminal_span_closed = False
            continue

        if is_imported:
            acc.native_import = acc.native_import or dict(metadata["native_import"])

        if k == "assistant":
            terminal_span_closed = False
            # D1: the frame's durable ts IS the tool_use start stamp — the same instant the
            # live broker passes here as it enqueues the frame, so both planes agree exactly.
            part_start = len(acc.parts)
            apply_assistant_blocks(acc, _content_blocks(p), ts=ts)
            stamp_parts(acc.parts, p, start=part_start)
            acc.touch(ts, seq)
            continue

        if k == "content_block_start":
            terminal_span_closed = False
            part_start = len(acc.parts)
            apply_text_start(acc, p)
            stamp_parts(acc.parts, p, start=part_start)
            acc.touch(ts, seq)
            continue

        if k == "content_block_stop":
            part_start = len(acc.parts)
            apply_text_stop(acc, p)
            stamp_parts(acc.parts, p, start=part_start)
            acc.touch(ts, seq)
            continue

        if k == "content_block_delta":
            delta = p.get("delta", {}) if isinstance(p.get("delta"), dict) else {}
            if delta.get("text") or delta.get("thinking"):
                terminal_span_closed = False
            part_start = len(acc.parts)
            if delta.get("type") == "thinking_delta":
                apply_thinking_delta(acc, delta.get("thinking", ""))
            else:
                apply_text_delta(acc, delta.get("text", ""), frame=p)
            stamp_parts(acc.parts, p, start=part_start)
            acc.touch(ts, seq)
            continue

        if k in ("terminal_frame", "terminal_snapshot"):
            if terminal_span_closed:
                continue
            rows_text = p.get("rows")
            if not isinstance(rows_text, list):
                rows_text = str(p.get("text", "")).split("\n")
            apply_tmux_rows(acc, rows_text)
            acc.touch(ts, seq)
            continue

        if k == "result":
            part_start = len(acc.parts)
            apply_result_content(acc, p)
            stamp_parts(acc.parts, p, start=part_start)
            acc.touch(ts, seq)
            flush(md=result_metadata(p))
            terminal_span_closed = True
            continue

        if k == "error":
            flush()
            acc.touch(ts, seq)
            turns.append(build_error_turn(session_id, seq, _error_text(p), ts))
            terminal_span_closed = True
            continue

    if not acc.is_empty() or acc.pending_tmux_rows is not None:
        flush(status="interrupted")

    _apply_steering_states(turns, steering)
    turns = apply_steering_frames(turns, rows)

    partial = any(
        t.get("metadata", {}).get("status") in ("interrupted", "error")
        or bool(t.get("metadata", {}).get("native_import", {}).get("partial"))
        for t in turns
    )
    return ReduceResult(turns=project_timeline(turns, session_id), partial=partial)


# --------------------------------------------------------------------------- internal helpers


def apply_steering_frames(turns: list[dict], frames: list[Frame]) -> list[dict]:
    """Apply logged delivery outcomes even when saved assistant seeds cover the raw tail.

    Content seeds do not supersede lifecycle ACKs for timed same-turn input.
    Untimed authoritative history retains its prior projection exactly: this is
    not a retrospective repair of legacy delivery labels. Copy timed user metadata
    so a read never mutates an authoritative stored payload.
    """
    result = [
        {
            **turn,
            **(
                {"metadata": dict(turn["metadata"])}
                if isinstance(turn.get("metadata"), dict)
                else {}
            ),
        }
        if turn.get("role") == "user"
        else turn
        for turn in turns
    ]
    states: dict[str, tuple[int, str]] = {}
    users = {
        turn.get("id"): turn
        for turn in result
        if turn.get("role") == "user" and timeline(turn.get("metadata")) is not None
    }
    for row in sorted(frames, key=lambda frame: frame.seq):
        payload = row.payload
        if not isinstance(payload, dict) or steering_target_id(row.kind, payload) not in users:
            continue
        _record_steering(states, row.kind, payload, row.seq)
        accepted = payload.get("accepted_at")
        if row.kind != "user_active" or not isinstance(accepted, str) or not accepted:
            continue
        user = users.get(payload.get("id"))
        if user is not None:
            user.setdefault("metadata", {}).setdefault("steering_accepted_at", accepted)
    _apply_steering_states(result, states)
    return result


def _record_steering(
    steering: dict[str, tuple[int, str]], kind: str, payload: dict, seq: int
) -> None:
    """Stash the delivery-state transition a steering-ACK frame implies, keyed by its target
    user-turn id, keeping the LAST writer by seq (== the live path's mutate-in-place order)."""
    state = steering_state_from_frame(kind, payload)
    if state is None:
        return
    tid = steering_target_id(kind, payload)
    if not tid:
        return
    prior = steering.get(tid)
    if prior is not None and prior[0] >= seq:
        return
    steering[tid] = (seq, state)


def _apply_steering_states(
    turns: list[dict[str, Any]], steering: dict[str, tuple[int, str]]
) -> None:
    """Stamp the reconstructed ``steering_state`` onto each user turn whose id saw a delivery
    transition — byte-identical to what the live broker mutates onto its in-memory turn."""
    if not steering:
        return
    for turn in turns:
        if turn.get("role") != "user":
            continue
        resolved = steering.get(str(turn.get("id") or ""))
        if resolved is None:
            continue
        turn.setdefault("metadata", {})["steering_state"] = resolved[1]


def _apply_user_frame(acc, turns, seen_ids, session_id, seq, ts, kind, payload, flush) -> bool:
    """Fold a ``user`` or ``user_confirmed`` frame.

    Epic-A carry-over: ONE human message is durably written as BOTH a ``user`` frame (string
    content, ``uuid``) AND a ``user_confirmed`` broker frame (``id`` + ``content``). They share
    the id/content, so dedup on that id and emit a single user turn — never a doubled turn.
    Return whether a new human turn was added; a replayed confirmation cannot reopen an idle pane.
    """
    content = _user_string_content(payload) if kind == "user" else _confirmed_content(payload)
    if content is None:
        if kind == "user":
            # D1: the frame's durable ts IS the tool_result end stamp (see the assistant branch).
            part_start = len(acc.parts)
            apply_tool_result_blocks(acc, _tool_result_blocks(payload), ts=ts)
            stamp_parts(acc.parts, payload, start=part_start)
            acc.touch(ts, seq)
        return False
    uid = _user_id(payload, kind)
    if uid and uid in seen_ids:
        return False  # already emitted (seed double-log, or the user/user_confirmed pair)
    # A same-turn steering input is a display boundary, NOT an execution end.
    # Keep native items open so a later full text/tool completion updates the
    # original item. The shared presentation projection positions the input.
    if timeline(payload) is None:
        flush()
    if uid:
        seen_ids.add(uid)
    metadata = {}
    source_metadata = payload.get("metadata")
    if isinstance(source_metadata, dict) and isinstance(source_metadata.get("native_import"), dict):
        metadata["native_import"] = dict(source_metadata["native_import"])
    if payload.get("request_id"):
        metadata["request_id"] = payload["request_id"]
    if timeline(payload) is not None:
        metadata[TIMELINE_KEY] = dict(timeline(payload))
    turns.append(
        build_user_turn(session_id, seq, content, turn_id=uid or None, ts=ts, metadata=metadata)
    )
    return True


def _turn_dict(
    *, id: str, role: str, content: str, parts: list[dict], metadata: dict, ts: datetime | None
) -> dict:
    return {
        "id": id,
        "role": role,
        "content": content,
        "parts": parts,
        "created_at": _iso(ts),
        "metadata": metadata,
        "visibility": "public",
    }


def _user_string_content(payload: dict) -> str | None:
    msg = payload.get("message")
    content = msg.get("content") if isinstance(msg, dict) else payload.get("content")
    if isinstance(content, str) and content.strip():
        return content
    return None


def _confirmed_content(payload: dict) -> str | None:
    content = payload.get("content")
    if isinstance(content, str) and content.strip():
        return content
    return None


def _user_id(payload: dict, kind: str) -> str:
    if kind == "user_confirmed":
        return str(payload.get("id") or payload.get("uuid") or "").strip()
    return str(payload.get("uuid") or payload.get("id") or "").strip()


def _tool_result_blocks(payload: dict) -> list[dict]:
    msg = payload.get("message")
    content = msg.get("content") if isinstance(msg, dict) else payload.get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]


def _content_blocks(payload: dict) -> list:
    msg = payload.get("message")
    blocks = msg.get("content") if isinstance(msg, dict) else payload.get("content")
    return blocks if isinstance(blocks, list) else []


def _error_text(payload: dict) -> str:
    err = payload.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or "") or "Unknown error"
    return str(payload.get("content") or err or "Unknown error")


def _session_id_of(frame: Frame) -> str:
    sid = getattr(frame, "session_id", None)
    return str(sid) if sid is not None else ""


def _ts_of(frame: Frame) -> datetime | None:
    return getattr(frame, "ts", None)


def _iso(ts: datetime | None) -> str:
    if ts is None:
        return ""
    try:
        return ts.isoformat()
    except Exception:  # noqa: BLE001 — defensive; ts is best-effort metadata
        return str(ts)


def _stamp_iso(ts: datetime | str | None) -> str | None:
    """The ISO string to stamp for ``ts``, or None when there is nothing honest to stamp.

    Accepts a ``datetime`` (the batch path reads ``SessionLogEntry.ts``; the live path passes
    the very instant it hands ``_enqueue_event_log``) or an already-ISO string (a transport
    that pre-stamped the raw block). Returns None — never ``""`` — for absent/empty input so
    the caller OMITS the key rather than writing an empty stamp.
    """
    if ts is None:
        return None
    text = _iso(ts) if isinstance(ts, datetime) else str(ts)
    return text or None


def _duration_ms(started_at: Any, ended_at: str) -> int | None:
    """Elapsed milliseconds between two ISO stamps, or None when it cannot be computed.

    Both endpoints are read back as STRINGS (the start was already stamped onto the part), so
    the arithmetic is identical on the live and rebuild planes — neither reaches for a
    datetime the other does not have. A negative span (clock step / skew) clamps to 0 rather
    than surfacing a nonsense "-3 ms" in a UI.
    """
    if not isinstance(started_at, str) or not started_at:
        return None
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(ended_at)
    except ValueError:
        return None
    if (start.tzinfo is None) != (end.tzinfo is None):
        return None  # naive vs aware — not subtractable, and guessing would be a lie
    return max(0, int((end - start).total_seconds() * 1000))
