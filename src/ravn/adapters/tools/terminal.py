"""Terminal tool — persistent shell state across agent tool calls.

A ``PersistentShell`` keeps a single bash/zsh process alive for the
duration of a task.  Each command is written to the process stdin; a
UUID sentinel echoed at the end marks where output stops.  This means
``cd``, ``export``, virtual-environment activations, and any other
shell-state changes made in one call are visible in the next.

Safety note: all commands must pass through the permission enforcer and
any bash validator *before* reaching this layer.  ``PersistentShell``
itself performs no safety checks.
"""

from __future__ import annotations
from contextlib import suppress

import asyncio
import logging
import shlex
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ravn.domain.models import ToolResult
from ravn.ports.tool import ToolPort

logger = logging.getLogger(__name__)

_PERMISSION_SHELL = "shell:execute"
_SENTINEL_PREFIX = "RAVN_DONE"
_DEFAULT_SHELL = "/bin/bash"
_DEFAULT_TIMEOUT_SECONDS = 30.0
_EOF_WAIT_TIMEOUT_SECONDS = 5.0


# ---------------------------------------------------------------------------
# Shell state (checkpoint / resume)
# ---------------------------------------------------------------------------


@dataclass
class ShellState:
    """Snapshot of a persistent shell's state for checkpoint/resume.

    ``cwd`` is the current working directory; ``env_exports`` is the
    output of ``export -p`` — the full set of exported variables in
    POSIX-portable syntax, suitable for re-sourcing into a fresh shell.
    """

    cwd: str = ""
    env_exports: str = field(default="", repr=False)


# ---------------------------------------------------------------------------
# Shared helpers — sentinel protocol, state capture, result building
# ---------------------------------------------------------------------------


async def run_sentinel_command(
    process: asyncio.subprocess.Process,
    command: str,
    timeout: float,
    label: str = "shell",
    eof_wait_timeout: float = _EOF_WAIT_TIMEOUT_SECONDS,
) -> tuple[str, int, bool]:
    """Execute *command* via *process* stdin using the sentinel demarcation protocol.

    Writes the command followed by ``echo SENTINEL:$?`` to stdin, then
    reads stdout line-by-line until the sentinel is found.

    Returns ``(output, exit_code, process_exited)`` where *process_exited*
    is ``True`` when the process closed stdout (EOF) or was killed on timeout.
    The caller must clear its process reference when *process_exited* is True.

    Args:
        process:          A running asyncio subprocess with open stdin/stdout.
        command:          Shell command to execute (cd/export effects persist).
        timeout:          Seconds before the command is killed (exit code 124).
        label:            Descriptive label used in timeout warning log messages.
        eof_wait_timeout: Seconds to wait for process exit after EOF is detected.
    """
    assert process.stdin is not None  # noqa: S101 — narrowing
    assert process.stdout is not None  # noqa: S101 — narrowing

    sentinel = f"{_SENTINEL_PREFIX}_{uuid.uuid4().hex}"
    # Do NOT wrap in a subshell — we want cd/export to persist.
    wrapped = f"{command}\necho {sentinel}:$?\n"

    process.stdin.write(wrapped.encode())
    await process.stdin.drain()

    output_lines: list[str] = []
    try:
        async with asyncio.timeout(timeout):
            while True:
                line_bytes = await process.stdout.readline()
                if not line_bytes:
                    # EOF — process exited (e.g. the command was "exit N").
                    with suppress(TimeoutError):
                        await asyncio.wait_for(process.wait(), timeout=eof_wait_timeout)
                    actual_rc = process.returncode if process.returncode is not None else 1
                    return "\n".join(output_lines), actual_rc, True
                line_str = line_bytes.decode(errors="replace").rstrip("\n")
                if line_str.startswith(f"{sentinel}:"):
                    exit_code_str = line_str[len(sentinel) + 1 :]
                    exit_code = int(exit_code_str) if exit_code_str.isdigit() else 1
                    break
                output_lines.append(line_str)
    except TimeoutError:
        logger.warning("%s timed out — killing to avoid output pollution", label)
        process.kill()
        await process.wait()
        return "\n".join(output_lines) + "\n[timed out]", 124, True

    return "\n".join(output_lines), exit_code, False


async def get_shell_state(
    run_fn: Callable[[str], Awaitable[tuple[str, int]]],
) -> ShellState:
    """Capture the current shell state (cwd + exported env vars).

    Args:
        run_fn: Bound ``run`` method of a shell (``PersistentShell.run`` or
                ``DockerPersistentShell.run``).
    """
    cwd, _ = await run_fn("pwd")
    env, _ = await run_fn("export -p")
    return ShellState(cwd=cwd.strip(), env_exports=env)


async def restore_shell_state(
    run_fn: Callable[[str], Awaitable[tuple[str, int]]],
    state: ShellState,
) -> None:
    """Restore a shell to a previously captured state.

    Re-sources environment exports and changes to the saved working directory.
    Errors (e.g. directory no longer exists) are silently ignored so the shell
    remains usable.

    Args:
        run_fn: Bound ``run`` method of a shell.
        state:  Previously captured :class:`ShellState`.
    """
    if state.env_exports:
        await run_fn(state.env_exports)
    if state.cwd:
        await run_fn(f"cd {shlex.quote(state.cwd)} 2>/dev/null || true")


def _build_result(output: str, exit_code: int) -> ToolResult:
    """Build a :class:`~ravn.domain.models.ToolResult` from shell output."""
    content = output.rstrip("\n")
    if exit_code != 0:
        suffix = f"\n[exit {exit_code}]"
        content = (content + suffix).strip()
    return ToolResult(tool_call_id="", content=content, is_error=exit_code != 0)


