"""Durable store of a resident's voice calls — the system of record for the Voice tab.

A call is opened by ``session.start`` with the client's id, filled by
``session.log`` entries, and closed by ``session.end``. Starting an id again
reopens it (a continued call keeps logging into the same record). Entries are
stored verbatim, unknown fields included, and projected into the shapes the
voice client reads: ``/api/logs`` sessions with ISO times, and ``/api/call-history``
rows with epoch-millisecond times, finished turns and paired tool calls.

It also keeps ``call_requests``: each call the resident asked to place (a ``notify``
with ``speak``), how it was delivered, the phone's ``call.result`` and the voice call
it became (see :mod:`ravn.adapters.channels.voice.calls`).
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    call_id         TEXT PRIMARY KEY,
    state           TEXT NOT NULL,
    created_at_ms   INTEGER NOT NULL,
    ended_at_ms     INTEGER,
    persona         TEXT NOT NULL,
    provider        TEXT NOT NULL,
    metadata_json   TEXT NOT NULL,
    title           TEXT,
    pinned          INTEGER NOT NULL DEFAULT 0,
    summary         TEXT,
    duration_ms     INTEGER,
    end_reason      TEXT
);

CREATE TABLE IF NOT EXISTS entries (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id         TEXT NOT NULL,
    created_at_ms   INTEGER NOT NULL,
    type            TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    FOREIGN KEY (call_id) REFERENCES calls(call_id)
);
CREATE INDEX IF NOT EXISTS idx_entries_call ON entries(call_id, seq);

CREATE TABLE IF NOT EXISTS call_requests (
    request_id      TEXT PRIMARY KEY,
    notification_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL,
    channel         TEXT,
    text            TEXT NOT NULL,
    priority        TEXT NOT NULL,
    created_at_ms   INTEGER NOT NULL,
    expires_at_ms   INTEGER NOT NULL,
    state           TEXT NOT NULL,
    delivery        TEXT,
    device_id       TEXT,
    sent_at_ms      INTEGER,
    push_tried      INTEGER NOT NULL DEFAULT 0,
    push_reason     TEXT,
    outcome         TEXT,
    reason          TEXT,
    answered_at_ms  INTEGER,
    call_id         TEXT
);
CREATE INDEX IF NOT EXISTS idx_call_requests_state ON call_requests(state, created_at_ms);
"""

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


#: ``call_requests.state``: waiting for its window or a route to the phone, delivered and
#: waiting for the phone's ``call.result``, answered, or never delivered before it expired.
CALL_REQUEST_STATES = ("queued", "sent", "answered", "expired")
#: ``call.result`` outcomes the phone sends.
CALL_OUTCOMES = frozenset({"spoke", "aside", "deferred", "refused"})


class UnknownCallError(LookupError):
    """No call with that id."""


def now_ms() -> int:
    return int(time.time() * 1000)


def iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Call:
    call_id: str
    state: str
    created_at_ms: int
    ended_at_ms: int | None
    persona: str
    provider: str
    metadata: dict[str, Any]
    title: str | None
    pinned: bool
    summary: str | None
    duration_ms: int | None
    entry_count: int


@dataclass(frozen=True)
class Entry:
    call_id: str
    created_at_ms: int
    type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class CallRequest:
    """One request to ring the owner (a ``notify`` with ``speak``) and what became of it."""

    request_id: str
    notification_id: str
    idempotency_key: str
    channel: str | None
    text: str
    priority: str
    created_at_ms: int
    expires_at_ms: int
    state: str
    delivery: str | None
    device_id: str | None
    sent_at_ms: int | None
    push_tried: bool
    push_reason: str | None
    outcome: str | None
    reason: str | None
    answered_at_ms: int | None
    call_id: str | None


