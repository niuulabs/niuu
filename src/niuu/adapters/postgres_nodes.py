"""PostgreSQL adapter for registered Guild nodes."""

from __future__ import annotations

import asyncpg

from niuu.domain.models import RegisteredNode
from niuu.ports.nodes import NodeRepository


class PostgresNodeRepository(NodeRepository):
    """Raw-SQL repository for machines joined to Guild."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(
        self,
        *,
        node_id: str,
        name: str,
        public_key: str,
        tenant_id: str,
        created_by: str,
    ) -> RegisteredNode:
        row = await self._pool.fetchrow(
            """
            INSERT INTO niuu_nodes (id, name, public_key, tenant_id, created_by)
            VALUES ($1::uuid, $2, $3, $4, $5)
            RETURNING id, name, public_key, tenant_id, created_by, created_at,
                      last_seen_at, last_request_at
            """,
            node_id,
            name,
            public_key,
            tenant_id,
            created_by,
        )
        return self._row_to_node(row)

    async def get(self, node_id: str) -> RegisteredNode | None:
        row = await self._pool.fetchrow(
            """
            SELECT id, name, public_key, tenant_id, created_by, created_at,
                   last_seen_at, last_request_at
            FROM niuu_nodes WHERE id = $1::uuid
            """,
            node_id,
        )
        return self._row_to_node(row) if row is not None else None

    async def touch_heartbeat(self, node_id: str) -> RegisteredNode | None:
        row = await self._pool.fetchrow(
            """
            UPDATE niuu_nodes SET last_seen_at = NOW() WHERE id = $1::uuid
            RETURNING id, name, public_key, tenant_id, created_by, created_at,
                      last_seen_at, last_request_at
            """,
            node_id,
        )
        return self._row_to_node(row) if row is not None else None

    async def record_request(self, node_id: str, *, timestamp: int) -> None:
        await self._pool.execute(
            "UPDATE niuu_nodes SET last_request_at = $1 WHERE id = $2::uuid",
            timestamp,
            node_id,
        )

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
            last_seen_at=row["last_seen_at"],
            last_request_at=row["last_request_at"],
        )
