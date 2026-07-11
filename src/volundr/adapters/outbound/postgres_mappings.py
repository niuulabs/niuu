"""PostgreSQL adapter for project mapping repository."""

from __future__ import annotations

from uuid import UUID

import asyncpg

from tracker.models import ProjectMapping
from tracker.ports import ProjectMappingRepository


class PostgresMappingRepository(ProjectMappingRepository):
    """PostgreSQL implementation of ProjectMappingRepository using raw SQL."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def create(self, mapping: ProjectMapping) -> ProjectMapping:
        """Persist a new project mapping."""
        await self._pool.execute(
            """
            INSERT INTO project_mappings
                (id, repo_url, project_id, project_name, created_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            mapping.id,
            mapping.repo_url,
            mapping.project_id,
            mapping.project_name,
            mapping.created_at,
        )
        return mapping

    async def list(self) -> list[ProjectMapping]:
        """Retrieve all project mappings."""
        rows = await self._pool.fetch("SELECT * FROM project_mappings ORDER BY created_at DESC")
        return [self._row_to_mapping(row) for row in rows]

    async def get_by_repo(self, repo_url: str) -> ProjectMapping | None:
        """Retrieve a mapping by repo URL."""
        row = await self._pool.fetchrow(
            "SELECT * FROM project_mappings WHERE repo_url = $1",
            repo_url,
        )
        if row is None:
            return None
        return self._row_to_mapping(row)

    async def delete(self, mapping_id: UUID) -> bool:
        """Delete a mapping. Returns True if deleted."""
        result = await self._pool.execute(
            "DELETE FROM project_mappings WHERE id = $1",
            mapping_id,
        )
        return result == "DELETE 1"

    @staticmethod
    def _row_to_mapping(row: asyncpg.Record) -> ProjectMapping:
        """Convert a database row to a ProjectMapping."""
        return ProjectMapping(
            id=row["id"],
            repo_url=row["repo_url"],
            project_id=row["project_id"],
            project_name=row["project_name"],
            created_at=row["created_at"],
        )
