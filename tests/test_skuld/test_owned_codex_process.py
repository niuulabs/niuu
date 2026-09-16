"""Ownership fences and a real broker-death subprocess regression; no provider access."""

import asyncio
import json
import os
import signal
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from skuld.transports import owned_codex_process as owned
from skuld.transports.codex_ws import CodexWebSocketTransport

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux process ownership")


@pytest.fixture
def lease(tmp_path, monkeypatch):
    manager = owned.OwnedCodexProcess("session-a", str(tmp_path), tmp_path / "state")
    monkeypatch.setattr(manager, "_boot_id", lambda: "this-boot")
    record = {
        "session_id": "session-a",
        "workspace": str(tmp_path),
        "boot_id": "this-boot",
        "owner": {"pid": 100, "start": "10"},
        "child": {"pid": 200, "start": "20"},
        "nonce": "owned-nonce",
        "command": ["codex", "app-server"],
        "wrapper_command": ["python", "wrapper"],
    }
    manager.path.parent.mkdir(parents=True)
    manager.path.write_text(json.dumps(record))
    child = {"pid": 200, "start": "20", "argv": record["command"], "workspace": str(tmp_path)}
    env = {
        "SKULD__SESSION__ID": "session-a",
        "SKULD__SESSION__WORKSPACE_DIR": str(tmp_path),
        "SKULD_CODEX_OWNER_NONCE": "owned-nonce",
    }
    signals = []
    alive = [True]
    monkeypatch.setattr(
        owned, "process_identity", lambda pid: child if pid == 200 and alive[0] else None
    )
    monkeypatch.setattr(owned, "_process_environment", lambda pid: env)
    monkeypatch.setattr(owned, "open_pidfd", lambda pid: os.open(os.devnull, os.O_RDONLY))

    def send(fd, sig):
        signals.append(sig)
        alive[0] = False

    monkeypatch.setattr(owned, "signal_pidfd", send)
    monkeypatch.setattr(owned, "pidfd_exited", lambda fd: not alive[0])
    return manager, record, child, env, signals, alive


async def test_verified_orphan_is_reaped_before_lease_reuse(lease):
    manager, _, _, _, signals, _ = lease
    await manager.acquire()
    assert signals == [signal.SIGTERM]
    assert not manager.path.exists()
    assert manager.lock_fd is not None
    manager.release()


@pytest.mark.parametrize(
    "corruption",
    [
        "owner_alive",
        "nonce",
        "workspace",
        "argv",
        "reused_pid",
        "foreign_session",
        "foreign_lease_workspace",
        "invalid_json",
    ],
)
async def test_unproven_ownership_never_signals(lease, monkeypatch, corruption):
    manager, record, child, env, signals, _ = lease
    if corruption == "owner_alive":
        monkeypatch.setattr(
            owned, "process_identity", lambda pid: record["owner"] if pid == 100 else child
        )
    elif corruption == "nonce":
        env["SKULD_CODEX_OWNER_NONCE"] = "another-owner"
    elif corruption == "workspace":
        child["workspace"] = "/unrelated/workspace"
    elif corruption == "argv":
        child["argv"] = ["user-server"]
    elif corruption == "reused_pid":
        child["start"] = "new-incarnation"
    elif corruption == "foreign_session":
        record["session_id"] = "unrelated-session"
    elif corruption == "foreign_lease_workspace":
        record["workspace"] = "/other"
    manager.path.write_text("{broken" if corruption == "invalid_json" else json.dumps(record))
    with pytest.raises(owned.OwnedProcessError):
        await manager.acquire()
    assert signals == []
    assert manager.lock_fd is None
    assert manager.path.exists()


