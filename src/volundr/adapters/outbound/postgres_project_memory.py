"""PostgreSQL store of the Mimir mounts a Forge project owns."""

from __future__ import annotations

import json
from uuid import UUID

import asyncpg

from volundr.domain.project_memory import (
    MemoryAccess,
    MemoryMountExistsError,
    ProjectMemoryMount,
    ProjectMemoryRepository,
)

_COLUMNS = "project_id, name, registry_entry_id, access, write_prefixes, added_by, " + (
    "created_at, updated_at"
)


class PostgresProjectMemoryRepository(ProjectMemoryRepository):
    def __init__(self, pool=None, **_kwargs):
        self._pool = pool

    @staticmethod
    def _mount(row) -> ProjectMemoryMount:
        prefixes = row["write_prefixes"]
        if isinstance(prefixes, str):
            prefixes = json.loads(prefixes)
        return ProjectMemoryMount(
            project_id=row["project_id"],
            name=row["name"],
            registry_entry_id=row["registry_entry_id"],
            access=MemoryAccess(row["access"]),
            write_prefixes=tuple(prefixes),
            added_by=row["added_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def list_for_project(self, project_id: UUID) -> list[ProjectMemoryMount]:
        rows = await self._pool.fetch(
            f"SELECT {_COLUMNS} FROM project_memory_mounts WHERE project_id = $1 "
            "ORDER BY created_at, name",
            project_id,
        )
        return [self._mount(row) for row in rows]

    async def get(self, project_id: UUID, name: str) -> ProjectMemoryMount | None:
        row = await self._pool.fetchrow(
            f"SELECT {_COLUMNS} FROM project_memory_mounts WHERE project_id = $1 AND name = $2",
            project_id,
            name,
        )
        return self._mount(row) if row else None

    async def add(self, mount: ProjectMemoryMount) -> ProjectMemoryMount:
        try:
            row = await self._pool.fetchrow(
                "INSERT INTO project_memory_mounts(project_id, name, registry_entry_id, access, "
                "write_prefixes, added_by, created_at, updated_at) "
                "VALUES($1, $2, $3, $4, $5::jsonb, $6, $7, $8) "
                f"RETURNING {_COLUMNS}",
                mount.project_id,
                mount.name,
                mount.registry_entry_id,
                mount.access.value,
                json.dumps(list(mount.write_prefixes)),
                mount.added_by,
                mount.created_at,
                mount.updated_at,
            )
        except asyncpg.UniqueViolationError as exc:
            raise MemoryMountExistsError(
                f"The project already has a memory mount named {mount.name!r}; "
                "choose another name or change that one"
            ) from exc
        return self._mount(row)

    async def update(
        self,
        project_id: UUID,
        name: str,
        *,
        access: MemoryAccess,
        write_prefixes: tuple[str, ...],
    ) -> ProjectMemoryMount | None:
        row = await self._pool.fetchrow(
            "UPDATE project_memory_mounts SET access = $3, write_prefixes = $4::jsonb, "
            "updated_at = NOW() WHERE project_id = $1 AND name = $2 "
            f"RETURNING {_COLUMNS}",
            project_id,
            name,
            access.value,
            json.dumps(list(write_prefixes)),
        )
        return self._mount(row) if row else None

    async def remove(self, project_id: UUID, name: str) -> bool:
        result = await self._pool.execute(
            "DELETE FROM project_memory_mounts WHERE project_id = $1 AND name = $2",
            project_id,
            name,
        )
        return result.endswith(" 1")