class VoiceCallStore:
    """SQLite-backed calls and their entries, and the call requests the resident made.

    The ``notify`` tool may run in another process (``ravn tool-mcp``) and queues call
    requests in the same file; SQLite's WAL and busy timeout let both write.
    """

    def __init__(self, path: str) -> None:
        resolved = Path(path).expanduser()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(resolved), check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # -- lifecycle -------------------------------------------------------

    def start(self, call_id: str, *, persona: str, provider: str, metadata: dict[str, Any]) -> Call:
        """Open *call_id*, or reopen it with its metadata merged."""
        existing = self._row(call_id)
        if existing is None:
            self._db.execute(
                "INSERT INTO calls"
                " (call_id, state, created_at_ms, persona, provider, metadata_json)"
                " VALUES (?, 'active', ?, ?, ?, ?)",
                (call_id, now_ms(), persona, provider, json.dumps(metadata)),
            )
        else:
            merged = {**json.loads(existing["metadata_json"]), **metadata}
            self._db.execute(
                "UPDATE calls SET state = 'active', ended_at_ms = NULL, end_reason = NULL,"
                " metadata_json = ? WHERE call_id = ?",
                (json.dumps(merged), call_id),
            )
        self._db.commit()
        return self.get(call_id)

    def log(self, call_id: str, entry_type: str, payload: dict[str, Any]) -> Entry:
        if self._row(call_id) is None:
            raise UnknownCallError(f"Unknown voice call: {call_id}")
        if entry_type not in ENTRY_TYPES:
            raise ValueError(f"Unknown log entry type: {entry_type}")
        created = now_ms()
        self._db.execute(
            "INSERT INTO entries (call_id, created_at_ms, type, payload_json) VALUES (?, ?, ?, ?)",
            (call_id, created, entry_type, json.dumps(payload)),
        )
        self._db.commit()
        return Entry(call_id=call_id, created_at_ms=created, type=entry_type, payload=payload)

    def end(
        self,
        call_id: str,
        *,
        reason: str,
        duration_ms: int | None,
        summary: str | None,
        metadata: dict[str, Any],
    ) -> Call:
        row = self._row(call_id)
        if row is None:
            raise UnknownCallError(f"Unknown voice call: {call_id}")
        merged = {**json.loads(row["metadata_json"]), **metadata}
        self._db.execute(
            "UPDATE calls SET state = 'ended', ended_at_ms = ?, end_reason = ?, duration_ms = ?,"
            " summary = COALESCE(?, summary), metadata_json = ? WHERE call_id = ?",
            (now_ms(), reason, duration_ms, summary, json.dumps(merged), call_id),
        )
        self._db.commit()
        return self.get(call_id)

    def update(self, call_id: str, *, title: str | None = None, pinned: bool | None = None) -> Call:
        if self._row(call_id) is None:
            raise UnknownCallError(f"Unknown voice call: {call_id}")
        if title is not None:
            self._db.execute("UPDATE calls SET title = ? WHERE call_id = ?", (title, call_id))
        if pinned is not None:
            self._db.execute(
                "UPDATE calls SET pinned = ? WHERE call_id = ?", (int(pinned), call_id)
            )
        self._db.commit()
        return self.get(call_id)

    def delete(self, call_id: str) -> None:
        if self._row(call_id) is None:
            raise UnknownCallError(f"Unknown voice call: {call_id}")
        self._db.execute("DELETE FROM entries WHERE call_id = ?", (call_id,))
        self._db.execute("DELETE FROM calls WHERE call_id = ?", (call_id,))
        self._db.commit()

    # -- reads -----------------------------------------------------------

    def get(self, call_id: str) -> Call:
        row = self._row(call_id)
        if row is None:
            raise UnknownCallError(f"Unknown voice call: {call_id}")
        return self._call(row)

    def calls(self, limit: int) -> list[Call]:
        """Newest first."""
        rows = self._db.execute(
            "SELECT * FROM calls ORDER BY created_at_ms DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._call(row) for row in rows]

    def entries(self, call_id: str) -> list[Entry]:
        rows = self._db.execute(
            "SELECT * FROM entries WHERE call_id = ? ORDER BY seq", (call_id,)
        ).fetchall()
        return [
            Entry(
                call_id=row["call_id"],
                created_at_ms=row["created_at_ms"],
                type=row["type"],
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    def search(self, query: str, limit: int) -> list[Call]:
        """Calls whose spoken turns or title contain *query* (case-insensitive), newest first."""
        needle = f"%{query.lower()}%"
        rows = self._db.execute(
            "SELECT DISTINCT c.* FROM calls c LEFT JOIN entries e ON e.call_id = c.call_id"
            " WHERE lower(COALESCE(c.title, '')) LIKE ?"
            " OR (e.type IN ('user_speech', 'agent_speech') AND lower(e.payload_json) LIKE ?)"
            " ORDER BY c.created_at_ms DESC LIMIT ?",
            (needle, needle, limit),
        ).fetchall()
        return [self._call(row) for row in rows]

    # -- call requests ---------------------------------------------------

    def queue_call_request(
        self,
        *,
        request_id: str,
        notification_id: str,
        idempotency_key: str,
        channel: str | None,
        text: str,
        priority: str,
        created_at_ms: int,
        expires_at_ms: int,
    ) -> CallRequest | None:
        """Queue a request; ``None`` when its notification already has one (a retried notify)."""
        cursor = self._db.execute(
            "INSERT OR IGNORE INTO call_requests (request_id, notification_id, idempotency_key,"
            " channel, text, priority, created_at_ms, expires_at_ms, state)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued')",
            (
                request_id,
                notification_id,
                idempotency_key,
                channel,
                text,
                priority,
                created_at_ms,
                expires_at_ms,
            ),
        )
        self._db.commit()
        return self.call_request(request_id) if cursor.rowcount else None

    def call_request(self, request_id: str) -> CallRequest | None:
        row = self._db.execute(
            "SELECT * FROM call_requests WHERE request_id = ?", (request_id,)
        ).fetchone()
        return _call_request(row) if row is not None else None

    def call_requests(self, *, state: str | None = None, limit: int = 100) -> list[CallRequest]:
        """Oldest first; every state when *state* is ``None``."""
        if state is None:
            rows = self._db.execute(
                "SELECT * FROM call_requests ORDER BY created_at_ms LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM call_requests WHERE state = ? ORDER BY created_at_ms LIMIT ?",
                (state, limit),
            ).fetchall()
        return [_call_request(row) for row in rows]

    def mark_call_sent(
        self, request_id: str, *, delivery: str, device_id: str | None, at_ms: int
    ) -> None:
        self._db.execute(
            "UPDATE call_requests SET state = 'sent', delivery = ?, device_id = ?, sent_at_ms = ?"
            " WHERE request_id = ? AND state = 'queued'",
            (delivery, device_id, at_ms, request_id),
        )
        self._db.commit()

    def mark_call_pushed(self, request_id: str, *, sent: bool, reason: str, at_ms: int) -> None:
        """The request's one push: with no socket to send it on, or after a socket delivery
        the phone did not answer in time. A push that went out makes it a ``voip_push``."""
        self._db.execute(
            "UPDATE call_requests SET push_tried = 1, push_reason = ?,"
            " delivery = CASE WHEN ? THEN 'voip_push' ELSE delivery END,"
            " state = CASE WHEN ? AND state = 'queued' THEN 'sent' ELSE state END,"
            " sent_at_ms = CASE WHEN ? AND sent_at_ms IS NULL THEN ? ELSE sent_at_ms END"
            " WHERE request_id = ?",
            (reason, int(sent), int(sent), int(sent), at_ms, request_id),
        )
        self._db.commit()

    def expire_call_request(self, request_id: str) -> None:
        """Close a request never delivered before its ``expiresAt`` (``delivery: none``)."""
        self._db.execute(
            "UPDATE call_requests SET state = 'expired', delivery = 'none', reason = 'expired'"
            " WHERE request_id = ? AND state = 'queued'",
            (request_id,),
        )
        self._db.commit()

    def record_call_result(
        self, request_id: str, *, outcome: str, reason: str | None, at_ms: int
    ) -> bool:
        """The phone's one answer; ``False`` for an unknown id or one already answered."""
        cursor = self._db.execute(
            "UPDATE call_requests SET state = 'answered', outcome = ?, reason = ?,"
            " answered_at_ms = ? WHERE request_id = ? AND outcome IS NULL",
            (outcome, reason, at_ms, request_id),
        )
        self._db.commit()
        return bool(cursor.rowcount)

    def link_call_request(self, request_id: str, call_id: str) -> bool:
        """The voice call a request became (its ``session.start`` metadata names the request)."""
        cursor = self._db.execute(
            "UPDATE call_requests SET call_id = ? WHERE request_id = ?", (call_id, request_id)
        )
        self._db.commit()
        return bool(cursor.rowcount)

    def placed_calls_since(self, since_ms: int) -> list[int]:
        """When each request delivered since *since_ms* was sent, that the phone did not turn down.

        A refusal or deferral leaves the window open: only a call that rang, spoke, or is
        still awaiting the phone's answer counts against the guardrails.
        """
        rows = self._db.execute(
            "SELECT sent_at_ms FROM call_requests WHERE sent_at_ms IS NOT NULL"
            " AND sent_at_ms >= ? AND (outcome IS NULL OR outcome IN ('spoke', 'aside'))"
            " ORDER BY sent_at_ms",
            (since_ms,),
        ).fetchall()
        return [int(row["sent_at_ms"]) for row in rows]

    # -- projections -----------------------------------------------------

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

    def entry_json(self, entry: Entry) -> dict[str, Any]:
        """A ``/api/logs/:id`` entry: ISO timestamp, payload verbatim."""
        return {
            "sessionId": entry.call_id,
            "timestamp": iso(entry.created_at_ms),
            "type": entry.type,
            "payload": entry.payload,
        }

    def history_json(self, call: Call) -> dict[str, Any]:
        """A ``/api/call-history`` row: finished turns and paired tool calls."""
        entries = self.entries(call.call_id)
        messages: list[dict[str, Any]] = []
        tool_calls: dict[str, dict[str, Any]] = {}
        for entry in entries:
            payload = entry.payload
            at = _entry_ms(entry)
            if entry.type in ("user_speech", "agent_speech"):
                text = str(payload.get("text") or "").strip()
                if not text or payload.get("isLive") is True:
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
            call_id = str(payload.get("callId") or uuid.uuid4())
            row = tool_calls.setdefault(
                call_id,
                {"id": call_id, "name": str(payload.get("name") or ""), "status": "pending"},
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
        ended = call.ended_at_ms
        duration = call.duration_ms
        if duration is None:
            duration = (ended or now_ms()) - call.created_at_ms
        return {
            "id": call.call_id,
            "provider": _HISTORY_PROVIDERS.get(call.provider.lower(), call.provider),
            "client": str(call.metadata.get("client") or "unknown"),
            "agentId": str(call.metadata.get("agentId") or call.persona),
            "personaId": call.persona,
            "startedAt": call.created_at_ms,
            "endedAt": ended,
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

    def _row(self, call_id: str) -> sqlite3.Row | None:
        return self._db.execute("SELECT * FROM calls WHERE call_id = ?", (call_id,)).fetchone()

    def _call(self, row: sqlite3.Row) -> Call:
        count = self._db.execute(
            "SELECT COUNT(*) FROM entries WHERE call_id = ?", (row["call_id"],)
        ).fetchone()[0]
        return Call(
            call_id=row["call_id"],
            state=row["state"],
            created_at_ms=row["created_at_ms"],
            ended_at_ms=row["ended_at_ms"],
            persona=row["persona"],
            provider=row["provider"],
            metadata=json.loads(row["metadata_json"]),
            title=row["title"],
            pinned=bool(row["pinned"]),
            summary=row["summary"],
            duration_ms=row["duration_ms"],
            entry_count=int(count),
        )


def _call_request(row: sqlite3.Row) -> CallRequest:
    return CallRequest(
        request_id=row["request_id"],
        notification_id=row["notification_id"],
        idempotency_key=row["idempotency_key"],
        channel=row["channel"],
        text=row["text"],
        priority=row["priority"],
        created_at_ms=int(row["created_at_ms"]),
        expires_at_ms=int(row["expires_at_ms"]),
        state=row["state"],
        delivery=row["delivery"],
        device_id=row["device_id"],
        sent_at_ms=row["sent_at_ms"],
        push_tried=bool(row["push_tried"]),
        push_reason=row["push_reason"],
        outcome=row["outcome"],
        reason=row["reason"],
        answered_at_ms=row["answered_at_ms"],
        call_id=row["call_id"],
    )


def _entry_ms(entry: Entry) -> int:
    """The client's own timestamp (epoch ms) when it sent one, else when it was stored."""
    stamp = entry.payload.get("timestamp")
    if isinstance(stamp, int | float):
        return int(stamp)
    return entry.created_at_ms
