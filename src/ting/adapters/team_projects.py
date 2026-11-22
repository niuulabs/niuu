"""Which projects' team trackers a person has, whichever Forge is each project's home."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from niuu.domain.project_team import TeamMemberships
from ting.ports.volundr import VolundrFactory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TeamProject:
    """A project someone is on, and which of its connections count.

    ``connection_ids`` are those of the team the Guild records; ``None`` is every one the
    project has here, for a project only its own Forge vouches for.
    """

    project_id: str
    connection_ids: frozenset[str] | None = None

    def has(self, connection_id: str) -> bool:
        return self.connection_ids is None or connection_id in self.connection_ids


class TeamRecords(Protocol):
    async def memberships(self, owner_id: str, project_ids: list[str]) -> TeamMemberships: ...


class TeamProjects:
    """The Guild's record decides. A project no Forge has published yet is asked of the
    owner's primary Forge, which is also all there is without a Guild."""

    def __init__(self, forges: VolundrFactory, guild: TeamRecords | None) -> None:
        self._forges = forges
        self._guild = guild

    async def __call__(self, owner_id: str) -> tuple[list[TeamProject], list[str]]:
        """The owner's projects, and what could not be asked."""
        failures: list[str] = []
        local: list[str] = []
        try:
            local = await self._local(owner_id)
        except Exception as exc:
            logger.error(
                "The primary Forge could not list the projects of %s", owner_id, exc_info=True
            )
            failures.append(f"the primary Forge could not list the owner's projects: {exc}")
        if self._guild is None:
            return [TeamProject(project_id) for project_id in local], failures
        try:
            known = await self._guild.memberships(owner_id, local)
        except Exception as exc:
            logger.error("The Guild could not list the projects of %s", owner_id, exc_info=True)
            # Without the Guild's record, a Forge's own word is not enough.
            return [], [*failures, f"the Guild could not list the owner's projects: {exc}"]
        recorded = [
            TeamProject(str(one.project_id), frozenset(map(str, one.connection_ids)))
            for one in known.member_of
        ]
        return [*recorded, *(TeamProject(str(pid)) for pid in known.unrecorded)], failures

    async def _local(self, owner_id: str) -> list[str]:
        primary = await self._forges.primary_for_owner(owner_id)
        if primary is None:
            raise LookupError(
                f"owner {owner_id} has no Forge connection, so their project memberships "
                "cannot be read; connect a Forge for this owner"
            )
        return await primary.list_member_project_ids()
