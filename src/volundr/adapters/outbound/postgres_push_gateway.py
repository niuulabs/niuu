"""PostgreSQL store of the push gateway: feed positions and the alerts it owes.

Raw asyncpg SQL. ``forge_push_deliveries`` is the gateway's outbox, one row per
notification of any Guild node (``instance_id`` + ``notification_id``); a row is
inserted ``pending`` in the same transaction that advances the positions, so a
notification the gateway read is never lost and never alerted twice. Claims are
leases (``next_attempt_at`` moves forward); settles are fenced by ``attempts``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import asyncpg

from volundr.adapters.outbound._jsonb import dumps_jsonb, scrub_text
from volundr.domain.push import GuildNotification
from volundr.domain.push_ports import PushDelivery, PushDeliveryRepository, PushDeliveryStatus

#: The single state row: this gateway reads one Guild.
STATE_ID = "guild"

LOAD_POSITIONS_SQL = "SELECT positions FROM forge_push_gateway_state WHERE id = $1"

# ``||`` merges per node, so two writers can only move different nodes' positions.
SAVE_POSITIONS_SQL = """INSERT INTO forge_push_gateway_state (id, positions)
VALUES ($1, $2::jsonb)
ON CONFLICT (id) DO UPDATE
    SET positions = forge_push_gateway_state.positions || EXCLUDED.positions,
        updated_at = now()"""

INSERT_DELIVERY_SQL = """INSERT INTO forge_push_deliveries
    (instance_id, notification_id, owner_id, notified_at, payload)
VALUES ($1, $2, $3, $4, $5::jsonb)
ON CONFLICT (instance_id, notification_id) DO NOTHING
RETURNING notification_id"""

CLAIM_DUE_SQL = """WITH due AS (
    SELECT instance_id, notification_id FROM forge_push_deliveries
    WHERE status = 'pending' AND next_attempt_at <= now()
    ORDER BY created_at, notified_at NULLS LAST
    FOR UPDATE SKIP LOCKED
    LIMIT $1
)
UPDATE forge_push_deliveries d
SET attempts = d.attempts + 1,
    next_attempt_at = now() + make_interval(secs => $2::double precision),
    updated_at = now()
FROM due
WHERE d.instance_id = due.instance_id AND d.notification_id = due.notification_id
RETURNING d.*"""

_CLAIM_GUARD = (
    "WHERE instance_id = $1 AND notification_id = $2 AND attempts = $3 AND status = 'pending'"
)

SETTLE_SQL = f"""UPDATE forge_push_deliveries
SET status = $4, detail = $5, settled_at = now(), updated_at = now()
{_CLAIM_GUARD}"""

RETRY_SQL = f"""UPDATE forge_push_deliveries
SET next_attempt_at = $4, detail = $5, updated_at = now()
{_CLAIM_GUARD}"""

COUNT_SENT_SQL = """SELECT COUNT(*) FROM forge_push_deliveries
WHERE owner_id = $1 AND status = 'sent' AND settled_at >= $2"""

PRUNE_SQL = """DELETE FROM forge_push_deliveries
WHERE status <> 'pending' AND settled_at < $1"""


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _positions(value: Any) -> dict[str, int]:
    decoded = _json(value)
    if not isinstance(decoded, dict):
        return {}
    return {str(key): seq for key, seq in decoded.items() if type(seq) is int}


class PostgresPushDeliveryRepository(PushDeliveryRepository):
    """Raw-SQL implementation of the push gateway's store."""

    def __init__(self, pool: asyncpg.Pool, *, max_detail_chars: int) -> None:
        if max_detail_chars < 1:
            raise ValueError("max_detail_chars must be at least 1")
        self._pool = pool
        self._max_detail_chars = max_detail_chars

    async def load_positions(self) -> dict[str, int]:
        return _positions(await self._pool.fetchval(LOAD_POSITIONS_SQL, STATE_ID))

    async def save_positions(self, positions: Mapping[str, int]) -> None:
        if not positions:
            return
        await self._pool.execute(SAVE_POSITIONS_SQL, STATE_ID, json.dumps(dict(positions)))

    async def record(
        self, notifications: list[GuildNotification], positions: Mapping[str, int]
    ) -> list[GuildNotification]:
        fresh: list[GuildNotification] = []
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for notification in notifications:
                    inserted = await conn.fetchval(
                        INSERT_DELIVERY_SQL,
                        notification.instance_id,
                        notification.id,
                        notification.owner_id,
                        notification.created_at,
                        dumps_jsonb(notification.stored()),
                    )
                    if inserted is not None:
                        fresh.append(notification)
                if positions:
                    await conn.execute(SAVE_POSITIONS_SQL, STATE_ID, json.dumps(dict(positions)))
        return fresh

    async def claim_due(self, *, limit: int, lease_seconds: float) -> list[PushDelivery]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        rows = await self._pool.fetch(CLAIM_DUE_SQL, limit, float(lease_seconds))
        claimed = [
            PushDelivery(
                notification=GuildNotification.from_feed_item(_json(row["payload"])),
                attempts=row["attempts"],
                created_at=row["created_at"],
            )
            for row in rows
        ]
        claimed.sort(key=lambda item: (item.created_at, item.notification.seq))
        return claimed

    def _detail(self, detail: str) -> str:
        return str(scrub_text(detail[: self._max_detail_chars]))

    async def settle(self, delivery: PushDelivery, status: PushDeliveryStatus, detail: str) -> bool:
        if status is PushDeliveryStatus.PENDING:
            raise ValueError("A delivery is settled as sent, suppressed, skipped or failed")
        result = await self._pool.execute(
            SETTLE_SQL,
            delivery.notification.instance_id,
            delivery.notification.id,
            delivery.attempts,
            status.value,
            self._detail(detail),
        )
        return result == "UPDATE 1"

    async def retry(self, delivery: PushDelivery, *, detail: str, retry_at: datetime) -> bool:
        result = await self._pool.execute(
            RETRY_SQL,
            delivery.notification.instance_id,
            delivery.notification.id,
            delivery.attempts,
            retry_at,
            self._detail(detail),
        )
        return result == "UPDATE 1"

    async def count_sent_since(self, owner_id: str, since: datetime) -> int:
        return int(await self._pool.fetchval(COUNT_SENT_SQL, owner_id, since) or 0)

    async def prune_settled(self, before: datetime) -> int:
        result = await self._pool.execute(PRUNE_SQL, before)
        return int(result.rsplit(" ", 1)[-1])
