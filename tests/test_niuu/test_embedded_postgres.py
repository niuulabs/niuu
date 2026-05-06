"""Tests for the embedded PostgreSQL adapter.

These tests mock subprocess and asyncpg so they run in CI without
actually starting an embedded PostgreSQL instance.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from niuu.adapters.embedded_postgres import (
    EmbeddedPostgresDatabase,
    _choose_socket_dir,
    _find_pg_bin_dir,
    _socket_path_ok,
)
from niuu.ports.embedded_database import ConnectionInfo, EmbeddedDatabasePort

# ---------------------------------------------------------------------------
# Port contract
# ---------------------------------------------------------------------------


class TestEmbeddedDatabasePort:
    """Verify EmbeddedPostgresDatabase satisfies the port interface."""

    def test_implements_port(self):
        assert issubclass(EmbeddedPostgresDatabase, EmbeddedDatabasePort)

    def test_connection_info_is_frozen(self):
        info = ConnectionInfo(host="localhost", port=5432, dbname="test", user="pg")
        with pytest.raises(AttributeError):
            info.host = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------


class TestFindPgBinDir:
    def test_env_override(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "pg" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "postgres").touch()
        with patch.dict("os.environ", {"NIUU_PG_BIN_DIR": str(bin_dir)}):
            result = _find_pg_bin_dir()
        assert result == bin_dir

    def test_env_override_missing_binary(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "pg" / "bin"
        bin_dir.mkdir(parents=True)
        # No postgres binary
        with patch.dict("os.environ", {"NIUU_PG_BIN_DIR": str(bin_dir)}):
            result = _find_pg_bin_dir()
        # Should fall through to other checks (which also won't find it)
        assert result is None or result != bin_dir

    def test_returns_none_when_not_found(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            # Override all search paths to not exist
            with patch("niuu.adapters.embedded_postgres.Path.exists", return_value=False):
                result = _find_pg_bin_dir()
        assert result is None


# ---------------------------------------------------------------------------
# Socket path handling
# ---------------------------------------------------------------------------


class TestSocketPath:
    def test_short_path_ok(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp", prefix="niuu-socket-") as short_path:
            assert _socket_path_ok(short_path)

    def test_long_path_fails(self) -> None:
        long_path = "/tmp/" + "a" * 200
        assert not _socket_path_ok(long_path)

    def test_choose_socket_dir_uses_data_dir_when_short(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp", prefix="niuu-socket-") as short_path:
            short_dir = Path(short_path)
            result = _choose_socket_dir(short_dir)
            assert result.resolve() == short_dir.resolve()

    def test_choose_socket_dir_falls_back_for_long_paths(self) -> None:
        long_path = Path("/tmp/" + "a" * 200)
        with patch(
            "niuu.adapters.embedded_postgres._socket_path_ok",
            return_value=False,
        ):
            result = _choose_socket_dir(long_path)
        assert "niuu_pg_" in str(result)
        assert result != long_path


# ---------------------------------------------------------------------------
# URI parsing
# ---------------------------------------------------------------------------


class TestParseUri:
    """Test _parse_uri covers the URI variants the adapter produces."""

    def test_tcp_uri(self):
        uri = "postgresql://myuser:@127.0.0.1:5433/mydb"
        info = EmbeddedPostgresDatabase._parse_uri(uri)
        assert info.host == "127.0.0.1"
        assert info.port == 5433
        assert info.dbname == "mydb"
        assert info.user == "myuser"

    def test_unix_socket_uri(self):
        uri = "postgresql://postgres:@/postgres?host=/tmp/pg_data"
        info = EmbeddedPostgresDatabase._parse_uri(uri)
        assert info.host == "/tmp/pg_data"
        assert info.port == 5432  # default when not specified
        assert info.dbname == "postgres"
        assert info.user == "postgres"

    def test_minimal_uri_uses_defaults(self):
        uri = "postgresql:///test"
        info = EmbeddedPostgresDatabase._parse_uri(uri)
        assert info.host == "localhost"
        assert info.port == 5432
        assert info.dbname == "test"

    def test_empty_uri_uses_all_defaults(self):
        uri = "postgresql:///"
        info = EmbeddedPostgresDatabase._parse_uri(uri)
        assert info.host == "localhost"
        assert info.port == 5432
        assert info.dbname == "postgres"
        assert info.user == "postgres"


# ---------------------------------------------------------------------------
# Lifecycle tests (mocked)
# ---------------------------------------------------------------------------


def _make_mock_conn():
    """Create a mock asyncpg connection."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=1)
    conn.close = AsyncMock()
    return conn


