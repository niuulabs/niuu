"""Capture build identity once when the platform starts, never from later edits."""

import hashlib
import subprocess
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class BuildIdentitySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NIUU_BUILD_")
    revision: str = ""
    version: str = "development"


GIT_IDENTITY_TIMEOUT_SECONDS = 5


def build_identity() -> dict[str, str | bool]:
    root = Path(__file__).resolve().parents[2]
    settings = BuildIdentitySettings()
    revision = settings.revision
    dirty = False
    try:
        if not revision:
            revision = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=GIT_IDENTITY_TIMEOUT_SECONDS,
            ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain", "--untracked-files=no"],
                cwd=root,
                stderr=subprocess.DEVNULL,
                timeout=GIT_IDENTITY_TIMEOUT_SECONDS,
            ).strip()
        )
    except (OSError, subprocess.SubprocessError):
        revision = revision or "unknown"
    digest = hashlib.sha256()
    for path in sorted((root / "src").rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return {
        "revision": revision,
        "build": settings.version,
        "source_sha256": digest.hexdigest(),
        "dirty": dirty,
    }
