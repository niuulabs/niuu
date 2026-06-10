"""Host path mount policy — shared prefix allowlist for local workspaces.

One source of truth for deciding whether a host directory may be used as
a session workspace or mount. Used by the local mount contributor, the
local process pod manager, and external session import.

Paths are resolved (symlinks followed) before matching, so an allowlist
of ``/tmp`` also covers macOS's ``/private/tmp`` and symlinks inside an
allowed directory cannot escape the policy.
"""

from pathlib import Path


def is_host_path_allowed(
    host_path: str,
    allowed_prefixes: list[str],
    *,
    allow_root_mount: bool = False,
) -> bool:
    """Return whether *host_path* may be mounted under the prefix policy.

    An empty *allowed_prefixes* list means every path is allowed (the
    policy is opt-in), except the filesystem root which always requires
    ``allow_root_mount``.
    """
    resolved = Path(host_path).resolve()

    if str(resolved) == "/":
        return allow_root_mount

    if not allowed_prefixes:
        return True

    return any(resolved.is_relative_to(Path(prefix).resolve()) for prefix in allowed_prefixes)


def ensure_host_path_allowed(
    host_path: str,
    allowed_prefixes: list[str],
    *,
    allow_root_mount: bool = False,
) -> None:
    """Raise ``ValueError`` when *host_path* violates the prefix policy."""
    if is_host_path_allowed(host_path, allowed_prefixes, allow_root_mount=allow_root_mount):
        return

    if str(Path(host_path).resolve()) == "/":
        raise ValueError("Mounting root filesystem (/) requires allow_root_mount=true")

    raise ValueError(f"Host path '{host_path}' is not under any allowed prefix: {allowed_prefixes}")
