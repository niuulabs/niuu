"""Shared supervision helpers for CLI-managed child processes.

``ravn flock`` and ``ravn room`` both supervise detached daemons on this host,
so port probing, liveness checks, and graceful shutdown live here rather than
being written twice.
"""

from __future__ import annotations

import os
import signal
import socket
import time
from contextlib import suppress

# Seconds to wait for SIGTERM to be honoured before escalating to SIGKILL.
DEFAULT_STOP_TIMEOUT_S = 5.0

# Poll interval while waiting for processes to exit.
_STOP_POLL_INTERVAL_S = 0.2

# Upper bound on the port scan performed by :func:`find_free_port`.
_PORT_SCAN_LIMIT = 200


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    """Return True when nothing is accepting connections on *port*."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.connect_ex((host, port)) != 0


def find_free_port(base_port: int, host: str = "127.0.0.1") -> int:
    """Return the first free port at or above *base_port*.

    Raises ``RuntimeError`` rather than returning a busy port, so a caller
    never silently binds something already in use.
    """
    for candidate in range(base_port, base_port + _PORT_SCAN_LIMIT):
        if port_free(candidate, host):
            return candidate
    raise RuntimeError(
        f"No free port found in {base_port}..{base_port + _PORT_SCAN_LIMIT - 1} on {host}."
    )


def is_alive(pid: int) -> bool:
    """Return True when *pid* names a live process."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def stop_pids(pids: list[int], *, timeout_s: float = DEFAULT_STOP_TIMEOUT_S) -> None:
    """Send SIGTERM to all *pids*, then SIGKILL whatever is still running."""
    for pid in pids:
        if is_alive(pid):
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not any(is_alive(pid) for pid in pids):
            return
        time.sleep(_STOP_POLL_INTERVAL_S)

    for pid in pids:
        if is_alive(pid):
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
