"""Pure transcript reducer — reconstruct conversation turns from durable event-log frames.

BUG-2 fix: a tmux-interactive Forge session that crashes mid-turn (WS dies while the
agent is blocked on AskUserQuestion) reloads as an empty "dead" session even though
hundreds of frames are durably persisted in ``session_event_log``. The conversation read
path historically rebuilt turns ONLY from ``kind == "conversation.turn"`` rows (the SDK
happy path), so a tmux/crash session — whose work survives as raw ``terminal_frame`` /
``assistant`` / ``content_block_delta`` / ``result`` frames — returned nothing.

This module folds the FULL ordered frame list into renderable turns, mirroring the
broker's live ``_handle_cli_event`` logic (``src/skuld/broker.py`` ~3108-3200) so a
log-replay matches what streamed live. It is a PURE domain helper — no I/O, no ``skuld``
imports — called only by ``SessionArchiveService._load_event_log_transcript``.

Data-safety contract (see the Bug-2 spec §8):
  * Fallback-only: the caller runs this only after live/archive sources are empty.
  * No write-back: never mutates ``message_count`` (the /usage path is the single writer).
  * No double-count: ``conversation.turn`` rows are authoritative; raw frames that share a
    saved turn's ``request_id`` (or a human turn's ``uuid``) are skipped — this neutralises
    the seed double-log (the same human turn written as BOTH a conversation.turn AND a raw
    ``user`` frame with the same uuid).
  * Ordering: strictly by ``entry.seq`` (the outer log seq), never ``payload["seq"]``.
  * Idempotent: rebuilt/interrupted turn ids are ``uuid5(session_id, seq, role)`` so
    repeated reloads produce byte-identical turns (no flicker for polling clients).
  * Partial turns: assistant work with no terminating ``result`` (the incident) is flushed
    at end-of-stream as one assistant turn flagged ``metadata.status="interrupted"``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from volundr.domain.models import SessionLogEntry

# Deterministic namespace for rebuilt-turn ids (stable across reloads).
_TURN_NAMESPACE = uuid.UUID("6f2d2e2a-7b1c-4e8a-9d3f-0a1b2c3d4e5f")

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
    }
)


@dataclass
class RebuildResult:
    """Outcome of a log replay. ``partial`` is True if any turn was interrupted/errored."""

    turns: list[dict[str, Any]]
    partial: bool = False


@dataclass
class _Acc:
    """Accumulator for the in-progress assistant turn span."""

    content: str = ""
    parts: list[dict] = field(default_factory=list)
    reasoning: str = ""
    last_ts: datetime | None = None
    last_seq: int = 0

    def add_text(self, text: str) -> None:
        if text:
            self.content += text

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
        # last_ts / last_seq intentionally retained as a floor for the next span.


def rebuild_turns(entries: list[SessionLogEntry]) -> RebuildResult:
    """Fold ordered ``SessionLogEntry`` rows into renderable conversation turns."""
    rows = sorted(entries, key=lambda e: e.seq)
    if not rows:
        return RebuildResult(turns=[], partial=False)

    session_id = str(rows[0].session_id)

    sdk_turn_rows = [r for r in rows if r.kind == "conversation.turn"]
    folded_request_ids = {r.request_id for r in sdk_turn_rows if r.request_id}

    turns: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    # ---- PASS 1: conversation.turn rows are AUTHORITATIVE (SDK happy path + seed user) ----
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
        # Remember a human conversation.turn's uuid so the raw `user` seed double-log dedups.
        uid = str(turn.get("uuid") or turn.get("id") or "").strip()
        if turn.get("role") == "user" and uid:
            seen_ids.add(uid)
        # Append VERBATIM — conversation.turn rows are already serialized turns; the SDK
        # transcript must reload byte-identical (do NOT re-normalize / add fields here).
        turns.append(turn)

    # ---- PASS 2: raw-frame / tmux reduction (the crash tail, or a pure tmux session) ----
    acc = _Acc()
    pending_tmux_rows: list[str] | None = None

    def flush_assistant(status: str | None = None, md: dict | None = None) -> None:
        nonlocal pending_tmux_rows
        text = acc.content
        parts = list(acc.parts)
        if acc.reasoning:
            parts.append({"type": "reasoning", "text": acc.reasoning[-500:]})
        meta = dict(md or {})
        # tmux LAST-RESORT: scrape the pane ONLY when no delta/assistant content exists.
        if not text and not parts and pending_tmux_rows is not None:
            scraped = _extract_assistant_text(pending_tmux_rows)
            if scraped:
                text = scraped
                meta["provenance"] = "terminal_scrape"
        if not text and not parts:
            acc.reset()
            pending_tmux_rows = None
            return
        meta["source"] = "log-rebuild"
        if status:
            meta["status"] = status
        turns.append(
            _make_turn(session_id, acc.last_seq, "assistant", text, parts, meta, acc.last_ts)
        )
        acc.reset()
        pending_tmux_rows = None

    for r in rows:
        k = r.kind
        if k == "conversation.turn" or k in _IGNORED_KINDS:
            continue
        if r.request_id and r.request_id in folded_request_ids:
            continue  # already saved as a conversation.turn — never re-fold
        p = r.payload if isinstance(r.payload, dict) else {}

        if k == "user":
            content = _user_string_content(p)
            if content is None:
                # tool_result-only user event -> enrich the OPEN assistant turn, not a new turn.
                for b in _tool_result_blocks(p):
                    acc.parts.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": b.get("tool_use_id"),
                            "content": b.get("content"),
                            "is_error": bool(b.get("is_error")),
                        }
                    )
                    acc.touch(_ts(r), int(r.seq))
                continue
            uid = str(p.get("uuid") or "").strip()
            if uid and uid in seen_ids:
                continue  # seed double-log: same human turn already emitted in PASS 1
            flush_assistant()
            if uid:
                seen_ids.add(uid)
            turns.append(
                _make_turn(
                    session_id, int(r.seq), "user", content, [], {"source": "log-rebuild"}, _ts(r)
                )
            )

        elif k == "assistant":
            for b in _content_blocks(p):
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text" and b.get("text"):
                    acc.parts.append({"type": "text", "text": b["text"]})
                    acc.add_text(("\n" if acc.content else "") + b["text"])
                elif bt == "thinking" and b.get("thinking"):
                    acc.parts.append({"type": "reasoning", "text": str(b["thinking"])[-500:]})
                elif bt == "tool_use" and b.get("id"):
                    acc.parts.append(
                        {
                            "type": "tool_use",
                            "id": b.get("id"),
                            "name": b.get("name"),
                            "input": b.get("input") or {},
                        }
                    )
            acc.touch(_ts(r), int(r.seq))

        elif k == "content_block_delta":
            delta = p.get("delta", {}) if isinstance(p.get("delta"), dict) else {}
            if delta.get("type") == "thinking_delta":
                acc.reasoning += delta.get("thinking", "")
            else:
                acc.add_text(delta.get("text", ""))
            acc.touch(_ts(r), int(r.seq))

        elif k in ("terminal_frame", "terminal_snapshot"):
            rows_text = p.get("rows")
            if not isinstance(rows_text, list):
                rows_text = str(p.get("text", "")).split("\n")
            pending_tmux_rows = rows_text
            acc.touch(_ts(r), int(r.seq))

        elif k == "result":
            if acc.is_empty():
                acc.content = str(p.get("result", "") or "")
            acc.touch(_ts(r), int(r.seq))
            flush_assistant(md=_result_metadata(p))

        elif k == "error":
            flush_assistant()
            acc.touch(_ts(r), int(r.seq))
            err = _error_text(p)
            turns.append(
                _make_turn(
                    session_id,
                    int(r.seq),
                    "assistant",
                    err,
                    [{"type": "text", "text": err}] if err else [],
                    {"status": "error", "source": "log-rebuild"},
                    _ts(r),
                )
            )

    # ---- END OF STREAM: crash-mid-turn — pending work that never reached a result ----
    if not acc.is_empty() or pending_tmux_rows is not None:
        flush_assistant(status="interrupted")

    partial = any(t.get("metadata", {}).get("status") in ("interrupted", "error") for t in turns)
    return RebuildResult(turns=turns, partial=partial)


# --------------------------------------------------------------------------- helpers


def _make_turn(
    session_id: str,
    seq: int,
    role: str,
    content: str,
    parts: list[dict],
    metadata: dict,
    ts: datetime | None,
) -> dict:
    return {
        "id": str(uuid.uuid5(_TURN_NAMESPACE, f"{session_id}:{seq}:{role}")),
        "role": role,
        "content": content,
        "parts": parts,
        "created_at": _iso(ts),
        "metadata": metadata,
        "visibility": "public",
    }


def _user_string_content(payload: dict) -> str | None:
    """The human turn's text, or None if this is a tool_result-only user event."""
    msg = payload.get("message")
    content = msg.get("content") if isinstance(msg, dict) else payload.get("content")
    if isinstance(content, str) and content.strip():
        return content
    return None


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


def _result_metadata(payload: dict) -> dict:
    """Lift usage/cost/model from a result frame into turn metadata (best-effort)."""
    md: dict[str, Any] = {}
    usage = payload.get("modelUsage")
    if isinstance(usage, dict) and usage:
        md["modelUsage"] = usage
    for k in ("stop_reason", "is_error"):
        if k in payload:
            md[k] = payload[k]
    return md


def _error_text(payload: dict) -> str:
    err = payload.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or "") or "Unknown error"
    return str(payload.get("content") or err or "Unknown error")


def _first_text_block(payload: dict) -> str:
    for b in _content_blocks(payload):
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
            return str(b["text"])
    return ""


def _ts(entry: SessionLogEntry) -> datetime | None:
    return getattr(entry, "ts", None)


def _iso(ts: datetime | None) -> str:
    if ts is None:
        return ""
    try:
        return ts.isoformat()
    except Exception:  # noqa: BLE001 — defensive; ts is best-effort metadata
        return str(ts)


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
