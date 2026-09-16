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