@pytest.mark.parametrize("state", ["gone", "previous_boot", "owner_pid_reused"])
async def test_stale_lease_handling(lease, monkeypatch, state):
    manager, record, child, _, signals, alive = lease
    if state == "gone":
        alive[0] = False
    elif state == "previous_boot":
        record["boot_id"] = "previous-boot"
        manager.path.write_text(json.dumps(record))
    else:
        monkeypatch.setattr(
            owned,
            "process_identity",
            lambda pid: (
                {"pid": 100, "start": "reused"} if pid == 100 else child if alive[0] else None
            ),
        )
    await manager.acquire()
    assert signals == ([signal.SIGTERM] if state == "owner_pid_reused" else [])
    manager.release()


async def test_process_race_after_pidfd_open_is_rejected(lease, monkeypatch):
    manager, _, child, _, signals, _ = lease

    def anchor(pid):
        child["start"] = "different-after-open"
        return os.open(os.devnull, os.O_RDONLY)

    monkeypatch.setattr(owned, "open_pidfd", anchor)
    with pytest.raises(owned.OwnedProcessError, match="reused"):
        await manager.acquire()
    assert signals == []


@pytest.mark.parametrize("exits", [True, False])
async def test_bounded_escalation_only_signals_anchored_child(lease, monkeypatch, exits):
    manager, _, _, _, signals, alive = lease
    monkeypatch.setattr(owned.asyncio, "sleep", AsyncMock())

    def send(fd, sig):
        signals.append(sig)
        if sig == signal.SIGKILL and exits:
            alive[0] = False

    monkeypatch.setattr(owned, "signal_pidfd", send)
    if exits:
        await manager.acquire()
        manager.release()
    else:
        with pytest.raises(owned.OwnedProcessError, match="did not exit"):
            await manager.acquire()
    assert signals == [signal.SIGTERM, signal.SIGKILL]


async def test_live_session_file_lock_excludes_second_broker(tmp_path):
    first = owned.OwnedCodexProcess("same-session", str(tmp_path), tmp_path)
    second = owned.OwnedCodexProcess("same-session", str(tmp_path), tmp_path)
    await first.acquire()
    try:
        with pytest.raises(owned.OwnedProcessError, match="Another live broker"):
            await second.acquire()
        assert second.lock_fd is None
    finally:
        first.release()
    await second.acquire()
    second.release()


async def test_spawn_lease_preserves_arguments_environment_and_process_identity(
    tmp_path, monkeypatch
):
    manager = owned.OwnedCodexProcess("session-a", str(tmp_path), tmp_path)
    await manager.acquire()
    command = ["/bin/codex", "app-server", "--listen", "unix:///socket", "-c", 'key="a b"']
    env = {"OPENAI_API_KEY": "private-value", "CUSTOM_OPTION": "unchanged"}
    wrapper = manager.spawn_command(command, env)
    assert wrapper[4:] == command
    assert env["OPENAI_API_KEY"] == "private-value" and env["CUSTOM_OPTION"] == "unchanged"
    manager.record_child(os.getpid(), command, wrapper)
    record = json.loads(manager.path.read_text())
    assert record["child"]["pid"] == record["owner"]["pid"] == os.getpid()
    assert record["nonce"] == env["SKULD_CODEX_OWNER_NONCE"]
    assert "private-value" not in manager.path.read_text()
    assert manager.path.stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(owned, "process_identity", lambda pid: None)
    manager.release()
    assert not manager.path.exists()


async def test_ownership_failure_never_creates_fallback_conversation(tmp_path):
    transport = CodexWebSocketTransport(str(tmp_path), session_id="session-a")
    transport._spawn_app_server = AsyncMock(side_effect=owned.OwnedProcessError("live owner"))
    transport._start_fallback_transport = AsyncMock()
    with pytest.raises(owned.OwnedProcessError):
        await transport.start()
    transport._start_fallback_transport.assert_not_awaited()


def test_other_platforms_keep_existing_subprocess_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    transport = CodexWebSocketTransport(str(tmp_path), session_id="session-a")
    assert transport._process_owner is None


