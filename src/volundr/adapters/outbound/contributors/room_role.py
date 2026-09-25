"""Room-role-source contributor — wires ws_auth.room_role_source into pods."""

from __future__ import annotations

from typing import Any

from volundr.domain.models import Session
from volundr.domain.ports import SessionContext, SessionContribution, SessionContributor


class RoomRoleSourceContributor(SessionContributor):
    """Renders ``wsAuth.room_role_source``/``room_role_remote`` for remote-capable backends.

    Mirrors Forge's own ``pod_manager.room_role_source`` setting — the SAME
    setting ``rest_session_participants.py``'s 409 gate reads — into THIS
    session's Helm values, so an operator sets ``room_role_source`` in one
    place instead of keeping Forge's config and the skuld chart's static
    default in sync by hand.

    ``"deployment"`` (the config default) contributes nothing: the chart's
    own ``deployment`` default already matches, and existing clusters must
    render byte-identical values until they deliberately opt in (see
    ``PodManagerConfig.room_role_source`` in ``volundr/config.py``).

    Limited to "kubernetes" — the only backend both independently verified
    end-to-end AND the only one ``WorkloadIdentityContributor`` projects a
    niuu-workload service-account token onto (see
    ``rest_session_participants.REMOTE_CAPABLE_RUNTIME_BACKENDS``, which this
    mirrors exactly).
    """

    _REMOTE_CAPABLE_BACKENDS = frozenset({"kubernetes"})

    def __init__(
        self,
        *,
        room_role_source: str = "deployment",
        adapter: str = "skuld.room_role_remote.RemoteAuthorizationAdapter",
        scope: str = "forge:session:room-role",
        cache_ttl_seconds: float = 5.0,
        **_extra: object,
    ) -> None:
        self._room_role_source = room_role_source
        self._adapter = adapter
        self._scope = scope
        self._cache_ttl_seconds = cache_ttl_seconds

    @property
    def name(self) -> str:
        return "room_role_source"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        if self._room_role_source != "remote":
            return SessionContribution()
        if context.runtime_backend not in self._REMOTE_CAPABLE_BACKENDS:
            return SessionContribution()

        values: dict[str, Any] = {
            "wsAuth": {
                "room_role_source": "remote",
                "room_role_remote": {
                    "adapter": self._adapter,
                    "kwargs": {
                        "scope": self._scope,
                        "cache_ttl_seconds": self._cache_ttl_seconds,
                    },
                },
            }
        }
        return SessionContribution(values=values)
