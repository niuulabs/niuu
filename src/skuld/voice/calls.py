"""The session's voice calls, recorded in the session's own durable event log.

A call is opened by ``session.start`` with the phone's id, filled by ``session.log``
entries and closed by ``session.end``; starting an id again reopens it. Every change is
one ``voice_call`` frame in the session's event log (:data:`VOICE_CALL_FRAME`), so the
record lives where the session's transcript lives, in Forge, and survives the pod. The
book folds those frames the same way live and on restore (``restore`` reads them back
from the durable history when the broker starts).

``voice_call`` frames are durable only: they are never broadcast and the transcript
reducer skips them (``niuu.domain.transcript_reducer.NON_BROADCAST_KINDS``).

Speech entries the phone marks ``isLive`` (an interim transcript the next entry replaces)
are acknowledged and not recorded. Entries are otherwise kept verbatim, and projected
into the shapes the app reads: ``/api/logs`` sessions with ISO times, and
``/api/call-history`` rows with epoch-millisecond times, finished turns and paired tool
calls.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

#: The event-log kind of a call-record frame.
VOICE_CALL_FRAME = "voice_call"

#: Entry types the app logs (``VoiceSessionLogPayloads``).
ENTRY_TYPES = frozenset(
    {"user_speech", "agent_speech", "tool_call", "tool_result", "system_event", "error"}
)

#: ``session.start`` provider (the app's ``VoiceProvider`` raw value) → call-history provider.
_HISTORY_PROVIDERS = {
    "xai": "grok-voice",
    "grok": "grok-voice",
    "openai": "openai-realtime",
    "gpt": "openai-realtime",
    "live": "openai-live",
    "gemini": "gemini-live",
    "google": "gemini-live",
}

_EVENTS = frozenset({"started", "entry", "ended", "updated", "deleted"})


class UnknownCallError(LookupError):
    """No call with that id."""


class CallRecordError(ValueError):
    """A durable call-record frame that cannot be folded."""


def now_ms() -> int:
    return int(time.time() * 1000)


def iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Entry:
    call_id: str
    created_at_ms: int
    type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class Call:
    call_id: str
    state: str
    created_at_ms: int
    persona: str
    provider: str
    metadata: dict[str, Any]
    ended_at_ms: int | None = None
    title: str | None = None
    pinned: bool = False
    summary: str | None = None
    duration_ms: int | None = None
    end_reason: str | None = None
    entries: tuple[Entry, ...] = field(default_factory=tuple)

    @property
    def entry_count(self) -> int:
        return len(self.entries)


class VoiceCallBook:
    """The session's calls, folded from its ``voice_call`` frames."""

    def __init__(self, record: Any) -> None:
        #: Appends one frame to the session's durable event log.
        self._record = record
        self._calls: dict[str, Call] = {}

    # -- restore ---------------------------------------------------------

    def restore(self, frames: list[Any]) -> int:
        """Fold the durable history's ``voice_call`` frames; the number folded."""
        folded = 0
        for history_frame in frames:
            if getattr(history_frame, "kind", "") != VOICE_CALL_FRAME:
                continue
            self._apply(history_frame.payload)
            folded += 1
        return folded

    # -- changes -----------------------------------------------------------

    def start(self, call_id: str, *, persona: str, provider: str, metadata: dict[str, Any]) -> Call:
        """Open *call_id*, or reopen it with its metadata merged."""
        return self._commit(
            {
                "event": "started",
                "call_id": call_id,
                "persona": persona,
                "provider": provider,
                "metadata": metadata,
            }
        )

    def log(self, call_id: str, entry_type: str, payload: dict[str, Any]) -> Entry:
        """Record one entry; an interim (``isLive``) speech entry is answered, not recorded."""
        self.get(call_id)
        if entry_type not in ENTRY_TYPES:
            raise ValueError(f"Unknown log entry type: {entry_type}")
        at = now_ms()
        entry = Entry(call_id=call_id, created_at_ms=at, type=entry_type, payload=payload)
        if payload.get("isLive") is True:
            return entry
        self._commit(
            {
                "event": "entry",
                "call_id": call_id,
                "entry_type": entry_type,
                "payload": payload,
            },
            at_ms=at,
        )
        return entry

    def end(
        self,
        call_id: str,
        *,
        reason: str,
        duration_ms: int | None,
        summary: str | None,
        metadata: dict[str, Any],
    ) -> Call:
        self.get(call_id)
        return self._commit(
            {
                "event": "ended",
                "call_id": call_id,
                "reason": reason,
                "duration_ms": duration_ms,
                "summary": summary,
                "metadata": metadata,
            }
        )

    def update(self, call_id: str, *, title: str | None = None, pinned: bool | None = None) -> Call:
        self.get(call_id)
        frame: dict[str, Any] = {"event": "updated", "call_id": call_id}
        if title is not None:
            frame["title"] = title
        if pinned is not None:
            frame["pinned"] = pinned
        return self._commit(frame)

    def delete(self, call_id: str) -> None:
        """Hide a call from the list; its frames stay in the session's log."""
        self.get(call_id)
        self._commit({"event": "deleted", "call_id": call_id})

    # -- reads -----------------------------------------------------------

    def get(self, call_id: str) -> Call:
        call = self._calls.get(call_id)
        if call is None:
            raise UnknownCallError(f"Unknown voice call: {call_id}")
        return call

    def calls(self, limit: int | None = None) -> list[Call]:
        """Newest first; every call unless *limit* is given."""
        ordered = sorted(self._calls.values(), key=lambda c: c.created_at_ms, reverse=True)
        return ordered if limit is None else ordered[:limit]

    def active_count(self) -> int:
        return sum(1 for call in self._calls.values() if call.state == "active")

    def session_json(self, call: Call) -> dict[str, Any]:
        """A ``/api/logs`` session row."""
        metadata = dict(call.metadata)
        if call.title:
            metadata["title"] = call.title
        metadata["pinned"] = call.pinned
        return {
            "id": call.call_id,
            "state": call.state,
            "createdAt": iso(call.created_at_ms),
            "endedAt": iso(call.ended_at_ms),
            "entryCount": call.entry_count,
            "pinned": call.pinned,
            "metadata": metadata,
        }

    @staticmethod
    def entry_json(entry: Entry) -> dict[str, Any]:
        """A ``/api/logs/:id`` entry: ISO timestamp, payload verbatim."""
        return {
            "sessionId": entry.call_id,
            "timestamp": iso(entry.created_at_ms),
            "type": entry.type,
            "payload": entry.payload,
        }

    def history_json(self, call: Call) -> dict[str, Any]:
        """A ``/api/call-history`` row: finished turns and paired tool calls."""
        messages: list[dict[str, Any]] = []
        tool_calls: dict[str, dict[str, Any]] = {}
        for entry in call.entries:
            payload = entry.payload
            at = _entry_ms(entry)
            if entry.type in ("user_speech", "agent_speech"):
                text = str(payload.get("text") or "").strip()
                if not text:
                    continue
                messages.append(
                    {
                        "id": str(payload.get("entryId") or f"{call.call_id}-{len(messages)}"),
                        "role": "user" if entry.type == "user_speech" else "assistant",
                        "text": text,
                        "timestamp": at,
                        "isFinal": True,
                    }
                )
                continue
            if entry.type not in ("tool_call", "tool_result"):
                continue
            tool_call_id = str(payload.get("callId") or uuid.uuid4())
            row = tool_calls.setdefault(
                tool_call_id,
                {"id": tool_call_id, "name": str(payload.get("name") or ""), "status": "pending"},
            )
            if entry.type == "tool_call":
                row["startedAt"] = at
                row["args"] = payload.get("args")
                continue
            row["completedAt"] = at
            row["result"] = payload.get("result")
            row["status"] = "error" if payload.get("error") else "completed"
            if payload.get("durationMs") is not None:
                row["durationMs"] = payload["durationMs"]
        duration = call.duration_ms
        if duration is None:
            duration = (call.ended_at_ms or now_ms()) - call.created_at_ms
        return {
            "id": call.call_id,
            "provider": _HISTORY_PROVIDERS.get(call.provider.lower(), call.provider),
            "client": str(call.metadata.get("client") or "unknown"),
            "agentId": str(call.metadata.get("agentId") or call.persona),
            "personaId": call.persona,
            "startedAt": call.created_at_ms,
            "endedAt": call.ended_at_ms,
            "durationMs": duration,
            "state": call.state,
            "messages": messages,
            "toolCalls": list(tool_calls.values()),
            "summary": call.summary,
            "title": call.title,
            "pinned": call.pinned,
            "continuedFrom": call.metadata.get("continuedFrom"),
        }

    # -- internals -------------------------------------------------------

    def _commit(self, body: dict[str, Any], *, at_ms: int | None = None) -> Call:
        """Record the frame durably, then fold it: the book never runs ahead of the log."""
        frame = {"type": VOICE_CALL_FRAME, "at_ms": at_ms if at_ms is not None else now_ms()}
        frame.update(body)
        self._record(frame)
        return self._apply(frame)

    def _apply(self, frame: Any) -> Call:
        if not isinstance(frame, dict):
            raise CallRecordError("a voice_call frame must be an object")
        event = frame.get("event")
        call_id = frame.get("call_id")
        at = frame.get("at_ms")
        if event not in _EVENTS or not isinstance(call_id, str) or not call_id:
            raise CallRecordError(f"voice_call frame has no known event and call id: {frame!r}")
        if not isinstance(at, int):
            raise CallRecordError(f"voice_call frame {event} of {call_id} has no at_ms")
        existing = self._calls.get(call_id)
        if event == "started":
            metadata = frame.get("metadata") if isinstance(frame.get("metadata"), dict) else {}
            if existing is None:
                call = Call(
                    call_id=call_id,
                    state="active",
                    created_at_ms=at,
                    persona=str(frame.get("persona") or ""),
                    provider=str(frame.get("provider") or ""),
                    metadata=dict(metadata),
                )
            else:
                call = replace(
                    existing,
                    state="active",
                    ended_at_ms=None,
                    end_reason=None,
                    metadata={**existing.metadata, **metadata},
                )
            self._calls[call_id] = call
            return call
        if existing is None:
            raise CallRecordError(f"voice_call frame {event} names an unknown call {call_id}")
        if event == "entry":
            payload = frame.get("payload") if isinstance(frame.get("payload"), dict) else {}
            entry = Entry(
                call_id=call_id,
                created_at_ms=at,
                type=str(frame.get("entry_type") or ""),
                payload=payload,
            )
            call = replace(existing, entries=(*existing.entries, entry))
        elif event == "ended":
            metadata = frame.get("metadata") if isinstance(frame.get("metadata"), dict) else {}
            duration = frame.get("duration_ms")
            summary = frame.get("summary")
            call = replace(
                existing,
                state="ended",
                ended_at_ms=at,
                end_reason=str(frame.get("reason") or "ended"),
                duration_ms=int(duration) if isinstance(duration, int | float) else None,
                summary=summary if isinstance(summary, str) else existing.summary,
                metadata={**existing.metadata, **metadata},
            )
        elif event == "updated":
            call = replace(
                existing,
                title=frame["title"] if isinstance(frame.get("title"), str) else existing.title,
                pinned=frame["pinned"]
                if isinstance(frame.get("pinned"), bool)
                else existing.pinned,
            )
        else:
            del self._calls[call_id]
            return existing
        self._calls[call_id] = call
        return call


def _entry_ms(entry: Entry) -> int:
    """The phone's own timestamp (epoch ms) when it sent one, else when it was recorded."""
    stamp = entry.payload.get("timestamp")
    if isinstance(stamp, int | float):
        return int(stamp)
    return entry.created_at_ms
