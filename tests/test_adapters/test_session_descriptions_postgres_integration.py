"""Real PostgreSQL checks for migration 000092 and the targeted description write.

Opt-in: set ``FORGE_HISTORY_TEST_DATABASE_URL`` to a disposable database. Each test
gets its own schema, migrated through the production startup runner, and drops it.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from cli.resources import ordered_migration_files
from volundr.adapters.outbound.postgres import PostgresSessionRepository
from volundr.adapters.outbound.startup_schema import apply_startup_migrations
from volundr.domain.models import Session, SessionStatus

pytestmark = pytest.mark.integration
MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
T0 = datetime(2026, 10, 16, 12, tzinfo=UTC)


@pytest_asyncio.fixture
async def pool():
    dsn = os.environ.get("FORGE_HISTORY_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set FORGE_HISTORY_TEST_DATABASE_URL for isolated PostgreSQL checks")
    schema = "forge_descriptions_test_" + uuid4().hex
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


async def test_seeded_create_round_trips(pool) -> None:
    repo = PostgresSessionRepository(pool)
    session = Session(name="seeded", short_description="Seed", short_description_updated_at=T0)
    await repo.create(session)
    stored = await repo.get(session.id)
    assert stored.short_description == "Seed" and stored.short_description_updated_at == T0
    assert stored.description is None and stored.description_updated_at is None


async def test_write_semantics(pool) -> None:
    repo = PostgresSessionRepository(pool)
    session = await repo.create(Session(name="s"))

    first = await repo.update_description(
        session.id, {"short_description": "One", "description": "## Two"}, T0
    )
    assert (first.short_description, first.description) == ("One", "## Two")
    assert first.short_description_updated_at == T0 == first.description_updated_at
    assert first.updated_at == T0

    later = T0 + timedelta(minutes=5)
    same = await repo.update_description(session.id, {"short_description": "One"}, later)
    assert same.short_description_updated_at == T0 and same.updated_at == T0

    only_long = await repo.update_description(session.id, {"description": "## Three"}, later)
    assert only_long.short_description == "One"
    assert only_long.short_description_updated_at == T0
    assert only_long.description_updated_at == later == only_long.updated_at

    cleared_at = later + timedelta(minutes=5)
    cleared = await repo.update_description(session.id, {"short_description": None}, cleared_at)
    assert cleared.short_description is None
    assert cleared.short_description_updated_at == cleared_at
    assert cleared.description == "## Three"

    again = await repo.update_description(
        session.id, {"short_description": None}, cleared_at + timedelta(minutes=5)
    )
    assert again.short_description_updated_at == cleared_at

    assert await repo.update_description(uuid4(), {"description": "x"}, T0) is None


async def test_generic_update_keeps_and_returns_the_stored_description(pool) -> None:
    repo = PostgresSessionRepository(pool)
    session = await repo.create(Session(name="s"))
    stale = await repo.get(session.id)
    await repo.update_description(session.id, {"short_description": "Fresh"}, T0)

    saved = await repo.update(
        stale.model_copy(update={"status": SessionStatus.STOPPED, "short_description": "Stale"})
    )
    assert saved.status is SessionStatus.STOPPED
    assert saved.short_description == "Fresh" and saved.short_description_updated_at == T0
    stored = await repo.get(session.id)
    assert stored.short_description == "Fresh" and stored.status is SessionStatus.STOPPED


async def test_down_migration_drops_the_columns(pool) -> None:
    down = (MIGRATIONS / "000092_session_descriptions.down.sql").read_text()
    up = (MIGRATIONS / "000092_session_descriptions.up.sql").read_text()
    async with pool.acquire() as conn:
        await conn.execute(down)
        columns = {
            row["column_name"]
            for row in await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'sessions' AND table_schema = current_schema()"
            )
        }
        assert not {"short_description", "description_updated_at"} & columns
        await conn.execute(up)
        await conn.execute(up)  # idempotent
