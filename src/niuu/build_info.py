"""Runtime build identification shared by all niuu services.

Lets an operator confirm *which* build of the Forge API / Skuld broker is live —
the git commit the running process was started from — via ``GET
/api/v1/forge/version`` and the broker's ``system/init`` event.

Computed once (cached): from git when running off a checkout, else from baked-in
env vars (``NIUU_BUILD_SHA`` / ``NIUU_BUILD_REF``, set at container build time),
else ``"unknown"``. Never raises.
"""

from __future__ import annotations

import functools
import os
import subprocess
from pathlib import Path

VERSION = "0.1.0"
_GIT_TIMEOUT_S = 2.0
_SHORT_SHA_LEN = 12

# src/niuu/build_info.py -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


@functools.lru_cache(maxsize=1)
def build_info() -> dict[str, object]:
    """Best-effort identification of the running build (never raises).

    Keys: ``version``, ``git_sha`` (short), ``git_sha_full``, ``git_branch``,
    ``git_dirty`` (tracked-file changes only; ignores untracked local config).
    Env vars ``NIUU_BUILD_SHA`` / ``NIUU_BUILD_REF`` take precedence so a
    container image built without a ``.git`` dir still reports its build.
    """
    sha_full = os.environ.get("NIUU_BUILD_SHA") or _git("rev-parse", "HEAD")
    branch = os.environ.get("NIUU_BUILD_REF") or _git("rev-parse", "--abbrev-ref", "HEAD")
    # --untracked-files=no: untracked local config (bifrost.yaml, etc.) is not "dirty source".
    dirty_raw = _git("status", "--porcelain", "--untracked-files=no")
    return {
        "version": VERSION,
        "git_sha": sha_full[:_SHORT_SHA_LEN] if sha_full else "unknown",
        "git_sha_full": sha_full or "unknown",
        "git_branch": branch or "unknown",
        "git_dirty": bool(dirty_raw),
    }
