"""Tests for the shared CLI process-supervision helpers.

These run against real sockets and real short-lived child processes — the
helpers exist to be correct about liveness and port availability, which a
mock cannot demonstrate.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import pytest

from ravn.cli.process_supervision import (
    find_free_port,
    is_alive,
    port_free,
    stop_pids,
)


class TestPortFree:
    def test_unbound_port_is_free(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        # Socket closed — nothing is listening on that port now.
        assert port_free(port) is True

    def test_listening_port_is_not_free(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]

            assert port_free(port) is False


class TestFindFreePort:
    def test_returns_the_base_port_when_free(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            base = probe.getsockname()[1]

        assert find_free_port(base) == base

    def test_skips_a_busy_port(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            base = listener.getsockname()[1]

            assert find_free_port(base) > base

    def test_raises_rather_than_returning_a_busy_port(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail loudly: silently handing back a used port would bind a broker onto it."""
        monkeypatch.setattr(
            "ravn.cli.process_supervision.port_free", lambda port, host="127.0.0.1": False
        )

        with pytest.raises(RuntimeError, match="No free port"):
            find_free_port(7500)


class TestIsAlive:
    def test_current_process_is_alive(self) -> None:
        assert is_alive(os.getpid()) is True

    def test_reaped_child_is_not_alive(self) -> None:
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()

        assert is_alive(proc.pid) is False


class TestStopPids:
    def test_terminates_a_running_child(self) -> None:
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            assert is_alive(proc.pid) is True

            stop_pids([proc.pid])
            proc.wait(timeout=10)

            assert proc.poll() is not None
        finally:
            if proc.poll() is None:  # pragma: no cover - only on an unexpected survival
                proc.kill()
                proc.wait(timeout=5)

    def test_escalates_to_sigkill_when_sigterm_is_ignored(self) -> None:
        script = (
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"
        )
        proc = subprocess.Popen([sys.executable, "-c", script])
        try:
            # Give the child time to install its SIGTERM handler.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and proc.poll() is None:
                time.sleep(0.05)
                break

            stop_pids([proc.pid], timeout_s=0.5)
            proc.wait(timeout=10)

            assert proc.poll() is not None
        finally:
            if proc.poll() is None:  # pragma: no cover - only on an unexpected survival
                proc.kill()
                proc.wait(timeout=5)

    def test_dead_pids_are_a_no_op(self) -> None:
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()

        stop_pids([proc.pid])  # must not raise

    def test_empty_list_is_a_no_op(self) -> None:
        stop_pids([])
