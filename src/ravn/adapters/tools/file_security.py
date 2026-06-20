"""File security utilities shared across all file tools."""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Default limits (read from config at tool construction; these are fallbacks)
# ---------------------------------------------------------------------------

DEFAULT_MAX_READ_BYTES: int = 1 * 1024 * 1024  # 1 MB
DEFAULT_MAX_WRITE_BYTES: int = 5 * 1024 * 1024  # 5 MB
DEFAULT_BINARY_CHECK_BYTES: int = 8 * 1024  # 8 KB

# ---------------------------------------------------------------------------
# System path prefixes that are always rejected
# ---------------------------------------------------------------------------

_SYSTEM_PREFIXES: tuple[str, ...] = (
    "/etc",
    "/usr",
    "/var",
    "/boot",
    "/sys",
    "/proc",
)


class PathSecurityError(Exception):
    """Raised when a path violates workspace or system-path constraints."""


def resolve_safe(path: str | Path, workspace: Path) -> Path:
    """Resolve *path* to an absolute path that lies within *workspace*.

    Steps
    -----
    1. Resolve the path via ``Path.resolve()``, which normalises ``../``
       components and follows any existing symlinks.
    2. Verify the resolved path is inside *workspace*.
    3. Verify the resolved path is not a system path (/etc, /usr, …).

    The path need not exist (useful for write targets).

    Raises
    ------
    PathSecurityError
        If the path escapes the workspace or matches a system prefix.
    """
    workspace = workspace.resolve()
    raw = Path(path)
    target = raw if raw.is_absolute() else workspace / raw
    resolved = target.resolve()

    # Check system prefixes on both the original (pre-resolve) and resolved
    # paths so that macOS symlinks (/etc → /private/etc) are caught correctly.
    _assert_not_system(raw)
    _assert_within(resolved, workspace)
    _assert_not_system(resolved)

    return resolved


def _assert_within(path: Path, workspace: Path) -> None:
    """Raise PathSecurityError if *path* is not a sub-path of *workspace*."""
    try:
        path.relative_to(workspace)
    except ValueError:
        raise PathSecurityError(f"Path '{path}' is outside the workspace '{workspace}'")


def _assert_not_system(path: Path) -> None:
    """Raise PathSecurityError if *path* starts with a system prefix."""
    path_str = str(path)
    for prefix in _SYSTEM_PREFIXES:
        if path_str == prefix or path_str.startswith(prefix + "/"):
            raise PathSecurityError(f"Access to system path '{path}' is not allowed")


def is_binary(data: bytes, check_bytes: int = DEFAULT_BINARY_CHECK_BYTES) -> bool:
    """Return True if *data* looks like binary content (contains NUL bytes)."""
    return b"\x00" in data[:check_bytes]