class TestStart:
    @pytest.mark.asyncio
    async def test_start_returns_connection_info(self, tmp_path: Path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "postgres").touch()
        (bin_dir / "initdb").touch()
        (bin_dir / "pg_ctl").touch()

        data_dir = tmp_path / "pgdata"
        mock_conn = _make_mock_conn()

        with (
            patch(
                "niuu.adapters.embedded_postgres._find_pg_bin_dir",
                return_value=bin_dir,
            ),
            patch(
                "niuu.adapters.embedded_postgres.subprocess.run",
                return_value=MagicMock(returncode=0, stderr=""),
            ),
            patch("niuu.adapters.embedded_postgres.asyncpg", create=True) as mock_asyncpg,
        ):
            import sys

            sys.modules["asyncpg"] = mock_asyncpg
            mock_asyncpg.connect = AsyncMock(return_value=mock_conn)

            try:
                db = EmbeddedPostgresDatabase()
                info = await db.start(str(data_dir))

                assert isinstance(info, ConnectionInfo)
                assert info.dbname == "postgres"
                assert info.user == "postgres"
            finally:
                sys.modules.pop("asyncpg", None)

    @pytest.mark.asyncio
    async def test_start_raises_without_binaries(self):
        with patch(
            "niuu.adapters.embedded_postgres._find_pg_bin_dir",
            return_value=None,
        ):
            db = EmbeddedPostgresDatabase()
            with pytest.raises(RuntimeError, match="PostgreSQL binaries not found"):
                await db.start("/tmp/test")

    @pytest.mark.asyncio
    async def test_start_skips_initdb_when_pgversion_exists(self, tmp_path: Path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "postgres").touch()
        (bin_dir / "initdb").touch()
        (bin_dir / "pg_ctl").touch()

        data_dir = tmp_path / "pgdata"
        data_dir.mkdir()
        (data_dir / "PG_VERSION").write_text("17")

        mock_conn = _make_mock_conn()

        with (
            patch(
                "niuu.adapters.embedded_postgres._find_pg_bin_dir",
                return_value=bin_dir,
            ),
            patch(
                "niuu.adapters.embedded_postgres.subprocess.run",
                return_value=MagicMock(returncode=0, stderr=""),
            ) as mock_run,
            patch("niuu.adapters.embedded_postgres.asyncpg", create=True) as mock_asyncpg,
        ):
            import sys

            sys.modules["asyncpg"] = mock_asyncpg
            mock_asyncpg.connect = AsyncMock(return_value=mock_conn)

            try:
                db = EmbeddedPostgresDatabase()
                await db.start(str(data_dir))

                # initdb should NOT have been called — only pg_ctl start
                calls = mock_run.call_args_list
                # Check that no call has "initdb" as the first argument (the binary)
                first_args = [str(c[0][0][0]) for c in calls]
                assert not any(a.endswith("initdb") for a in first_args)
            finally:
                sys.modules.pop("asyncpg", None)


