"""PostgreSQL adapter for the shared runtime instance registry."""

from __future__ import annotations

import json

import asyncpg

from niuu.domain.models import (
    InstanceKind,
    InstanceVisibility,
    RegisteredInstance,
)
from niuu.ports.instances import InstanceRepository


class PostgresInstanceRepository(InstanceRepository):
    """Raw-SQL repository for registered runtime instances."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_instances(self, kind: InstanceKind | None = None) -> list[RegisteredInstance]:
        if kind is None:
            rows = await self._pool.fetch(
                "SELECT * FROM niuu_instances ORDER BY kind ASC, created_at DESC"
            )
        else:
            rows = await self._pool.fetch(
                "SELECT * FROM niuu_instances WHERE kind = $1 ORDER BY created_at DESC",
                str(kind),
            )
        return [self._row_to_instance(row) for row in rows]

    async def get_instance(self, instance_id: str) -> RegisteredInstance | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM niuu_instances WHERE id = $1::uuid",
            instance_id,
        )
        return self._row_to_instance(row) if row is not None else None

    async def save_instance(self, instance: RegisteredInstance) -> RegisteredInstance:
        await self._pool.execute(
            """
            INSERT INTO niuu_instances (
                id, kind, slug, name, base_url, visibility, owner_id, tenant_id,
                enabled, is_default, config, created_at, updated_at
            )
            VALUES (
                $1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12, $13
            )
            ON CONFLICT (id) DO UPDATE SET
                kind = EXCLUDED.kind,
                slug = EXCLUDED.slug,
                name = EXCLUDED.name,
                base_url = EXCLUDED.base_url,
                visibility = EXCLUDED.visibility,
                owner_id = EXCLUDED.owner_id,
                tenant_id = EXCLUDED.tenant_id,
                enabled = EXCLUDED.enabled,
                is_default = EXCLUDED.is_default,
                config = EXCLUDED.config,
                updated_at = EXCLUDED.updated_at
            """,
            instance.id,
            str(instance.kind),
            instance.slug,
            instance.name,
            instance.base_url,
            str(instance.visibility),
            instance.owner_id,
            instance.tenant_id,
            instance.enabled,
            instance.is_default,
            json.dumps(instance.config),
            instance.created_at,
            instance.updated_at,
        )
        return instance

    async def delete_instance(self, instance_id: str) -> None:
        await self._pool.execute("DELETE FROM niuu_instances WHERE id = $1::uuid", instance_id)

    @staticmethod
    def _row_to_instance(row: asyncpg.Record) -> RegisteredInstance:
        config_raw = row["config"]
        if isinstance(config_raw, str):
            config = json.loads(config_raw)
        else:
            config = dict(config_raw) if config_raw else {}

        return RegisteredInstance(
            id=str(row["id"]),
            kind=InstanceKind(row["kind"]),
            slug=row["slug"],
            name=row["name"],
            base_url=row["base_url"],
            visibility=InstanceVisibility(row["visibility"]),
            owner_id=row["owner_id"],
            tenant_id=row["tenant_id"],
            enabled=row["enabled"],
            is_default=row["is_default"],
            config=config,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
