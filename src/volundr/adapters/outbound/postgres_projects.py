"""PostgreSQL project index and append-only handoff inbox."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from uuid import UUID

from volundr.domain.project_ports import ProjectConflictError, ProjectRepository
from volundr.domain.projects import ForgeProject, ProjectReceipt


class PostgresProjectRepository(ProjectRepository):
    def __init__(
        self, pool=None, dispatch_wait_seconds=30.0, dispatch_poll_seconds=0.05, **_kwargs
    ):
        self._pool = pool
        self._dispatch_wait = dispatch_wait_seconds
        self._dispatch_poll = dispatch_poll_seconds
        pool_size = pool.get_max_size()
        if pool_size < 2:
            raise ValueError("Projects require a PostgreSQL pool with at least two connections")
        # Different dispatch keys can all acquire locks simultaneously. Reserve
        # pool capacity for their session writes instead of exhausting it with locks.
        self._dispatch_slots = asyncio.Semaphore(max(1, pool_size // 2))

    @asynccontextmanager
    async def dispatch_lock(self, key: UUID):
        async with self._dispatch_slots:
            async with self._locked_dispatch(key):
                yield

    @asynccontextmanager
    async def _locked_dispatch(self, key: UUID):
        # Session locks span the short create/start transaction across repositories.
        lock_id = int.from_bytes(key.bytes[:8], signed=True)
        deadline = time.monotonic() + self._dispatch_wait
        while True:
            async with self._pool.acquire() as connection:
                locked = await connection.fetchval(
                    "SELECT pg_try_advisory_lock($1::bigint)", lock_id
                )
                if locked:
                    try:
                        yield
                    finally:
                        await connection.execute("SELECT pg_advisory_unlock($1::bigint)", lock_id)
                    return
            # Never occupy the pool while waiting on another dispatch: the owner
            # needs connections for the session repository's create/start writes.
            if time.monotonic() >= deadline:
                raise ProjectConflictError(
                    "Dispatch is in progress; retry with the same dispatch ID"
                )
            await asyncio.sleep(self._dispatch_poll)

    async def claim_dispatch(self, key: UUID, fingerprint: str, session_id: UUID) -> bool:
        row = await self._pool.fetchrow(
            "INSERT INTO forge_project_dispatches(id,fingerprint,session_id) VALUES($1,$2,$3) "
            "ON CONFLICT(id) DO NOTHING RETURNING fingerprint,completed",
            key,
            fingerprint,
            session_id,
        )
        if row is None:
            row = await self._pool.fetchrow(
                "SELECT fingerprint,completed FROM forge_project_dispatches WHERE id=$1",
                key,
            )
        if row["fingerprint"] != fingerprint:
            raise ProjectConflictError(
                "Dispatch ID was already used with a different launch request"
            )
        return row["completed"]

    async def complete_dispatch(self, key: UUID) -> None:
        await self._pool.execute(
            "UPDATE forge_project_dispatches SET completed=true WHERE id=$1", key
        )

    @staticmethod
    def _project(row) -> ForgeProject:
        data = row["document"]
        return ForgeProject.model_validate(json.loads(data) if isinstance(data, str) else data)

    async def list(self, owner_id: str, tenant_id: str) -> list[ForgeProject]:
        rows = await self._pool.fetch(
            "SELECT document FROM forge_projects WHERE owner_id=$1 AND tenant_id=$2 "
            "ORDER BY updated_at DESC, id",
            owner_id,
            tenant_id,
        )
        return [self._project(row) for row in rows]

    async def get(self, project_id: UUID) -> ForgeProject | None:
        row = await self._pool.fetchrow(
            "SELECT document FROM forge_projects WHERE id=$1", project_id
        )
        return self._project(row) if row else None

    async def register(self, project: ForgeProject) -> ForgeProject:
        row = await self._pool.fetchrow(
            "INSERT INTO forge_projects(id, owner_id, tenant_id, document, revision, updated_at) "
            "VALUES($1,$2,$3,$4,1,$5) ON CONFLICT(id) DO NOTHING RETURNING document",
            project.id,
            project.owner_id,
            project.tenant_id,
            project.model_dump_json(),
            project.updated_at,
        )
        if row:
            return self._project(row)
        existing = await self.get(project.id)
        identity = ("owner_id", "tenant_id", "repo_url", "slug", "workspace_path")
        if existing and all(getattr(existing, key) == getattr(project, key) for key in identity):
            return existing
        raise ProjectConflictError("Project ID already identifies a different registration")

    async def update(self, project: ForgeProject, expected_revision: int) -> ForgeProject:
        row = await self._pool.fetchrow(
            "UPDATE forge_projects SET document=$2, revision=$3, updated_at=$4 "
            "WHERE id=$1 AND revision=$5 RETURNING document",
            project.id,
            project.model_dump_json(),
            project.revision,
            project.updated_at,
            expected_revision,
        )
        if row is None:
            raise ProjectConflictError("Project changed; reload before updating")
        return self._project(row)

    async def put_receipt(self, receipt: ProjectReceipt) -> ProjectReceipt:
        # Assign cursor sequence numbers in commit order within each project.
        # Otherwise a reader could advance past an earlier, uncommitted receipt.
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock($1::bigint)",
                    int.from_bytes(receipt.project_id.bytes[:8], signed=True),
                )
                return await self._put_receipt(connection, receipt)

    async def _put_receipt(self, connection, receipt: ProjectReceipt) -> ProjectReceipt:
        row = await connection.fetchrow(
            "INSERT INTO forge_project_receipts(id,project_id,document) VALUES($1,$2,$3) "
            "ON CONFLICT(id) DO NOTHING RETURNING document,acknowledged_at",
            receipt.id,
            receipt.project_id,
            receipt.model_dump_json(),
        )
        if row is None:
            row = await connection.fetchrow(
                "SELECT document,acknowledged_at FROM forge_project_receipts WHERE id=$1",
                receipt.id,
            )
        data = row["document"]
        stored = ProjectReceipt.model_validate(json.loads(data) if isinstance(data, str) else data)
        excluded = {"created_at", "acknowledged_at"}
        if stored.model_dump(exclude=excluded) != receipt.model_dump(exclude=excluded):
            raise ProjectConflictError("Receipt ID already contains a different handoff")
        return stored.model_copy(update={"acknowledged_at": row["acknowledged_at"]})

    async def receipts(self, project_id: UUID, after: int, limit: int) -> list[dict]:
        rows = await self._pool.fetch(
            "SELECT seq,document,acknowledged_at FROM forge_project_receipts "
            "WHERE project_id=$1 AND seq>$2 ORDER BY seq LIMIT $3",
            project_id,
            after,
            limit,
        )
        result = []
        for row in rows:
            data = row["document"]
            item = json.loads(data) if isinstance(data, str) else dict(data)
            item.update(seq=row["seq"], acknowledged_at=row["acknowledged_at"])
            result.append(item)
        return result

    async def acknowledge(self, project_id: UUID, receipt_id: UUID) -> bool:
        row = await self._pool.fetchrow(
            "UPDATE forge_project_receipts SET acknowledged_at=COALESCE(acknowledged_at,now()) "
            "WHERE id=$1 AND project_id=$2 RETURNING id",
            receipt_id,
            project_id,
        )
        return row is not None
