"""Schema adoption, atomic recording, drift and failure visibility."""

import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from volundr.adapters.outbound.startup_schema import apply_startup_migrations


def connection():
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.fetchval.return_value = None
    return conn


@pytest.mark.asyncio
async def test_matching_migration_is_not_run_again(tmp_path):
    path = tmp_path / "000001_test.up.sql"
    path.write_text("CREATE TABLE example (id INT)")
    conn = connection()
    conn.fetchval.return_value = hashlib.sha256(path.read_bytes()).hexdigest()
    await apply_startup_migrations(conn, [path])
    assert all(call.args[0] != path.read_text() for call in conn.execute.await_args_list)
    assert not any("INSERT INTO" in call.args[0] for call in conn.execute.await_args_list)


@pytest.mark.asyncio
async def test_applied_file_drift_fails_loudly_and_releases_lock(tmp_path, caplog):
    path = tmp_path / "000001_changed.up.sql"
    path.write_text("DROP TABLE important")
    conn = connection()
    conn.fetchval.return_value = "different-checksum"
    with pytest.raises(RuntimeError, match="add a new migration"):
        await apply_startup_migrations(conn, [path])
    assert "schema is not ready" in caplog.text
    assert all(call.args[0] != path.read_text() for call in conn.execute.await_args_list)
    assert "pg_advisory_unlock" in conn.execute.await_args_list[-1].args[0]


@pytest.mark.asyncio
async def test_failed_ddl_is_not_recorded_and_later_migrations_do_not_run(tmp_path, caplog):
    first = tmp_path / "000001_bad.up.sql"
    second = tmp_path / "000002_later.up.sql"
    first.write_text("broken SQL")
    second.write_text("later SQL")
    conn = connection()

    async def execute(sql, *_):
        if sql == "broken SQL":
            raise RuntimeError("invalid schema")

    conn.execute.side_effect = execute
    with pytest.raises(RuntimeError, match="invalid schema"):
        await apply_startup_migrations(conn, [first, second])
    attempted = [call.args[0] for call in conn.execute.await_args_list]
    assert "later SQL" not in attempted
    assert not any("INSERT INTO" in sql for sql in attempted)
    assert "000001_bad.up.sql failed" in caplog.text
    assert conn.transaction.return_value.__aexit__.await_args.args[0] is RuntimeError


@pytest.mark.asyncio
async def test_new_migration_and_checksum_share_transaction(tmp_path):
    path = tmp_path / "000001_test.up.sql"
    path.write_text("CREATE TABLE example (id INT)")
    conn = connection()
    await apply_startup_migrations(conn, [path])
    conn.transaction.assert_called_once()
    ledger = [c for c in conn.execute.await_args_list if "INSERT INTO" in c.args[0]]
    assert ledger[0].args[1:] == (path.name, hashlib.sha256(path.read_bytes()).hexdigest())
    conn.transaction.return_value.__aexit__.assert_awaited_once_with(None, None, None)


@pytest.mark.parametrize("has_yaml", [False, True])
async def test_ting_adoption_preserves_graph_updates_after_yaml_column_drop(has_yaml):
    from pathlib import Path
    from unittest.mock import patch

    from niuu.adapters.postgres_schema import _legacy_plan

    path = Path(__file__).parents[2] / "migrations/ting/000025_legacy_schema_compat.up.sql"
    sql = path.read_text()
    columns = {"id", "name", "description", "graph_json"}
    if has_yaml:
        columns.add("definition_yaml")
    with patch("niuu.adapters.postgres_schema._columns", AsyncMock(return_value=columns)):
        plan = await _legacy_plan(connection(), path.name, sql)
    assert ("definition_yaml" in plan.sql) is has_yaml
    assert "graph_json = replace(" in plan.sql
    assert "UPDATE workflows" in plan.sql
    if has_yaml:
        assert plan.sql == sql
    else:
        assert plan.columns == {"workflows": columns}
        assert len(sql.splitlines()) - len(plan.sql.splitlines()) == 3


async def test_ting_adoption_rejects_incomplete_graph_schema():
    from unittest.mock import patch

    from niuu.adapters.postgres_schema import _legacy_plan

    with patch("niuu.adapters.postgres_schema._columns", AsyncMock(return_value={"id"})):
        with pytest.raises(RuntimeError, match="canonical graph schema"):
            await _legacy_plan(connection(), "000025_legacy_schema_compat.up.sql", "SQL")


async def test_independent_streams_with_same_filename_have_distinct_ledger_keys(tmp_path):
    ledger = {}
    conn = connection()
    conn.fetchval.side_effect = lambda sql, key: ledger.get(key)

    async def execute(sql, *args):
        if "INSERT INTO volundr_schema_history" in sql:
            ledger[args[0]] = args[1]

    conn.execute.side_effect = execute
    path = tmp_path / "000001_initial_schema.up.sql"
    path.write_text("CREATE TABLE forge_data (id INT)")
    await apply_startup_migrations(conn, [path])
    forge_checksum = ledger[path.name]
    path.write_text("CREATE TABLE ting_data (id INT)")
    await apply_startup_migrations(conn, [path], namespace="ting")
    await apply_startup_migrations(conn, [path], namespace="ting")
    assert ledger[path.name] == forge_checksum
    assert ledger["ting/" + path.name] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert sum(call.args[0] == path.read_text() for call in conn.execute.await_args_list) == 1


async def test_ting_shared_integration_table_adopts_owner_column():
    from pathlib import Path
    from unittest.mock import patch

    from niuu.adapters.postgres_schema import _legacy_plan

    path = Path(__file__).parents[2] / "migrations/ting/000002_integration_connections.up.sql"
    with patch("niuu.adapters.postgres_schema._columns", AsyncMock(return_value={"owner_id"})):
        plan = await _legacy_plan(connection(), path.name, path.read_text())
    assert "user_id" not in plan.sql
    assert "ON integration_connections(owner_id, integration_type)" in plan.sql
    assert plan.indexes == [
        (
            "idx_integration_connections_owner",
            "integration_connections",
            ["owner_id", "integration_type"],
            False,
        )
    ]
