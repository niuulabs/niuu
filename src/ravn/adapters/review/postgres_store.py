"""PostgreSQL-backed review queue store (raw SQL, asyncpg)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ravn.odin.review import ReviewItem
from ravn.ports.review_queue import ReviewQueueStore

_UPSERT_SQL = """
INSERT INTO odin_review_items (
    item_id, kind, status, environment_id, valkyrie_id, requested_at, payload, updated_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
ON CONFLICT (item_id) DO UPDATE SET
    kind = EXCLUDED.kind,
    status = EXCLUDED.status,
    environment_id = EXCLUDED.environment_id,
    valkyrie_id = EXCLUDED.valkyrie_id,
    requested_at = EXCLUDED.requested_at,
    payload = EXCLUDED.payload,
    updated_at = NOW()
"""


class PostgresReviewQueueStore(ReviewQueueStore):
    """Store review items in ``odin_review_items`` with the full envelope as JSONB."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def upsert(self, item: ReviewItem) -> ReviewItem:
        await self._pool.execute(
            _UPSERT_SQL,
            item.item_id,
            item.kind,
            item.status,
            item.environment_id,
            item.valkyrie_id,
            _requested_at(item),
            json.dumps(item.to_payload()),
        )
        return item

    async def get(self, item_id: str) -> ReviewItem | None:
        row = await self._pool.fetchrow(
            "SELECT payload FROM odin_review_items WHERE item_id = $1",
            item_id,
        )
        if row is None:
            return None
        return _item_from_row(row)

    async def list_items(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        environment_id: str | None = None,
        risk_class: str | None = None,
        query: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ReviewItem]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            params.append(status)
            clauses.append(f"status = ${len(params)}")
        if kind:
            params.append(kind)
            clauses.append(f"kind = ${len(params)}")
        if environment_id:
            params.append(environment_id)
            clauses.append(f"environment_id = ${len(params)}")
        if risk_class:
            params.append(risk_class)
            clauses.append(f"payload->>'risk_class' = ${len(params)}")
        if query:
            params.append(f"%{query}%")
            placeholder = f"${len(params)}"
            clauses.append(
                "("
                + " OR ".join(
                    (
                        f"payload->>'title' ILIKE {placeholder}",
                        f"payload->>'summary' ILIKE {placeholder}",
                        f"environment_id ILIKE {placeholder}",
                        f"valkyrie_id ILIKE {placeholder}",
                        f"kind ILIKE {placeholder}",
                        f"payload->>'requested_action' ILIKE {placeholder}",
                    )
                )
                + ")"
            )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT payload FROM odin_review_items {where} ORDER BY requested_at DESC"
        if limit is not None:
            params.append(max(limit, 0))
            sql += f" LIMIT ${len(params)}"
        if offset:
            params.append(max(offset, 0))
            sql += f" OFFSET ${len(params)}"
        rows = await self._pool.fetch(sql, *params)
        return [_item_from_row(row) for row in rows]

    async def counts(self) -> dict[str, int]:
        rows = await self._pool.fetch(
            "SELECT status, COUNT(*) AS total FROM odin_review_items GROUP BY status"
        )
        return {str(row["status"]): int(row["total"]) for row in rows}


def _item_from_row(row: Any) -> ReviewItem:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return ReviewItem.from_payload(payload)


def _requested_at(item: ReviewItem) -> datetime:
    try:
        return datetime.fromisoformat(item.requested_at)
    except ValueError:
        return datetime.now(UTC)
