"""Real PostgreSQL contention and rollback, isolated from user sessions."""

import asyncio
import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from volundr.adapters.outbound.pg_message_delivery import PostgresMessageDelivery
from volundr.adapters.outbound.startup_schema import apply_startup_migrations
from volundr.domain.message_delivery import MessageDeliveryConflictError

pytestmark = pytest.mark.integration
MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


@pytest_asyncio.fixture
async def isolated_pool():
    dsn = os.environ.get("FORGE_HISTORY_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set FORGE_HISTORY_TEST_DATABASE_URL for isolated PostgreSQL checks")
    schema = "forge_delivery_test_" + uuid4().hex
    admin = await asyncpg.connect(dsn)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    pool = None
    try:
        pool = await asyncpg.create_pool(
            dsn, min_size=1, max_size=5, server_settings={"search_path": schema}
        )
        yield pool
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


async def seed_claims(pool):
    await pool.execute("CREATE TABLE sessions (id UUID PRIMARY KEY)")
    await pool.execute((MIGRATIONS / "000070_message_delivery_claims.up.sql").read_text())
    sid = uuid4()
    await pool.execute("INSERT INTO sessions VALUES ($1)", sid)
    return sid


async def test_concurrent_dispatchers_have_exactly_one_claimant(isolated_pool):
    sid = await seed_claims(isolated_pool)
    tokens = [uuid4() for _ in range(10)]
    results = await asyncio.gather(
        *(
            PostgresMessageDelivery(isolated_pool).claim(sid, "same-request", "a" * 64, token)
            for token in tokens
        )
    )
    assert sum(result.claimed for result in results) == 1
    winner = tokens[next(i for i, result in enumerate(results) if result.claimed)]
    repo = PostgresMessageDelivery(isolated_pool)
    assert (await repo.claim(sid, "same-request", "a" * 64, winner)).claimed
    # A new broker instance after a crash must never steal an uncertain dispatch.
    assert not (await repo.claim(sid, "same-request", "a" * 64, uuid4())).claimed
    await repo.settle(sid, "same-request", winner, "delivered")
    assert (
        await PostgresMessageDelivery(isolated_pool).get(sid, "same-request")
    ).status == "delivered"
    assert not (await repo.claim(sid, "same-request", "a" * 64, uuid4())).claimed
    with pytest.raises(MessageDeliveryConflictError):
        await repo.claim(sid, "same-request", "b" * 64, uuid4())


async def test_failed_and_delivered_settlement_are_monotonic(isolated_pool):
    sid = await seed_claims(isolated_pool)
    repo = PostgresMessageDelivery(isolated_pool)
    token = uuid4()
    await repo.claim(sid, "r", "a" * 64, token)
    with pytest.raises(MessageDeliveryConflictError):
        await repo.settle(sid, "r", uuid4(), "delivered")
    await repo.settle(sid, "r", token, "failed", "native unavailable" + chr(0))
    with pytest.raises(MessageDeliveryConflictError):
        await repo.settle(sid, "r", token, "delivered")
    assert (await repo.get(sid, "r")).status == "failed"


async def test_migration_failure_rolls_back_ddl_and_ledger(isolated_pool, tmp_path):
    first = tmp_path / "000001_ok.up.sql"
    bad = tmp_path / "000002_bad.up.sql"
    first.write_text("CREATE TABLE kept (id INT)")
    bad.write_text("CREATE TABLE rolled_back (id INT); SELECT missing_column FROM kept;")
    async with isolated_pool.acquire() as conn:
        with pytest.raises(asyncpg.UndefinedColumnError):
            await apply_startup_migrations(conn, [first, bad])
        assert await conn.fetchval("SELECT to_regclass('kept')") is not None
        assert await conn.fetchval("SELECT to_regclass('rolled_back')") is None
        assert await conn.fetchval("SELECT count(*) FROM volundr_schema_history") == 1
        # Repair an unapplied migration, then retry; already committed DDL is skipped.
        bad.write_text("CREATE TABLE rolled_back (id INT)")
        await apply_startup_migrations(conn, [first, bad])
        assert await conn.fetchval("SELECT count(*) FROM volundr_schema_history") == 2
        first.write_text("DROP TABLE kept")
        with pytest.raises(RuntimeError, match="changed"):
            await apply_startup_migrations(conn, [first, bad])
        assert await conn.fetchval("SELECT to_regclass('kept')") is not None


