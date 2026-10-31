"""Forge MCP grants and the launch binding, persisted on the session row.

A session's grants and its current launch live in ``sessions.workload_config``
under ``forge_mcp``. That column already survives restarts (the start path reuses
it), so no migration is needed::

    {"forge_mcp": {"grants": ["message"], "launch_id": "<32 hex>"}}

* ``grants`` accumulate from the session-create request and the launch spec in
  use (``workload_config.forge_mcp.grants`` of the spec). A restart keeps them.
* ``launch_id`` is replaced on every start. The session credential carries it,
  and Forge rejects a credential whose launch is no longer the session's current
  one, which revokes the previous launch's token on restart.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any
from uuid import UUID

from niuu.forge_mcp.credentials import normalize_grants
from niuu.forge_mcp.models import ForgeMcpGrant
from volundr.domain.models import LaunchSpec

FORGE_MCP_KEY = "forge_mcp"
GRANTS_KEY = "grants"
LAUNCH_ID_KEY = "launch_id"


class ForgeMcpGrantEscalationError(Exception):
    """A session credential tried to give another session grants it does not hold."""

    def __init__(self, session_id: UUID | None, grants: Iterable[ForgeMcpGrant]) -> None:
        self.session_id = session_id
        self.grants = tuple(sorted(grant.value for grant in grants))
        target = f" (session {session_id})" if session_id is not None else ""
        super().__init__(
            "A Forge session credential cannot grant what it does not hold: "
            f"{', '.join(self.grants)}{target}"
        )


def _block(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not config:
        return {}
    block = config.get(FORGE_MCP_KEY)
    return block if isinstance(block, Mapping) else {}


def grants_in(config: Mapping[str, Any] | None, *, source: str) -> frozenset[ForgeMcpGrant]:
    """The grants a ``workload_config``-shaped mapping holds.

    Raises ``ValueError`` naming ``source`` for a malformed or unknown grant, so a
    typo in a launch spec fails the launch instead of silently granting less.
    """
    raw = _block(config).get(GRANTS_KEY)
    if raw is None:
        return frozenset()
    if isinstance(raw, str) or not isinstance(raw, Iterable):
        raise ValueError(f"{source}: forge_mcp.grants must be a list")
    try:
        return frozenset(normalize_grants(raw))
    except ValueError as exc:
        allowed = ", ".join(grant.value for grant in ForgeMcpGrant)
        raise ValueError(f"{source}: unknown forge_mcp grant ({exc}); use {allowed}") from exc


def launch_spec_grants(spec: LaunchSpec | None) -> frozenset[ForgeMcpGrant]:
    if spec is None:
        return frozenset()
    return grants_in(spec.workload_config, source=f"launch spec {spec.name!r}")


def launch_id_of(config: Mapping[str, Any] | None) -> str | None:
    value = _block(config).get(LAUNCH_ID_KEY)
    return value if isinstance(value, str) and value else None


def with_forge_mcp(
    config: Mapping[str, Any] | None,
    *,
    grants: Iterable[ForgeMcpGrant],
    launch_id: str,
) -> dict[str, Any]:
    """``config`` with its ``forge_mcp`` block replaced by these grants and launch."""
    updated = dict(config or {})
    updated[FORGE_MCP_KEY] = {
        GRANTS_KEY: [grant.value for grant in normalize_grants(grants)],
        LAUNCH_ID_KEY: launch_id,
    }
    return updated


def without_forge_mcp(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """``config`` minus the ``forge_mcp`` block (for consumers that do not own it)."""
    return {key: value for key, value in (config or {}).items() if key != FORGE_MCP_KEY}
