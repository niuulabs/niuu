"""PostgreSQL adapter for the shared runtime instance registry."""

from __future__ import annotations

import json
from datetime import datetime

import asyncpg

from niuu.domain.models import (
    InstanceHealthStatus,
    InstanceKind,
    InstanceVisibility,
    RegisteredInstance,
)
from niuu.ports.instances import InstanceRepository
from niuu.service_databases import GUILD_BOOTSTRAP_SQL


class PostgresInstanceRepository(InstanceRepository):
    """Raw-SQL repository for registered runtime instances."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        """Ensure the shared instance registry schema exists."""
        for statement in GUILD_BOOTSTRAP_SQL:
            await self._pool.execute(statement)

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

    async def list_for_node(self, node_id: str) -> list[RegisteredInstance]:
        """Instances owned by *node_id* — the real ownership column, never the slug."""
        rows = await self._pool.fetch(
            "SELECT * FROM niuu_instances WHERE node_id = $1::uuid ORDER BY created_at ASC",
            node_id,
        )
        return [self._row_to_instance(row) for row in rows]

    async def save_instance(self, instance: RegisteredInstance) -> RegisteredInstance:
        # Health/last_seen_at/last_checked_at/last_error are set on INSERT
        # (a brand-new instance starts unknown) but deliberately left out of
        # the ON CONFLICT UPDATE SET — those columns are owned exclusively by
        # record_health. A metadata PATCH racing the periodic health loop
        # must never clobber the loop's own, possibly newer, write with a
        # stale value carried in this in-memory object.
        await self._pool.execute(
            """
            INSERT INTO niuu_instances (
                id, kind, slug, name, base_url, visibility, owner_id, tenant_id,
                enabled, is_default, config, created_at, updated_at, tags,
                health, last_seen_at, last_checked_at, last_error, node_id
            )
            VALUES (
                $1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12, $13,
                $14::jsonb, $15, $16, $17, $18, $19::uuid
            )
            -- node_id is deliberately excluded from the UPDATE SET, same as
            -- the health columns above: it is set once at INSERT (by
            -- GuildJoinRepository, never by the PATCH-driven update path)
            -- and never changes afterwards, so it stays immutable at the
            -- database layer even if a future caller ever passed a
            -- different value here by mistake.
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
                updated_at = EXCLUDED.updated_at,
                tags = EXCLUDED.tags
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
            json.dumps(list(instance.tags)),
            str(instance.health),
            instance.last_seen_at,
            instance.last_checked_at,
            instance.last_error,
            instance.node_id,
        )
        return instance

    async def delete_instance(self, instance_id: str) -> None:
        await self._pool.execute("DELETE FROM niuu_instances WHERE id = $1::uuid", instance_id)

    async def record_health(
        self,
        instance_id: str,
        *,
        health: InstanceHealthStatus,
        last_seen_at: datetime | None,
        last_checked_at: datetime,
        last_error: str | None,
    ) -> None:
        await self._pool.execute(
            """
            UPDATE niuu_instances
            SET health = $2, last_seen_at = $3, last_checked_at = $4, last_error = $5
            WHERE id = $1::uuid
            """,
            instance_id,
            str(health),
            last_seen_at,
            last_checked_at,
            last_error,
        )

    @staticmethod
    def _row_to_instance(row: asyncpg.Record) -> RegisteredInstance:
        config_raw = row["config"]
        if isinstance(config_raw, str):
            config = json.loads(config_raw)
        else:
            config = dict(config_raw) if config_raw else {}

        tags_raw = row["tags"]
        if isinstance(tags_raw, str):
            tags = json.loads(tags_raw)
        else:
            tags = list(tags_raw) if tags_raw else []

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
            tags=tags,
            # health is NOT NULL with a CHECK constraint (migration 000077),
            # so this is a direct parse, never a guess at a missing value.
            health=InstanceHealthStatus(row["health"]),
            last_seen_at=row["last_seen_at"],
            last_checked_at=row["last_checked_at"],
            last_error=row["last_error"],
            node_id=str(row["node_id"]) if row["node_id"] else None,
        )