async def test_full_schema_bootstrap_adoption_and_retry(isolated_pool):
    from cli.resources import ordered_migration_files

    files = ordered_migration_files(MIGRATIONS)
    async with isolated_pool.acquire() as conn:
        await apply_startup_migrations(conn, files)
        assert await conn.fetchval("SELECT count(*) FROM volundr_schema_history") == len(files)
        assert await conn.fetchval("SELECT to_regclass('session_message_deliveries')") is not None
        assert (
            await conn.fetchval("SELECT to_regclass('idx_session_event_log_session_seq')") is None
        )
        await apply_startup_migrations(conn, files)
        assert await conn.fetchval("SELECT count(*) FROM volundr_schema_history") == len(files)


async def _old_bootstrap(conn, files):
    """Reproduce the previous startup path: continue after errors, with no ledger."""
    for path in files:
        try:
            await conn.execute(path.read_text())
        except asyncpg.PostgresError:
            pass


async def test_existing_schema_without_ledger_adopts_and_preserves_user_rows(isolated_pool):
    from cli.resources import ordered_migration_files

    files = ordered_migration_files(MIGRATIONS)
    historical = [p for p in files if p.name[:6] <= "000052"]
    sid, spec, integration = uuid4(), uuid4(), uuid4()
    async with isolated_pool.acquire() as conn:
        await _old_bootstrap(conn, historical)
        await conn.execute(
            """INSERT INTO volundr_launch_specs (id, name, source)
               VALUES ($1, 'real-user-spec', '{"type":"local_mount","path":"/work"}')""",
            spec,
        )
        await conn.execute(
            """INSERT INTO sessions (id, name, model, launch_spec_id, source)
               VALUES ($1, 'real-user-session', 'codex', $2,
                       '{"type":"local_mount","path":"/work"}')""",
            sid,
            spec,
        )
        await conn.execute(
            """INSERT INTO integration_connections
               (id, owner_id, integration_type, adapter, credential_name, config)
               VALUES ($1, 'owner', 'test', 'adapter', 'credential', '{"keep":"yes"}')""",
            integration,
        )
        # A subsequent old startup re-created empty presets/preset_id and then
        # swallowed rename/index errors. This is the real legacy upgrade shape.
        await _old_bootstrap(conn, historical)
        before = {
            table: await conn.fetchval(
                f"SELECT row_to_json(t)::text FROM {table} t WHERE id=$1", key
            )
            for table, key in [
                ("sessions", sid),
                ("volundr_launch_specs", spec),
                ("integration_connections", integration),
            ]
        }
        assert await conn.fetchval("SELECT count(*) FROM volundr_presets") == 0
        assert await conn.fetchval("SELECT to_regclass('volundr_schema_history')") is None
        await apply_startup_migrations(conn, files)
        for table, key in [
            ("sessions", sid),
            ("volundr_launch_specs", spec),
            ("integration_connections", integration),
        ]:
            after = json.loads(
                await conn.fetchval(f"SELECT row_to_json(t)::text FROM {table} t WHERE id=$1", key)
            )
            original = json.loads(before[table])
            assert {column: after[column] for column in original} == original
        assert await conn.fetchval("SELECT count(*) FROM volundr_presets") == 0
        assert await conn.fetchval("SELECT count(*) FROM volundr_schema_history") == len(files)

        assert await conn.fetchval("SELECT to_regclass('session_message_deliveries')") is not None
        await apply_startup_migrations(conn, files)
        assert await conn.fetchval("SELECT count(*) FROM volundr_schema_history") == len(files)