class TestExecute:
    @pytest.mark.asyncio
    async def test_execute_returns_rows_as_dicts(self):
        mock_conn = _make_mock_conn()
        mock_record = {"id": 1, "name": "test"}
        mock_conn.fetch = AsyncMock(return_value=[mock_record])

        db = EmbeddedPostgresDatabase()
        db._conn = mock_conn

        rows = await db.execute("SELECT id, name FROM t WHERE id = $1", 1)
        assert rows == [{"id": 1, "name": "test"}]
        mock_conn.fetch.assert_awaited_once_with("SELECT id, name FROM t WHERE id = $1", 1)

    @pytest.mark.asyncio
    async def test_execute_raises_when_not_started(self):
        db = EmbeddedPostgresDatabase()
        with pytest.raises(RuntimeError, match="Database not started"):
            await db.execute("SELECT 1")


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_closes_connection_and_stops_server(self, tmp_path: Path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "pg_ctl").touch()

        mock_conn = _make_mock_conn()

        db = EmbeddedPostgresDatabase()
        db._bin_dir = bin_dir
        db._data_dir = tmp_path / "pgdata"
        db._conn = mock_conn

        with patch(
            "niuu.adapters.embedded_postgres.subprocess.run",
            return_value=MagicMock(returncode=0, stderr=""),
        ):
            await db.stop()

        mock_conn.close.assert_awaited_once()
        assert db._conn is None

    @pytest.mark.asyncio
    async def test_stop_cleans_up_even_if_conn_close_fails(self, tmp_path: Path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "pg_ctl").touch()

        mock_conn = _make_mock_conn()
        mock_conn.close = AsyncMock(side_effect=ConnectionError("already closed"))

        db = EmbeddedPostgresDatabase()
        db._bin_dir = bin_dir
        db._data_dir = tmp_path / "pgdata"
        db._conn = mock_conn

        with patch(
            "niuu.adapters.embedded_postgres.subprocess.run",
            return_value=MagicMock(returncode=0, stderr=""),
        ) as mock_run:
            await db.stop()

        # pg_ctl stop should still have been called
        assert mock_run.called
        assert db._conn is None

    @pytest.mark.asyncio
    async def test_stop_is_safe_when_not_started(self):
        db = EmbeddedPostgresDatabase()
        await db.stop()  # should not raise


class TestIsRunning:
    @pytest.mark.asyncio
    async def test_is_running_true_when_connected(self):
        mock_conn = _make_mock_conn()
        mock_conn.fetchval = AsyncMock(return_value=1)

        db = EmbeddedPostgresDatabase()
        db._conn = mock_conn

        assert await db.is_running() is True

    @pytest.mark.asyncio
    async def test_is_running_false_when_no_connection(self):
        db = EmbeddedPostgresDatabase()
        assert await db.is_running() is False

    @pytest.mark.asyncio
    async def test_is_running_false_on_connection_error(self):
        mock_conn = _make_mock_conn()
        mock_conn.fetchval = AsyncMock(side_effect=ConnectionError("gone"))

        db = EmbeddedPostgresDatabase()
        db._conn = mock_conn

        assert await db.is_running() is False


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_default_timeouts(self):
        db = EmbeddedPostgresDatabase()
        assert db._startup_timeout_s == 30
        assert db._cleanup_timeout_s == 10

    def test_custom_timeouts(self):
        db = EmbeddedPostgresDatabase(startup_timeout_s=60, cleanup_timeout_s=20)
        assert db._startup_timeout_s == 60
        assert db._cleanup_timeout_s == 20


# ---------------------------------------------------------------------------
# Low-level command tests (mocked subprocess)
# ---------------------------------------------------------------------------


