"""Security helpers for filesystem boundaries and untrusted log values."""

from __future__ import annotations

import os
from pathlib import Path


class UnsafePathError(ValueError):
    """Raised when an untrusted path escapes its permitted filesystem root."""


def resolve_contained_path(
    base: str | Path,
    relative_path: str,
    *,
    allow_root: bool = True,
    strict: bool = False,
) -> Path:
    """Resolve an untrusted relative path beneath *base*.

    Both lexical parent traversal and escapes through existing symlinks are
    rejected.  The resolved path is returned so callers never need to reuse
    the untrusted input at a filesystem sink.
    """
    if "\0" in relative_path:
        raise UnsafePathError("invalid path")

    normalised = os.path.normpath(relative_path)
    candidate_input = Path(normalised)
    if candidate_input.is_absolute() or os.pardir in candidate_input.parts:
        raise UnsafePathError("path traversal not allowed")

    base_path = Path(base).resolve(strict=True)
    try:
        candidate = (base_path / candidate_input).resolve(strict=strict)
        candidate.relative_to(base_path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise UnsafePathError("path traversal not allowed") from exc

    if not allow_root and candidate == base_path:
        raise UnsafePathError("path must name an entry beneath the root")
    return candidate


def resolve_path_in_roots(
    path: str | Path,
    roots: tuple[str | Path, ...],
    *,
    strict: bool = True,
) -> Path:
    """Resolve *path* only when its final target belongs to an allowed root."""
    if "\0" in str(path):
        raise UnsafePathError("invalid path")
    try:
        candidate = Path(path).expanduser().resolve(strict=strict)
    except (OSError, RuntimeError) as exc:
        raise UnsafePathError("invalid path") from exc

    for root in roots:
        try:
            candidate.relative_to(Path(root).resolve(strict=True))
        except (OSError, RuntimeError, ValueError):
            continue
        return candidate
    raise UnsafePathError("path is outside the permitted roots")
