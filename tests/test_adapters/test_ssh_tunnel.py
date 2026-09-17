"""Tunnel ownership tests using explicit fake children and real local processes."""

import asyncio
import io
import os
import signal
import subprocess
import sys
from unittest.mock import Mock

import pytest

from volundr.adapters.outbound import ssh_tunnel


@pytest.mark.parametrize("mode", ["exited", "eof", "stuck"])
def test_supervisor_reaps_children_and_preserves_unexpected_exit(monkeypatch, mode):
    child = Mock()
    child.poll.return_value = 7 if mode == "exited" else None
    child.returncode = 7
    if mode == "stuck":
        child.wait.side_effect = [subprocess.TimeoutExpired("test", 0.1), 0]
    monkeypatch.setattr(ssh_tunnel.subprocess, "Popen", Mock(return_value=child))
    monkeypatch.setattr(ssh_tunnel.sys, "stdin", Mock(buffer=io.BytesIO(b"")))
    monkeypatch.setattr(ssh_tunnel.select, "select", lambda *args: ([sys.stdin], [], []))
    assert ssh_tunnel.supervise(["test-child"], poll_seconds=0.01, stop_timeout=0.1) == (
        7 if mode == "exited" else 0
    )
    if mode != "exited":
        child.terminate.assert_called_once()
        child.wait.assert_called()
    if mode == "stuck":
        child.kill.assert_called_once()


def test_termination_unwinds_cleanup():
    with pytest.raises(SystemExit) as exc:
        ssh_tunnel._terminate(signal.SIGTERM, None)
    assert exc.value.code == 128 + signal.SIGTERM


async def test_controller_sigkill_does_not_leave_tunnel_process():
    # A real parent owns the supervisor's input. The explicit test child mimics
    # SSH holding a listener, and ignores TERM to exercise bounded forced reap.
    child_code = (
        "import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "print(os.getpid(),flush=True); time.sleep(60)"
    )
    parent_code = (
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-m',"
        "'volundr.adapters.outbound.ssh_tunnel','0.01','0.05',"
        f"sys.executable,'-c',{child_code!r}],stdin=subprocess.PIPE); "
        "time.sleep(60)"
    )
    parent = await asyncio.create_subprocess_exec(
        sys.executable, "-c", parent_code, stdout=asyncio.subprocess.PIPE
    )
    child_pid = None
    try:
        child_pid = int(await asyncio.wait_for(parent.stdout.readline(), 5))
        parent.kill()
        # The supervisor also owns stdout. EOF proves it exits after reaping
        # its child even though the original parent never ran any cleanup.
        await asyncio.wait_for(parent.communicate(), 5)
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        if parent.returncode is None:
            parent.kill()
            await parent.wait()
        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
