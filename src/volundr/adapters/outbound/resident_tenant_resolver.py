"""Resolves a resident runtime's real tenant_id for workload identity exchange.

Implements ``niuu.ports.owner_tenant_resolver.OwnerTenantResolverPort`` —
wired as ``workloadIdentity.tenantResolver`` (a dynamic adapter, like every
other pluggable piece in this codebase) so a mapping that derives owner_id
per-caller (``owner_id_claim`` — see the ``residentMapping`` this chart
synthesizes) can also derive that caller's real tenant_id from
``resident_runtimes``, instead of the mapping's own single static
``tenant_id`` being silently wrong for every tenant but one.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from niuu.ports.owner_tenant_resolver import OwnerTenantResolverPort


class PostgresResidentTenantResolver(OwnerTenantResolverPort):
    """Raw-SQL lookup: ``resident_runtimes.id`` -> ``resident_runtimes.tenant_id``."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def tenant_id_for_owner(self, owner_id: str) -> str | None:
        try:
            runtime_id = UUID(owner_id)
        except ValueError:
            # owner_id here is always a resident_runtimes.id (the
            # owner_id_claim_pattern this chart renders extracts nothing
            # else) — a non-UUID value can never match a row, and asyncpg
            # would otherwise raise on the type-mismatched query parameter.
            return None
        row = await self._pool.fetchrow(
            "SELECT tenant_id FROM resident_runtimes WHERE id = $1",
            runtime_id,
        )
        if row is None:
            return None
        return str(row["tenant_id"])


class LazyPostgresResidentTenantResolver(OwnerTenantResolverPort):
    """Dynamic-adapter entry point: ``adapter:`` + ``kwargs: {dsn: ...}``.

    Rejects an empty DSN immediately (configured but impossible is fatal —
    see .claude/rules/no-fallbacks.md) rather than deferring the failure to
    the first workload-identity exchange, where it would present as an
    opaque, hard-to-diagnose 401/403 on a resident's very first trigger poll
    or budget report.
    """

    def __init__(self, dsn: str) -> None:
        if not dsn.strip():
            raise ValueError(
                "LazyPostgresResidentTenantResolver requires a non-empty dsn — set "
                "workloadIdentity.tenantResolver.kwargs.dsn (or its secret_kwargs_env "
                "equivalent) to the same Postgres this Völundr deployment already uses "
                "for resident_runtimes"
            )
        self._dsn = dsn
        self._pool: Any | None = None
        self._lock = asyncio.Lock()

    async def _resolve_pool(self) -> Any:
        if self._pool is not None:
            return self._pool
        async with self._lock:
            if self._pool is None:
                import asyncpg  # noqa: PLC0415

                self._pool = await asyncpg.create_pool(dsn=self._dsn, min_size=1, max_size=4)
        return self._pool

    async def tenant_id_for_owner(self, owner_id: str) -> str | None:
        pool = await self._resolve_pool()
        return await PostgresResidentTenantResolver(pool).tenant_id_for_owner(owner_id)
