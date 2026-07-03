"""Checked-in replay fixtures: name whitelist + path-traversal guard.

Fixtures are ``SessionLogEntry[]`` JSON arrays in the exact shape returned by
the ``GET .../log`` replay endpoint (and the lexi-frontend Phase-1
``*.frames.json`` files). They let the replay WebSocket serve a deterministic
corpus offline / in CI, with no database.
"""

from __future__ import annotations

import re
from pathlib import Path

# Fixture slug is intentionally strict; callers may pass either:
#   - "<slug>"
#   - "<slug>.frames.json"
# where slug is alnum/underscore/hyphen only.
_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _normalize_fixture_base(name: str) -> str:
    """Validate user-provided fixture name and return canonical file name."""
    slug = name.removesuffix(".frames.json")
    if not _SLUG_RE.fullmatch(slug):
        raise ValueError("invalid fixture name")
    return f"{slug}.frames.json"


def default_fixtures_dir() -> Path:
    """The packaged fixtures directory: ``src/volundr/replay/fixtures``."""
    return Path(__file__).resolve().parent / "fixtures"


def resolve_fixture(name: str, fixtures_dir: Path) -> Path:
    """Resolve a fixture ``name`` to a path inside ``fixtures_dir``.

    Accepts a bare slug (``"two-turn"``) or a full ``"<slug>.frames.json"``.
    Rejects anything outside the strict slug format, then re-checks after
    resolution that the path is still under ``fixtures_dir`` (defense in depth).

    Raises:
        ValueError: invalid name, or the path escapes ``fixtures_dir``.
        FileNotFoundError: no such fixture file.
    """
    base = _normalize_fixture_base(name)
    root = fixtures_dir.resolve()
    path = (root / base).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("fixture escapes fixtures_dir") from exc
    if not path.is_file():
        raise FileNotFoundError(name)
    return path
