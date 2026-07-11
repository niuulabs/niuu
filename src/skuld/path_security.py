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

    Lexical parent traversal and escapes through existing symlinks are rejected.
    The common-path comparison is deliberately explicit so static analysis can
    prove that the returned canonical path stays beneath the canonical root.
    """
    if "\0" in relative_path:
        raise UnsafePathError("invalid path")

    normalised = os.path.normpath(relative_path)
    if os.path.isabs(normalised):
        raise UnsafePathError("path traversal not allowed")
    if normalised == os.pardir or normalised.startswith(os.pardir + os.sep):
        raise UnsafePathError("path traversal not allowed")

    try:
        lexical_root = os.path.abspath(os.fspath(base))
        lexical_candidate = os.path.abspath(os.path.join(lexical_root, normalised))
        if os.path.commonpath((lexical_root, lexical_candidate)) != lexical_root:
            raise UnsafePathError("path traversal not allowed")

        canonical_root = os.path.realpath(lexical_root, strict=True)
        candidate = os.path.realpath(lexical_candidate, strict=strict)
        if os.path.commonpath((canonical_root, candidate)) != canonical_root:
            raise UnsafePathError("path traversal not allowed")
    except (OSError, RuntimeError, ValueError) as exc:
        raise UnsafePathError("path traversal not allowed") from exc

    if not allow_root and candidate == canonical_root:
        raise UnsafePathError("path must name an entry beneath the root")
    return Path(candidate)


def resolve_path_in_roots(
    path: str | Path,
    roots: tuple[str | Path, ...],
    *,
    strict: bool = True,
) -> Path:
    """Resolve *path* only when its final target belongs to an allowed root."""
    raw_path = os.fspath(path)
    if "\0" in raw_path:
        raise UnsafePathError("invalid path")

    lexical_candidate = os.path.abspath(os.path.expanduser(raw_path))
    for root in roots:
        try:
            lexical_root = os.path.abspath(os.fspath(root))
            if os.path.commonpath((lexical_root, lexical_candidate)) != lexical_root:
                continue

            canonical_root = os.path.realpath(lexical_root, strict=True)
            candidate = os.path.realpath(lexical_candidate, strict=strict)
            if os.path.commonpath((canonical_root, candidate)) == canonical_root:
                return Path(candidate)
        except (OSError, RuntimeError, ValueError):
            continue
    raise UnsafePathError("path is outside the permitted roots")