async def test_linux_parent_death_kills_native_child_without_provider(tmp_path):
    child_code = "import time; time.sleep(60)"
    parent_code = (
        "import os,subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-I',sys.argv[2],"
        "str(os.getpid()),sys.executable,'-c',sys.argv[1]], stdout=subprocess.DEVNULL); "
        "print(p.pid,flush=True); time.sleep(60)"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_code, child_code, owned.__file__],
        stdout=subprocess.PIPE,
        text=True,
        cwd=str(tmp_path),
    )
    child = None
    try:
        child_pid = int(await asyncio.to_thread(parent.stdout.readline))
        parent.stdout.close()
        async with asyncio.timeout(10):
            while True:
                child = owned.process_identity(child_pid)
                if child and child["argv"] == [sys.executable, "-c", child_code]:
                    break
                await asyncio.sleep(0.05)
        parent.kill()
        await asyncio.to_thread(parent.wait)
        async with asyncio.timeout(5):
            while owned.process_identity(child_pid) is not None:
                await asyncio.sleep(0.05)
    finally:
        parent.stdout.close()
        if parent.poll() is None:
            parent.kill()
            await asyncio.to_thread(parent.wait)
        if child and owned.process_identity(child["pid"]) == child:
            fd = owned.open_pidfd(child["pid"])
            try:
                owned.signal_pidfd(fd, signal.SIGKILL)
            finally:
                os.close(fd)


def test_parent_died_before_wrapper_cannot_spawn_command(tmp_path):
    marker = tmp_path / "should-not-exist"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            owned.__file__,
            "0",
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(marker)!r}).touch()",
        ],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode != 0 and not marker.exists()
    assert b"Broker exited before Codex parent-death protection was installed" in result.stderr
    assert b"RuntimeWarning" not in result.stderr


async def test_real_owned_orphan_releases_writer_and_cleans_lease(tmp_path):
    manager = owned.OwnedCodexProcess("canary-only", str(tmp_path), tmp_path)
    await manager.acquire()
    command = [sys.executable, "-c", "import time; time.sleep(60)"]
    env = dict(os.environ)
    wrapper = manager.spawn_command(command, env)
    # An intentionally unwrapped test-owned child models an orphan from a prior process.
    process = await asyncio.create_subprocess_exec(*command, cwd=tmp_path, env=env)
    try:
        manager.record_child(process.pid, command, wrapper)
        record = json.loads(manager.path.read_text())
        record["owner"] = {"pid": 2147483647, "start": "gone"}
        manager.path.write_text(json.dumps(record))
        manager.release()
        replacement = owned.OwnedCodexProcess("canary-only", str(tmp_path), tmp_path)
        await replacement.acquire()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
            assert process.returncode == -signal.SIGTERM
            assert not replacement.path.exists()
        finally:
            replacement.release()
    finally:
        manager.release()
        if process.returncode is None:
            process.kill()
            await process.wait()


async def test_lease_filesystem_failure_is_explicit_and_cannot_start_fallback(
    tmp_path, monkeypatch
):
    manager = owned.OwnedCodexProcess("session-a", str(tmp_path), tmp_path)
    monkeypatch.setattr(owned.os, "open", lambda *a, **kw: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(owned.OwnedProcessError, match="Cannot acquire"):
        await manager.acquire()
    assert manager.lock_fd is None


@pytest.mark.parametrize("failure", [ProcessLookupError(), RuntimeError("stop failed")])
async def test_stop_releases_lease_even_when_process_exit_races(tmp_path, monkeypatch, failure):
    transport = CodexWebSocketTransport(str(tmp_path))
    transport._process = MagicMock()
    transport._process_owner = MagicMock()
    monkeypatch.setattr("skuld.transports.codex_ws._stop_process", AsyncMock(side_effect=failure))
    if isinstance(failure, ProcessLookupError):
        await transport.stop()
        assert transport._process is None
    else:
        with pytest.raises(RuntimeError, match="stop failed"):
            await transport.stop()
    transport._process_owner.release.assert_called_once()