# ---------------------------------------------------------------------------
# PersistentShell
# ---------------------------------------------------------------------------


class PersistentShell:
    """Manages a single long-lived shell process with asyncio pipe I/O.

    Commands are written to stdin.  A UUID sentinel string is echoed
    after each command so the reader knows when output ends without
    closing the pipe.

    Usage::

        shell = PersistentShell()
        await shell.start()
        output, rc = await shell.run("cd /tmp && pwd")
        output2, rc2 = await shell.run("ls")  # still in /tmp
        await shell.close()

    Or as an async context manager::

        async with PersistentShell() as shell:
            output, rc = await shell.run("echo hello")
    """

    def __init__(
        self,
        shell: str = _DEFAULT_SHELL,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._shell = shell
        self._timeout = timeout_seconds
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """True if the shell process is alive."""
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        """Start the shell process (no-op if already running)."""
        if self.is_running:
            return
        self._process = await asyncio.create_subprocess_exec(
            self._shell,
            "--norc",
            "--noprofile",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        logger.debug("PersistentShell started pid=%s shell=%s", self._process.pid, self._shell)

    async def close(self) -> None:
        """Cleanly terminate the shell process."""
        if not self.is_running:
            return
        assert self._process is not None  # noqa: S101 — narrowing
        assert self._process.stdin is not None  # noqa: S101 — narrowing
        try:
            self._process.stdin.write(b"exit 0\n")
            await self._process.stdin.drain()
            await asyncio.wait_for(self._process.wait(), timeout=5.0)
        except Exception:
            self._process.kill()
            await self._process.wait()
        finally:
            self._process = None
            logger.debug("PersistentShell closed")

    async def __aenter__(self) -> PersistentShell:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    async def run(self, command: str) -> tuple[str, int]:
        """Run *command* in the persistent shell.

        Returns ``(output, exit_code)``.  Output is the combined
        stdout+stderr of the command.  On timeout the output is
        truncated and exit code 124 is returned (matching ``timeout(1)``
        convention).
        """
        if not self.is_running:
            await self.start()

        async with self._lock:
            return await self._run_locked(command)

    async def _run_locked(self, command: str) -> tuple[str, int]:
        assert self._process is not None  # noqa: S101 — narrowing
        output, exit_code, process_exited = await run_sentinel_command(
            self._process, command, self._timeout, label="PersistentShell"
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
        directory.  Errors (e.g. directory no longer exists) are
        silently ignored so the shell remains usable.
        """
        await restore_shell_state(self.run, state)


# ---------------------------------------------------------------------------
# TerminalTool
# ---------------------------------------------------------------------------


class TerminalTool(ToolPort):
    """Execute shell commands with persistent state across calls.

    When ``persistent_shell=True`` (the default), a single bash process
    is kept alive for the lifetime of this tool instance.  Working
    directory changes, environment exports, and venv activations made in
    one call are visible in all subsequent calls.

    When ``persistent_shell=False``, each call spawns a fresh
    subprocess — equivalent to the previous single-shot behaviour.

    Pass ``initial_state`` to resume from a previously checkpointed
    :class:`ShellState` (e.g., for task restart after interruption).
    """

    def __init__(
        self,
        shell: str = _DEFAULT_SHELL,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        persistent_shell: bool = True,
        initial_state: ShellState | None = None,
    ) -> None:
        self._shell_path = shell
        self._timeout = timeout_seconds
        self._persistent = persistent_shell
        self._initial_state = initial_state
        self._shell: PersistentShell | None = None

    # ------------------------------------------------------------------
    # ToolPort interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "terminal"

    @property
    def description(self) -> str:
        return (
            "Run a shell command and return its combined stdout/stderr output. "
            "Working directory, environment variables, and activated virtual "
            "environments persist across calls within the same task."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute.",
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

        if self._persistent:
            return await self._run_persistent(command)
        return await self._run_ephemeral(command)

    # ------------------------------------------------------------------
    # Execution backends
    # ------------------------------------------------------------------

    async def _run_persistent(self, command: str) -> ToolResult:
        if self._shell is None:
            self._shell = PersistentShell(
                shell=self._shell_path,
                timeout_seconds=self._timeout,
            )
            await self._shell.start()
            if self._initial_state is not None:
                await self._shell.restore_state(self._initial_state)

        output, exit_code = await self._shell.run(command)
        return _build_result(output, exit_code)

    async def _run_ephemeral(self, command: str) -> ToolResult:
        """Spawn a fresh subprocess per call (non-persistent fallback)."""
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
            except TimeoutError:
                proc.kill()
                await proc.communicate()
                return ToolResult(tool_call_id="", content="[timed out]", is_error=True)
            output = stdout.decode(errors="replace").rstrip("\n") if stdout else ""
            return _build_result(output, proc.returncode or 0)
        except OSError as exc:
            return ToolResult(tool_call_id="", content=f"Execution error: {exc}", is_error=True)

    # ------------------------------------------------------------------
    # State / cleanup
    # ------------------------------------------------------------------

    async def get_state(self) -> ShellState | None:
        """Return the current shell state for checkpointing.

        Returns ``None`` if the persistent shell has not been started
        yet (i.e. no commands have been executed in this session).
        """
        if self._shell is None or not self._shell.is_running:
            return None
        return await self._shell.get_state()

    async def close(self) -> None:
        """Cleanly shut down the persistent shell (if active)."""
        if self._shell is not None:
            await self._shell.close()
            self._shell = None
