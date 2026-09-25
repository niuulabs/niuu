"""PostgreSQL adapter for registered Guild nodes."""

from __future__ import annotations

import asyncpg

from niuu.domain.models import RegisteredNode
from niuu.ports.nodes import NodeRepository


class PostgresNodeRepository(NodeRepository):
    """Raw-SQL repository for machines joined to Guild."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, node_id: str) -> RegisteredNode | None:
        row = await self._pool.fetchrow(
            """
            SELECT id, name, public_key, tenant_id, created_by, allow_plaintext, created_at,
                   last_seen_at, last_request_at
            FROM niuu_nodes WHERE id = $1::uuid
            """,
            node_id,
        )
        return self._row_to_node(row) if row is not None else None

    async def list_for_tenant(self, tenant_id: str) -> list[RegisteredNode]:
        rows = await self._pool.fetch(
            """
            SELECT id, name, public_key, tenant_id, created_by, allow_plaintext, created_at,
                   last_seen_at, last_request_at
            FROM niuu_nodes WHERE tenant_id = $1
            ORDER BY created_at DESC
            """,
            tenant_id,
        )
        return [self._row_to_node(row) for row in rows]

    async def touch_heartbeat(self, node_id: str) -> RegisteredNode | None:
        row = await self._pool.fetchrow(
            """
            UPDATE niuu_nodes SET last_seen_at = NOW() WHERE id = $1::uuid
            RETURNING id, name, public_key, tenant_id, created_by, allow_plaintext, created_at,
                      last_seen_at, last_request_at
            """,
            node_id,
        )
        return self._row_to_node(row) if row is not None else None

    async def try_advance_watermark(self, node_id: str, *, timestamp_ms: int) -> bool:
        # One atomic conditional UPDATE: the WHERE clause re-checks the
        # watermark at write time under the row's lock, so two concurrent
        # calls can never both succeed for a non-increasing timestamp pair —
        # see the port docstring for why a separate check-then-write is unsafe.
        row = await self._pool.fetchrow(
            """
            UPDATE niuu_nodes
            SET last_request_at = $2
            WHERE id = $1::uuid AND (last_request_at IS NULL OR last_request_at < $2)
            RETURNING id
            """,
            node_id,
            timestamp_ms,
        )
        return row is not None

    async def delete(self, node_id: str) -> None:
        await self._pool.execute("DELETE FROM niuu_nodes WHERE id = $1::uuid", node_id)

    @staticmethod
    def _row_to_node(row: asyncpg.Record) -> RegisteredNode:
        return RegisteredNode(
            id=str(row["id"]),
            name=row["name"],
            public_key=row["public_key"],
            tenant_id=row["tenant_id"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            allow_plaintext=row["allow_plaintext"],
            last_seen_at=row["last_seen_at"],
            last_request_at=row["last_request_at"],
        )
