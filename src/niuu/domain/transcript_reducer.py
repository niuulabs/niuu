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
  (``interrupted`` / ``error``) and ``provenance`` (``terminal_scrape``). The old rebuild-only
  ``modelUsage`` key is gone — it is normalised to ``usage`` here. There is deliberately NO
  ``source`` provenance tag: metadata must be path-IDENTICAL (live == rebuild) for INV-4, and a
  "which path produced this" marker cannot be both meaningful and identical across paths.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

# Deterministic namespace for folded-turn ids (stable across reloads AND across paths).
_TURN_NAMESPACE = uuid.UUID("6f2d2e2a-7b1c-4e8a-9d3f-0a1b2c3d4e5f")

# Per-reasoning-block summary cap (kept identical to the historical live + rebuild behaviour).
_REASONING_TAIL = 500

# Synthetic / non-wire kinds: rows written to the durable log purely as derived
# reducer SEEDS, never broadcast to a live channel (SRD FR-3 / FR-10). The broker
# appends ``conversation.turn`` rows via ``Broker._append_turn`` so a fold (live OR
# crash-rebuild) can consume them as authoritative ``sdk_turns`` without
# re-deriving turns from raw frames — but the live broadcast NEVER emits them. The
# RAW-streaming read paths (cold-read GET /log, replay-as-live tail) therefore MUST
# exclude these so literal frame-for-frame equality holds (live == replay == cold).
# This is NOT a visibility concern (independent of ``show_internal``); it is the set
# of kinds that simply do not belong on the wire. The reduce/rebuild path STILL
# reads them as authoritative seeds — only the verbatim wire stream drops them.
NON_BROADCAST_KINDS: frozenset[str] = frozenset({"conversation.turn"})


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

    def touch(self, ts: datetime | None, seq: int) -> None:
        if ts is not None:
            self.last_ts = ts
        self.last_seq = max(self.last_seq, seq)

    def is_empty(self) -> bool:
        return not self.content and not self.parts and not self.reasoning

    def reset(self) -> None:
        self.content = ""
        self.parts = []
        self.reasoning = ""
        self.pending_tmux_rows = None
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


def apply_assistant_blocks(acc: TurnAccumulator, content_blocks: list) -> None:
    """Fold an ``assistant`` frame's content blocks (text / thinking / tool_use) into ``acc``.

    Mirrors the live broker's accumulation EXACTLY: text blocks append to both ``parts`` and
    ``content`` (newline-joined), thinking blocks append a capped reasoning part, tool_use
    blocks append a tool card carrying subagent attribution when present.
    """
    if not isinstance(content_blocks, list):
        return
    text_parts: list[str] = []
    for block in content_blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text" and block.get("text"):
            text_parts.append(block["text"])
            acc.parts.append({"type": "text", "text": block["text"]})
        elif btype == "thinking" and block.get("thinking"):
            summary = str(block["thinking"])[-_REASONING_TAIL:]
            acc.parts.append({"type": "reasoning", "text": summary})
        elif btype == "tool_use" and block.get("id"):
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
            acc.parts.append(part)
    text_content = "\n".join(text_parts)
    if text_content:
        acc.content = f"{acc.content}\n{text_content}" if acc.content else text_content


def apply_text_delta(acc: TurnAccumulator, text: str) -> None:
    """Fold a streaming text delta into ``acc`` (HTTP streaming format)."""
    if text:
        acc.content += text


def apply_thinking_delta(acc: TurnAccumulator, thinking: str) -> None:
    """Fold a streaming thinking delta into ``acc`` (HTTP streaming format)."""
    if thinking:
        acc.reasoning += thinking


def apply_tool_result_blocks(acc: TurnAccumulator, blocks: list) -> None:
    """Enrich the OPEN assistant turn with tool_result blocks (a tool_result-only user event
    is NOT a new turn — it carries the output of the calls already in ``acc``).
    """
    for block in blocks or []:
        if not (isinstance(block, dict) and block.get("type") == "tool_result"):
            continue
        if not block.get("tool_use_id"):
            continue
        acc.parts.append(
            {
                "type": "tool_result",
                "tool_use_id": block.get("tool_use_id"),
                "content": block.get("content"),
                "is_error": bool(block.get("is_error")),
            }
        )


def apply_tmux_rows(acc: TurnAccumulator, rows: list[str]) -> None:
    """Stash terminal pane rows as the last-resort scrape source for the open turn."""
    acc.pending_tmux_rows = rows


