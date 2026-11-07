"""Durable VM claims, atomic admission and connection-scoped operation locks.

A machine is a ``compute_leases`` row; each session it serves is a ``compute_slots``
row. Admission runs under one advisory lock per pool, so slot placement, the warm
target and the machine cap are decided from one consistent read.
"""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from uuid import UUID

import asyncpg

from volundr.domain.compute import (
    LIVE_SLOT_STATES,
    ComputeCapacityError,
    ComputeLease,
    ComputeLeaseBusyError,
    ComputeLeaseRepository,
    ComputePoolFullError,
    ComputePoolPolicy,
    ComputeProvisioningLimitError,
    ComputeSlot,
    ComputeStandbyTargetMetError,
    LeaseState,
    SlotLease,
    SlotState,
    usable_slots,
)

#: Slot states that hold a session, as the text values stored in ``compute_slots.state``.
_LIVE = sorted(state.value for state in LIVE_SLOT_STATES)


def _lock_key(kind: str, key: str) -> int:
    return int.from_bytes(
        hashlib.sha256(f"compute:{kind}:{key}".encode()).digest()[:8], signed=True
    )


class PostgresComputeLeaseRepository(ComputeLeaseRepository):
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool
        self._operation: ContextVar[tuple | None] = ContextVar("compute_operation", default=None)

    def _reader(self):
        """The operation's own connection inside an operation, else the pool."""
        operation = self._operation.get()
        return operation[1] if operation else self._pool

    async def reserve(
        self, lease: ComputeLease, limit: int, binding: ComputeSlot | None = None
    ) -> tuple[ComputeLease, ComputeSlot | None]:
        if limit < 1:
            raise ValueError("Compute pool limit must be positive")
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", _lock_key("pool", lease.pool_id))
            if binding is not None:
                row = await conn.fetchrow(
                    "SELECT l.data AS machine, s.data AS slot FROM compute_slots s "
                    "JOIN compute_leases l ON l.id = s.allocation_id "
                    "WHERE s.pool_id = $1 AND s.session_id = $2 AND s.state = ANY($3::text[])",
                    lease.pool_id,
                    binding.session_id,
                    _LIVE,
                )
                if row:
                    machine = ComputeLease.model_validate_json(row["machine"])
                    current = ComputeSlot.model_validate_json(row["slot"])
                    if any(
                        getattr(current, key) != getattr(binding, key)
                        for key in ("tenant_id", "owner_id")
                    ) or any(
                        getattr(machine, key) != getattr(lease, key)
                        for key in ("profile", "request_fingerprint")
                    ):
                        raise ValueError("Session already owns a different compute claim")
                    return machine, current
            policy_data = await conn.fetchval(
                "SELECT data FROM compute_pools WHERE pool_id = $1", lease.pool_id
            )
            policy = ComputePoolPolicy.model_validate_json(policy_data) if policy_data else None
            if policy is not None:
                if policy.paused or policy.drain:
                    raise ComputeCapacityError("Compute pool admission is paused")
                limit = policy.max_machines
                # A machine whose runtime failed is ready but no longer provisioning.
                provisioning = await conn.fetchval(
                    "SELECT COUNT(*) FROM compute_leases l WHERE l.pool_id = $1 "
                    "AND (l.state = 'provisioning' OR (l.state = 'ready' AND NOT EXISTS ("
                    "SELECT 1 FROM compute_slots s WHERE s.allocation_id = l.id "
                    "AND s.state = 'failed')))",
                    lease.pool_id,
                )
                if provisioning >= policy.max_provisioning:
                    raise ComputeProvisioningLimitError("Compute pool provisioning limit reached")
            if policy is not None and binding is None:
                if await self._standby_free(conn, lease.pool_id, policy) >= policy.warm_min:
                    raise ComputeStandbyTargetMetError("Compute pool standby target already met")
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM compute_leases WHERE pool_id = $1 AND state != 'released'",
                lease.pool_id,
            )
            if count >= limit:
                raise ComputePoolFullError(
                    f"Compute pool {lease.pool_id} has no unreserved machines; raise its "
                    "maximum machines or release unused ones"
                )
            await conn.execute(
                """INSERT INTO compute_leases (id, pool_id, session_id, state, data)
                   VALUES ($1, $2, NULL, $3, $4::jsonb)""",
                lease.id,
                lease.pool_id,
                lease.state.value,
                lease.model_dump_json(),
            )
            if binding is not None:
                await self._insert_slot(conn, binding)
            return lease, binding

    async def _standby_free(self, conn, pool_id: str, policy: ComputePoolPolicy) -> int:
        """Free slots on machines that are provisioning, ready or idle."""
        rows = await conn.fetch(
            "SELECT data FROM compute_leases WHERE pool_id = $1 "
            "AND state IN ('provisioning', 'ready', 'idle')",
            pool_id,
        )
        machines = [ComputeLease.model_validate_json(row["data"]) for row in rows]
        if not machines:
            return 0
        slot_rows = await conn.fetch(
            "SELECT data FROM compute_slots WHERE allocation_id = ANY($1::uuid[])",
            [machine.id for machine in machines],
        )
        bindings: dict[UUID, list[ComputeSlot]] = {machine.id: [] for machine in machines}
        for row in slot_rows:
            slot = ComputeSlot.model_validate_json(row["data"])
            bindings[slot.allocation_id].append(slot)
        now = datetime.now(UTC)
        return sum(usable_slots(m, bindings[m.id], policy, now) for m in machines)

    @staticmethod
    async def _insert_slot(conn, binding: ComputeSlot) -> None:
        await conn.execute(
            """INSERT INTO compute_slots (id, allocation_id, slot, pool_id, session_id, state, data)
               VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)""",
            binding.id,
            binding.allocation_id,
            binding.slot,
            binding.pool_id,
            binding.session_id,
            binding.state.value,
            binding.model_dump_json(),
        )

    async def get(self, lease_id: UUID) -> ComputeLease | None:
        data = await self._reader().fetchval(
            "SELECT data FROM compute_leases WHERE id = $1", lease_id
        )
        return ComputeLease.model_validate_json(data) if data else None

    async def get_slot(self, slot_id: UUID) -> ComputeSlot | None:
        data = await self._reader().fetchval(
            "SELECT data FROM compute_slots WHERE id = $1", slot_id
        )
        return ComputeSlot.model_validate_json(data) if data else None

    async def slot(self, allocation_id: UUID, slot: int) -> ComputeSlot | None:
        data = await self._reader().fetchval(
            "SELECT data FROM compute_slots WHERE allocation_id = $1 AND slot = $2 "
            "AND state = ANY($3::text[])",
            allocation_id,
            slot,
            _LIVE,
        )
        return ComputeSlot.model_validate_json(data) if data else None

    async def slots(self, allocation_id: UUID) -> list[ComputeSlot]:
        rows = await self._reader().fetch(
            "SELECT data FROM compute_slots WHERE allocation_id = $1 ORDER BY created_at, slot",
            allocation_id,
        )
        return [ComputeSlot.model_validate_json(row["data"]) for row in rows]

    async def list_slots(self, pool_id: str, *, live_only: bool = True) -> list[ComputeSlot]:
        rows = await self._reader().fetch(
            "SELECT data FROM compute_slots WHERE pool_id = $1 "
            "AND ($2 OR state = ANY($3::text[])) ORDER BY created_at, slot",
            pool_id,
            not live_only,
            _LIVE,
        )
        return [ComputeSlot.model_validate_json(row["data"]) for row in rows]

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
        conn = operation[1]
        async with conn.transaction():
            result = await conn.execute(
                """UPDATE compute_leases
                   SET state = $2, data = $3::jsonb, updated_at = NOW()
                   WHERE id = $1""",
                lease.id,
                lease.state.value,
                lease.model_dump_json(),
            )
            if result != "UPDATE 1":
                raise LookupError("Compute lease disappeared during an operation")
            if lease.state is LeaseState.RELEASED:
                # A released machine holds no session; end its bindings in the same commit.
                await conn.execute(
                    "UPDATE compute_slots SET state = $3, "
                    "data = jsonb_set(data, '{state}', to_jsonb($3::text)), updated_at = NOW() "
                    "WHERE allocation_id = $1 AND state = ANY($2::text[])",
                    lease.id,
                    _LIVE,
                    SlotState.RETIRED.value,
                )

    async def save_slot(self, binding: ComputeSlot) -> None:
        operation = self._operation.get()
        if operation is None or operation[0] != binding.allocation_id:
            raise RuntimeError("Compute updates require ownership of the allocation operation")
        result = await operation[1].execute(
            """UPDATE compute_slots
               SET state = $2, data = $3::jsonb, updated_at = NOW()
               WHERE id = $1""",
            binding.id,
            binding.state.value,
            binding.model_dump_json(),
        )
        if result != "UPDATE 1":
            raise LookupError("Compute slot disappeared during an operation")

    async def active_for_session(self, pool_id: str, session_id: UUID) -> SlotLease | None:
        row = await self._reader().fetchrow(
            "SELECT l.data AS machine, s.data AS slot FROM compute_slots s "
            "JOIN compute_leases l ON l.id = s.allocation_id "
            "WHERE s.pool_id = $1 AND s.session_id = $2 AND s.state = ANY($3::text[])",
            pool_id,
            session_id,
            _LIVE,
        )
        if not row:
            return None
        return SlotLease(
            allocation=ComputeLease.model_validate_json(row["machine"]),
            binding=ComputeSlot.model_validate_json(row["slot"]),
        )

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

    async def bind(self, machine: ComputeLease, binding: ComputeSlot) -> SlotLease:
        operation = self._operation.get()
        if operation is None or operation[0] != machine.id or binding.allocation_id != machine.id:
            raise RuntimeError("Binding requires allocation operation ownership")
        conn = operation[1]
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1)", _lock_key("pool", machine.pool_id)
            )
            data = await conn.fetchval(
                "SELECT data FROM compute_pools WHERE pool_id = $1", machine.pool_id
            )
            policy = ComputePoolPolicy.model_validate_json(data)
            if policy.paused or policy.drain:
                raise ComputeCapacityError("Compute pool admission is paused")
            current = await self.get(machine.id)
            if current is None or current.state is not LeaseState.IDLE:
                raise ComputeLeaseBusyError("Standby allocation is no longer idle")
            bindings = await self.slots(machine.id)
            if usable_slots(current, bindings, policy, datetime.now(UTC)) < 1:
                raise ComputeLeaseBusyError("Standby allocation has no usable slot")
            if binding.slot >= current.slots or any(
                other.slot == binding.slot and other.state is not SlotState.RELEASED
                for other in bindings
            ):
                raise ComputeLeaseBusyError(f"Slot {binding.slot} is no longer free")
            if await self.active_for_session(machine.pool_id, binding.session_id) is not None:
                raise ComputeLeaseBusyError("Session already owns an allocation")
            await self._insert_slot(conn, binding)
            updated = current.model_copy(update={"idle_since": None})
            await self.save(updated)
        return SlotLease(allocation=updated, binding=binding)

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
