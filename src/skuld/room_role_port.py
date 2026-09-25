"""Pluggable room-role resolution port for a session's broker.

Selected dynamically via ``ws_auth.room_role_remote`` (``adapter:`` + kwargs,
see ``.claude/rules/dynamic-adapters.md``) when ``ws_auth.room_role_source ==
"remote"``. Mirrors the shape of ``niuu.session_proxy.SkuldPortRegistry``'s
in-process ``set_room_role_resolver`` hook (mini-mode's own room-role
resolution), but this port is for a session pod calling OUT to Forge over
HTTP, not an in-process callback.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class RoomRoleResolutionError(RuntimeError):
    """Room-role resolution failed operationally (unreachable, timeout, bad response).

    Callers MUST treat this as a deny — never substitute a default role. See
    ``.claude/rules/no-fallbacks.md``: "configured but impossible" is fatal,
    not degraded.
    """


class RoomRoleResolverPort(ABC):
    """Resolve a caller's effective room role for per-route/per-message authorization."""

    @abstractmethod
    async def resolve_role(
        self,
        *,
        session_id: str,
        user_id: str,
        tenant_id: str,
        roles: list[str],
    ) -> str | None:
        """Return "owner"/"approver"/"viewer" for this caller, or None (no grant).

        ``None`` is a real, expected answer — the caller has no active grant
        on this session — distinct from raising ``RoomRoleResolutionError``,
        which means the answer could not be determined at all. Both must be
        treated as "not entitled" by callers; the distinction is for
        diagnostics only.
        """
