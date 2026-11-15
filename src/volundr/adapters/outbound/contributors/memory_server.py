"""Memory server contributor: turns on Skuld's ``memory`` server for every session.

When Forge is given the memory gateway's address (``memory.gateway_url``), every session it
launches gets ``SKULD__MEMORY__ENABLED=true`` and ``SKULD__MEMORY__GATEWAY_URL=<url>``. Skuld
then runs one ``memory`` server over all the session's layers for its harness, and the broker
calls the gateway with the session's own Forge credential (scope ``forge:memory``), which
the gateway checks against Forge's ``GET /sessions/{id}/memory-context``.

The variables travel as pod-spec env, which every pod manager hands to Skuld (local process,
Kubernetes, Flux, OpenShell, Docker, VM), so the setting reaches a session whichever runtime
starts it. Without ``memory.gateway_url`` the contributor is not wired and no session runs a
memory server: that is the operator's decision to go without memory, not a degraded mode.
"""

from __future__ import annotations

from volundr.domain.models import PodSpecAdditions, Session
from volundr.domain.ports import SessionContext, SessionContribution, SessionContributor

#: Skuld's ``memory.enabled`` / ``memory.gateway_url`` settings, in their environment form.
MEMORY_ENABLED_ENV = "SKULD__MEMORY__ENABLED"
MEMORY_GATEWAY_URL_ENV = "SKULD__MEMORY__GATEWAY_URL"


class MemoryServerContributor(SessionContributor):
    """Emit the memory server settings for every session."""

    def __init__(self, *, gateway_url: str, **_extra: object):
        self._gateway_url = gateway_url.strip()
        if not self._gateway_url:
            raise ValueError(
                "The memory server contributor needs the memory gateway's URL: set "
                "memory.gateway_url, or leave memory.gateway_url empty to run sessions "
                "without a memory server"
            )

    @property
    def name(self) -> str:
        return "memory_server"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        return SessionContribution(
            pod_spec=PodSpecAdditions(
                env=(
                    {"name": MEMORY_ENABLED_ENV, "value": "true"},
                    {"name": MEMORY_GATEWAY_URL_ENV, "value": self._gateway_url},
                )
            )
        )
