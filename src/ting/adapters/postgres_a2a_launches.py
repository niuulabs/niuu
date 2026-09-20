"""PostgreSQL receiver-side A2A launch reservations."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from ting.domain.a2a_launch import A2ALaunchReservation
from ting.domain.delivery_execution import ExecutionConflictError
from ting.ports.a2a_launch import A2ALaunchReservationRepository


class PostgresA2ALaunchReservationRepository(A2ALaunchReservationRepository):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def reserve(self, reservation: A2ALaunchReservation) -> tuple[A2ALaunchReservation, bool]:
        result = await self._pool.execute(
            """
            INSERT INTO a2a_launch_reservations (
                id, owner_id, tenant_id, message_id, request_digest, workflow_id,
                task_id, campaign_id, state, created_at, updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'reserved',$9,$10)
            ON CONFLICT (owner_id, tenant_id, message_id) DO NOTHING
            """,
            reservation.id,
            reservation.owner_id,
            reservation.tenant_id,
            reservation.message_id,
            reservation.request_digest,
            reservation.workflow_id,
            reservation.task_id,
            reservation.campaign_id,
            reservation.created_at,
            reservation.updated_at,
        )
        row = await self._pool.fetchrow(
            """
            SELECT * FROM a2a_launch_reservations
            WHERE owner_id = $1 AND tenant_id = $2 AND message_id = $3
            """,
            reservation.owner_id,
            reservation.tenant_id,
            reservation.message_id,
        )
        if row is None:
            raise ExecutionConflictError("A2A launch reservation disappeared")
        existing = _from_row(row)
        if (
            existing.request_digest != reservation.request_digest
            or existing.workflow_id != reservation.workflow_id
        ):
            raise ExecutionConflictError("A2A messageId was reused for different content")
        return existing, result == "INSERT 0 1"

    async def claim(
        self, reservation_id: UUID, *, lease_token: UUID, lease_until: datetime
    ) -> A2ALaunchReservation | None:
        row = await self._pool.fetchrow(
            """
            UPDATE a2a_launch_reservations
            SET state = 'launching', lease_token = $2, lease_expires_at = $3,
                fencing_generation = fencing_generation + 1, updated_at = NOW()
            WHERE id = $1 AND state <> 'launched'
              AND (state = 'reserved' OR lease_expires_at IS NULL OR lease_expires_at <= NOW())
            RETURNING *
            """,
            reservation_id,
            lease_token,
            lease_until,
        )
        return _from_row(row) if row is not None else None

    async def mark_launched(
        self, reservation_id: UUID, *, lease_token: UUID, session_id: str
    ) -> A2ALaunchReservation:
        row = await self._pool.fetchrow(
            """
            UPDATE a2a_launch_reservations
            SET state = 'launched', session_id = $3, lease_token = NULL,
                lease_expires_at = NULL, updated_at = NOW()
            WHERE id = $1 AND lease_token = $2
            RETURNING *
            """,
            reservation_id,
            lease_token,
            session_id,
        )
        if row is None:
            raise ExecutionConflictError("A2A launch completion lost its lease")
        return _from_row(row)


def _from_row(row) -> A2ALaunchReservation:
    return A2ALaunchReservation(
        id=row["id"],
        owner_id=row["owner_id"],
        tenant_id=row["tenant_id"],
        message_id=row["message_id"],
        request_digest=row["request_digest"],
        workflow_id=row["workflow_id"],
        task_id=row["task_id"],
        campaign_id=row["campaign_id"],
        state=row["state"],
        session_id=row.get("session_id") or "",
        lease_token=row.get("lease_token"),
        lease_expires_at=row.get("lease_expires_at"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
