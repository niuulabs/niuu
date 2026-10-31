"""Durable store of a resident's voice calls — the system of record for the Voice tab.

A call is opened by ``session.start`` with the client's id, filled by
``session.log`` entries, and closed by ``session.end``. Starting an id again
reopens it (a continued call keeps logging into the same record). Entries are
stored verbatim, unknown fields included, and projected into the shapes the
voice client reads: ``/api/logs`` sessions with ISO times, and ``/api/call-history``
rows with epoch-millisecond times, finished turns and paired tool calls.
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


class VoiceCallStore:
    """SQLite-backed calls and their entries."""

    def __init__(self, path: str) -> None:
        resolved = Path(path).expanduser()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(resolved), check_same_thread=False)
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


def _entry_ms(entry: Entry) -> int:
    """The client's own timestamp (epoch ms) when it sent one, else when it was stored."""
    stamp = entry.payload.get("timestamp")
    if isinstance(stamp, int | float):
        return int(stamp)
    return entry.created_at_ms
