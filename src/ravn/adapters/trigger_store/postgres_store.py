"""PostgreSQL-backed trigger store (raw SQL, asyncpg)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from ravn.adapters._lazy_asyncpg_pool import lazy_pool_for_dsn
from ravn.domain.trigger_record import TriggerRecord
from ravn.ports.trigger_store import TriggerStorePort


class PostgresTriggerStore(TriggerStorePort):
    """Store trigger records in ``ravn_triggers``, scoped by ``tenant_id``."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def list_triggers(self, *, tenant_id: str) -> list[TriggerRecord]:
        rows = await self._pool.fetch(
            "SELECT id, kind, persona_name, spec, repo, enabled, owner_id, tenant_id, created_at "
            "FROM ravn_triggers WHERE tenant_id = $1 ORDER BY created_at DESC",
            tenant_id,
        )
        return [_record_from_row(row) for row in rows]

    async def get_trigger(self, trigger_id: str) -> TriggerRecord | None:
        row = await self._pool.fetchrow(
            "SELECT id, kind, persona_name, spec, repo, enabled, owner_id, tenant_id, created_at "
            "FROM ravn_triggers WHERE id = $1",
            trigger_id,
        )
        return _record_from_row(row) if row is not None else None

    async def create_trigger(
        self,
        *,
        kind: str,
        persona_name: str,
        spec: str,
        enabled: bool,
        owner_id: str,
        tenant_id: str,
        repo: str = "",
    ) -> TriggerRecord:
        row = await self._pool.fetchrow(
            """
            INSERT INTO ravn_triggers
                (id, kind, persona_name, spec, repo, enabled, owner_id, tenant_id, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
            RETURNING id, kind, persona_name, spec, repo, enabled, owner_id, tenant_id, created_at
            """,
            str(uuid4()),
            kind,
            persona_name,
            spec,
            repo,
            enabled,
            owner_id,
            tenant_id,
        )
        return _record_from_row(row)

    async def delete_trigger(self, trigger_id: str) -> bool:
        result = await self._pool.execute(
            "DELETE FROM ravn_triggers WHERE id = $1",
            trigger_id,
        )
        return result == "DELETE 1"


class LazyPostgresTriggerStore(TriggerStorePort):
    """Dynamic-adapter entry point: ``adapter:`` + ``kwargs: {dsn: ...}``.

    Connection is deferred (not opened in ``__init__``, so constructing this
    adapter never blocks) but callers should ``await warmup()`` once at
    process startup (see ``ravn.api.persistence_wiring``) rather than let the
    first real request discover a bad DSN — configured but impossible is
    fatal, not a surprise on someone's first trigger poll. Shares its pool
    with any other Ravn store configured with the same DSN (see
    ``ravn.adapters._lazy_asyncpg_pool``) — two stores pointed at the same
    Postgres open one pool, not two.
    """

    def __init__(self, dsn: str) -> None:
        if not dsn.strip():
            raise ValueError(
                "LazyPostgresTriggerStore requires a non-empty dsn — asyncpg would "
                "otherwise silently fall back to libpq environment defaults instead "
                "of the Postgres this deployment actually configured"
            )
        self._lazy = lazy_pool_for_dsn(dsn)

    async def warmup(self) -> None:
        """Connect now, so a bad DSN fails at startup, not on first request."""
        await self._lazy.resolve()

    async def _delegate(self) -> PostgresTriggerStore:
        return PostgresTriggerStore(await self._lazy.resolve())

    async def list_triggers(self, *, tenant_id: str) -> list[TriggerRecord]:
        return await (await self._delegate()).list_triggers(tenant_id=tenant_id)

    async def get_trigger(self, trigger_id: str) -> TriggerRecord | None:
        return await (await self._delegate()).get_trigger(trigger_id)

    async def create_trigger(self, **kwargs: Any) -> TriggerRecord:
        return await (await self._delegate()).create_trigger(**kwargs)

    async def delete_trigger(self, trigger_id: str) -> bool:
        return await (await self._delegate()).delete_trigger(trigger_id)


def _record_from_row(row: Any) -> TriggerRecord:
    return TriggerRecord(
        id=str(row["id"]),
        kind=row["kind"],
        persona_name=row["persona_name"],
        spec=row["spec"],
        enabled=row["enabled"],
        owner_id=row["owner_id"],
        tenant_id=row["tenant_id"],
        created_at=row["created_at"],
        repo=row["repo"] or "",
    )
