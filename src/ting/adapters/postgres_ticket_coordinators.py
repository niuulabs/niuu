"""PostgreSQL storage for ticket coordinators."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from ting.domain.ticket_coordinator import TicketCoordinator, TicketCoordinatorStatus
from ting.ports.ticket_coordinator_repository import TicketCoordinatorRepository


class PostgresTicketCoordinatorRepository(TicketCoordinatorRepository):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, saga_id: UUID, issue_id: str) -> TicketCoordinator | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM ticket_coordinators WHERE saga_id = $1 AND issue_id = $2",
            saga_id,
            issue_id,
        )
        return _from_row(row) if row is not None else None

    async def get_by_session(self, session_id: str) -> TicketCoordinator | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM ticket_coordinators WHERE session_id = $1 AND status = 'active'",
            session_id,
        )
        return _from_row(row) if row is not None else None

    async def list_active_owner_ids(self) -> list[str]:
        rows = await self._pool.fetch(
            "SELECT DISTINCT owner_id FROM ticket_coordinators WHERE status = 'active'"
        )
        return [row["owner_id"] for row in rows]

    async def list_for_saga(self, saga_id: UUID, *, owner_id: str) -> list[TicketCoordinator]:
        rows = await self._pool.fetch(
            "SELECT * FROM ticket_coordinators WHERE saga_id = $1 AND owner_id = $2",
            saga_id,
            owner_id,
        )
        return [_from_row(row) for row in rows]

    async def claim_advance(
        self,
        saga_id: UUID,
        issue_id: str,
        *,
        finished_campaign_id: str,
        workflow_id: str,
        requested_at: datetime,
    ) -> TicketCoordinator | None:
        row = await self._pool.fetchrow(
            """
            UPDATE ticket_coordinators
            SET advanced_from = $3,
                requested_workflow_id = $4,
                requested_at = $5,
                start_error = NULL
            WHERE saga_id = $1
              AND issue_id = $2
              AND status = 'active'
              AND advanced_from IS DISTINCT FROM $3
            RETURNING *
            """,
            saga_id,
            issue_id,
            finished_campaign_id,
            workflow_id,
            requested_at,
        )
        return _from_row(row) if row is not None else None

    async def save(self, coordinator: TicketCoordinator) -> TicketCoordinator:
        row = await self._pool.fetchrow(
            """
            INSERT INTO ticket_coordinators (
                saga_id, issue_id, owner_id, tenant_id, session_id, connection_id,
                status, created_at, closed_at, requested_workflow_id, requested_at,
                pending_brief, start_error, advanced_from
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
            ON CONFLICT (saga_id, issue_id) DO UPDATE SET
                session_id = EXCLUDED.session_id,
                connection_id = EXCLUDED.connection_id,
                status = EXCLUDED.status,
                closed_at = EXCLUDED.closed_at,
                requested_workflow_id = EXCLUDED.requested_workflow_id,
                requested_at = EXCLUDED.requested_at,
                pending_brief = EXCLUDED.pending_brief,
                start_error = EXCLUDED.start_error,
                advanced_from = EXCLUDED.advanced_from
            RETURNING *
            """,
            coordinator.saga_id,
            coordinator.issue_id,
            coordinator.owner_id,
            coordinator.tenant_id,
            coordinator.session_id,
            coordinator.connection_id,
            coordinator.status.value,
            coordinator.created_at,
            coordinator.closed_at,
            coordinator.requested_workflow_id,
            coordinator.requested_at,
            coordinator.pending_brief,
            coordinator.start_error,
            coordinator.advanced_from,
        )
        return _from_row(row)

    async def close(self, saga_id: UUID, issue_id: str) -> None:
        await self._pool.execute(
            """
            UPDATE ticket_coordinators SET status = $3, closed_at = NOW()
            WHERE saga_id = $1 AND issue_id = $2
            """,
            saga_id,
            issue_id,
            TicketCoordinatorStatus.CLOSED.value,
        )


def _from_row(row: asyncpg.Record) -> TicketCoordinator:
    return TicketCoordinator(
        saga_id=row["saga_id"],
        issue_id=row["issue_id"],
        owner_id=row["owner_id"],
        tenant_id=row["tenant_id"],
        session_id=row["session_id"],
        connection_id=row["connection_id"],
        status=TicketCoordinatorStatus(row["status"]),
        created_at=row["created_at"],
        closed_at=row["closed_at"],
        requested_workflow_id=row["requested_workflow_id"],
        requested_at=row["requested_at"],
        pending_brief=row["pending_brief"],
        start_error=row["start_error"],
        advanced_from=row["advanced_from"],
    )
