"""PostgreSQL store of how project teams stand with the Guild (raw SQL, asyncpg)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from volundr.domain.project_ports import ProjectTeamSyncRepository
from volundr.domain.projects import ProjectTeamSync

_RETURNING = " RETURNING version, pending, synced_at, error"


class PostgresProjectTeamSyncRepository(ProjectTeamSyncRepository):
    def __init__(self, pool) -> None:
        self._pool = pool

    async def get(self, project_id: UUID) -> ProjectTeamSync:
        row = await self._pool.fetchrow(
            "SELECT version, pending, synced_at, error FROM project_team_sync "
            "WHERE project_id = $1",
            project_id,
        )
        return ProjectTeamSync(**dict(row)) if row else ProjectTeamSync()

    async def changed(self, project_id: UUID) -> ProjectTeamSync:
        return await self._write(
            "INSERT INTO project_team_sync (project_id, version, pending) VALUES ($1, 1, TRUE) "
            "ON CONFLICT (project_id) DO UPDATE SET "
            "version = project_team_sync.version + 1, pending = TRUE",
            project_id,
        )

    async def published(self, project_id: UUID, version: int, at: datetime) -> ProjectTeamSync:
        # A change made while the Guild was taking this version is still to be published.
        return await self._write(
            "INSERT INTO project_team_sync (project_id, version, synced_at) VALUES ($1, $2, $3) "
            "ON CONFLICT (project_id) DO UPDATE SET synced_at = $3, error = '', "
            "pending = project_team_sync.version > $2",
            project_id,
            version,
            at,
        )

    async def copied(self, project_id: UUID, version: int, at: datetime) -> ProjectTeamSync:
        return await self._write(
            "INSERT INTO project_team_sync (project_id, version, synced_at) VALUES ($1, $2, $3) "
            "ON CONFLICT (project_id) DO UPDATE SET version = $2, synced_at = $3, error = '', "
            "pending = FALSE",
            project_id,
            version,
            at,
        )

    async def failed(self, project_id: UUID, error: str) -> ProjectTeamSync:
        return await self._write(
            "INSERT INTO project_team_sync (project_id, error) VALUES ($1, $2) "
            "ON CONFLICT (project_id) DO UPDATE SET error = $2",
            project_id,
            error,
        )

    async def _write(self, sql: str, *args: object) -> ProjectTeamSync:
        row = await self._pool.fetchrow(sql + _RETURNING, *args)
        return ProjectTeamSync(**dict(row))
