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
    ComputePoolPolicy,
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
            policy_data = await conn.fetchval(
                "SELECT data FROM compute_pools WHERE pool_id = $1", lease.pool_id
            )
            if policy_data:
                policy = ComputePoolPolicy.model_validate_json(policy_data)
                if policy.paused or policy.drain:
                    raise ComputeCapacityError("Compute pool admission is paused")
                limit = policy.max_machines
                provisioning = await conn.fetchval(
                    "SELECT COUNT(*) FROM compute_leases WHERE pool_id = $1 "
                    "AND state IN ('provisioning', 'ready')",
                    lease.pool_id,
                )
                if provisioning >= policy.max_provisioning:
                    raise ComputeCapacityError("Compute pool provisioning limit reached")
            if policy_data and lease.session_id is None:
                warm = await conn.fetchval(
                    "SELECT COUNT(*) FROM compute_leases WHERE pool_id = $1 "
                    "AND session_id IS NULL AND state IN ('provisioning', 'ready', 'idle')",
                    lease.pool_id,
                )
                if warm >= policy.warm_min:
                    raise ComputeCapacityError("Compute pool standby target already met")
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

    async def list(self, pool_id: str, *, include_released: bool = True) -> list[ComputeLease]:
        rows = await self._pool.fetch(
            "SELECT data FROM compute_leases WHERE pool_id = $1 "
            "AND ($2 OR state != 'released') ORDER BY created_at",
            pool_id,
            include_released,
        )
        return [ComputeLease.model_validate_json(row["data"]) for row in rows]

    async def save(self, lease: ComputeLease) -> None:
        operation = self._operation.get()
        if operation is None or operation[0] != lease.id:
            raise RuntimeError("Compute updates require ownership of the allocation operation")
        result = await operation[1].execute(
            """UPDATE compute_leases
               SET state = $2, data = $3::jsonb, session_id = $4, updated_at = NOW()
               WHERE id = $1""",
            lease.id,
            lease.state.value,
            lease.model_dump_json(),
            lease.session_id,
        )
        if result != "UPDATE 1":
            raise LookupError("Compute lease disappeared during an operation")

    async def active_for_session(self, pool_id: str, session_id: UUID) -> ComputeLease | None:
        operation = self._operation.get()
        conn = operation[1] if operation else self._pool
        data = await conn.fetchval(
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

    async def policy(self, pool_id: str, default: ComputePoolPolicy) -> ComputePoolPolicy:
        await self._pool.execute(
            "INSERT INTO compute_pools (pool_id, data) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (pool_id) DO NOTHING",
            pool_id,
            default.model_dump_json(),
        )
        data = await self._pool.fetchval(
            "SELECT data FROM compute_pools WHERE pool_id = $1", pool_id
        )
        return ComputePoolPolicy.model_validate_json(data)

    async def set_policy(self, pool_id: str, policy: ComputePoolPolicy) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", _lock_key("pool", pool_id))
            await conn.execute(
                "INSERT INTO compute_pools (pool_id, data) VALUES ($1, $2::jsonb) "
                "ON CONFLICT (pool_id) DO UPDATE SET data = EXCLUDED.data",
                pool_id,
                policy.model_dump_json(),
            )

    async def bind(self, lease: ComputeLease) -> ComputeLease:
        operation = self._operation.get()
        if operation is None or operation[0] != lease.id:
            raise RuntimeError("Binding requires allocation operation ownership")
        conn = operation[1]
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", _lock_key("pool", lease.pool_id))
            data = await conn.fetchval(
                "SELECT data FROM compute_pools WHERE pool_id = $1", lease.pool_id
            )
            policy = ComputePoolPolicy.model_validate_json(data)
            if policy.paused or policy.drain:
                raise ComputeCapacityError("Compute pool admission is paused")
            current = await self.get(lease.id)
            if current is None or current.state.value != "idle" or current.session_id is not None:
                raise ComputeLeaseBusyError("Standby allocation is no longer idle")
            existing = await self.active_for_session(lease.pool_id, lease.session_id)
            if existing is not None:
                raise ComputeLeaseBusyError("Session already owns an allocation")
            await self.save(lease)
        return lease

    async def quarantine(self, lease: ComputeLease) -> None:
        await self._pool.execute(
            "INSERT INTO compute_leases (id, pool_id, session_id, state, data) "
            "VALUES ($1, $2, NULL, $3, $4::jsonb) ON CONFLICT (id) DO UPDATE "
            "SET state = EXCLUDED.state, session_id = NULL, data = EXCLUDED.data "
            "WHERE compute_leases.state = 'released' AND compute_leases.pool_id = EXCLUDED.pool_id",
            lease.id,
            lease.pool_id,
            lease.state.value,
            lease.model_dump_json(),
        )
