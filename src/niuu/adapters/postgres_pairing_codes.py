"""PostgreSQL adapter for single-use Guild node-pairing codes."""

from __future__ import annotations

from datetime import datetime

import asyncpg

from niuu.domain.models import PairingCode
from niuu.ports.pairing_codes import PairingCodeRepository


class PostgresPairingCodeRepository(PairingCodeRepository):
    """Raw-SQL repository for node-pairing codes."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(
        self,
        *,
        code_hash: str,
        created_by: str,
        tenant_id: str,
        expires_at: datetime,
    ) -> PairingCode:
        row = await self._pool.fetchrow(
            """
            INSERT INTO niuu_pairing_codes (code_hash, created_by, tenant_id, expires_at)
            VALUES ($1, $2, $3, $4)
            RETURNING id, code_hash, created_by, tenant_id, expires_at, created_at,
                      consumed_at, consumed_by_node_id
            """,
            code_hash,
            created_by,
            tenant_id,
            expires_at,
        )
        return self._row_to_pairing_code(row)

    async def consume(self, code_hash: str) -> PairingCode | None:
        # A single UPDATE ... RETURNING is atomic in PostgreSQL: under
        # concurrent presentation of the same code, the row lock serializes
        # the two UPDATEs and only the first commits with consumed_at set to
        # NULL still true in its WHERE clause, so only one caller ever gets a
        # row back (see the race test in test_guild_join_service.py).
        row = await self._pool.fetchrow(
            """
            UPDATE niuu_pairing_codes
            SET consumed_at = NOW()
            WHERE code_hash = $1 AND consumed_at IS NULL AND expires_at > NOW()
            RETURNING id, code_hash, created_by, tenant_id, expires_at, created_at,
                      consumed_at, consumed_by_node_id
            """,
            code_hash,
        )
        if row is None:
            return None
        return self._row_to_pairing_code(row)

    async def attach_node(self, pairing_code_id: str, node_id: str) -> None:
        await self._pool.execute(
            "UPDATE niuu_pairing_codes SET consumed_by_node_id = $1::uuid WHERE id = $2::uuid",
            node_id,
            pairing_code_id,
        )

    @staticmethod
    def _row_to_pairing_code(row: asyncpg.Record) -> PairingCode:
        return PairingCode(
            id=str(row["id"]),
            code_hash=row["code_hash"],
            created_by=row["created_by"],
            tenant_id=row["tenant_id"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
            consumed_at=row["consumed_at"],
            consumed_by_node_id=(
                str(row["consumed_by_node_id"]) if row["consumed_by_node_id"] else None
            ),
        )
