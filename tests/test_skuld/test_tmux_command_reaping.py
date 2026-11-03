"""A tmux command cancelled mid-flight (the transport stopping) never leaks its child.

An unreaped child keeps its subprocess transport open past the event loop, which
surfaces later as an unclosed-transport ResourceWarning in an unrelated test.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from skuld.transports.tmux_interactive import TmuxInteractiveTransport, _reap


@pytest.fixture
def slow_tmux(tmp_path):
    script = tmp_path / "tmux"
    script.write_text(f"#!/bin/sh\necho $$ > {tmp_path / 'pid'}\nexec sleep 30\n")
    script.chmod(0o755)
    return SimpleNamespace(
        _tmux_bin=lambda: str(script),
        _socket_path=tmp_path / "socket",
        pid_file=tmp_path / "pid",
    )


async def _cancel_once_started(coro, pid_file) -> None:
    task = asyncio.create_task(coro)
    for _ in range(200):
        if pid_file.exists() and pid_file.read_text().strip():
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def test_a_cancelled_command_reaps_its_child(slow_tmux):
    run = TmuxInteractiveTransport._run_tmux(slow_tmux, "list-panes")
    await _cancel_once_started(run, slow_tmux.pid_file)
    assert not _is_running(int(slow_tmux.pid_file.read_text()))


async def test_a_cancelled_liveness_probe_reaps_its_child(slow_tmux):
    probe = TmuxInteractiveTransport._socket_server_is_live(slow_tmux, slow_tmux._socket_path)
    await _cancel_once_started(probe, slow_tmux.pid_file)
    assert not _is_running(int(slow_tmux.pid_file.read_text()))


async def test_reaping_a_finished_process_only_waits():
    process = await asyncio.create_subprocess_exec("true")
    await process.wait()
    await _reap(process)
    assert process.returncode == 0
