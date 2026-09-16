"""Durable transcript for the OpenClaw shim — the system of record.

Ravn persists nothing. ``RavnGateway._sessions`` is a plain in-process dict with
no save, no load, no TTL and no eviction, and ``Session.messages`` is an in-RAM
list. Kill the gateway and every conversation ceases to exist. There is no
history endpoint of any kind, and ``GET /events`` hands each subscriber a fresh
empty queue with no backlog.

That is fatal for a chat client, because all three of ``ChatScreenModel``'s
self-healing rails — the ``activate()`` history seed, ``reconcileWithServerHistory``
and the reconnect scoped resync — call ``chat.history``. Without a durable
transcript they have nothing to heal from, and a Ravn channel could only ever
show what arrived while the app happened to be connected.

**This store is what makes those rails real.** It, not the phone and not Ravn,
is the system of record for what was said.

Design notes
------------
* WAL journal: the shim writes from an asyncio task while a future ``chat.history``
  read may be in flight; WAL lets them not block each other.
* Whole frames are persisted, unknown fields included. ForgeKit lost turn
  identity precisely because its encoder dropped an ``extra`` bag, and that was
  the phantom-messages incident — so ``blocks_json`` round-trips verbatim.
* ``messages.seq_in_session`` is assigned by us, not by Ravn, because Ravn's
  frames carry no sequence number and no message id at all.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_key         TEXT PRIMARY KEY,
    ravn_session_id     TEXT,
    agent_id            TEXT NOT NULL,
    kind                TEXT NOT NULL DEFAULT 'direct',
    display_name        TEXT,
    derived_title       TEXT,
    created_at_ms       INTEGER NOT NULL,
    updated_at_ms       INTEGER NOT NULL,
    last_message_preview TEXT,
    status              TEXT NOT NULL DEFAULT 'idle',
    hidden              INTEGER NOT NULL DEFAULT 0,
    total_tokens        INTEGER,
    input_tokens        INTEGER,
    output_tokens       INTEGER,
    estimated_cost_usd  REAL,
    metadata_json       TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    message_id      TEXT PRIMARY KEY,
    session_key     TEXT NOT NULL,
    role            TEXT NOT NULL,
    blocks_json     TEXT NOT NULL,
    created_at_ms   INTEGER NOT NULL,
    run_id          TEXT,
    seq_in_session  INTEGER NOT NULL,
    FOREIGN KEY (session_key) REFERENCES sessions(session_key)
);
CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages(session_key, seq_in_session);

CREATE TABLE IF NOT EXISTS devices (
    device_id       TEXT PRIMARY KEY,
    public_key      TEXT NOT NULL,
    first_seen_ms   INTEGER NOT NULL,
    last_seen_ms    INTEGER NOT NULL,
    label           TEXT
);

CREATE TABLE IF NOT EXISTS idempotency (
    key             TEXT PRIMARY KEY,
    session_key     TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    created_at_ms   INTEGER NOT NULL
);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class StoredMessage:
    message_id: str
    session_key: str
    role: str
    blocks: list[dict[str, Any]]
    created_at_ms: int
    run_id: str | None
    seq_in_session: int

    def to_history_dict(self) -> dict[str, Any]:
        """The shape ``LiveChatParsing.historyMessages`` actually reads.

        It prefers a structured ``blocks`` array (text blocks only; tool_use,
        tool_result and thinking are dropped) and falls back to a flat
        ``content`` only when ``blocks`` is absent. It takes the id from
        ``id`` / ``messageId`` / ``runId`` and the timestamp from
        ``createdAt`` / ``timestamp`` / ``ts`` / ``updatedAt`` in epoch ms —
        anything else sorts to 1970 and lands above the whole transcript.
        """
        return {
            "id": self.message_id,
            "messageId": self.message_id,
            "role": self.role,
            "blocks": self.blocks,
            "content": _flatten_text(self.blocks),
            "createdAt": self.created_at_ms,
            "runId": self.run_id,
        }


def _flatten_text(blocks: list[dict[str, Any]]) -> str:
    """Plain-text fallback for clients that ignore ``blocks``.

    Deliberately mirrors the client's own filter: only plain text contributes,
    so a tool result never leaks into the transcript as if the agent had said it.
    """
    skip = {"tool_use", "tool_result", "thinking", "reasoning", "redacted_thinking"}
    return "".join(
        str(b.get("text") or "")
        for b in blocks
        if isinstance(b, dict) and b.get("type") not in skip
    )


class OpenClawStore:
    """SQLite-backed transcript and session registry."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- sessions ----------------------------------------------------------

    def upsert_session(
        self,
        session_key: str,
        *,
        agent_id: str,
        display_name: str | None = None,
        kind: str = "direct",
        ravn_session_id: str | None = None,
        status: str | None = None,
        hidden: bool | None = None,
    ) -> None:
        ts = now_ms()
        existing = self.get_session(session_key)
        if existing is None:
            self._conn.execute(
                "INSERT INTO sessions (session_key, ravn_session_id, agent_id, kind,"
                " display_name, created_at_ms, updated_at_ms, status, hidden)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    session_key,
                    ravn_session_id,
                    agent_id,
                    kind,
                    display_name,
                    ts,
                    ts,
                    status or "idle",
                    int(bool(hidden)),
                ),
            )
        else:
            sets, args = ["updated_at_ms = ?"], [ts]
            for column, value in (
                ("ravn_session_id", ravn_session_id),
                ("display_name", display_name),
                ("status", status),
            ):
                if value is not None:
                    sets.append(f"{column} = ?")
                    args.append(value)
            if hidden is not None:
                sets.append("hidden = ?")
                args.append(int(hidden))
            args.append(session_key)
            self._conn.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE session_key = ?", args)
        self._conn.commit()

    def get_session(self, session_key: str) -> sqlite3.Row | None:
        cur = self._conn.execute("SELECT * FROM sessions WHERE session_key = ?", (session_key,))
        return cur.fetchone()

    def list_sessions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Rows in the ``sessions.list`` shape ``GatewaySession(from:)`` parses.

        A row needs only ``key`` to render a channel; everything else improves
        it. ``updatedAt`` must be epoch **ms** — the client divides by 1000.
        """
        cur = self._conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at_ms DESC LIMIT ?", (limit,)
        )
        out: list[dict[str, Any]] = []
        for row in cur.fetchall():
            entry: dict[str, Any] = {
                "key": row["session_key"],
                "sessionId": row["session_key"],
                "kind": row["kind"],
                "agentId": row["agent_id"],
                "updatedAt": row["updated_at_ms"],
                "status": row["status"],
                "hidden": bool(row["hidden"]),
            }
            if row["display_name"]:
                entry["displayName"] = row["display_name"]
            if row["derived_title"]:
                entry["derivedTitle"] = row["derived_title"]
            if row["last_message_preview"]:
                entry["lastMessagePreview"] = row["last_message_preview"]
            usage = {
                k: row[c]
                for k, c in (
                    ("totalTokens", "total_tokens"),
                    ("inputTokens", "input_tokens"),
                    ("outputTokens", "output_tokens"),
                    ("estimatedCostUsd", "estimated_cost_usd"),
                )
                if row[c] is not None
            }
            if usage:
                entry["usage"] = usage
            out.append(entry)
        return out

    def session_count(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) AS n FROM sessions")
        return int(cur.fetchone()["n"])

    def delete_session(self, session_key: str) -> None:
        self._conn.execute("DELETE FROM messages WHERE session_key = ?", (session_key,))
        self._conn.execute("DELETE FROM sessions WHERE session_key = ?", (session_key,))
        self._conn.commit()

    # -- messages ----------------------------------------------------------

    def append_message(
        self,
        session_key: str,
        *,
        message_id: str,
        role: str,
        blocks: list[dict[str, Any]],
        run_id: str | None = None,
        created_at_ms: int | None = None,
    ) -> StoredMessage:
        """Append one message and refresh the session's preview + timestamp.

        Idempotent on ``message_id``: a re-delivered final for the same run
        replaces the row rather than duplicating the turn.
        """
        ts = created_at_ms if created_at_ms is not None else now_ms()
        cur = self._conn.execute(
            "SELECT COALESCE(MAX(seq_in_session), 0) AS m FROM messages WHERE session_key = ?",
            (session_key,),
        )
        seq = int(cur.fetchone()["m"]) + 1

        existing = self._conn.execute(
            "SELECT seq_in_session FROM messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        if existing is not None:
            seq = int(existing["seq_in_session"])
            self._conn.execute(
                "UPDATE messages SET role=?, blocks_json=?, created_at_ms=?, run_id=?"
                " WHERE message_id=?",
                (role, json.dumps(blocks), ts, run_id, message_id),
            )
        else:
            self._conn.execute(
                "INSERT INTO messages (message_id, session_key, role, blocks_json,"
                " created_at_ms, run_id, seq_in_session) VALUES (?,?,?,?,?,?,?)",
                (message_id, session_key, role, json.dumps(blocks), ts, run_id, seq),
            )

        preview = _flatten_text(blocks).strip().replace("\n", " ")
        self._conn.execute(
            "UPDATE sessions SET updated_at_ms = ?, last_message_preview = ? WHERE session_key = ?",
            (ts, preview[:200] or None, session_key),
        )
        self._conn.commit()
        return StoredMessage(
            message_id=message_id,
            session_key=session_key,
            role=role,
            blocks=blocks,
            created_at_ms=ts,
            run_id=run_id,
            seq_in_session=seq,
        )

    def history(self, session_key: str, *, limit: int = 30) -> list[dict[str, Any]]:
        """The most recent *limit* messages, oldest-first.

        Oldest-first matters: the client uses the order verbatim, so a
        newest-first array renders the conversation backwards.
        """
        cur = self._conn.execute(
            "SELECT * FROM messages WHERE session_key = ? ORDER BY seq_in_session DESC LIMIT ?",
            (session_key, limit),
        )
        rows = list(cur.fetchall())[::-1]
        return [
            StoredMessage(
                message_id=r["message_id"],
                session_key=r["session_key"],
                role=r["role"],
                blocks=json.loads(r["blocks_json"]),
                created_at_ms=r["created_at_ms"],
                run_id=r["run_id"],
                seq_in_session=r["seq_in_session"],
            ).to_history_dict()
            for r in rows
        ]

    def message_count(self, session_key: str) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE session_key = ?", (session_key,)
        )
        return int(cur.fetchone()["n"])

    # -- devices -----------------------------------------------------------

    def remember_device(self, device_id: str, public_key: str, label: str | None = None) -> bool:
        """Trust-on-first-use, returning True when the device is new.

        Safe because device ids are self-certifying —
        ``deviceId == sha256(publicKey)`` is verified before we get here, so a
        device cannot claim an id whose key it does not hold. A known id
        presenting a *different* key is refused by the caller.
        """
        ts = now_ms()
        row = self._conn.execute(
            "SELECT public_key FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO devices (device_id, public_key, first_seen_ms, last_seen_ms, label)"
                " VALUES (?,?,?,?,?)",
                (device_id, public_key, ts, ts, label),
            )
            self._conn.commit()
            return True
        self._conn.execute(
            "UPDATE devices SET last_seen_ms = ? WHERE device_id = ?", (ts, device_id)
        )
        self._conn.commit()
        return False

    def known_public_key(self, device_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT public_key FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        return row["public_key"] if row else None

    # -- idempotency -------------------------------------------------------

    def seen_idempotency_key(self, key: str) -> str | None:
        row = self._conn.execute("SELECT run_id FROM idempotency WHERE key = ?", (key,)).fetchone()
        return row["run_id"] if row else None

    def record_idempotency(self, key: str, session_key: str, run_id: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO idempotency (key, session_key, run_id, created_at_ms)"
            " VALUES (?,?,?,?)",
            (key, session_key, run_id, now_ms()),
        )
        self._conn.commit()
