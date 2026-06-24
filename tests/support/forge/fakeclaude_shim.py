"""Install ``fakeagent`` as the ``claude`` binary on PATH for tmux smoke tests.

The transport spawns whatever ``claude`` it finds on PATH. We write a tiny
executable shim that re-execs the *current* interpreter (``sys.executable``) on
``fakeagent.py``, forwarding argv verbatim, so the agent runs with the same
Python/env the test process has.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

_FAKEAGENT = Path(__file__).resolve().parent / "fakeagent.py"


def install_fake_claude(bin_dir: Path, *, boot: str | None = None) -> dict[str, str]:
    """Write an executable ``claude`` shim into ``bin_dir`` and return the env
    mutations a caller should apply (PATH prepend + optional boot directive)."""
    bin_dir = Path(bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "claude"
    shim.write_text(
        f'#!/usr/bin/env bash\nexec {_quote(sys.executable)} {_quote(str(_FAKEAGENT))} "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    env: dict[str, str] = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    if boot is not None:
        env["FORGE_FAKEAGENT_BOOT"] = boot
    return env


def _quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"
