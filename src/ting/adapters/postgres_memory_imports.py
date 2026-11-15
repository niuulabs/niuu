"""PostgreSQL storage for memory imports."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

import asyncpg

from ting.domain.memory_imports import MemoryImport, MemoryImportExistsError
from ting.ports.memory_imports import MemoryImportRepository

_COLUMNS = (
    "id, project_id, tenant_id, owner_id, connector, connection_id, tracker_project_id, "
    "compile, cursor, created_at, last_run_at, last_error, pending_source_ids"
)


class PostgresMemoryImportRepository(MemoryImportRepository):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(self, record: MemoryImport) -> MemoryImport:
        try:
            await self._pool.execute(
                f"""
                INSERT INTO memory_imports ({_COLUMNS})
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                """,
                record.id,
                record.project_id,
                record.tenant_id,
                record.owner_id,
                record.connector,
                record.connection_id,
                record.tracker_project_id,
                record.compile,
                record.cursor,
                record.created_at,
                record.last_run_at,
                record.last_error,
                list(record.pending_source_ids),
            )
        except asyncpg.UniqueViolationError as exc:
            raise MemoryImportExistsError(
                f"project {record.project_id} already imports {record.connector} project "
                f"{record.tracker_project_id!r} through connection {record.connection_id}; "
                "delete that import to set it up again"
            ) from exc
        return record

    async def get(self, import_id: UUID) -> MemoryImport | None:
        row = await self._pool.fetchrow(
            f"SELECT {_COLUMNS} FROM memory_imports WHERE id = $1", import_id
        )
        return _from_row(row) if row is not None else None

    async def list_for_project(self, project_id: UUID) -> list[MemoryImport]:
        rows = await self._pool.fetch(
            f"SELECT {_COLUMNS} FROM memory_imports WHERE project_id = $1 ORDER BY created_at, id",
            project_id,
        )
        return [_from_row(row) for row in rows]

    async def list_all(self) -> list[MemoryImport]:
        rows = await self._pool.fetch(
            f"SELECT {_COLUMNS} FROM memory_imports ORDER BY created_at, id"
        )
        return [_from_row(row) for row in rows]

    async def delete(self, import_id: UUID) -> bool:
        deleted = await self._pool.fetchrow(
            "DELETE FROM memory_imports WHERE id = $1 RETURNING id", import_id
        )
        return deleted is not None

    async def record_progress(
        self,
        import_id: UUID,
        *,
        cursor: datetime | None,
        add_pending: Sequence[str],
    ) -> None:
        # The cursor only ever moves forward, even when two runs race.
        await self._pool.execute(
            """
            UPDATE memory_imports
            SET cursor = CASE
                    WHEN $2::timestamptz IS NULL THEN cursor
                    ELSE GREATEST(cursor, $2::timestamptz)
                END,
                pending_source_ids = ARRAY(
                    SELECT DISTINCT ON (source_id) source_id
                    FROM unnest(pending_source_ids || $3::text[]) WITH ORDINALITY
                        AS pending(source_id, ord)
                    ORDER BY source_id, ord
                )
            WHERE id = $1
            """,
            import_id,
            cursor,
            list(add_pending),
        )

    async def clear_pending(self, import_id: UUID, source_ids: Sequence[str]) -> None:
        await self._pool.execute(
            """
            UPDATE memory_imports
            SET pending_source_ids = ARRAY(
                SELECT source_id FROM unnest(pending_source_ids) WITH ORDINALITY
                    AS pending(source_id, ord)
                WHERE source_id <> ALL($2::text[])
                ORDER BY ord
            )
            WHERE id = $1
            """,
            import_id,
            list(source_ids),
        )

    async def record_run(self, import_id: UUID, *, ran_at: datetime, error: str) -> None:
        await self._pool.execute(
            "UPDATE memory_imports SET last_run_at = $2, last_error = $3 WHERE id = $1",
            import_id,
            ran_at,
            error,
        )


def _from_row(row: asyncpg.Record) -> MemoryImport:
    return MemoryImport(
        id=row["id"],
        project_id=row["project_id"],
        tenant_id=row["tenant_id"],
        owner_id=row["owner_id"],
        connector=row["connector"],
        connection_id=row["connection_id"],
        tracker_project_id=row["tracker_project_id"],
        compile=row["compile"],
        cursor=row["cursor"],
        created_at=row["created_at"],
        last_run_at=row["last_run_at"],
        last_error=row["last_error"],
        pending_source_ids=tuple(row["pending_source_ids"]),
    )
