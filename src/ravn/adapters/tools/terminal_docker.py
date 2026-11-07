"""Docker-sandboxed terminal backend.

Runs commands inside an ephemeral Docker container rather than the host
shell.  A single container is started per task; shell state (cwd,
environment variables) persists across calls within that container.
The container is destroyed cleanly when :meth:`DockerTerminalTool.close`
is called or on interruption.

Sandboxing properties
---------------------
* **Network isolation** — the container's network is ``none`` by default
  (no outbound or inbound traffic).  Set ``network`` to ``"bridge"`` or
  ``"host"`` in :class:`~ravn.config.DockerTerminalConfig` to relax this.
* **Filesystem isolation** — only the workspace directory is mounted
  read-write.  The host filesystem outside the workspace is not visible
  unless ``extra_mounts`` are configured.
* **Ephemeral** — the container is started with ``--rm`` so it is removed
  automatically if the process exits unexpectedly.

Usage::

    tool = DockerTerminalTool(config=DockerTerminalConfig(), workspace_root=Path("."))
    result = await tool.execute({"command": "echo hello"})
    await tool.close()

Or wire via :class:`~ravn.config.TerminalToolConfig` with ``backend="docker"``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from niuu.domain.tool import ToolResult
from niuu.ports.tool import ToolPort
from ravn.adapters.tools.terminal import (
    _DEFAULT_SHELL,
    _DEFAULT_TIMEOUT_SECONDS,
    _PERMISSION_SHELL,
    _SENTINEL_PREFIX,
    ShellState,
    _build_result,
    get_shell_state,
    restore_shell_state,
    run_sentinel_command,
)
from ravn.config import DockerTerminalConfig

logger = logging.getLogger(__name__)

_CONTAINER_STOP_TIMEOUT_SECONDS = 5.0
_CONTAINER_REMOVE_TIMEOUT_SECONDS = 10.0

# Re-export shared symbols so existing imports from this module keep working.
__all__ = [
    "DockerPersistentShell",
    "DockerTerminalTool",
    "ShellState",
    "_DEFAULT_SHELL",
    "_DEFAULT_TIMEOUT_SECONDS",
    "_PERMISSION_SHELL",
    "_SENTINEL_PREFIX",
]


# ---------------------------------------------------------------------------
# DockerPersistentShell
# ---------------------------------------------------------------------------


class DockerPersistentShell:
    """Manages a long-lived bash session inside an ephemeral Docker container.

    A single container is started on the first :meth:`run` call.  Commands
    are written to the container's stdin pipe; a UUID sentinel echoed after
    each command marks the end of output.  This preserves ``cd``, ``export``,
    and venv activations across calls — identical semantics to
    :class:`~ravn.adapters.tools.terminal.PersistentShell`.

    The container is destroyed when :meth:`close` is called (or on timeout).

    Args:
        config:           Docker configuration (image, network, mounts).
        workspace_root:   Host path mounted read-write at the same path
                          inside the container when ``mount_workspace=True``.
        timeout_seconds:  Per-command timeout.
    """

    def __init__(
        self,
        config: DockerTerminalConfig,
        workspace_root: Path,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._config = config
        self._workspace_root = workspace_root.resolve()
        self._timeout = timeout_seconds
        self._container_name: str | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """True if the container process is alive."""
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        """Start the Docker container (no-op if already running)."""
        if self.is_running:
            return

        self._container_name = f"ravn-sandbox-{uuid.uuid4().hex[:12]}"
        cmd = self._build_docker_cmd()

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        logger.debug(
            "DockerPersistentShell started container=%s image=%s pid=%s",
            self._container_name,
            self._config.image,
            self._process.pid,
        )

    def _build_docker_cmd(self) -> list[str]:
        """Build the ``docker run`` command list."""
        cmd = [
            "docker",
            "run",
            "--rm",
            "--interactive",
            "--name",
            self._container_name,  # type: ignore[list-item]
            f"--network={self._config.network}",
        ]

        if self._config.mount_workspace:
            ws = str(self._workspace_root)
            cmd.extend(["-v", f"{ws}:{ws}", "-w", ws])

        for mount in self._config.extra_mounts:
            cmd.extend(["-v", mount])

        cmd.extend([self._config.image, _DEFAULT_SHELL, "--norc", "--noprofile"])
        return cmd

    async def close(self) -> None:
        """Cleanly terminate the container and shell process."""
        if not self.is_running:
            await self._force_remove_container()
            return

        assert self._process is not None  # noqa: S101 — narrowing
        assert self._process.stdin is not None  # noqa: S101 — narrowing

        try:
            self._process.stdin.write(b"exit 0\n")
            await self._process.stdin.drain()
            await asyncio.wait_for(self._process.wait(), timeout=_CONTAINER_STOP_TIMEOUT_SECONDS)
        except Exception:
            self._process.kill()
            await self._process.wait()
        finally:
            self._process = None
            await self._force_remove_container()
            logger.debug("DockerPersistentShell closed container=%s", self._container_name)

    async def _force_remove_container(self) -> None:
        """Remove the container by name, ignoring errors (already gone is fine)."""
        if not self._container_name:
            return
        name = self._container_name
        self._container_name = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "rm",
                "-f",
                name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=_CONTAINER_REMOVE_TIMEOUT_SECONDS)
        except Exception:
            logger.debug("docker rm -f %s failed (container may already be gone)", name)

    async def __aenter__(self) -> DockerPersistentShell:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    async def run(self, command: str) -> tuple[str, int]:
        """Run *command* inside the Docker container.

        Returns ``(output, exit_code)``.  On timeout, output is truncated
        and exit code 124 is returned (matching ``timeout(1)`` convention).
        The container is killed on timeout to avoid output pollution.
        """
        if not self.is_running:
            await self.start()

        async with self._lock:
            return await self._run_locked(command)

    async def _run_locked(self, command: str) -> tuple[str, int]:
        assert self._process is not None  # noqa: S101 — narrowing
        label = f"DockerPersistentShell container={self._container_name}"
        output, exit_code, process_exited = await run_sentinel_command(
            self._process, command, self._timeout, label=label
        )
        if process_exited:
            self._process = None
        return output, exit_code

    # ------------------------------------------------------------------
    # State capture / restore
    # ------------------------------------------------------------------

    async def get_state(self) -> ShellState:
        """Capture the current shell state (cwd + exported env vars)."""
        return await get_shell_state(self.run)

    async def restore_state(self, state: ShellState) -> None:
        """Restore shell to a previously captured state.

        Re-sources environment exports and changes to the saved working
        directory.  Errors are silently ignored so the shell remains usable.
        """
        await restore_shell_state(self.run, state)


# ---------------------------------------------------------------------------
# DockerTerminalTool
# ---------------------------------------------------------------------------


class DockerTerminalTool(ToolPort):
    """Execute shell commands inside an ephemeral Docker container.

    Implements the same :class:`~niuu.ports.tool.ToolPort` interface as
    :class:`~ravn.adapters.tools.terminal.TerminalTool` — the tool registry
    treats them identically; backend selection is done by configuration.

    One container is started per task (on first ``execute`` call).  Shell
    state — working directory, environment variables, activated venvs —
    persists across calls within the container.

    Pass ``initial_state`` to resume from a previously checkpointed
    :class:`ShellState` (e.g. for task restart after interruption).

    Args:
        config:         :class:`~ravn.config.DockerTerminalConfig` instance.
        workspace_root: Host path to mount read-write inside the container.
        timeout_seconds: Per-command execution timeout.
        initial_state:  Optional shell state to restore on first call.
    """

    def __init__(
        self,
        config: DockerTerminalConfig | None = None,
        workspace_root: Path | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        initial_state: ShellState | None = None,
    ) -> None:
        self._config = config or DockerTerminalConfig()
        self._workspace_root = (workspace_root or Path.cwd()).resolve()
        self._timeout = timeout_seconds
        self._initial_state = initial_state
        self._shell: DockerPersistentShell | None = None

    # ------------------------------------------------------------------
    # ToolPort interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "terminal"

    @property
    def description(self) -> str:
        return (
            "Run a shell command inside an isolated Docker container and return its "
            "combined stdout/stderr output. "
            "Working directory, environment variables, and activated virtual "
            "environments persist across calls within the same task. "
            "The container is destroyed cleanly when the task ends."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute inside the Docker sandbox.",
                },
            },
            "required": ["command"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_SHELL

    @property
    def parallelisable(self) -> bool:
        return False

    async def execute(self, input: dict) -> ToolResult:
        command = input.get("command", "").strip()
        if not command:
            return ToolResult(tool_call_id="", content="No command provided.", is_error=True)

        if self._shell is None:
            self._shell = DockerPersistentShell(
                config=self._config,
                workspace_root=self._workspace_root,
                timeout_seconds=self._timeout,
            )
            await self._shell.start()
            if self._initial_state is not None:
                await self._shell.restore_state(self._initial_state)

        output, exit_code = await self._shell.run(command)
        return _build_result(output, exit_code)

    # ------------------------------------------------------------------
    # State / cleanup
    # ------------------------------------------------------------------

    async def get_state(self) -> ShellState | None:
        """Return the current shell state for checkpointing.

        Returns ``None`` if the container has not been started yet.
        """
        if self._shell is None or not self._shell.is_running:
            return None
        return await self._shell.get_state()

    async def close(self) -> None:
        """Destroy the Docker container and clean up resources."""
        if self._shell is not None:
            await self._shell.close()
            self._shell = None
