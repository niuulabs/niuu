"""PostgreSQL record of team (project) connection uses."""

from __future__ import annotations

from datetime import datetime

import asyncpg

from niuu.domain.models import IntegrationUsage
from niuu.ports.integrations import IntegrationUsageLog

_INSERT = """INSERT INTO integration_usage
    (connection_id, project_id, acting_for, actor, action, subject, session_id)
VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7)"""

_LIST = """SELECT * FROM integration_usage
WHERE project_id = $1::uuid AND ($2::timestamptz IS NULL OR occurred_at < $2)
ORDER BY occurred_at DESC, id
LIMIT $3"""


class PostgresIntegrationUsageLog(IntegrationUsageLog):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record(self, usage: IntegrationUsage) -> None:
        await self._pool.execute(
            _INSERT,
            usage.connection_id,
            usage.project_id,
            usage.acting_for,
            usage.actor,
            usage.action,
            usage.subject,
            usage.session_id,
        )

    async def list_for_project(
        self, project_id: str, *, limit: int, before: datetime | None = None
    ) -> list[IntegrationUsage]:
        rows = await self._pool.fetch(_LIST, project_id, before, limit)
        return [
            IntegrationUsage(
                id=str(row["id"]),
                connection_id=str(row["connection_id"]),
                project_id=str(row["project_id"]),
                acting_for=row["acting_for"],
                actor=row["actor"],
                action=row["action"],
                subject=row["subject"],
                session_id=row["session_id"],
                occurred_at=row["occurred_at"],
            )
            for row in rows
        ]
