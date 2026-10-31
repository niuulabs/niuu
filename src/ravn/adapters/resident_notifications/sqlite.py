"""SQLite-backed resident notification outbox.

It lives in the OpenClaw shim's own state database: Forge drains it through the
shim, so the outbox is exactly as durable as the shim's transcript, and a
resident whose shim is off has neither.

Two tables:

* ``resident_notification_outbox`` holds the entries Forge has not acknowledged,
  in write order. ``seq`` is the order Forge reads them in.
* ``resident_notification_requests`` remembers every attention request ever
  raised and whether it is still open, so a request is raised once (a question
  re-published after a restart stays one notification) and :meth:`resolve`
  knows which requests a group's answer settles.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from niuu.domain.notifications import NotificationDraft
from niuu.domain.resident_notifications import (
    MAX_RESOLVED_REQUESTS,
    AttentionInputKind,
    ResidentNotificationEntry,
    ResidentNotificationOp,
)
from ravn.ports.resident_notifications import ResidentNotificationOutboxPort

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS resident_notification_outbox (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_json      TEXT NOT NULL,
    created_at_ms   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS resident_notification_requests (
    request_id      TEXT PRIMARY KEY,
    request_group   TEXT NOT NULL,
    channel         TEXT,
    open            INTEGER NOT NULL DEFAULT 1,
    created_at_ms   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_resident_notification_requests_group
    ON resident_notification_requests(request_group, open);
"""


class SqliteResidentNotificationOutbox(ResidentNotificationOutboxPort):
    """The resident's notification outbox in one SQLite file."""

    def __init__(self, path: str | Path, *, max_pending: int) -> None:
        if max_pending < 1:
            raise ValueError("max_pending must be at least 1")
        self._max_pending = max_pending
        self._path = Path(path).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._listeners: list[Callable[[], None]] = []

    def close(self) -> None:
        # sqlite3.Connection.close is idempotent; a closed connection refuses
        # every later call with ProgrammingError rather than reopening.
        self._conn.close()

    # -- writers -----------------------------------------------------------

    async def attention(
        self,
        *,
        request_id: str,
        group: str,
        input_kind: AttentionInputKind,
        prompt: str,
        channel: str | None = None,
    ) -> bool:
        if not request_id or not group:
            raise ValueError("attention needs a request_id and a group")
        now = _now()
        with self._conn:
            inserted = self._conn.execute(
                "INSERT OR IGNORE INTO resident_notification_requests "
                "(request_id, request_group, channel, open, created_at_ms) "
                "VALUES (?, ?, ?, 1, ?)",
                (request_id, group, channel, _ms(now)),
            ).rowcount
            if not inserted:
                return False
            self._append(
                {
                    "op": ResidentNotificationOp.ATTENTION.value,
                    "channel": channel,
                    "request_id": request_id,
                    "input_kind": AttentionInputKind(input_kind).value,
                    "prompt": prompt,
                },
                now,
            )
        self._changed()
        return True

    async def resolve(self, *, group: str) -> int:
        if not group:
            raise ValueError("resolve needs a group")
        now = _now()
        with self._conn:
            rows = self._conn.execute(
                "SELECT request_id, channel FROM resident_notification_requests "
                "WHERE request_group = ? AND open = 1 ORDER BY created_at_ms",
                (group,),
            ).fetchall()
            if not rows:
                return 0
            self._conn.execute(
                "UPDATE resident_notification_requests SET open = 0 "
                "WHERE request_group = ? AND open = 1",
                (group,),
            )
            by_channel: dict[str | None, list[str]] = {}
            for row in rows:
                by_channel.setdefault(row["channel"], []).append(row["request_id"])
            for channel, request_ids in by_channel.items():
                for start in range(0, len(request_ids), MAX_RESOLVED_REQUESTS):
                    self._append(
                        {
                            "op": ResidentNotificationOp.RESOLVED.value,
                            "channel": channel,
                            "resolved_request_ids": request_ids[
                                start : start + MAX_RESOLVED_REQUESTS
                            ],
                        },
                        now,
                    )
        self._changed()
        return len(rows)

    async def notify(
        self,
        *,
        draft: NotificationDraft,
        idempotency_key: str,
        channel: str | None = None,
    ) -> ResidentNotificationEntry:
        now = _now()
        with self._conn:
            entry = self._append(
                {
                    "op": ResidentNotificationOp.NOTIFY.value,
                    "channel": channel,
                    "draft": draft.model_dump(mode="json"),
                    "idempotency_key": idempotency_key,
                },
                now,
            )
        self._changed()
        return entry

    # -- reader (Forge, through the shim) ----------------------------------

    async def pending(self, *, limit: int) -> list[ResidentNotificationEntry]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        rows = self._conn.execute(
            "SELECT seq, entry_json FROM resident_notification_outbox ORDER BY seq LIMIT ?",
            (limit,),
        ).fetchall()
        return [_entry(row["seq"], json.loads(row["entry_json"])) for row in rows]

    async def acknowledge(self, *, through_seq: int) -> int:
        with self._conn:
            return self._conn.execute(
                "DELETE FROM resident_notification_outbox WHERE seq <= ?",
                (through_seq,),
            ).rowcount

    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    # -- internals ---------------------------------------------------------

    def _append(self, fields: dict[str, Any], now: datetime) -> ResidentNotificationEntry:
        payload = {**fields, "created_at": now.isoformat()}
        # Validated inside the caller's transaction: a malformed entry rolls back and
        # raises here instead of being stored where it would stop Forge's drain.
        cursor = self._conn.execute(
            "INSERT INTO resident_notification_outbox (entry_json, created_at_ms) VALUES (?, ?)",
            (json.dumps(payload), _ms(now)),
        )
        seq = int(cursor.lastrowid or 0)
        entry = _entry(seq, payload)
        self._trim()
        return entry

    def _trim(self) -> None:
        """Keep at most ``max_pending`` unacknowledged entries, dropping the oldest.

        Only reached when Forge has not drained this resident for a long time;
        every drop is logged with its count.
        """
        (count,) = self._conn.execute(
            "SELECT COUNT(*) FROM resident_notification_outbox"
        ).fetchone()
        excess = int(count) - self._max_pending
        if excess <= 0:
            return
        self._conn.execute(
            "DELETE FROM resident_notification_outbox WHERE seq IN "
            "(SELECT seq FROM resident_notification_outbox ORDER BY seq LIMIT ?)",
            (excess,),
        )
        logger.warning(
            "Resident notification outbox is full (%d pending, max_pending=%d): dropped the "
            "%d oldest entries Forge never acknowledged. Check that Forge reaches this "
            "resident's OpenClaw shim.",
            count,
            self._max_pending,
            excess,
        )

    def _changed(self) -> None:
        for listener in list(self._listeners):
            listener()


def _entry(seq: int, payload: dict[str, Any]) -> ResidentNotificationEntry:
    return ResidentNotificationEntry.model_validate({**payload, "seq": seq})


def _now() -> datetime:
    return datetime.now(UTC)


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)
