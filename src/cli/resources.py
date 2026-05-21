"""Access embedded resources (web UI assets, migration SQL files).

In development these are read from the filesystem.  When compiled into a
Nuitka ``--onefile`` binary the files are bundled as package data and
accessed via :mod:`importlib.resources`.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path


def web_dist_dir() -> Path:
    """Return the path to the bundled web UI ``dist/`` directory.

    Falls back to the source-tree ``web-next`` build when running from source.
    """
    repo_root = Path(__file__).resolve().parents[2]
    repo_candidates = (
        repo_root / "web-next" / "apps" / "niuu" / "dist",
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
        ``"volundr"`` (default) for main migrations, ``"ting"`` for Ting.
    """
    # Prefer repo-relative migrations (source of truth when running from source)
    if variant == "ting":
        repo_dir = Path(__file__).resolve().parents[2] / "migrations" / "ting"
    else:
        repo_dir = Path(__file__).resolve().parents[2] / "migrations"

    if repo_dir.is_dir():
        return repo_dir

    # Fallback: bundled package data (Nuitka binary)
    if variant == "ting":
        pkg_dir = importlib.resources.files("cli") / "migrations" / "ting"
    else:
        pkg_dir = importlib.resources.files("cli") / "migrations" / "volundr"

    resolved = _resource_path(pkg_dir)
    if resolved.is_dir():
        return resolved

    msg = f"Migration files not found for variant={variant!r}"
    raise FileNotFoundError(msg)


def ordered_migration_files(mig_dir: Path) -> list[Path]:
    """Return migration files in execution order.

    Legacy compatibility migrations must run before the normal numbered set so
    older developer databases can be healed before newer DDL expects run-era
    table and column names.
    """

    def _sort_key(path: Path) -> tuple[int, str]:
        return (0, path.name) if "legacy_schema_compat" in path.name else (1, path.name)

    return sorted(mig_dir.glob("*.up.sql"), key=_sort_key)


def _resource_path(traversable: importlib.resources.abc.Traversable) -> Path:
    """Convert an importlib Traversable to a concrete Path."""
    # In Python 3.12+ ``as_posix()`` + Path works for both filesystem and
    # zip-backed resources.  For Nuitka onefile the resources are extracted
    # to a temp directory, so ``str()`` gives a real filesystem path.
    return Path(str(traversable))
