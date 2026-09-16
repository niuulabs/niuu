"""Linux ownership leases and parent-death protection for Codex app-server.

Never discover processes by name: only a recorded PID/start identity with the
exact session, workspace, command and nonce may be reaped. Other platforms keep
the transport's ordinary subprocess lifecycle without speculative PID signals.
"""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import os
import select
import signal
import sys
from pathlib import Path


class OwnedProcessError(RuntimeError):
    """The prior native writer cannot safely be reclaimed."""


def process_identity(pid: int) -> dict | None:
    try:
        path = Path(f"/proc/{pid}")
        fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        identity = {
            "pid": pid,
            "start": fields[19],
            "argv": (path / "cmdline").read_bytes().rstrip(b"\0").decode().split("\0"),
            "workspace": str((path / "cwd").resolve(strict=True)),
        }
        current = (path / "stat").read_text().rsplit(")", 1)[1].split()
        if current[0] == "Z":
            return None
        if current[19] != fields[19]:
            raise OwnedProcessError("Process incarnation changed during identity inspection")
        return identity
    except FileNotFoundError:
        return None


def _process_environment(pid: int) -> dict[str, str]:
    raw = Path(f"/proc/{pid}/environ").read_bytes()
    return {
        key.decode(): value.decode()
        for item in raw.split(b"\0")
        if b"=" in item
        for key, value in [item.split(b"=", 1)]
        if key
        in {
            b"SKULD__SESSION__ID",
            b"SKULD__SESSION__WORKSPACE_DIR",
            b"SKULD_CODEX_OWNER_NONCE",
        }
    }


def open_pidfd(pid: int) -> int:
    # Some supported Python builds lack os.pidfd_open, although glibc supports it.
    libc = ctypes.CDLL(None, use_errno=True)
    call = libc.pidfd_open
    call.argtypes = [ctypes.c_int, ctypes.c_uint]
    call.restype = ctypes.c_int
    fd = call(pid, 0)
    if fd < 0:
        raise OSError(ctypes.get_errno(), "Could not anchor the recorded Codex process")
    return fd


