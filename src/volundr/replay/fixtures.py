"""Checked-in replay fixtures: name whitelist + path-traversal guard.

Fixtures are ``SessionLogEntry[]`` JSON arrays in the exact shape returned by
the ``GET .../log`` replay endpoint (and the lexi-frontend Phase-1
``*.frames.json`` files). They let the replay WebSocket serve a deterministic
corpus offline / in CI, with no database.
"""

from __future__ import annotations

import re
from pathlib import Path

# No slashes, no "..", no absolute paths — only a flat fixture file name.
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def default_fixtures_dir() -> Path:
    """The packaged fixtures directory: ``src/volundr/replay/fixtures``."""
    return Path(__file__).resolve().parent / "fixtures"


def resolve_fixture(name: str, fixtures_dir: Path) -> Path:
    """Resolve a fixture ``name`` to a path inside ``fixtures_dir``.

    Accepts a bare slug (``"two-turn"``) or a full ``"<slug>.frames.json"``.
    Rejects anything with slashes or ``..`` (whitelist), and re-checks after
    resolution that the path is still under ``fixtures_dir`` (defense in depth).

    Raises:
        ValueError: invalid name, or the path escapes ``fixtures_dir``.
        FileNotFoundError: no such fixture file.
    """
    if not _NAME_RE.match(name):
        raise ValueError("invalid fixture name")
    base = name if name.endswith(".frames.json") else f"{name}.frames.json"
    root = fixtures_dir.resolve()
    path = (root / base).resolve()
    if root != path and root not in path.parents:
        raise ValueError("fixture escapes fixtures_dir")
    if not path.is_file():
        raise FileNotFoundError(name)
    return path
