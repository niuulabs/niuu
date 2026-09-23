"""The single permission-mode vocabulary shared by personas, config and enforcement.

Personas, RAVN.md and ``permission.mode`` have historically spelled modes
with hyphens (``read-only``) while the enforcer compared underscore forms
(``read_only``).  A spelling the enforcer did not recognise fell through to its
permissive default, so a ``read-only`` persona could write and execute.

Every surface now parses through :func:`parse_permission_mode`: both
spellings are accepted, anything else raises with the valid values named.
"""

from __future__ import annotations

from enum import StrEnum


class PermissionMode(StrEnum):
    """How much a Ravn may mutate. Values are the canonical underscore spelling."""

    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    FULL_ACCESS = "full_access"
    PROMPT = "prompt"
    # Legacy aliases kept for backwards compatibility.
    ALLOW_ALL = "allow_all"
    DENY_ALL = "deny_all"


def valid_permission_mode_spellings() -> list[str]:
    """Return every accepted spelling, for error messages and validators."""
    spellings = {mode.value for mode in PermissionMode}
    spellings |= {mode.value.replace("_", "-") for mode in PermissionMode}
    return sorted(spellings)


def parse_permission_mode(value: object) -> PermissionMode:
    """Parse *value* into a :class:`PermissionMode`.

    Accepts the enum itself or a string in hyphen or underscore spelling
    (``read-only`` / ``read_only``).  Raises :class:`ValueError` naming the
    valid spellings for anything else — an unrecognised mode must never be
    treated as some default.
    """
    if isinstance(value, PermissionMode):
        return value
    if not isinstance(value, str):
        raise ValueError(
            f"permission_mode must be a string, got {type(value).__name__} {value!r}. "
            f"Valid values: {valid_permission_mode_spellings()}"
        )
    try:
        return PermissionMode(value.strip().replace("-", "_"))
    except ValueError:
        raise ValueError(
            f"Unknown permission_mode {value!r}. Valid values: {valid_permission_mode_spellings()}"
        ) from None


def parse_optional_permission_mode(value: object) -> PermissionMode | None:
    """Parse an optional *value*; ``None`` or blank means "not set".

    Used where the permission mode is an override (persona, RAVN.md) whose
    absence defers to ``permission.mode`` in Settings.
    """
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return parse_permission_mode(value)
