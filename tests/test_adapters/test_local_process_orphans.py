"""LEX-1858: stopping a session ends what its broker leaves behind, each survivor logged."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

from volundr.adapters.outbound import local_process
from volundr.adapters.outbound.local_process import (
    LocalProcessPodManager,
    ProcessInfo,
    ProcessState,
    session_runtime_pids,
)
from volundr.domain.models import GitSource, Session

SID = "39bd5e2b-1d56-48ae-816b-f78a3aea9c51"


def fake_proc(root: Path, processes: dict[int, tuple[list[str], dict[str, str]]]) -> Path:
    for pid, (argv, env) in processes.items():
        entry = root / str(pid)
        entry.mkdir(parents=True)
        (entry / "cmdline").write_bytes(b"\0".join(arg.encode() for arg in argv) + b"\0")
        (entry / "environ").write_bytes(
            b"\0".join(f"{k}={v}".encode() for k, v in env.items()) + b"\0"
        )
    return root


def test_runtime_processes_are_found_by_the_session_id_in_their_environment(tmp_path) -> None:
    ours = {"HOME": "/h", "SKULD__SESSION__ID": SID}
    proc = fake_proc(
        tmp_path,
        {
            101: (["tmux", "-S", f"/tmp/skuld-tmux-1/skuld-{SID}.sock", "new-session"], ours),
            102: (["claude", "--resume"], ours),
            # An operator reading the pane log names the session but is not its runtime.
            103: (["tail", "-f", f"/w/.skuld/tmux-interactive/skuld-{SID}/panes/0.log"], {}),
            104: (["claude"], {"SKULD__SESSION__ID": str(uuid4())}),
            105: (["claude"], {"SKULD__SESSION__ID": SID + "0"}),  # a prefix is not a match
            os.getpid(): (["python"], ours),  # never this process
        },
    )

    found = session_runtime_pids(SID, proc=proc)

    assert found == {
        101: f"tmux -S /tmp/skuld-tmux-1/skuld-{SID}.sock new-session",
        102: "claude --resume",
    }


def manager(tmp_path: Path) -> LocalProcessPodManager:
    return LocalProcessPodManager(
        workspaces_dir=str(tmp_path / "w"),
        state_file=str(tmp_path / "state.json"),
        tmux_socket_dir=str(tmp_path / "tmux"),
    )


def session() -> Session:
    return Session(
        id=uuid4(), name="s", source=GitSource(repo="https://example.com/r.git", branch="main")
    )


async def test_stopping_ends_the_runtime_and_logs_each_survivor(tmp_path, monkeypatch, caplog):
    caplog.set_level("WARNING", logger="volundr.adapters.outbound.local_process")
    pods = manager(tmp_path)
    s = session()
    pods._processes[str(s.id)] = ProcessInfo(
        session_id=str(s.id), pid=4242, state=ProcessState.RUNNING
    )
    terminated: list[int] = []
    pods._terminate_process = AsyncMock(side_effect=lambda pid: terminated.append(pid))
    monkeypatch.setattr(
        local_process, "session_runtime_pids", lambda sid: {777: f"claude skuld-{sid}"}
    )

    assert await pods.stop(s) is True

    assert terminated == [4242, 777]  # the broker, then what it left running
    assert f"Session {s.id}: stopping a process its broker left running: pid=777" in caplog.text


async def test_a_stopped_session_still_has_its_leftover_runtime_ended(tmp_path, monkeypatch):
    pods = manager(tmp_path)
    s = session()
    pods._processes[str(s.id)] = ProcessInfo(session_id=str(s.id), state=ProcessState.STOPPED)
    terminated: list[int] = []
    pods._terminate_process = AsyncMock(side_effect=lambda pid: terminated.append(pid))
    monkeypatch.setattr(local_process, "session_runtime_pids", lambda sid: {888: "tmux"})

    assert await pods.stop(s) is True

    assert terminated == [888]


async def test_an_unknown_session_is_left_to_whichever_forge_owns_it(tmp_path, monkeypatch):
    pods = manager(tmp_path)
    found = AsyncMock()
    monkeypatch.setattr(local_process, "session_runtime_pids", found)

    assert await pods.stop(session()) is False

    found.assert_not_called()


def fake_tmux(tmp_path: Path, monkeypatch, script: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tmux = bin_dir / "tmux"
    tmux.write_text(f"#!/bin/sh\n{script}\n")
    tmux.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))


async def socket_of(pods: LocalProcessPodManager, sid: str) -> Path:
    socket = pods._tmux_socket_dir / f"skuld-{sid}.sock"
    socket.parent.mkdir(parents=True, exist_ok=True)
    socket.touch()
    return socket


async def test_a_failed_tmux_kill_is_an_error_and_the_stop_completes(tmp_path, monkeypatch, caplog):
    caplog.set_level("ERROR", logger="volundr.adapters.outbound.local_process")
    pods = manager(tmp_path)
    s = session()
    pods._processes[str(s.id)] = ProcessInfo(session_id=str(s.id), state=ProcessState.STOPPED)
    await socket_of(pods, str(s.id))
    fake_tmux(tmp_path, monkeypatch, "echo 'no server running' >&2; exit 1")
    monkeypatch.setattr(local_process, "session_runtime_pids", lambda sid: {})

    assert await pods.stop(s) is True

    assert "tmux kill-server" in caplog.text and "failed (exit 1): no server running" in caplog.text


async def test_a_hung_tmux_kill_is_bounded_by_stop_timeout(tmp_path, monkeypatch, caplog):
    caplog.set_level("ERROR", logger="volundr.adapters.outbound.local_process")
    pods = manager(tmp_path)
    pods._stop_timeout = 1
    s = session()
    pods._processes[str(s.id)] = ProcessInfo(session_id=str(s.id), state=ProcessState.STOPPED)
    await socket_of(pods, str(s.id))
    fake_tmux(tmp_path, monkeypatch, f"exec {shutil.which('sleep')} 60")
    monkeypatch.setattr(local_process, "session_runtime_pids", lambda sid: {})

    assert await pods.stop(s) is True

    assert "did not answer within stop_timeout=1s" in caplog.text


async def test_a_running_tmux_server_is_killed_on_its_socket(tmp_path, monkeypatch):
    pods = manager(tmp_path)
    s = session()
    pods._processes[str(s.id)] = ProcessInfo(session_id=str(s.id), state=ProcessState.STOPPED)
    socket = await socket_of(pods, str(s.id))
    calls = tmp_path / "calls"
    fake_tmux(tmp_path, monkeypatch, f'echo "$@" > {calls}')
    monkeypatch.setattr(local_process, "session_runtime_pids", lambda sid: {})

    assert await pods.stop(s) is True

    assert calls.read_text().strip() == f"-S {socket} kill-server"


async def test_slots_are_listed_for_live_running_sessions(tmp_path, monkeypatch):
    pods = manager(tmp_path)
    pods._processes["a"] = ProcessInfo(session_id="a", pid=os.getpid(), state=ProcessState.RUNNING)
    pods._processes["b"] = ProcessInfo(session_id="b", state=ProcessState.STOPPED)

    assert await pods.active_session_ids() == ["a"]
