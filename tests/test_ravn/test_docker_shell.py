"""The Docker-backed persistent shell the learned-tool sandbox runner drives.

``asyncio.create_subprocess_exec`` is patched, so Docker is not required: the
fake process answers a command with the sentinel line the shell waits for.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from ravn.valkyrie_evolution.docker_shell import (
    TIMEOUT_EXIT_CODE,
    DockerPersistentShell,
    DockerShellConfig,
    run_sentinel_command,
)

WORKSPACE = Path("/workspace")
PREFIX = "TEST_DONE"


class _Stdin:
    def __init__(self) -> None:
        self.written = bytearray()

    def write(self, data: bytes) -> None:
        self.written.extend(data)

    async def drain(self) -> None:
        return None


class _Stdout:
    """Serves the output lines of one command, then the sentinel built from its stdin."""

    def __init__(self, stdin: _Stdin, output: list[bytes], exit_code: int | None) -> None:
        self._stdin = stdin
        self._output = list(output)
        self._exit_code = exit_code

    async def readline(self) -> bytes:
        if self._output:
            return self._output.pop(0)
        if self._exit_code is None:
            return b""
        sentinel = next(
            line.removeprefix("echo ").removesuffix(":$?")
            for line in self._stdin.written.decode().splitlines()
            if line.startswith(f"echo {PREFIX}_")
        )
        self._exit_code, code = None, self._exit_code
        return f"{sentinel}:{code}\n".encode()


class _Hang:
    async def readline(self) -> bytes:
        await asyncio.sleep(3600)
        return b""


def _process(output: list[bytes], exit_code: int | None = 0, *, hang: bool = False) -> MagicMock:
    proc = MagicMock()
    proc.pid = 4242
    proc.returncode = None
    proc.stdin = _Stdin()
    proc.stdout = _Hang() if hang else _Stdout(proc.stdin, output, exit_code)

    async def _wait() -> int:
        if proc.returncode is None:
            proc.returncode = 0
        return proc.returncode

    proc.wait = _wait
    proc.kill = MagicMock(side_effect=lambda: setattr(proc, "returncode", -9))
    return proc


async def _sentinel(proc: MagicMock, command: str, timeout: float = 5.0):
    return await run_sentinel_command(
        proc,
        command,
        timeout,
        sentinel_prefix=PREFIX,
        eof_wait_timeout=0.1,
        label="test",
    )


class TestSentinelProtocol:
    async def test_returns_output_and_exit_code(self) -> None:
        proc = _process([b"hello\n", b"world\n"], exit_code=3)

        output, exit_code, exited = await _sentinel(proc, "echo hi")

        assert (output, exit_code, exited) == ("hello\nworld", 3, False)
        assert proc.stdin.written.decode().startswith("echo hi\necho TEST_DONE_")

    async def test_eof_means_the_process_exited(self) -> None:
        proc = _process([b"bye\n"], exit_code=None)

        output, exit_code, exited = await _sentinel(proc, "exit 0")

        assert output == "bye"
        assert exited is True
        assert exit_code == 0

    async def test_timeout_kills_the_process_and_says_so(self) -> None:
        proc = _process([], hang=True)

        output, exit_code, exited = await _sentinel(proc, "sleep 99", timeout=0.05)

        assert exit_code == TIMEOUT_EXIT_CODE
        assert output.endswith("[timed out]")
        assert exited is True
        proc.kill.assert_called_once()


class TestDockerPersistentShell:
    def _command(self, config: DockerShellConfig) -> list[str]:
        shell = DockerPersistentShell(config, WORKSPACE, 5.0)
        shell._container_name = "box"
        return shell._build_docker_cmd()

    def test_command_scopes_network_workspace_and_mounts(self) -> None:
        command = self._command(
            DockerShellConfig(image="img:1", network="bridge", extra_mounts=("/a:/b",))
        )

        assert command[:3] == ["docker", "run", "--rm"]
        assert "--network=bridge" in command
        assert command[command.index("-v") + 1] == "/workspace:/workspace"
        assert ["-v", "/a:/b"] == command[command.index("/a:/b") - 1 : command.index("/a:/b") + 1]
        assert command[-4:] == ["img:1", "/bin/bash", "--norc", "--noprofile"]

    def test_command_without_a_workspace_mount(self) -> None:
        command = self._command(DockerShellConfig(image="img:1", mount_workspace=False))

        assert "-w" not in command
        assert "--network=none" in command

    async def test_run_starts_the_container_once_and_returns_the_command_result(self) -> None:
        shell = DockerPersistentShell(
            DockerShellConfig(image="img:1"), WORKSPACE, 5.0, sentinel_prefix=PREFIX
        )
        proc = _process([b"ok\n"], exit_code=0)

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn:
            spawn.return_value = proc
            output, exit_code = await shell.run("echo ok")
            await shell.start()

        assert (output, exit_code) == ("ok", 0)
        assert spawn.call_count == 1
        assert shell.is_running

    async def test_run_after_the_process_exited_starts_a_new_container(self) -> None:
        shell = DockerPersistentShell(
            DockerShellConfig(image="img:1"), WORKSPACE, 5.0, sentinel_prefix=PREFIX
        )
        first = _process([], exit_code=None)
        second = _process([b"again\n"], exit_code=0)

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn:
            spawn.side_effect = [first, second]
            await shell.run("exit 0")
            output, _ = await shell.run("echo again")

        assert output == "again"
        assert spawn.call_count == 2

    async def test_close_removes_the_container(self) -> None:
        shell = DockerPersistentShell(
            DockerShellConfig(image="img:1"), WORKSPACE, 5.0, sentinel_prefix=PREFIX
        )
        proc = _process([])
        remover = _process([])

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn:
            spawn.side_effect = [proc, remover]
            async with shell:
                assert shell.is_running

        assert not shell.is_running
        assert spawn.call_args_list[1].args[:3] == ("docker", "rm", "-f")
        assert b"exit 0\n" in bytes(proc.stdin.written)

    async def test_close_before_start_is_a_no_op(self) -> None:
        shell = DockerPersistentShell(
            DockerShellConfig(image="img:1"), WORKSPACE, 5.0, sentinel_prefix=PREFIX
        )

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn:
            await shell.close()

        spawn.assert_not_called()

    async def test_a_container_that_will_not_stop_is_killed(self) -> None:
        shell = DockerPersistentShell(
            DockerShellConfig(image="img:1"), WORKSPACE, 5.0, stop_timeout_seconds=0.01
        )
        proc = _process([])
        waits = 0

        async def _wait_until_killed() -> int:
            nonlocal waits
            waits += 1
            if waits == 1:
                await asyncio.sleep(3600)
            return 0

        proc.wait = _wait_until_killed

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn:
            spawn.side_effect = [proc, _process([])]
            await shell.start()
            await shell.close()

        proc.kill.assert_called_once()
        assert not shell.is_running
