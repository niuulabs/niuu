"""Memory in mini mode: one database-only gbrain on this machine, run with the stack.

``niuu platform up`` starts gbrain before the root server, so Mímir's store
(``MIMIR__BACKEND``) and the memory gateway (``MIMIR__MEMORY``) can reach it when
their apps are built, and stops it after the root server. On the first start it
creates gbrain's admin token and the memory signing key (0600, kept across
restarts), initialises gbrain with ``--pglite --db-only`` and creates Mímir's
read/write token, all before ``gbrain serve`` holds the database.

gbrain is the executable ``scripts/build_gbrain.sh`` compiles from the pinned
upstream commit (the same one ``containers/gbrain/Dockerfile`` builds), so no Bun
is needed at run time. When memory is on and gbrain cannot run, the start fails
with the remedy; ``local_memory.enabled: false`` is the way to run without it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import secrets
import signal
import socket
import stat
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import yaml

from niuu.ports.plugin import Service

if TYPE_CHECKING:
    from cli.config import LocalMemoryConfig

logger = logging.getLogger(__name__)

#: Where a source checkout keeps the compiled gbrain (``make build-gbrain``).
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEV_GBRAIN_EXECUTABLE = _REPO_ROOT / "build" / "gbrain" / "gbrain"
#: Where the release binary carries it (``cli.build`` maps build/gbrain to cli/gbrain).
BUNDLED_GBRAIN_EXECUTABLE = Path(__file__).resolve().parents[1] / "gbrain" / "gbrain"

GBRAIN_STORE_ADAPTER = "mimir.adapters.gbrain.GBrainMimirAdapter"
GBRAIN_MEMORY_ADAPTER = "mimir.backends.gbrain.GBrainBackend"
MEMORY_SERVICE_AUTH_ADAPTER = "niuu.adapters.outbound.http_auth.NoAuthHeaderAdapter"

#: The variables :func:`memory_environment` sets. An operator value that differs is
#: refused at start, never silently replaced.
MEMORY_ENV_KEYS = ("MIMIR__BACKEND", "MIMIR__MEMORY", "MEMORY__GATEWAY_URL")

#: Process environment gbrain is given besides its own variables. Nothing else from the
#: platform's environment reaches it: a DATABASE_URL there would move gbrain off PGlite.
_INHERITED_ENV = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")
#: How `gbrain auth create` prints the token it created (on its own line).
_TOKEN_LINE = re.compile(r"(?m)^\s+(gbrain_[A-Za-z0-9_-]+)\s*$")
#: Bytes of entropy in each generated secret (64 hex characters).
_SECRET_BYTES = 32
#: Bytes of gbrain's log quoted in a start failure.
_LOG_TAIL_BYTES = 4096


class LocalMemoryError(RuntimeError):
    """The local memory brain cannot start or run. The message names the remedy."""


def resolve_gbrain_executable(configured: str) -> Path:
    """The gbrain executable to run: configured, else the release's, else the dev build's."""
    remedy = (
        "Build it with `make build-gbrain` (needs Bun >= 1.3.11), set "
        "local_memory.gbrain.executable to a gbrain executable, or set "
        "local_memory.enabled: false to run without memory."
    )
    if configured.strip():
        path = Path(configured).expanduser()
        if not (path.is_file() and os.access(path, os.X_OK)):
            raise LocalMemoryError(
                f"local_memory.gbrain.executable={configured!r} is not an executable file. "
                + remedy
            )
        return path
    for candidate in (BUNDLED_GBRAIN_EXECUTABLE, DEV_GBRAIN_EXECUTABLE):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise LocalMemoryError(
        "local_memory.enabled is true, but this niuu carries no gbrain (looked for "
        f"{BUNDLED_GBRAIN_EXECUTABLE} and {DEV_GBRAIN_EXECUTABLE}). " + remedy
    )


def _ensure_secret(path: Path, what: str) -> None:
    """Create *path* once with a random secret (0600); refuse one others can read."""
    if path.exists():
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise LocalMemoryError(
                f"{what} file {path} is readable by others (mode {mode:o}); run `chmod 600 {path}`"
            )
        if not path.read_text(encoding="utf-8").strip():
            raise LocalMemoryError(
                f"{what} file {path} is empty. Restore it from a backup; deleting it makes "
                "a new one, and for the memory signing key every existing record then fails "
                "its seal."
            )
        return
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(secrets.token_hex(_SECRET_BYTES))


def _command_line(pid: int) -> str:
    """The command line of process *pid*, or "" when there is none."""
    result = subprocess.run(
        ["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


class LocalMemoryBrain(Service):
    """Supervises the gbrain process of mini mode (start, health, stop)."""

    def __init__(self, config: LocalMemoryConfig, *, executable: Path) -> None:
        self._config = config
        self._gbrain = config.gbrain
        self._executable = executable
        self.home = Path(config.home).expanduser()
        self.gbrain_home = self.home / "gbrain"
        self.admin_token_file = self.home / "gbrain-admin-token"
        self.store_token_file = self.home / "gbrain-mimir-token"
        self.signing_key_file = self.home / "memory-signing-key"
        self.log_file = self.home / "gbrain.log"
        self.pid_file = self.home / "gbrain.pid"
        #: Mímir's settings for this brain, for the operator's own `python -m mimir`
        #: commands (``MIMIR_CONFIG=<this> python -m mimir import-wiki ...``).
        self.mimir_config_file = self.home / "mimir.yaml"
        self._mimir_settings: dict[str, object] | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._watcher: asyncio.Task[None] | None = None
        self._stopping = False

    def keep_mimir_settings(self, environment: dict[str, str]) -> None:
        """Write *environment*'s Mímir settings to :attr:`mimir_config_file` on start."""
        self._mimir_settings = {
            "backend": json.loads(environment["MIMIR__BACKEND"]),
            "memory": json.loads(environment["MIMIR__MEMORY"]),
        }

    @property
    def base_url(self) -> str:
        return f"http://{self._gbrain.host}:{self._gbrain.port}"

    @property
    def mcp_url(self) -> str:
        return f"{self.base_url}/mcp"

    @property
    def health_url(self) -> str:
        return f"{self.base_url}/health"

    @property
    def initialized(self) -> bool:
        return (self.gbrain_home / ".gbrain" / "config.json").is_file()

    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.home.chmod(0o700)
        _ensure_secret(self.admin_token_file, "gbrain admin token")
        _ensure_secret(self.signing_key_file, "memory signing key")
        self._refuse_leftover_process()
        self._refuse_taken_port()
        if not self.initialized:
            logger.info("Initialising the local memory brain in %s", self.gbrain_home)
            await self._run_once(
                "init", "--pglite", "--non-interactive", "--no-embedding", "--db-only"
            )
        if not self.store_token_file.exists():
            await self._create_store_token()
        _ensure_secret(self.store_token_file, "gbrain store token")
        if self._mimir_settings is not None:
            self.mimir_config_file.write_text(
                "# Written by `niuu platform up` on every start; edits are replaced.\n"
                + yaml.safe_dump(self._mimir_settings, sort_keys=False),
                encoding="utf-8",
            )
        await self._serve()

    def _refuse_leftover_process(self) -> None:
        if not self.pid_file.exists():
            return
        try:
            pid = int(self.pid_file.read_text(encoding="utf-8").strip())
        except ValueError:
            pid = 0
        command = _command_line(pid) if pid > 0 else ""
        # Only this gbrain's own serve holds the database; a pid the system gave to
        # another process since is a stale record.
        if str(self._executable) in command and " serve" in command:
            raise LocalMemoryError(
                f"gbrain from an earlier `niuu platform up` is still running (pid {pid}, "
                f"recorded in {self.pid_file}) and holds the memory database. Stop it with "
                f"`kill {pid}` and start again."
            )
        self.pid_file.unlink()

    def _refuse_taken_port(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            # As a server binds: connections of the previous run in TIME_WAIT do not hold
            # the port, a listener does.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((self._gbrain.host, self._gbrain.port))
            except OSError as exc:
                raise LocalMemoryError(
                    f"gbrain cannot listen on {self._gbrain.host}:{self._gbrain.port} ({exc}). "
                    "Stop what holds the port, or set local_memory.gbrain.port to a free one."
                ) from exc

    def _environment(self, **extra: str) -> dict[str, str]:
        env = {key: os.environ[key] for key in _INHERITED_ENV if key in os.environ}
        env["GBRAIN_HOME"] = str(self.gbrain_home)
        env.update(extra)
        return env

    async def _run_once(self, *args: str) -> str:
        """Run a one-off gbrain command; its failure or timeout fails the start."""
        process = await asyncio.create_subprocess_exec(
            str(self._executable),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._environment(),
            cwd=str(self.home),
        )
        try:
            out, err = await asyncio.wait_for(
                process.communicate(), timeout=self._gbrain.command_timeout_seconds
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise LocalMemoryError(
                f"`gbrain {args[0]}` did not finish within "
                f"local_memory.gbrain.command_timeout_seconds="
                f"{self._gbrain.command_timeout_seconds}; raise it if this machine is slow"
            ) from exc
        if process.returncode:
            detail = err.decode(errors="replace").strip() or out.decode(errors="replace")
            raise LocalMemoryError(
                f"`gbrain {args[0]}` exited {process.returncode} (GBRAIN_HOME="
                f"{self.gbrain_home}): {detail[-_LOG_TAIL_BYTES:]}"
            )
        return out.decode(errors="replace")

    async def _create_store_token(self) -> None:
        name = f"niuu-mimir-{secrets.token_hex(4)}"
        output = await self._run_once("auth", "create", name, "--scopes", "read,write")
        match = _TOKEN_LINE.search(output)
        if match is None:
            raise LocalMemoryError(
                "`gbrain auth create` did not print the token it created; this gbrain build "
                "is not the one niuu pins (scripts/build_gbrain.sh)"
            )
        descriptor = os.open(self.store_token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(match.group(1))

    async def _serve(self) -> None:
        admin_token = self.admin_token_file.read_text(encoding="utf-8").strip()
        log = self.log_file.open("ab")
        try:
            self._process = await asyncio.create_subprocess_exec(
                str(self._executable),
                "serve",
                "--http",
                "--bind",
                self._gbrain.host,
                "--port",
                str(self._gbrain.port),
                stdout=log,
                stderr=log,
                env=self._environment(GBRAIN_ADMIN_BOOTSTRAP_TOKEN=admin_token),
                cwd=str(self.home),
                # Its own process group: the terminal's Ctrl+C reaches the platform,
                # which then stops gbrain itself, in order.
                start_new_session=True,
            )
        finally:
            log.close()
        self._loop = asyncio.get_running_loop()
        self.pid_file.write_text(str(self._process.pid), encoding="utf-8")
        try:
            await self._wait_healthy()
        except BaseException:
            await self._terminate()
            raise
        self._stopping = False
        self._watcher = asyncio.create_task(self._watch(self._process))
        logger.info("Local memory brain ready at %s (log %s)", self.mcp_url, self.log_file)

    async def _wait_healthy(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._gbrain.startup_timeout_seconds
        while True:
            process = self._process
            if process is None or process.returncode is not None:
                code = None if process is None else process.returncode
                raise LocalMemoryError(
                    f"gbrain exited with code {code} while starting. Its log "
                    f"({self.log_file}) ends with:\n{self._log_tail()}"
                )
            if await self._answers_health():
                return
            if loop.time() >= deadline:
                raise LocalMemoryError(
                    f"gbrain did not answer {self.health_url} within "
                    f"local_memory.gbrain.startup_timeout_seconds="
                    f"{self._gbrain.startup_timeout_seconds}. Its log ({self.log_file}) "
                    f"ends with:\n{self._log_tail()}"
                )
            await asyncio.sleep(self._gbrain.health_poll_interval_seconds)

    async def _answers_health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self._gbrain.health_timeout_seconds) as client:
                response = await client.get(self.health_url)
        except httpx.HTTPError:
            return False
        return response.is_success

    def _log_tail(self) -> str:
        if not self.log_file.exists():
            return "(no log)"
        with self.log_file.open("rb") as handle:
            handle.seek(max(0, self.log_file.stat().st_size - _LOG_TAIL_BYTES))
            return handle.read().decode(errors="replace")

    async def _watch(self, process: asyncio.subprocess.Process) -> None:
        code = await process.wait()
        if self._stopping:
            return
        logger.error(
            "The local memory brain (gbrain, pid %s) exited with code %s while the platform "
            "runs: memory search, review and sessions' memory tools fail until "
            "`niuu platform up` is restarted. gbrain's log: %s",
            process.pid,
            code,
            self.log_file,
        )

    # ------------------------------------------------------------------
    # Stop and health
    # ------------------------------------------------------------------

    async def stop(self) -> None:
        await self._terminate()

    async def _terminate(self) -> None:
        self._stopping = True
        process = self._process
        if process is None:
            return
        if self._loop is not None and self._loop is not asyncio.get_running_loop():
            # Stopped from a later event loop (the shutdown after Ctrl+C), where the old
            # loop's process handle and watcher cannot be awaited: signal the pid directly.
            await self._terminate_pid(process.pid)
            self._watcher = None
        else:
            await self._terminate_process(process)
        self.pid_file.unlink(missing_ok=True)
        self._process = None

    async def _terminate_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is None:
            process.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), timeout=self._gbrain.stop_timeout_seconds)
            except TimeoutError:
                logger.error(
                    "gbrain (pid %s) did not exit within "
                    "local_memory.gbrain.stop_timeout_seconds=%s and was killed; its database "
                    "may need recovery on the next start",
                    process.pid,
                    self._gbrain.stop_timeout_seconds,
                )
                process.kill()
                await process.wait()
        if self._watcher is not None:
            self._watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watcher
            self._watcher = None

    async def _terminate_pid(self, pid: int) -> None:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._gbrain.stop_timeout_seconds
        while str(self._executable) in _command_line(pid):
            if loop.time() >= deadline:
                logger.error(
                    "gbrain (pid %s) did not exit within "
                    "local_memory.gbrain.stop_timeout_seconds=%s and was killed; its database "
                    "may need recovery on the next start",
                    pid,
                    self._gbrain.stop_timeout_seconds,
                )
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
                return
            await asyncio.sleep(self._gbrain.health_poll_interval_seconds)

    async def health_check(self) -> bool:
        if self._process is None or self._process.returncode is not None:
            return False
        return await self._answers_health()


def memory_environment(
    brain: LocalMemoryBrain,
    config: LocalMemoryConfig,
    *,
    forge_url: str,
    session_gateway_url: str,
) -> dict[str, str]:
    """The settings that point Mímir and Forge at the local memory brain.

    Mímir's store and the memory gateway both use this gbrain: the store with its own
    read/write token, the gateway with the admin token it creates each layer's source and
    client with. Forge gives every session a memory server at *session_gateway_url*.
    """
    system = {
        "name": config.system_name,
        "adapter": GBRAIN_MEMORY_ADAPTER,
        "mcp_url": brain.mcp_url,
        "admin_token_file": str(brain.admin_token_file),
        "signing_key_file": str(brain.signing_key_file),
    }
    return {
        "MIMIR__BACKEND": json.dumps(
            {
                "adapter": GBRAIN_STORE_ADAPTER,
                "mcp_url": brain.mcp_url,
                "api_token_file": str(brain.store_token_file),
                # Expansion asks a model to rewrite the query; this gbrain has none.
                "query_expansion": False,
            }
        ),
        "MIMIR__MEMORY": json.dumps(
            {
                "enabled": True,
                "forge_base_url": forge_url,
                "systems": [system],
                "tenants": {tenant: config.system_name for tenant in config.tenants},
                "service_auth": {"adapter": MEMORY_SERVICE_AUTH_ADAPTER},
            }
        ),
        "MEMORY__GATEWAY_URL": session_gateway_url,
    }
