"""SQL adapter unit tests; the concurrent-writer guarantee is the statement itself."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import asyncpg

from volundr.adapters.outbound.postgres_guest_request_watermarks import (
    PostgresGuestRequestWatermarks,
)

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


async def test_advance_is_one_conditional_upsert_returning_whether_it_wrote():
    pool = AsyncMock()
    allocation = uuid4()
    adapter = PostgresGuestRequestWatermarks(pool)

    pool.fetchrow.return_value = {"allocation_id": allocation}
    assert await adapter.try_advance(allocation, 1_700_000_000_000) is True
    pool.fetchrow.return_value = None
    assert await adapter.try_advance(allocation, 1_700_000_000_000) is False

    sql = " ".join(pool.fetchrow.call_args.args[0].split())
    assert pool.fetchrow.await_count == 2
    assert pool.fetchrow.call_args.args[1:] == (allocation, 1_700_000_000_000)
    assert "INSERT INTO compute_guest_request_watermarks" in sql
    assert "ON CONFLICT (allocation_id) DO UPDATE" in sql
    assert (
        "WHERE compute_guest_request_watermarks.last_request_at < EXCLUDED.last_request_at" in sql
    )
    assert "RETURNING allocation_id" in sql


async def test_an_allocation_that_no_longer_exists_is_not_advanced():
    pool = AsyncMock()
    pool.fetchrow.side_effect = asyncpg.ForeignKeyViolationError("gone")

    assert await PostgresGuestRequestWatermarks(pool).try_advance(uuid4(), 1) is False


async def test_any_other_database_error_is_not_swallowed():
    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=asyncpg.PostgresConnectionError("down"))

    try:
        await PostgresGuestRequestWatermarks(pool).try_advance(uuid4(), 1)
    except asyncpg.PostgresConnectionError:
        return
    raise AssertionError("a connection failure must propagate")


def test_the_migration_is_idempotent_and_reversible():
    up = (MIGRATIONS / "000104_compute_guest_request_watermarks.up.sql").read_text()
    down = (MIGRATIONS / "000104_compute_guest_request_watermarks.down.sql").read_text()

    assert "CREATE TABLE IF NOT EXISTS compute_guest_request_watermarks" in up
    assert "allocation_id UUID PRIMARY KEY REFERENCES compute_leases (id) ON DELETE CASCADE" in up
    assert "last_request_at BIGINT NOT NULL" in up
    assert "DROP TABLE IF EXISTS compute_guest_request_watermarks" in down
