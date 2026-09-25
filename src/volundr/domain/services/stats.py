"""Domain service for aggregate statistics."""

from __future__ import annotations

from volundr.domain.models import Principal, Stats
from volundr.domain.ports import StatsRepository

from .session import SessionService


class StatsService:
    """Aggregate statistics over the sessions a principal may list."""

    def __init__(self, repository: StatsRepository, sessions: SessionService):
        self._repository = repository
        self._sessions = sessions

    async def get_stats(self, principal: Principal | None) -> Stats:
        """Get aggregate statistics for the dashboard, bounded like ``list_sessions``.

        A caller's figures cover its own sessions, or its tenant's as a tenant
        admin; never another tenant's.

        Raises:
            PermissionError: Authorization is configured and no principal was given.
        """
        tenant_id, owner_id = self._sessions.visibility_scope(principal)
        return await self._repository.get_stats(tenant_id=tenant_id, owner_id=owner_id)
