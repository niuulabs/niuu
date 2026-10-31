"""PostgreSQL workload delegations (table ``workload_delegations``, migration 000088)."""

from __future__ import annotations

import builtins
from typing import Any
from uuid import UUID

import asyncpg

from niuu.domain.models import WorkloadDelegation
from niuu.ports.workload_delegations import (
    WorkloadDelegationExistsError,
    WorkloadDelegationRepository,
)

_COLUMNS = (
    "id, verifier, subject, owner_id, tenant_id, created_by, created_at, name, "
    "resident_id, revoked_at"
)


class PostgresWorkloadDelegationRepository(WorkloadDelegationRepository):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_active(self, verifier: str, subject: str) -> WorkloadDelegation | None:
        row = await self._pool.fetchrow(
            f"""SELECT {_COLUMNS} FROM workload_delegations
                WHERE verifier = $1 AND subject = $2 AND revoked_at IS NULL""",
            verifier,
            subject,
        )
        return _record(row) if row is not None else None

    async def create(
        self,
        *,
        verifier: str,
        subject: str,
        owner_id: str,
        tenant_id: str,
        created_by: str,
        name: str = "",
        resident_id: str = "",
    ) -> WorkloadDelegation:
        # The partial unique index admits one active record per workload; a
        # conflicting insert returns nothing and the existing record decides.
        row = await self._pool.fetchrow(
            f"""INSERT INTO workload_delegations
                    (verifier, subject, owner_id, tenant_id, created_by, name, resident_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (verifier, subject) WHERE revoked_at IS NULL DO NOTHING
                RETURNING {_COLUMNS}""",
            verifier,
            subject,
            owner_id,
            tenant_id,
            created_by,
            name,
            resident_id,
        )
        if row is not None:
            return _record(row)
        existing = await self.get_active(verifier, subject)
        if existing is None or existing.owner_id != owner_id:
            raise WorkloadDelegationExistsError(
                f"{subject!r} on {verifier!r} already acts for another owner"
            )
        return existing

    async def list(
        self, *, tenant_id: str, owner_id: str | None = None
    ) -> builtins.list[WorkloadDelegation]:
        rows = await self._pool.fetch(
            f"""SELECT {_COLUMNS} FROM workload_delegations
                WHERE tenant_id = $1 AND revoked_at IS NULL
                  AND ($2::text IS NULL OR owner_id = $2)
                ORDER BY created_at""",
            tenant_id,
            owner_id,
        )
        return [_record(row) for row in rows]

    async def get(self, delegation_id: UUID) -> WorkloadDelegation | None:
        row = await self._pool.fetchrow(
            f"SELECT {_COLUMNS} FROM workload_delegations WHERE id = $1",
            delegation_id,
        )
        return _record(row) if row is not None else None

    async def revoke(self, delegation_id: UUID) -> WorkloadDelegation | None:
        row = await self._pool.fetchrow(
            f"""UPDATE workload_delegations SET revoked_at = now()
                WHERE id = $1 AND revoked_at IS NULL
                RETURNING {_COLUMNS}""",
            delegation_id,
        )
        return _record(row) if row is not None else None


def _record(row: Any) -> WorkloadDelegation:
    return WorkloadDelegation(
        id=row["id"],
        verifier=row["verifier"],
        subject=row["subject"],
        owner_id=row["owner_id"],
        tenant_id=row["tenant_id"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        name=row["name"] or "",
        resident_id=row["resident_id"] or "",
        revoked_at=row["revoked_at"],
    )
