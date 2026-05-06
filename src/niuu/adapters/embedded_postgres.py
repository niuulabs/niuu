"""Embedded PostgreSQL adapter using bundled binaries built from source.

Manages the full PostgreSQL lifecycle (initdb, start, stop) by calling
the pg_ctl / initdb binaries that are compiled from source during the
build step and bundled into the Nuitka single-binary distribution.

Binary search order:
    1. ``NIUU_PG_BIN_DIR`` environment variable
    2. Relative to this module: ``../../pginstall/bin`` (Nuitka bundle)
    3. ``build/pginstall/bin`` relative to repo root (development)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import socket
import subprocess
import tempfile
from pathlib import Path

from niuu.ports.embedded_database import ConnectionInfo, EmbeddedDatabasePort

logger = logging.getLogger(__name__)

# Defaults — no magic numbers in business logic.
_DEFAULT_STARTUP_TIMEOUT_S = 30
_DEFAULT_CLEANUP_TIMEOUT_S = 10
_PG_CTL_TIMEOUT_S = 10

# Unix domain socket paths are limited to 104-108 bytes depending on OS.
_MAX_SOCKET_PATH_LEN = 100


def _find_pg_bin_dir() -> Path | None:
    """Locate the PostgreSQL bin directory.

    Returns None if binaries are not found.
    """
    # 1. Explicit override
    env_dir = os.environ.get("NIUU_PG_BIN_DIR")
    if env_dir:
        p = Path(env_dir)
        if (p / "postgres").exists():
            return p

    # 2. Nuitka bundle: niuu/pginstall/bin relative to this module
    bundled = Path(__file__).resolve().parent.parent / "pginstall" / "bin"
    if (bundled / "postgres").exists():
        return bundled

    # 3. Development: build/pginstall/bin relative to repo root
    # Walk up from src/niuu/adapters/ to find repo root (contains pyproject.toml)
    candidate = Path(__file__).resolve().parent.parent.parent.parent
    dev_path = candidate / "build" / "pginstall" / "bin"
    if (dev_path / "postgres").exists():
        return dev_path

    return None


def pg_bin_dir() -> Path | None:
    """Public accessor for the PostgreSQL binary directory."""
    return _find_pg_bin_dir()


def _socket_path_ok(path: str) -> bool:
    """Check if a Unix domain socket path fits within the OS limit."""
    # The actual socket file is <path>/.s.PGSQL.<port>
    test_path = f"{path}/.s.PGSQL.5432"
    if len(test_path.encode()) > _MAX_SOCKET_PATH_LEN:
        return False

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(test_path)
        finally:
            sock.close()
            if os.path.exists(test_path):
                os.unlink(test_path)
        return True
    except OSError:
        return False


def _socket_dir_candidates(data_dir: Path) -> tuple[Path, ...]:
    """Return candidate socket directories, preferring equivalent short aliases."""
    candidates = [data_dir]
    path_str = str(data_dir)

    if path_str.startswith("/private/"):
        alias = Path(path_str.removeprefix("/private"))
        try:
            if alias.exists() and alias.resolve() == data_dir.resolve():
                candidates.append(alias)
        except OSError:
            pass

    return tuple(dict.fromkeys(candidates))


def _choose_socket_dir(data_dir: Path) -> Path:
    """Choose a socket directory that fits the Unix socket path limit.

    Prefers the data directory itself. Falls back to a short hashed
    temp directory if the data_dir path is too long.
    """
    for candidate in _socket_dir_candidates(data_dir):
        if _socket_path_ok(str(candidate)):
            return candidate

    # Fall back to a short hashed path
    dir_hash = hashlib.sha256(str(data_dir).encode()).hexdigest()[:10]
    fallback = Path(tempfile.gettempdir()) / f"niuu_pg_{dir_hash}"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


class EmbeddedPostgresDatabase(EmbeddedDatabasePort):
    """EmbeddedDatabasePort backed by bundled PostgreSQL binaries + asyncpg."""

    def __init__(
        self,
        *,
        startup_timeout_s: int = _DEFAULT_STARTUP_TIMEOUT_S,
        cleanup_timeout_s: int = _DEFAULT_CLEANUP_TIMEOUT_S,
    ) -> None:
        self._startup_timeout_s = startup_timeout_s
        self._cleanup_timeout_s = cleanup_timeout_s
        self._conn: object | None = None
        self._connection_info: ConnectionInfo | None = None
        self._data_dir: Path | None = None
        self._socket_dir: Path | None = None
        self._bin_dir: Path | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, data_dir: str) -> ConnectionInfo:
        """Initialise pgdata, start PostgreSQL, and open an asyncpg connection."""
        bin_dir = _find_pg_bin_dir()
        if bin_dir is None:
            raise RuntimeError("PostgreSQL binaries not found. Rebuild with: make build-postgres")
        self._bin_dir = bin_dir
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

        loop = asyncio.get_running_loop()

        # initdb if needed
        if not (self._data_dir / "PG_VERSION").exists():
            await loop.run_in_executor(None, self._run_initdb)

        # Start server
        self._socket_dir = _choose_socket_dir(self._data_dir)
        await loop.run_in_executor(None, self._start_server)

        # Build connection info
        uri = f"postgresql://postgres:@/postgres?host={self._socket_dir}"
        info = self._parse_uri(uri)
        self._connection_info = info

        try:
            import asyncpg  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "asyncpg is not installed. Install it with: pip install asyncpg"
            ) from exc

        self._conn = await asyncio.wait_for(
            asyncpg.connect(dsn=uri),
            timeout=self._startup_timeout_s,
        )
        logger.info("Embedded PG started — %s", uri)
        return info

    async def execute(self, sql: str, *args: object) -> list[dict]:
        if self._conn is None:
            raise RuntimeError("Database not started — call start() first")

        result = await self._conn.fetch(sql, *args)
        return [dict(row) for row in result]

    async def stop(self) -> None:
        if self._conn is not None:
            try:
                await asyncio.wait_for(
                    self._conn.close(),
                    timeout=self._cleanup_timeout_s,
                )
            except Exception:
                logger.warning("Failed to close asyncpg connection cleanly", exc_info=True)
            self._conn = None
            logger.info("asyncpg connection closed")

        if self._data_dir is not None and self._bin_dir is not None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._stop_server)
            logger.info("Embedded PG stopped")

    async def is_running(self) -> bool:
        if self._conn is None:
            return False
        try:
            await self._conn.fetchval("SELECT 1")
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # PostgreSQL commands
    # ------------------------------------------------------------------

    def _run_initdb(self) -> None:
        """Run initdb to create the pgdata directory."""
        initdb = self._bin_dir / "initdb"
        cmd = [
            str(initdb),
            "-D",
            str(self._data_dir),
            "--auth=trust",
            "--auth-local=trust",
            "--encoding=utf8",
            "-U",
            "postgres",
        ]
        logger.info("Running initdb: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"initdb failed (exit {result.returncode}): {result.stderr}")

    def _start_server(self) -> None:
        """Start PostgreSQL via pg_ctl."""
        pg_ctl = self._bin_dir / "pg_ctl"
        log_file = self._data_dir / "log"
        cmd = [
            str(pg_ctl),
            "-D",
            str(self._data_dir),
            "-w",
            "-o",
            f'-h "" -k "{self._socket_dir}"',
            "-l",
            str(log_file),
            "start",
        ]
        logger.info("Starting PostgreSQL: %s", " ".join(cmd))

        env = os.environ.copy()
        # Ensure the bundled lib dir is on the library path so postgres
        # can find libpq and other shared libraries.
        lib_dir = str(self._bin_dir.parent / "lib")
        if os.uname().sysname == "Darwin":
            env["DYLD_LIBRARY_PATH"] = lib_dir + ":" + env.get("DYLD_LIBRARY_PATH", "")
        else:
            env["LD_LIBRARY_PATH"] = lib_dir + ":" + env.get("LD_LIBRARY_PATH", "")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=_PG_CTL_TIMEOUT_S,
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(f"pg_ctl start failed (exit {result.returncode}): {result.stderr}")

    def _stop_server(self) -> None:
        """Stop PostgreSQL via pg_ctl."""
        if self._bin_dir is None or self._data_dir is None:
            return

        pg_ctl = self._bin_dir / "pg_ctl"
        cmd = [
            str(pg_ctl),
            "-D",
            str(self._data_dir),
            "-w",
            "stop",
        ]
        logger.info("Stopping PostgreSQL: %s", " ".join(cmd))

        env = os.environ.copy()
        lib_dir = str(self._bin_dir.parent / "lib")
        if os.uname().sysname == "Darwin":
            env["DYLD_LIBRARY_PATH"] = lib_dir + ":" + env.get("DYLD_LIBRARY_PATH", "")
        else:
            env["LD_LIBRARY_PATH"] = lib_dir + ":" + env.get("LD_LIBRARY_PATH", "")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=_PG_CTL_TIMEOUT_S,
            env=env,
        )
        if result.returncode != 0:
            logger.warning("pg_ctl stop failed (exit %d): %s", result.returncode, result.stderr)
            # Try to kill the process directly as fallback
            pid_file = self._data_dir / "postmaster.pid"
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().split("\n")[0])
                    os.kill(pid, 15)  # SIGTERM
                except (ValueError, ProcessLookupError, OSError):
                    pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_uri(uri: str) -> ConnectionInfo:
        """Parse a PostgreSQL URI into ConnectionInfo.

        Handles both Unix socket URIs:
            ``postgresql://postgres:@/postgres?host=/tmp/pgdata``
        and TCP-style:
            ``postgresql://postgres:@localhost:5432/postgres``
        """
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(uri)
        qs = parse_qs(parsed.query)

        # Unix socket: host is in query params
        host = qs.get("host", [parsed.hostname or "localhost"])[0]
        port = parsed.port or 5432
        dbname = (parsed.path or "/postgres").lstrip("/") or "postgres"
        user = parsed.username or "postgres"

        return ConnectionInfo(host=host, port=port, dbname=dbname, user=user)