def released_migration_stream(directory):
    """Reconstruct released filenames from their byte-identical canonical SQL."""
    directory.mkdir()
    for path in MIGRATIONS.glob("*.up.sql"):
        if path.name[:6] < "000049":
            shutil.copyfile(path, directory / path.name)
    aliases = json.loads((MIGRATIONS / "lineage-aliases.json").read_text())
    for canonical, entries in aliases.items():
        for entry in entries:
            shutil.copyfile(MIGRATIONS / canonical, directory / entry["filename"])
    return sorted(directory.glob("*.up.sql"))


async def test_released_checksum_ledger_is_preserved_during_full_upgrade(isolated_pool, tmp_path):
    old = released_migration_stream(tmp_path / "released")
    canonical = sorted(MIGRATIONS.glob("*.up.sql"))
    async with isolated_pool.acquire() as conn:
        await apply_startup_migrations(conn, old)
        before = await conn.fetch("SELECT * FROM volundr_schema_history ORDER BY filename")
        await conn.execute(
            "INSERT INTO sessions (id, name, model) VALUES ($1, 'keep', 'codex')", uuid4()
        )
        await apply_startup_migrations(conn, canonical)
        await apply_startup_migrations(conn, canonical)
        for row in before:
            assert (
                await conn.fetchrow(
                    "SELECT * FROM volundr_schema_history WHERE filename=$1", row["filename"]
                )
                == row
            )
        assert await conn.fetchval("SELECT count(*) FROM sessions WHERE name='keep'") == 1
        for table in ("session_message_deliveries", "resident_runtimes", "realms"):
            assert await conn.fetchval("SELECT to_regclass($1)", table) is not None


@pytest.mark.parametrize("lineage", ["released", "integration"])
async def test_numbered_55_upgrade_preserves_cursor_and_satisfies_dependencies(
    isolated_pool, tmp_path, lineage
):
    from volundr.schema_bridge import prepare_numbered_migrations

    files = sorted(MIGRATIONS.glob("*.up.sql"))
    history = (
        released_migration_stream(tmp_path / "released")
        if lineage == "released"
        else [path for path in files if path.name[:6] <= "000055"]
    )
    async with isolated_pool.acquire() as conn:
        for path in history:
            await conn.execute(path.read_text())
        await conn.execute("CREATE TABLE schema_migrations (version BIGINT, dirty BOOLEAN)")
        await conn.execute("INSERT INTO schema_migrations VALUES (55, false)")
        before = await conn.fetchrow("SELECT * FROM schema_migrations")
        assert await prepare_numbered_migrations(conn, MIGRATIONS) == 4
        assert await conn.fetchrow("SELECT * FROM schema_migrations") == before
        # This is the numbered runner's next step: execute files above its cursor.
        for path in files:
            if path.name[:6] > "000055":
                await conn.execute(path.read_text())
        assert await conn.fetchval("SELECT to_regclass('resident_runtimes')") is not None
        assert await conn.fetchval("SELECT to_regclass('session_message_deliveries')") is not None
        assert await conn.fetchval("SELECT workload_config FROM sessions LIMIT 1") is None


async def test_adoption_rejects_conflicting_legacy_presets_without_deleting_rows(isolated_pool):
    from cli.resources import ordered_migration_files

    files = ordered_migration_files(MIGRATIONS)
    historical = [p for p in files if p.name[:6] <= "000052"]
    async with isolated_pool.acquire() as conn:
        await _old_bootstrap(conn, historical)
        await _old_bootstrap(conn, historical)
        for table in ("volundr_presets", "volundr_launch_specs"):
            await conn.execute(f"INSERT INTO {table} (id, name) VALUES ($1, $2)", uuid4(), table)
        with pytest.raises(RuntimeError, match="legacy user rows"):
            await apply_startup_migrations(conn, files)
        assert await conn.fetchval("SELECT count(*) FROM volundr_presets") == 1
        assert await conn.fetchval("SELECT count(*) FROM volundr_launch_specs") == 1
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM volundr_schema_history WHERE filename LIKE '000011%'"
            )
            == 0
        )


