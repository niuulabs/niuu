"""Real PostgreSQL checks for migration 000102 and ``changes_since``.

Opt-in: set ``FORGE_HISTORY_TEST_DATABASE_URL`` to a disposable database. Each test
gets its own schema, migrated through the production startup runner, and drops it.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from cli.resources import ordered_migration_files
from volundr.adapters.outbound.postgres import PostgresSessionRepository
from volundr.adapters.outbound.startup_schema import apply_startup_migrations
from volundr.domain.models import Session
from volundr.domain.session_read_state import SessionReadStateChange

pytestmark = pytest.mark.integration
MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


@pytest_asyncio.fixture
async def pool():
    dsn = os.environ.get("FORGE_HISTORY_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set FORGE_HISTORY_TEST_DATABASE_URL for isolated PostgreSQL checks")
    schema = "forge_session_changes_test_" + uuid4().hex
    admin = await asyncpg.connect(dsn)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    created = None
    try:
        created = await asyncpg.create_pool(
            dsn, min_size=1, max_size=4, server_settings={"search_path": schema}
        )
        async with created.acquire() as conn:
            await apply_startup_migrations(conn, ordered_migration_files(MIGRATIONS))
        yield created
    finally:
        if created is not None:
            await created.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


async def test_every_write_path_moves_a_session_past_the_cursor(pool) -> None:
    repo = PostgresSessionRepository(pool)
    quiet = await repo.create(Session(name="quiet"))
    busy = await repo.create(Session(name="busy"))

    first = await repo.changes_since(0, None)
    assert {quiet.id, busy.id} <= first.ids

    # Nothing written since: nothing to send.
    assert (await repo.changes_since(first.cursor, None)).ids == frozenset()

    # A write that leaves updated_at alone (session usage) still counts.
    await pool.execute("UPDATE sessions SET session_usage = '{}'::jsonb WHERE id = $1", busy.id)
    second = await repo.changes_since(first.cursor, None)
    assert second.ids == {busy.id}
    assert second.cursor >= first.cursor


async def test_a_write_still_in_flight_is_sent_on_the_next_read(pool) -> None:
    repo = PostgresSessionRepository(pool)
    session = await repo.create(Session(name="slow-writer"))
    base = await repo.changes_since(0, None)

    async with pool.acquire() as writer:
        txn = writer.transaction()
        await txn.start()
        await writer.execute("UPDATE sessions SET name = 'renamed' WHERE id = $1", session.id)
        # The uncommitted write is invisible, and the cursor stays below it.
        during = await repo.changes_since(base.cursor, None)
        assert session.id not in during.ids
        await txn.commit()

    after = await repo.changes_since(during.cursor, None)
    assert session.id in after.ids


async def test_a_read_state_change_counts_only_for_its_reader(pool) -> None:
    repo = PostgresSessionRepository(pool)
    session = await repo.create(Session(name="inbox"))
    base = await repo.changes_since(0, None)

    await repo.change_read_state(
        session.id,
        "reader-a",
        SessionReadStateChange(state="unread", through_seq=0, expected_revision=0),
    )

    assert session.id in (await repo.changes_since(base.cursor, "reader-a")).ids
    assert session.id not in (await repo.changes_since(base.cursor, "reader-b")).ids


async def test_a_deleted_session_is_simply_absent(pool) -> None:
    repo = PostgresSessionRepository(pool)
    session = await repo.create(Session(name="gone"))
    base = await repo.changes_since(0, None)
    await repo.delete(session.id)
    assert session.id not in (await repo.changes_since(0, None)).ids
    assert session.id not in (await repo.changes_since(base.cursor, None)).ids


async def test_a_cursor_from_another_database_reads_as_everything(pool) -> None:
    repo = PostgresSessionRepository(pool)
    session = await repo.create(Session(name="restored"))
    future = (await repo.changes_since(0, None)).cursor + 1_000_000
    assert session.id in (await repo.changes_since(future, None)).ids
