"""PostgreSQL adapter for personal access token repository."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from niuu.domain.models import PersonalAccessToken
from niuu.ports.pat_repository import PATRepository


class PostgresPATRepository(PATRepository):
    """PostgreSQL implementation of PATRepository using raw SQL."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(
        self,
        owner_id: str,
        name: str,
        token_hash: str,
        *,
        tenant_id: str = "",
        scopes: tuple[str, ...] | None = None,
        expires_at: datetime | None = None,
    ) -> PersonalAccessToken:
        """Persist a new PAT record."""
        row = await self._pool.fetchrow(
            """
            INSERT INTO personal_access_tokens
                (owner_id, name, token_hash, tenant_id, scopes, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, owner_id, name, created_at, last_used_at, tenant_id, scopes, expires_at
            """,
            owner_id,
            name,
            token_hash,
            tenant_id,
            list(scopes) if scopes is not None else None,
            expires_at,
        )
        return self._row_to_pat(row)

    async def list(self, owner_id: str) -> list[PersonalAccessToken]:
        """List all PATs for an owner."""
        rows = await self._pool.fetch(
            """
            SELECT id, owner_id, name, created_at, last_used_at, tenant_id, scopes, expires_at
            FROM personal_access_tokens
            WHERE owner_id = $1
            ORDER BY created_at DESC
            """,
            owner_id,
        )
        return [self._row_to_pat(row) for row in rows]

    async def get(self, pat_id: UUID, owner_id: str) -> PersonalAccessToken | None:
        """Retrieve a PAT by ID scoped to an owner."""
        row = await self._pool.fetchrow(
            """
            SELECT id, owner_id, name, created_at, last_used_at, tenant_id, scopes, expires_at
            FROM personal_access_tokens
            WHERE id = $1 AND owner_id = $2
            """,
            pat_id,
            owner_id,
        )
        if row is None:
            return None
        return self._row_to_pat(row)

    async def delete(self, pat_id: UUID, owner_id: str) -> str | None:
        """Delete a PAT. Returns the token_hash if deleted, None otherwise."""
        row = await self._pool.fetchrow(
            """
            DELETE FROM personal_access_tokens
            WHERE id = $1 AND owner_id = $2
            RETURNING token_hash
            """,
            pat_id,
            owner_id,
        )
        if row is None:
            return None
        return row["token_hash"]

    async def exists_by_hash(self, token_hash: str) -> bool:
        """Check if a PAT with the given hash exists (i.e. not revoked)."""
        row = await self._pool.fetchrow(
            """SELECT 1 FROM personal_access_tokens
               WHERE token_hash = $1 AND tenant_id <> '' AND expires_at > NOW()""",
            token_hash,
        )
        return row is not None

    async def touch_last_used(self, token_hash: str) -> None:
        """Update last_used_at for the PAT identified by token_hash."""
        await self._pool.execute(
            "UPDATE personal_access_tokens SET last_used_at = NOW() WHERE token_hash = $1",
            token_hash,
        )

    @staticmethod
    def _row_to_pat(row: asyncpg.Record) -> PersonalAccessToken:
        """Convert a database row to a PersonalAccessToken domain model."""
        return PersonalAccessToken(
            id=row["id"],
            owner_id=row["owner_id"],
            name=row["name"],
            created_at=row["created_at"],
            last_used_at=row["last_used_at"],
            tenant_id=row["tenant_id"],
            scopes=tuple(row["scopes"]) if row["scopes"] is not None else None,
            expires_at=row["expires_at"],
        )