@pytest.mark.parametrize("corruption", ["missing-column", "wrong-index"])
async def test_adoption_verifies_canonical_columns_and_index_definition(isolated_pool, corruption):
    from cli.resources import ordered_migration_files

    files = ordered_migration_files(MIGRATIONS)
    historical = [p for p in files if p.name[:6] <= "000052"]
    async with isolated_pool.acquire() as conn:
        await _old_bootstrap(conn, historical)
        if corruption == "missing-column":
            await conn.execute("ALTER TABLE volundr_launch_specs DROP COLUMN description")
        else:
            await conn.execute("DROP INDEX idx_volundr_launch_specs_name")
            await conn.execute(
                "CREATE INDEX idx_volundr_launch_specs_name ON volundr_launch_specs(model)"
            )
        with pytest.raises(RuntimeError, match="Schema adoption missing"):
            await apply_startup_migrations(conn, files)
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM volundr_schema_history WHERE filename LIKE '000011%'"
            )
            == 0
        )


async def test_adoption_rejects_conflicting_session_preset_values(isolated_pool):
    from cli.resources import ordered_migration_files

    files = ordered_migration_files(MIGRATIONS)
    historical = [p for p in files if p.name[:6] <= "000052"]
    async with isolated_pool.acquire() as conn:
        await _old_bootstrap(conn, historical)
        await _old_bootstrap(conn, historical)
        sid, old, new = uuid4(), uuid4(), uuid4()
        await conn.execute(
            """INSERT INTO sessions(id,name,model,preset_id,launch_spec_id)
               VALUES($1,'conflicting','codex',$2,$3)""",
            sid,
            old,
            new,
        )
        with pytest.raises(RuntimeError, match="Conflicting preset_id"):
            await apply_startup_migrations(conn, files)
        row = await conn.fetchrow("SELECT preset_id, launch_spec_id FROM sessions WHERE id=$1", sid)
        assert (row["preset_id"], row["launch_spec_id"]) == (old, new)


@pytest.mark.parametrize("version", range(62, 67))
async def test_forge_numeric_cursor_upgrades_authority_and_vm_schema(isolated_pool, version):
    from volundr.schema_bridge import prepare_numbered_migrations

    files = sorted(MIGRATIONS.glob("*.up.sql"))
    # Forge's 62–66 are now canonical 69–73; upstream's 62–68 were absent there.
    history = [p for p in files if int(p.name[:6]) <= 61]
    history += [p for p in files if 69 <= int(p.name[:6]) <= version + 7]
    async with isolated_pool.acquire() as conn:
        for path in history:
            await conn.execute(path.read_text())
        sid = uuid4()
        await conn.execute(
            "INSERT INTO sessions (id, name, model) VALUES ($1, 'preserved', 'codex')", sid
        )
        await conn.execute("CREATE TABLE schema_migrations (version BIGINT, dirty BOOLEAN)")
        await conn.execute("INSERT INTO schema_migrations VALUES ($1, false)", version)
        assert await prepare_numbered_migrations(conn, MIGRATIONS) == 4 + version - 61
        assert await conn.fetchval("SELECT version FROM schema_migrations") == version
        for path in files:
            if int(path.name[:6]) > version:
                await conn.execute(path.read_text())
        for table in ("compute_leases", "admin_settings", "session_message_deliveries"):
            assert await conn.fetchval("SELECT to_regclass($1)", table) is not None
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name='personal_access_tokens' "
                "AND column_name='tenant_id'"
            )
            == 1
        )
        assert await conn.fetchval("SELECT name FROM sessions WHERE id=$1", sid) == "preserved"
