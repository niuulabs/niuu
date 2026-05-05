"""Access embedded resources (web UI assets, migration SQL files).

In development these are read from the filesystem.  When compiled into a
Nuitka ``--onefile`` binary the files are bundled as package data and
accessed via :mod:`importlib.resources`.
"""

from __future__ import annotations

import importlib.resources
import os
from pathlib import Path


def web_ui_variant() -> str:
    """Return the requested web UI variant.

    Supported values are ``"new"``, ``"old"``, and ``"auto"``.
    Defaults to ``"auto"`` when the environment variable is unset.
    """

    raw = os.getenv("NIUU_WEB_UI", "auto").strip().lower()
    if raw in {"", "auto"}:
        return "auto"
    if raw in {"new", "old"}:
        return raw

    msg = "Invalid NIUU_WEB_UI value — expected 'old', 'new', or 'auto'"
    raise ValueError(msg)


def web_dist_dir() -> Path:
    """Return the path to the bundled web UI ``dist/`` directory.

    Falls back to source-tree web builds when running from source.
    """
    repo_root = Path(__file__).resolve().parents[2]
    variant = web_ui_variant()
    if variant == "new":
        repo_candidates = (
            repo_root / "web-next" / "apps" / "niuu" / "dist",
            repo_root / "src" / "cli" / "web" / "dist",
        )
    elif variant == "old":
        repo_candidates = (
            repo_root / "web" / "dist",
            repo_root / "src" / "cli" / "web" / "dist",
        )
    else:
        repo_candidates = (
            repo_root / "web-next" / "apps" / "niuu" / "dist",
            repo_root / "web" / "dist",
            repo_root / "src" / "cli" / "web" / "dist",
        )
    for candidate in repo_candidates:
        if candidate.is_dir():
            return candidate

    pkg_dir = importlib.resources.files("cli") / "web" / "dist"
    pkg_path = _resource_path(pkg_dir)
    if pkg_path.is_dir():
        return pkg_path

    msg = "Web UI assets not found — run 'make build-web' first"
    raise FileNotFoundError(msg)


def migration_dir(variant: str = "volundr") -> Path:
    """Return the path to embedded SQL migration files.

    Parameters
    ----------
    variant:
        ``"volundr"`` (default) for main migrations, ``"tyr"`` for Tyr.
    """
    # Prefer repo-relative migrations (source of truth when running from source)
    if variant == "tyr":
        repo_dir = Path(__file__).resolve().parents[2] / "migrations" / "tyr"
    else:
        repo_dir = Path(__file__).resolve().parents[2] / "migrations"

    if repo_dir.is_dir():
        return repo_dir

    # Fallback: bundled package data (Nuitka binary)
    if variant == "tyr":
        pkg_dir = importlib.resources.files("cli") / "migrations" / "tyr"
    else:
        pkg_dir = importlib.resources.files("cli") / "migrations" / "volundr"

    resolved = _resource_path(pkg_dir)
    if resolved.is_dir():
        return resolved

    msg = f"Migration files not found for variant={variant!r}"
    raise FileNotFoundError(msg)


def _resource_path(traversable: importlib.resources.abc.Traversable) -> Path:
    """Convert an importlib Traversable to a concrete Path."""
    # In Python 3.12+ ``as_posix()`` + Path works for both filesystem and
    # zip-backed resources.  For Nuitka onefile the resources are extracted
    # to a temp directory, so ``str()`` gives a real filesystem path.
    return Path(str(traversable))