def signal_pidfd(fd: int, sig: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    call = libc.pidfd_send_signal
    call.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
    call.restype = ctypes.c_int
    if call(fd, sig, None, 0) < 0:
        raise OSError(ctypes.get_errno(), "Could not signal the recorded Codex process")


def pidfd_exited(fd: int) -> bool:
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    return bool(poller.poll(0))


class OwnedCodexProcess:
    def __init__(self, session_id: str, workspace: str, state_dir: Path) -> None:
        self.session_id = session_id
        self.workspace = str(Path(workspace).resolve())
        key = hashlib.sha256(session_id.encode()).hexdigest()
        self.path = state_dir / "codex-processes" / f"{key}.json"
        self.lock_fd: int | None = None
        self.nonce = os.urandom(24).hex()
        self.record: dict | None = None

    def _boot_id(self) -> str:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()

    async def acquire(self) -> None:
        import fcntl

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.lock_fd = os.open(self.path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.release()
            raise OwnedProcessError("Another live broker owns this Codex session") from exc
        except OSError as exc:
            self.release()
            raise OwnedProcessError("Cannot acquire the Codex ownership lease") from exc
        try:
            if self.path.exists():
                await self._reap(json.loads(self.path.read_text()))
                self.path.unlink(missing_ok=True)
        except Exception as exc:
            self.release()
            if isinstance(exc, OwnedProcessError):
                raise
            raise OwnedProcessError("Cannot verify the prior Codex process owner") from exc

    def _matching_child(self, record: dict) -> dict | None:
        child = process_identity(record["child"]["pid"])
        if child is None:
            return None
        expected = record["child"]
        if child["start"] != expected["start"]:
            raise OwnedProcessError("Recorded Codex PID has been reused; refusing to signal")
        if child["workspace"] != self.workspace or child["argv"] not in (
            record["command"],
            record["wrapper_command"],
        ):
            raise OwnedProcessError("Recorded Codex command or workspace no longer matches")
        env = _process_environment(child["pid"])
        if env != {
            "SKULD__SESSION__ID": self.session_id,
            "SKULD__SESSION__WORKSPACE_DIR": self.workspace,
            "SKULD_CODEX_OWNER_NONCE": record["nonce"],
        }:
            raise OwnedProcessError("Recorded Codex session or ownership nonce no longer matches")
        return child

    async def _reap(self, record: dict) -> None:
        if record.get("session_id") != self.session_id or record.get("workspace") != self.workspace:
            raise OwnedProcessError("Codex ownership lease belongs to another session/workspace")
        if record["boot_id"] != self._boot_id():
            return  # Processes from an earlier boot cannot still hold a writer lock.
        owner = process_identity(record["owner"]["pid"])
        if owner and owner["start"] == record["owner"]["start"]:
            raise OwnedProcessError("The recorded Codex broker is still alive")
        child = self._matching_child(record)
        if child is None:
            return
        fd = open_pidfd(child["pid"])
        try:
            # Recheck after pidfd_open: the descriptor must refer to the inspected incarnation.
            current = self._matching_child(record)
            if current is None:
                return
            if current != child:
                raise OwnedProcessError("Codex process changed while acquiring its pidfd")
            signal_pidfd(fd, signal.SIGTERM)
            for _ in range(50):
                if pidfd_exited(fd):
                    return
                await asyncio.sleep(0.1)
            signal_pidfd(fd, signal.SIGKILL)
            for _ in range(50):
                if pidfd_exited(fd):
                    return
                await asyncio.sleep(0.1)
            raise OwnedProcessError("Owned Codex process did not exit after termination")
        finally:
            os.close(fd)

    def spawn_command(self, command: list[str], env: dict[str, str]) -> list[str]:
        env["SKULD__SESSION__ID"] = self.session_id
        env["SKULD__SESSION__WORKSPACE_DIR"] = self.workspace
        env["SKULD_CODEX_OWNER_NONCE"] = self.nonce
        # Isolated mode excludes this directory's subprocess.py from stdlib imports.
        # The native exec still receives the unchanged environment, including PYTHONPATH.
        return [sys.executable, "-I", str(Path(__file__).resolve()), str(os.getpid()), *command]

    def record_child(self, pid: int, command: list[str], wrapper_command: list[str]) -> None:
        child = process_identity(pid)
        owner = process_identity(os.getpid())
        if child is None or owner is None:
            raise OwnedProcessError("Codex process exited before ownership could be recorded")
        self.record = {
            "session_id": self.session_id,
            "workspace": self.workspace,
            "boot_id": self._boot_id(),
            "nonce": self.nonce,
            "owner": {k: owner[k] for k in ("pid", "start")},
            "child": {k: child[k] for k in ("pid", "start")},
            "command": command,
            "wrapper_command": wrapper_command,
        }
        temporary = self.path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(self.record, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)

    def release(self) -> None:
        if self.lock_fd is None:
            return
        # Keep unresolved child evidence: the next owner must verify it before spawning.
        try:
            if self.record and process_identity(self.record["child"]["pid"]) is None:
                self.path.unlink(missing_ok=True)
                self.record = None
        finally:
            os.close(self.lock_fd)
            self.lock_fd = None


def parent_death_exec(parent_pid: int, command: list[str]) -> None:
    """Set PDEATHSIG in a fresh interpreter, avoiding preexec_fn in a threaded broker."""
    libc = ctypes.CDLL(None, use_errno=True)
    # Linux prctl(PR_SET_PDEATHSIG, SIGKILL). No permissions or sandbox settings change.
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Cannot install Codex parent-death protection")
    if os.getppid() != parent_pid:
        raise OwnedProcessError("Broker exited before Codex parent-death protection was installed")
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    parent_death_exec(int(sys.argv[1]), sys.argv[2:])
