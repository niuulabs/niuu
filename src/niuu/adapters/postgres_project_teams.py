"""PostgreSQL store of the project teams a Guild holds (raw SQL, asyncpg)."""

from __future__ import annotations

from uuid import UUID

import asyncpg

from niuu.domain.project_team import ProjectTeam, ProjectTeamRecord
from niuu.ports.project_teams import ProjectTeamHomeConflictError, ProjectTeamRecords


class PostgresProjectTeamRecords(ProjectTeamRecords):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, project_id: UUID) -> ProjectTeamRecord | None:
        row = await self._pool.fetchrow(
            "SELECT home_instance_id, team FROM guild_project_teams WHERE project_id = $1",
            project_id,
        )
        return _record(row) if row is not None else None

    async def of_member(self, user_id: str, tenant_id: str) -> list[ProjectTeamRecord]:
        rows = await self._pool.fetch(
            """
            SELECT home_instance_id, team FROM guild_project_teams
            WHERE team ->> 'tenant_id' = $2
              AND team -> 'members' @> jsonb_build_array(jsonb_build_object('user_id', $1::text))
            ORDER BY project_id
            """,
            user_id,
            tenant_id,
        )
        return [_record(row) for row in rows]

    async def recorded(self, project_ids: list[UUID]) -> set[UUID]:
        rows = await self._pool.fetch(
            "SELECT project_id FROM guild_project_teams WHERE project_id = ANY($1::uuid[])",
            project_ids,
        )
        return {row["project_id"] for row in rows}

    async def put(self, record: ProjectTeamRecord) -> ProjectTeamRecord:
        # One statement, so two publications at once cannot take the home from each other
        # or put an older team over a newer one.
        await self._pool.execute(
            """
            INSERT INTO guild_project_teams (project_id, home_instance_id, version, team)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (project_id) DO UPDATE
            SET version = EXCLUDED.version, team = EXCLUDED.team, updated_at = NOW()
            WHERE guild_project_teams.home_instance_id = EXCLUDED.home_instance_id
              AND guild_project_teams.version <= EXCLUDED.version
            """,
            record.team.project_id,
            record.home_instance_id,
            record.team.version,
            record.team.model_dump_json(),
        )
        kept = await self.get(record.team.project_id)
        if kept is None:
            raise RuntimeError(f"Project team {record.team.project_id} was removed while kept")
        if kept.home_instance_id != record.home_instance_id:
            raise ProjectTeamHomeConflictError(
                f"The team of project {record.team.project_id} is published by Forge "
                f"{kept.home_instance_id}, not {record.home_instance_id}"
            )
        return kept


def _record(row: asyncpg.Record) -> ProjectTeamRecord:
    return ProjectTeamRecord(
        home_instance_id=row["home_instance_id"],
        team=ProjectTeam.model_validate_json(row["team"]),
    )
