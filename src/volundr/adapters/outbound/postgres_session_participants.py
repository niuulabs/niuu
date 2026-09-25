"""PostgreSQL adapter for durable per-session collaboration grants."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from volundr.domain.ports import SessionParticipantRepository
from volundr.domain.session_participants import ParticipantRole, SessionParticipant

_COLUMNS = (
    "session_id, user_id, tenant_id, role, status, invited_by, created_at, updated_at, expires_at"
)


class PostgresSessionParticipantRepository(SessionParticipantRepository):
    """Raw-SQL persistence for ``session_participants``."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def invite(
        self,
        session_id: UUID,
        user_id: str,
        tenant_id: str,
        role: ParticipantRole,
        invited_by: str,
        expires_at: datetime | None,
    ) -> SessionParticipant:
        row = await self._pool.fetchrow(
            f"""
            INSERT INTO session_participants
                (session_id, user_id, tenant_id, role, status, invited_by, expires_at)
            VALUES ($1, $2, $3, $4, 'invited', $5, $6)
            ON CONFLICT (session_id, user_id) DO UPDATE SET
                role = EXCLUDED.role,
                status = 'invited',
                invited_by = EXCLUDED.invited_by,
                expires_at = EXCLUDED.expires_at,
                updated_at = NOW()
            RETURNING {_COLUMNS}
            """,
            session_id,
            user_id,
            tenant_id,
            role.value,
            invited_by,
            expires_at,
        )
        return self._row_to_participant(row)

    async def accept(self, session_id: UUID, user_id: str) -> SessionParticipant | None:
        row = await self._pool.fetchrow(
            f"""
            UPDATE session_participants
            SET status = 'active', updated_at = NOW()
            WHERE session_id = $1 AND user_id = $2 AND status = 'invited'
            RETURNING {_COLUMNS}
            """,
            session_id,
            user_id,
        )
        if row is None:
            return None
        return self._row_to_participant(row)

    async def revoke(self, session_id: UUID, user_id: str) -> SessionParticipant | None:
        row = await self._pool.fetchrow(
            f"""
            UPDATE session_participants
            SET status = 'revoked', updated_at = NOW()
            WHERE session_id = $1 AND user_id = $2
            RETURNING {_COLUMNS}
            """,
            session_id,
            user_id,
        )
        if row is None:
            return None
        return self._row_to_participant(row)

    async def get(self, session_id: UUID, user_id: str) -> SessionParticipant | None:
        row = await self._pool.fetchrow(
            f"SELECT {_COLUMNS} FROM session_participants WHERE session_id = $1 AND user_id = $2",
            session_id,
            user_id,
        )
        if row is None:
            return None
        return self._row_to_participant(row)

    async def list_for_session(self, session_id: UUID) -> list[SessionParticipant]:
        rows = await self._pool.fetch(
            f"SELECT {_COLUMNS} FROM session_participants WHERE session_id = $1 "
            "ORDER BY created_at",
            session_id,
        )
        return [self._row_to_participant(row) for row in rows]

    async def list_active_for_session(self, session_id: UUID) -> list[SessionParticipant]:
        rows = await self._pool.fetch(
            f"SELECT {_COLUMNS} FROM session_participants "
            "WHERE session_id = $1 AND status = 'active'",
            session_id,
        )
        return [self._row_to_participant(row) for row in rows]

    async def list_active_for_user(self, user_id: str) -> list[SessionParticipant]:
        rows = await self._pool.fetch(
            f"SELECT {_COLUMNS} FROM session_participants WHERE user_id = $1 AND status = 'active'",
            user_id,
        )
        return [self._row_to_participant(row) for row in rows]

    @staticmethod
    def _row_to_participant(row: asyncpg.Record) -> SessionParticipant:
        return SessionParticipant(
            session_id=row["session_id"],
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
            role=ParticipantRole(row["role"]),
            status=row["status"],
            invited_by=row["invited_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            expires_at=row["expires_at"],
        )
