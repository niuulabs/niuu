"""Regression coverage for `ChronicleMixin._git_diff_summary` subprocess cleanup.

A timed-out `communicate()` cancels the *read*, not the child `git` process.
Before this fix, the bare `except Exception: return ""` left a hung `git`
process (and its stdout/stderr pipe transports) referenced only by the local,
now-unreachable `proc` — never killed, never awaited — which Python reports
as a `ResourceWarning: unclosed transport` whenever it later happens to
garbage-collect it, typically during an unrelated test.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import patch

from skuld.chronicle import ChronicleMixin


class _FakeChronicle(ChronicleMixin):
    def __init__(self, workspace_dir: str, *, diff_timeout_s: float) -> None:
        self.workspace_dir = workspace_dir
        self._settings = SimpleNamespace(
            mesh=SimpleNamespace(diff_max_bytes=8192, diff_timeout_s=diff_timeout_s)
        )


async def test_git_diff_summary_kills_and_reaps_process_on_timeout(tmp_path):
    chronicle = _FakeChronicle(str(tmp_path), diff_timeout_s=0.05)
    real_create_subprocess_exec = asyncio.create_subprocess_exec
    spawned: list[asyncio.subprocess.Process] = []

    async def _slow_git_stand_in(*args, **kwargs):
        del args
        # Stands in for `git diff ...`: a real child that outlives the
        # configured timeout, so `communicate()` genuinely raises and the
        # fixed cleanup path (kill + await wait) actually runs.
        process = await real_create_subprocess_exec(
            sys.executable,
            "-c",
            "import time; time.sleep(5)",
            stdout=kwargs["stdout"],
            stderr=kwargs["stderr"],
            cwd=kwargs.get("cwd"),
        )
        spawned.append(process)
        return process

    with patch("skuld.chronicle.asyncio.create_subprocess_exec", _slow_git_stand_in):
        result = await chronicle._git_diff_summary()

    assert result == ""
    # Both `diff_args` attempts spawn and must each be reaped.
    assert spawned
    assert all(process.returncode is not None for process in spawned)