# --------------------------------------------------------------------------- turn builders


def finalize_parts(acc: TurnAccumulator) -> list[dict]:
    """The turn's parts at flush time: accumulated parts plus a trailing reasoning summary
    (reasoning is appended AT FLUSH on both paths — this fixes the old ordering divergence).
    """
    parts = list(acc.parts)
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
    """Lift usage/cost/model from a ``result`` frame into the UNIFIED metadata schema.

    Accepts either the live wire key ``modelUsage`` or a pre-normalised ``usage`` and always
    emits ``{usage, cost, model}`` — the names the UI reads — plus ``stop_reason`` when present.
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
    return md


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
        return ReduceResult(turns=[], partial=False)

    session_id = _session_id_of(rows[0])
    folded_request_ids = set(folded_request_ids or set())
    seen_ids = set(seen_ids or set())
    turns: list[dict[str, Any]] = list(sdk_turns or [])

    acc = TurnAccumulator()
    # turn_id -> (seq, steering_state): the delivery-state transitions seen in the log, applied
    # onto the matching user turns at the end (last-writer-wins by seq, == the live path).
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
        if k in NON_BROADCAST_KINDS or k in _IGNORED_KINDS:
            continue
        if r.request_id and r.request_id in folded_request_ids:
            continue
        p = r.payload if isinstance(r.payload, dict) else {}
        seq = int(r.seq)
        ts = _ts_of(r)

        if k in _STEERING_FRAME_KINDS:
            _record_steering(steering, k, p, seq)
            # user_confirmed also seeds/dedups the user turn (handled below); user_active /
            # user_delivery_failed carry no content, so they are pure state transitions.
            if k != "user_confirmed":
                continue

        if k in ("user", "user_confirmed"):
            _apply_user_frame(acc, turns, seen_ids, session_id, seq, ts, k, p, flush)
            continue

        if k == "assistant":
            apply_assistant_blocks(acc, _content_blocks(p))
            acc.touch(ts, seq)
            continue

        if k == "content_block_delta":
            delta = p.get("delta", {}) if isinstance(p.get("delta"), dict) else {}
            if delta.get("type") == "thinking_delta":
                apply_thinking_delta(acc, delta.get("thinking", ""))
            else:
                apply_text_delta(acc, delta.get("text", ""))
            acc.touch(ts, seq)
            continue

        if k in ("terminal_frame", "terminal_snapshot"):
            rows_text = p.get("rows")
            if not isinstance(rows_text, list):
                rows_text = str(p.get("text", "")).split("\n")
            apply_tmux_rows(acc, rows_text)
            acc.touch(ts, seq)
            continue

        if k == "result":
            if acc.is_empty():
                acc.content = str(p.get("result", "") or "")
            acc.touch(ts, seq)
            flush(md=result_metadata(p))
            continue

        if k == "error":
            flush()
            acc.touch(ts, seq)
            turns.append(build_error_turn(session_id, seq, _error_text(p), ts))
            continue

    if not acc.is_empty() or acc.pending_tmux_rows is not None:
        flush(status="interrupted")

    _apply_steering_states(turns, steering)

    partial = any(t.get("metadata", {}).get("status") in ("interrupted", "error") for t in turns)
    return ReduceResult(turns=turns, partial=partial)


# --------------------------------------------------------------------------- internal helpers


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


def _apply_user_frame(acc, turns, seen_ids, session_id, seq, ts, kind, payload, flush) -> None:
    """Fold a ``user`` or ``user_confirmed`` frame.

    Epic-A carry-over: ONE human message is durably written as BOTH a ``user`` frame (string
    content, ``uuid``) AND a ``user_confirmed`` broker frame (``id`` + ``content``). They share
    the id/content, so dedup on that id and emit a single user turn — never a doubled turn.
    """
    content = _user_string_content(payload) if kind == "user" else _confirmed_content(payload)
    if content is None:
        if kind == "user":
            apply_tool_result_blocks(acc, _tool_result_blocks(payload))
            acc.touch(ts, seq)
        return
    uid = _user_id(payload, kind)
    if uid and uid in seen_ids:
        return  # already emitted (seed double-log, or the user/user_confirmed pair)
    flush()
    if uid:
        seen_ids.add(uid)
    turns.append(build_user_turn(session_id, seq, content, turn_id=uid or None, ts=ts))


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
