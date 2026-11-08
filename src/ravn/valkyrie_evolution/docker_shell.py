"""A long-lived bash session inside an ephemeral Docker container.

The Docker-backed learned-tool runner (``ForgeSandboxLearnedToolRunner``) runs
tools through this shell. One container is started per shell; each command is
written to the container's stdin and a UUID sentinel echoed after it marks the
end of its output, so ``cd``, ``export`` and venv activations persist between
calls.

Sandboxing properties
---------------------
* **Network isolation** - the container's network is ``none`` unless
  :attr:`DockerShellConfig.network` says otherwise.
* **Filesystem isolation** - only the workspace directory is mounted
  read-write, plus any ``extra_mounts``.
* **Ephemeral** - the container is started with ``--rm`` so it is removed if
  the process exits unexpectedly, and is force-removed by :meth:`close`.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

SHELL = "/bin/bash"
#: Exit status of a command that timed out, matching ``timeout(1)``.
TIMEOUT_EXIT_CODE = 124


@dataclass(frozen=True)
class DockerShellConfig:
    """How the container behind a :class:`DockerPersistentShell` is started."""

    image: str
    #: Docker network mode: ``none`` (isolated), ``bridge`` or ``host``.
    network: str = "none"
    #: Mount the workspace directory read-write at the same path in the container.
    mount_workspace: bool = True
    #: Additional volume mounts in ``host:container`` form.
    extra_mounts: tuple[str, ...] = field(default_factory=tuple)


async def run_sentinel_command(
    process: asyncio.subprocess.Process,
    command: str,
    timeout: float,
    *,
    sentinel_prefix: str,
    eof_wait_timeout: float,
    label: str,
) -> tuple[str, int, bool]:
    """Execute *command* through *process* stdin and read until its sentinel.

    Writes the command followed by ``echo SENTINEL:$?`` to stdin, then reads
    stdout line by line until the sentinel is found.

    Returns ``(output, exit_code, process_exited)``. *process_exited* is True
    when the process closed stdout (EOF) or was killed on timeout; the caller
    must then drop its process reference.
    """
    assert process.stdin is not None  # noqa: S101 - narrowing
    assert process.stdout is not None  # noqa: S101 - narrowing

    sentinel = f"{sentinel_prefix}_{uuid.uuid4().hex}"
    # Not wrapped in a subshell: cd and export must persist between calls.
    process.stdin.write(f"{command}\necho {sentinel}:$?\n".encode())
    await process.stdin.drain()

    output_lines: list[str] = []
    try:
        async with asyncio.timeout(timeout):
            while True:
                line_bytes = await process.stdout.readline()
                if not line_bytes:
                    # EOF: the process exited (for example the command was "exit N").
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
        logger.warning("%s timed out - killing to avoid output pollution", label)
        process.kill()
        await process.wait()
        return "\n".join(output_lines) + "\n[timed out]", TIMEOUT_EXIT_CODE, True

    return "\n".join(output_lines), exit_code, False


class DockerPersistentShell:
    """Manages a long-lived bash session inside an ephemeral Docker container.

    The container is started on the first :meth:`run` (or by :meth:`start`) and
    destroyed by :meth:`close` or on timeout.

    Args:
        config: Image, network mode and mounts of the container.
        workspace_root: Host path mounted read-write at the same path inside the
            container when ``config.mount_workspace`` is set.
        timeout_seconds: Per-command timeout. A timed-out command returns exit
            code 124 and the container is killed so its output cannot pollute
            the next command's.
        sentinel_prefix: Prefix of the end-of-output marker echoed after each
            command.
        stop_timeout_seconds: How long :meth:`close` waits for the shell to exit
            before killing it.
        remove_timeout_seconds: How long :meth:`close` waits for ``docker rm``.
        eof_wait_timeout_seconds: How long to wait for the process to exit after
            it closed stdout.
    """

    def __init__(
        self,
        config: DockerShellConfig,
        workspace_root: Path,
        timeout_seconds: float,
        *,
        sentinel_prefix: str = "NIUU_DONE",
        stop_timeout_seconds: float = 5.0,
        remove_timeout_seconds: float = 10.0,
        eof_wait_timeout_seconds: float = 5.0,
    ) -> None:
        self._config = config
        self._workspace_root = workspace_root.resolve()
        self._timeout = timeout_seconds
        self._sentinel_prefix = sentinel_prefix
        self._stop_timeout = stop_timeout_seconds
        self._remove_timeout = remove_timeout_seconds
        self._eof_wait_timeout = eof_wait_timeout_seconds
        self._container_name: str | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        """True if the container process is alive."""
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        """Start the Docker container (no-op if already running)."""
        if self.is_running:
            return

        self._container_name = f"niuu-sandbox-{uuid.uuid4().hex[:12]}"
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

        cmd.extend([self._config.image, SHELL, "--norc", "--noprofile"])
        return cmd

    async def close(self) -> None:
        """Cleanly terminate the container and shell process."""
        if not self.is_running:
            await self._force_remove_container()
            return

        assert self._process is not None  # noqa: S101 - narrowing
        assert self._process.stdin is not None  # noqa: S101 - narrowing

        try:
            self._process.stdin.write(b"exit 0\n")
            await self._process.stdin.drain()
            await asyncio.wait_for(self._process.wait(), timeout=self._stop_timeout)
        except Exception:
            self._process.kill()
            await self._process.wait()
        finally:
            self._process = None
            await self._force_remove_container()
            logger.debug("DockerPersistentShell closed container=%s", self._container_name)

    async def _force_remove_container(self) -> None:
        """Remove the container by name; one that is already gone is not an error."""
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
            await asyncio.wait_for(proc.wait(), timeout=self._remove_timeout)
        except Exception:
            logger.debug("docker rm -f %s failed (container may already be gone)", name)

    async def __aenter__(self) -> DockerPersistentShell:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def run(self, command: str) -> tuple[str, int]:
        """Run *command* inside the Docker container.

        Returns ``(output, exit_code)``. On timeout the output is truncated at
        what had been read, ``[timed out]`` is appended, and the exit code is
        124.
        """
        if not self.is_running:
            await self.start()

        async with self._lock:
            return await self._run_locked(command)

    async def _run_locked(self, command: str) -> tuple[str, int]:
        assert self._process is not None  # noqa: S101 - narrowing
        output, exit_code, process_exited = await run_sentinel_command(
            self._process,
            command,
            self._timeout,
            sentinel_prefix=self._sentinel_prefix,
            eof_wait_timeout=self._eof_wait_timeout,
            label=f"DockerPersistentShell container={self._container_name}",
        )
        if process_exited:
            self._process = None
        return output, exit_code
