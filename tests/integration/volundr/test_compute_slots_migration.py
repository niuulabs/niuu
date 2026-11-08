"""Migration 000107 against real PostgreSQL: legacy machine rows of every state become slots.

The suite applies every migration once, so these tests insert rows shaped as the previous
controller stored them and run the migration's SQL over them again. The migration is
idempotent by statement order, which the second run proves.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from volundr.domain.compute import ComputeLease, ComputeSlot, LeaseState, SlotState

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

MIGRATIONS = Path(__file__).resolve().parents[3] / "migrations"
UP = (MIGRATIONS / "000107_compute_slots.up.sql").read_text(encoding="utf-8")
DOWN = (MIGRATIONS / "000107_compute_slots.down.sql").read_text(encoding="utf-8")

RUNTIME_FAILED = "Runtime startup failed; inspect guest and stop session"
RUNTIME_FAILED_AT_STAGE = "Runtime startup failed at stage sandbox-create; inspect guest and stop"
EXPIRED = "Provisioning expired; preserve data and release the allocation"
DEADLINE = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)


class Legacy:
    """One machine row exactly as a controller before 000107 wrote it."""

    def __init__(self, pool_id: str, state: str, *, session: bool = True, **fields):
        self.id = uuid4()
        self.session_id = uuid4() if session else None
        self.state = state
        self.payload = {
            "id": str(self.id),
            "pool_id": pool_id,
            "session_id": str(self.session_id) if session else None,
            "tenant_id": "tenant" if session else "",
            "owner_id": "owner" if session else "",
            "profile": "small",
            "profile_revision": "revision-1",
            "request_fingerprint": "digest",
            "provider_binding": "",
            "provider_fingerprint": "",
            "execution_plan": None,
            "host_preparation_started": False,
            "host_preparation_result": None,
            "bootstrap_ref": None,
            "bootstrap_owner": None,
            "session_bootstrap_ref": f"{self.id}-session-{self.session_id}" if session else None,
            "created_at": (DEADLINE - timedelta(hours=1)).isoformat(),
            "provision_deadline": DEADLINE.isoformat(),
            "idle_since": None,
            "retry_after": None,
            "failures": 0,
            "runtime_data_started": session and state in {"busy", "draining"},
            "runtime_started": session and state == "busy",
            "stop_requested": False,
            "state": state,
            "machine": None,
            "error": None,
            **fields,
        }

    async def insert(self, db_pool) -> None:
        await db_pool.execute(
            "INSERT INTO compute_leases (id, pool_id, session_id, state, data) "
            "VALUES ($1, $2, $3, $4, $5::jsonb)",
            self.id,
            self.payload["pool_id"],
            self.session_id,
            self.state,
            json.dumps(self.payload),
        )


async def machine(db_pool, legacy: Legacy) -> ComputeLease:
    data = await db_pool.fetchval("SELECT data FROM compute_leases WHERE id = $1", legacy.id)
    return ComputeLease.model_validate_json(data)


async def slot_of(db_pool, legacy: Legacy) -> ComputeSlot | None:
    data = await db_pool.fetchval(
        "SELECT data FROM compute_slots WHERE allocation_id = $1", legacy.id
    )
    return ComputeSlot.model_validate_json(data) if data else None


async def test_every_legacy_state_becomes_a_machine_and_at_most_one_slot_zero(db_pool):
    pool_id = f"migrate-{uuid4()}"
    legacy = {
        "provisioning": Legacy(pool_id, "provisioning"),
        "ready": Legacy(pool_id, "ready"),
        "busy": Legacy(pool_id, "busy", stop_requested=False),
        "stopping": Legacy(pool_id, "busy", stop_requested=True),
        "draining": Legacy(pool_id, "draining"),
        "runtime-failed": Legacy(pool_id, "failed", error=RUNTIME_FAILED),
        "staged-failed": Legacy(pool_id, "failed", error=RUNTIME_FAILED_AT_STAGE),
        "expired": Legacy(pool_id, "failed", error=EXPIRED),
        "released": Legacy(pool_id, "released"),
        "idle": Legacy(pool_id, "idle", session=False),
        "quarantined": Legacy(pool_id, "quarantined", session=False),
    }
    try:
        for row in legacy.values():
            await row.insert(db_pool)

        await db_pool.execute(UP)

        machines = {name: await machine(db_pool, row) for name, row in legacy.items()}
        slots = {name: await slot_of(db_pool, row) for name, row in legacy.items()}

        # Every machine row parses as the new model with one slot and no session fields.
        for lease in machines.values():
            assert lease.slots == 1
        # Machines that held a session get slot 0 with that session; the rest get none.
        for name in ("idle", "quarantined", "released"):
            assert slots[name] is None
        for name, row in legacy.items():
            if slots[name] is None:
                continue
            binding = slots[name]
            assert (binding.slot, binding.session_id) == (0, row.session_id)
            assert (binding.tenant_id, binding.owner_id) == ("tenant", "owner")
            assert binding.session_bootstrap_ref == f"{row.id}-session-{row.session_id}"
            assert binding.start_deadline == DEADLINE

        assert slots["provisioning"].state is SlotState.STARTING
        assert machines["provisioning"].state is LeaseState.PROVISIONING
        assert slots["ready"].state is SlotState.STARTING
        assert machines["ready"].state is LeaseState.READY
        # A machine in service is idle; the slot carries busy.
        assert slots["busy"].state is SlotState.BUSY
        assert machines["busy"].state is LeaseState.IDLE
        assert (slots["busy"].runtime_data_started, slots["busy"].runtime_started) == (True, True)
        assert slots["stopping"].stop_requested
        assert slots["draining"].state is SlotState.DRAINING
        assert machines["draining"].state is LeaseState.DRAINING
        # A failed runtime start fails the slot, not the machine; other failures fail both.
        assert slots["runtime-failed"].state is SlotState.FAILED
        assert slots["runtime-failed"].error == RUNTIME_FAILED
        assert machines["runtime-failed"].state is LeaseState.READY
        assert machines["runtime-failed"].error is None
        assert slots["staged-failed"].error == RUNTIME_FAILED_AT_STAGE
        assert machines["staged-failed"].state is LeaseState.READY
        assert slots["expired"].state is SlotState.FAILED
        assert machines["expired"].state is LeaseState.FAILED
        assert machines["expired"].error == EXPIRED
        assert machines["idle"].state is LeaseState.IDLE
        assert machines["quarantined"].state is LeaseState.QUARANTINED
        # The released machine keeps the session column; its payload no longer names a session.
        released = await db_pool.fetchrow(
            "SELECT session_id, data FROM compute_leases WHERE id = $1", legacy["released"].id
        )
        assert released["session_id"] == legacy["released"].session_id
        assert "session_id" not in json.loads(released["data"])
        live = await db_pool.fetchval(
            "SELECT session_id FROM compute_leases WHERE id = $1", legacy["busy"].id
        )
        assert live is None

        # Running it again changes nothing: same slots, same machines.
        before = {
            row["id"]: (row["state"], row["data"])
            for row in await db_pool.fetch(
                "SELECT id, state, data::text FROM compute_slots WHERE pool_id = $1", pool_id
            )
        }
        machines_before = await db_pool.fetch(
            "SELECT id, state, data::text FROM compute_leases WHERE pool_id = $1 ORDER BY id",
            pool_id,
        )
        await db_pool.execute(UP)
        after = {
            row["id"]: (row["state"], row["data"])
            for row in await db_pool.fetch(
                "SELECT id, state, data::text FROM compute_slots WHERE pool_id = $1", pool_id
            )
        }
        assert after == before
        assert (
            await db_pool.fetch(
                "SELECT id, state, data::text FROM compute_leases WHERE pool_id = $1 ORDER BY id",
                pool_id,
            )
            == machines_before
        )
    finally:
        await db_pool.execute("DELETE FROM compute_leases WHERE pool_id = $1", pool_id)


async def test_down_refuses_while_a_machine_exists_then_restores_the_old_shape(db_pool):
    pool_id = f"downgrade-{uuid4()}"
    policy = {
        "profile": "small",
        "max_machines": 2,
        "slots_per_machine": 2,
        "max_sessions_per_machine": 4,
        "max_machine_age_seconds": 3600.0,
    }
    async with db_pool.acquire() as conn:
        outer = conn.transaction()
        await outer.start()
        try:
            # Nothing else in the database may veto this downgrade.
            await conn.execute("DELETE FROM compute_leases")
            live = Legacy(pool_id, "ready", session=False)
            payload = {k: v for k, v in live.payload.items() if k != "session_id"}
            payload.update(slots=1)
            await conn.execute(
                "INSERT INTO compute_leases (id, pool_id, session_id, state, data) "
                "VALUES ($1, $2, NULL, 'ready', $3::jsonb)",
                live.id,
                pool_id,
                json.dumps(payload),
            )
            await conn.execute(
                "INSERT INTO compute_pools (pool_id, data) VALUES ($1, $2::jsonb)",
                pool_id,
                json.dumps(policy),
            )

            with pytest.raises(asyncpg.RaiseError, match="Drain and release all compute"):
                async with conn.transaction():
                    await conn.execute(DOWN)

            await conn.execute(
                "UPDATE compute_leases SET state = 'released', data = "
                "jsonb_set(data, '{state}', '\"released\"')"
            )
            await conn.execute(DOWN)

            restored = json.loads(
                await conn.fetchval("SELECT data FROM compute_leases WHERE id = $1", live.id)
            )
            assert "slots" not in restored
            assert (restored["tenant_id"], restored["owner_id"]) == ("", "")
            assert "session_id" in restored
            stored = json.loads(
                await conn.fetchval("SELECT data FROM compute_pools WHERE pool_id = $1", pool_id)
            )
            assert stored == {"profile": "small", "max_machines": 2}
            assert await conn.fetchval("SELECT to_regclass('compute_slots') IS NULL")
        finally:
            await outer.rollback()
