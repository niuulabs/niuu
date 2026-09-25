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
        allow_plaintext: bool,
        allow_untrusted_node_auth: bool,
        expires_at: datetime,
    ) -> PairingCode:
        row = await self._pool.fetchrow(
            """
            INSERT INTO niuu_pairing_codes
                (code_hash, created_by, tenant_id, allow_plaintext,
                 allow_untrusted_node_auth, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, code_hash, created_by, tenant_id, allow_plaintext,
                      allow_untrusted_node_auth, expires_at, created_at,
                      consumed_at, consumed_by_node_id
            """,
            code_hash,
            created_by,
            tenant_id,
            allow_plaintext,
            allow_untrusted_node_auth,
            expires_at,
        )
        return self._row_to_pairing_code(row)

    async def peek(self, code_hash: str) -> PairingCode | None:
        row = await self._pool.fetchrow(
            """
            SELECT id, code_hash, created_by, tenant_id, allow_plaintext,
                   allow_untrusted_node_auth, expires_at, created_at,
                   consumed_at, consumed_by_node_id
            FROM niuu_pairing_codes WHERE code_hash = $1
            """,
            code_hash,
        )
        if row is None:
            return None
        return self._row_to_pairing_code(row)

    @staticmethod
    def _row_to_pairing_code(row: asyncpg.Record) -> PairingCode:
        return PairingCode(
            id=str(row["id"]),
            code_hash=row["code_hash"],
            created_by=row["created_by"],
            tenant_id=row["tenant_id"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
            allow_plaintext=row["allow_plaintext"],
            allow_untrusted_node_auth=row["allow_untrusted_node_auth"],
            consumed_at=row["consumed_at"],
            consumed_by_node_id=(
                str(row["consumed_by_node_id"]) if row["consumed_by_node_id"] else None
            ),
        )