class TestRunInitdb:
    def test_initdb_success(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()

        db = EmbeddedPostgresDatabase()
        db._bin_dir = bin_dir
        db._data_dir = tmp_path / "pgdata"

        with patch(
            "niuu.adapters.embedded_postgres.subprocess.run",
            return_value=MagicMock(returncode=0, stderr=""),
        ) as mock_run:
            db._run_initdb()

        args = mock_run.call_args[0][0]
        assert str(args[0]).endswith("initdb")
        assert "--auth=trust" in args
        assert "--encoding=utf8" in args

    def test_initdb_failure_raises(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()

        db = EmbeddedPostgresDatabase()
        db._bin_dir = bin_dir
        db._data_dir = tmp_path / "pgdata"

        with patch(
            "niuu.adapters.embedded_postgres.subprocess.run",
            return_value=MagicMock(returncode=1, stderr="initdb: error"),
        ):
            with pytest.raises(RuntimeError, match="initdb failed"):
                db._run_initdb()


class TestStartServer:
    def test_start_success(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir.parent / "lib").mkdir()

        db = EmbeddedPostgresDatabase()
        db._bin_dir = bin_dir
        db._data_dir = tmp_path / "pgdata"
        db._socket_dir = tmp_path

        with patch(
            "niuu.adapters.embedded_postgres.subprocess.run",
            return_value=MagicMock(returncode=0, stderr=""),
        ) as mock_run:
            db._start_server()

        args = mock_run.call_args[0][0]
        assert str(args[0]).endswith("pg_ctl")
        assert "start" in args

    def test_start_failure_raises(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir.parent / "lib").mkdir()

        db = EmbeddedPostgresDatabase()
        db._bin_dir = bin_dir
        db._data_dir = tmp_path / "pgdata"
        db._socket_dir = tmp_path

        with patch(
            "niuu.adapters.embedded_postgres.subprocess.run",
            return_value=MagicMock(returncode=1, stderr="pg_ctl: error"),
        ):
            with pytest.raises(RuntimeError, match="pg_ctl start failed"):
                db._start_server()

    def test_start_sets_library_path(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir.parent / "lib").mkdir()

        db = EmbeddedPostgresDatabase()
        db._bin_dir = bin_dir
        db._data_dir = tmp_path / "pgdata"
        db._socket_dir = tmp_path

        with patch(
            "niuu.adapters.embedded_postgres.subprocess.run",
            return_value=MagicMock(returncode=0, stderr=""),
        ) as mock_run:
            db._start_server()

        env = mock_run.call_args[1]["env"]
        lib_dir = str(bin_dir.parent / "lib")
        # One of these should contain the lib dir
        lib_path = env.get("LD_LIBRARY_PATH", "") + env.get("DYLD_LIBRARY_PATH", "")
        assert lib_dir in lib_path


class TestStopServer:
    def test_stop_noop_when_not_started(self) -> None:
        db = EmbeddedPostgresDatabase()
        db._bin_dir = None
        db._data_dir = None
        # Should not raise
        db._stop_server()

    def test_stop_success(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir.parent / "lib").mkdir()

        db = EmbeddedPostgresDatabase()
        db._bin_dir = bin_dir
        db._data_dir = tmp_path / "pgdata"

        with patch(
            "niuu.adapters.embedded_postgres.subprocess.run",
            return_value=MagicMock(returncode=0, stderr=""),
        ) as mock_run:
            db._stop_server()

        args = mock_run.call_args[0][0]
        assert "stop" in args

    def test_stop_failure_tries_kill(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir.parent / "lib").mkdir()

        data_dir = tmp_path / "pgdata"
        data_dir.mkdir()
        pid_file = data_dir / "postmaster.pid"
        pid_file.write_text("99999\n/some/path\n")

        db = EmbeddedPostgresDatabase()
        db._bin_dir = bin_dir
        db._data_dir = data_dir

        with (
            patch(
                "niuu.adapters.embedded_postgres.subprocess.run",
                return_value=MagicMock(returncode=1, stderr="already stopped"),
            ),
            patch("niuu.adapters.embedded_postgres.os.kill") as mock_kill,
        ):
            db._stop_server()  # should not raise

        mock_kill.assert_called_once_with(99999, 15)

    def test_stop_failure_handles_missing_pid_file(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir.parent / "lib").mkdir()

        db = EmbeddedPostgresDatabase()
        db._bin_dir = bin_dir
        db._data_dir = tmp_path / "pgdata"  # no postmaster.pid

        with patch(
            "niuu.adapters.embedded_postgres.subprocess.run",
            return_value=MagicMock(returncode=1, stderr="error"),
        ):
            db._stop_server()  # should not raise


class TestPgBinDir:
    """Test the public pg_bin_dir() accessor."""

    def test_returns_path_when_found(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "postgres").touch()
        with patch("niuu.adapters.embedded_postgres._find_pg_bin_dir", return_value=bin_dir):
            from niuu.adapters.embedded_postgres import pg_bin_dir

            assert pg_bin_dir() == bin_dir

    def test_returns_none_when_not_found(self) -> None:
        with patch("niuu.adapters.embedded_postgres._find_pg_bin_dir", return_value=None):
            from niuu.adapters.embedded_postgres import pg_bin_dir

            assert pg_bin_dir() is None
