"""Durable VM claims, atomic admission and connection-scoped operation locks."""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
from contextvars import ContextVar
from uuid import UUID

import asyncpg

from volundr.domain.compute import (
    ComputeCapacityError,
    ComputeLease,
    ComputeLeaseBusyError,
    ComputeLeaseRepository,
)


def _lock_key(kind: str, key: str) -> int:
    return int.from_bytes(
        hashlib.sha256(f"compute:{kind}:{key}".encode()).digest()[:8], signed=True
    )


class PostgresComputeLeaseRepository(ComputeLeaseRepository):
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool
        self._operation: ContextVar[tuple | None] = ContextVar("compute_operation", default=None)

    async def reserve(self, lease: ComputeLease, limit: int) -> ComputeLease:
        if limit < 1:
            raise ValueError("Compute pool limit must be positive")
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", _lock_key("pool", lease.pool_id))
            existing = await conn.fetchval(
                """SELECT data FROM compute_leases
                   WHERE pool_id = $1 AND session_id = $2 AND state != 'released'""",
                lease.pool_id,
                lease.session_id,
            )
            if existing:
                current = ComputeLease.model_validate_json(existing)
                if any(
                    getattr(current, key) != getattr(lease, key)
                    for key in ("tenant_id", "owner_id", "profile", "request_fingerprint")
                ):
                    raise ValueError("Session already owns a different compute claim")
                return current
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM compute_leases WHERE pool_id = $1 AND state != 'released'",
                lease.pool_id,
            )
            if count >= limit:
                raise ComputeCapacityError(f"Compute pool {lease.pool_id} has no unreserved slots")
            await conn.execute(
                """INSERT INTO compute_leases (id, pool_id, session_id, state, data)
                   VALUES ($1, $2, $3, $4, $5::jsonb)""",
                lease.id,
                lease.pool_id,
                lease.session_id,
                lease.state.value,
                lease.model_dump_json(),
            )
            return lease

    async def get(self, lease_id: UUID) -> ComputeLease | None:
        operation = self._operation.get()
        conn = operation[1] if operation else self._pool
        data = await conn.fetchval("SELECT data FROM compute_leases WHERE id = $1", lease_id)
        return ComputeLease.model_validate_json(data) if data else None

    async def list(self, pool_id: str) -> list[ComputeLease]:
        rows = await self._pool.fetch(
            "SELECT data FROM compute_leases WHERE pool_id = $1 ORDER BY created_at", pool_id
        )
        return [ComputeLease.model_validate_json(row["data"]) for row in rows]

    async def save(self, lease: ComputeLease) -> None:
        operation = self._operation.get()
        if operation is None or operation[0] != lease.id:
            raise RuntimeError("Compute updates require ownership of the allocation operation")
        result = await operation[1].execute(
            """UPDATE compute_leases SET state = $2, data = $3::jsonb, updated_at = NOW()
               WHERE id = $1""",
            lease.id,
            lease.state.value,
            lease.model_dump_json(),
        )
        if result != "UPDATE 1":
            raise LookupError("Compute lease disappeared during an operation")

    async def active_for_session(self, pool_id: str, session_id: UUID) -> ComputeLease | None:
        data = await self._pool.fetchval(
            "SELECT data FROM compute_leases WHERE pool_id = $1 "
            "AND session_id = $2 AND state != 'released'",
            pool_id,
            session_id,
        )
        return ComputeLease.model_validate_json(data) if data else None

    @asynccontextmanager
    async def operation(self, lease_id: UUID):
        if self._operation.get() is not None:
            raise RuntimeError("Compute operations cannot be nested")
        # ponytail: one connection per in-flight operation; the DB pool bounds concurrency.
        # No transaction is held over network calls; connection death releases the lock.
        async with self._pool.acquire() as conn:
            key = _lock_key("lease", str(lease_id))
            locked = await conn.fetchval("SELECT pg_try_advisory_lock($1)", key)
            if not locked:
                raise ComputeLeaseBusyError("Allocation operation is already in progress")
            token = self._operation.set((lease_id, conn))
            try:
                yield
            finally:
                self._operation.reset(token)
                await asyncio.shield(conn.execute("SELECT pg_advisory_unlock($1)", key))
