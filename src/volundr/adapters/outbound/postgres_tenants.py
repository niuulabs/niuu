"""PostgreSQL adapter for tenant persistence."""

from __future__ import annotations

import asyncpg

from identity.models import Tenant, TenantTier
from identity.ports import TenantRepository


class PostgresTenantRepository(TenantRepository):
    """PostgreSQL implementation of TenantRepository."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    def _row_to_tenant(self, row: asyncpg.Record) -> Tenant:
        return Tenant(
            id=row["id"],
            path=row["path"],
            name=row["name"],
            parent_id=row["parent_id"],
            tier=TenantTier(row["tier"]),
            max_sessions=row["max_sessions"],
            max_storage_gb=row["max_storage_gb"],
            created_at=row["created_at"],
        )

    async def create(self, tenant: Tenant) -> Tenant:
        row = await self._pool.fetchrow(
            """
            INSERT INTO tenants (id, path, name, parent_id, tier, max_sessions, max_storage_gb)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING *
            """,
            tenant.id,
            tenant.path,
            tenant.name,
            tenant.parent_id,
            tenant.tier.value,
            tenant.max_sessions,
            tenant.max_storage_gb,
        )
        return self._row_to_tenant(row)

    async def get(self, tenant_id: str) -> Tenant | None:
        row = await self._pool.fetchrow("SELECT * FROM tenants WHERE id = $1", tenant_id)
        if row is None:
            return None
        return self._row_to_tenant(row)

    async def get_by_path(self, path: str) -> Tenant | None:
        row = await self._pool.fetchrow("SELECT * FROM tenants WHERE path = $1", path)
        if row is None:
            return None
        return self._row_to_tenant(row)

    async def list(self, parent_id: str | None = None) -> list[Tenant]:
        if parent_id is not None:
            rows = await self._pool.fetch(
                "SELECT * FROM tenants WHERE parent_id = $1 ORDER BY path",
                parent_id,
            )
        else:
            rows = await self._pool.fetch("SELECT * FROM tenants ORDER BY path")
        return [self._row_to_tenant(r) for r in rows]

    async def get_ancestors(self, path: str) -> list[Tenant]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM tenants
            WHERE $1 LIKE path || '%'
            ORDER BY length(path) ASC
            """,
            path,
        )
        return [self._row_to_tenant(r) for r in rows]

    async def update(self, tenant: Tenant) -> Tenant:
        row = await self._pool.fetchrow(
            """
            UPDATE tenants SET name = $2, tier = $3, max_sessions = $4, max_storage_gb = $5
            WHERE id = $1
            RETURNING *
            """,
            tenant.id,
            tenant.name,
            tenant.tier.value,
            tenant.max_sessions,
            tenant.max_storage_gb,
        )
        if row is None:
            raise ValueError(f"Tenant {tenant.id} not found")
        return self._row_to_tenant(row)

    async def delete(self, tenant_id: str) -> bool:
        result = await self._pool.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
        return result == "DELETE 1"
