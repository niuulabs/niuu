"""Keep an SSH tunnel alive only while its controller owns the stdin pipe."""

from __future__ import annotations

import select
import signal
import subprocess
import sys


def supervise(command: list[str], *, poll_seconds: float, stop_timeout: float) -> int:
    """Reap the child on controller EOF, normal shutdown, or a termination signal."""
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL)
    try:
        while child.poll() is None:
            readable, _, _ = select.select([sys.stdin], [], [], poll_seconds)
            if readable and not sys.stdin.buffer.read(1):
                return 0
        return child.returncode
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=stop_timeout)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def _terminate(signum, frame):
    raise SystemExit(128 + signum)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)
    raise SystemExit(
        supervise(sys.argv[3:], poll_seconds=float(sys.argv[1]), stop_timeout=float(sys.argv[2]))
    )
