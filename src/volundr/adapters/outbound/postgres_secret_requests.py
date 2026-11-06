"""PostgreSQL adapter for secure secret requests. Raw asyncpg SQL.

Every transition is one conditional ``UPDATE ... WHERE status = ...  RETURNING *``, so two
callers racing for the same request cannot both win: the loser gets no row back. The table
holds no secret value (see ``migrations/000105_session_secret_requests.up.sql``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from volundr.adapters.outbound._jsonb import scrub_text
from volundr.domain.secret_request_ports import SecretRequestRepository
from volundr.domain.secret_requests import (
    SecretKeep,
    SecretRequest,
    SecretRequestError,
    SecretRequestStatus,
)

INSERT_SQL = """INSERT INTO session_secret_requests
    (id, session_id, owner_id, name, purpose, target, status, created_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT (session_id, name) WHERE status IN ('pending', 'fulfilled') DO NOTHING
RETURNING *"""

SELECT_OPEN_SQL = """SELECT * FROM session_secret_requests
WHERE session_id = $1 AND name = $2 AND status IN ('pending', 'fulfilled')"""

SELECT_ONE_SQL = "SELECT * FROM session_secret_requests WHERE id = $1"

LIST_SQL = """SELECT * FROM session_secret_requests
WHERE session_id = $1 AND ($2::text IS NULL OR status = $2::text)
ORDER BY created_at DESC, id DESC"""

FULFILL_SQL = """UPDATE session_secret_requests
SET status = 'fulfilled', keep = $2, credential_name = $3, resolved_at = $4
WHERE id = $1 AND status = 'pending'
RETURNING *"""

DECLINE_SQL = """UPDATE session_secret_requests
SET status = 'declined', resolved_at = $2
WHERE id = $1 AND status = 'pending'
RETURNING *"""

DELIVER_SQL = """UPDATE session_secret_requests
SET status = 'delivered', delivered_at = $2
WHERE id = $1 AND status = 'fulfilled'
RETURNING *"""

CANCEL_SQL = """UPDATE session_secret_requests
SET status = 'cancelled', resolved_at = $2
WHERE id = $1 AND status IN ('pending', 'fulfilled')
RETURNING *"""

CANCEL_OPEN_FOR_SESSION_SQL = """UPDATE session_secret_requests
SET status = 'cancelled', resolved_at = $2
WHERE session_id = $1 AND status IN ('pending', 'fulfilled')
RETURNING *"""

DELETE_FOR_SESSION_SQL = "DELETE FROM session_secret_requests WHERE session_id = $1"


def row_to_request(row: Any) -> SecretRequest:
    return SecretRequest(
        id=row["id"],
        session_id=row["session_id"],
        owner_id=row["owner_id"],
        name=row["name"],
        purpose=row["purpose"],
        target=row["target"],
        status=SecretRequestStatus(row["status"]),
        created_at=row["created_at"],
        keep=SecretKeep(row["keep"]) if row["keep"] is not None else None,
        credential_name=row["credential_name"],
        resolved_at=row["resolved_at"],
        delivered_at=row["delivered_at"],
    )


class PostgresSecretRequestRepository(SecretRequestRepository):
    def __init__(self, pool: Any):
        self._pool = pool

    async def create_or_get_open(self, request: SecretRequest) -> tuple[SecretRequest, bool]:
        row = await self._pool.fetchrow(
            INSERT_SQL,
            request.id,
            request.session_id,
            request.owner_id,
            request.name,
            scrub_text(request.purpose),
            scrub_text(request.target),
            request.status.value,
            request.created_at,
        )
        if row is not None:
            return row_to_request(row), True
        existing = await self._pool.fetchrow(SELECT_OPEN_SQL, request.session_id, request.name)
        if existing is None:
            raise SecretRequestError(
                f"The open secret request for {request.name} closed while it was being "
                "looked up; ask again"
            )
        return row_to_request(existing), False

    async def get(self, request_id: UUID) -> SecretRequest | None:
        row = await self._pool.fetchrow(SELECT_ONE_SQL, request_id)
        return row_to_request(row) if row is not None else None

    async def list_for_session(
        self, session_id: UUID, status: SecretRequestStatus | None = None
    ) -> list[SecretRequest]:
        rows = await self._pool.fetch(
            LIST_SQL, session_id, status.value if status is not None else None
        )
        return [row_to_request(row) for row in rows]

    async def mark_fulfilled(
        self,
        request_id: UUID,
        *,
        keep: SecretKeep,
        credential_name: str,
        at: datetime,
    ) -> SecretRequest | None:
        row = await self._pool.fetchrow(FULFILL_SQL, request_id, keep.value, credential_name, at)
        return row_to_request(row) if row is not None else None

    async def mark_declined(self, request_id: UUID, *, at: datetime) -> SecretRequest | None:
        row = await self._pool.fetchrow(DECLINE_SQL, request_id, at)
        return row_to_request(row) if row is not None else None

    async def mark_delivered(self, request_id: UUID, *, at: datetime) -> SecretRequest | None:
        row = await self._pool.fetchrow(DELIVER_SQL, request_id, at)
        return row_to_request(row) if row is not None else None

    async def mark_cancelled(self, request_id: UUID, *, at: datetime) -> SecretRequest | None:
        row = await self._pool.fetchrow(CANCEL_SQL, request_id, at)
        return row_to_request(row) if row is not None else None

    async def cancel_open_for_session(
        self, session_id: UUID, *, at: datetime
    ) -> list[SecretRequest]:
        rows = await self._pool.fetch(CANCEL_OPEN_FOR_SESSION_SQL, session_id, at)
        return [row_to_request(row) for row in rows]

    async def delete_for_session(self, session_id: UUID) -> int:
        result = await self._pool.execute(DELETE_FOR_SESSION_SQL, session_id)
        return int(str(result).rsplit(" ", 1)[-1])
