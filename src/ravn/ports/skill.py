"""Skill port — interface for skill discovery and storage."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ravn.domain.models import Episode, Skill


class SkillPort(ABC):
    """Abstract interface for skill extraction and retrieval.

    Implementations detect when a reusable *skill* can be extracted from
    recurring successful episode patterns and persist discovered skills for
    future injection into agent context.
    """

    @abstractmethod
    async def record_episode(self, episode: Episode) -> Skill | None:
        """Inspect *episode* and optionally suggest a new skill.

        Called after each episode is recorded in episodic memory.  When
        the implementation detects that *episode* completes a recurring
        success pattern (same tool/tag combination seen >= threshold times),
        it returns a newly created ``Skill``; otherwise returns ``None``.

        The returned skill has already been persisted by the implementation.
        """
        raise NotImplementedError

    @abstractmethod
    async def list_skills(self, query: str | None = None) -> list[Skill]:
        """List discovered skills, optionally filtered by *query* text.

        Returns all stored skills when *query* is ``None`` or empty.
        """
        raise NotImplementedError

    @abstractmethod
    async def record_skill(self, skill: Skill) -> None:
        """Persist *skill* directly (bypass automatic discovery)."""
        raise NotImplementedError

    async def get_skill(self, name: str) -> Skill | None:
        """Return the skill with the given *name*, or None if not found.

        The default implementation calls :meth:`list_skills` and filters by
        name (case-insensitive).  Implementations may override this for
        more efficient lookup.
        """
        skills = await self.list_skills()
        name_lower = name.lower()
        for skill in skills:
            if skill.name.lower() == name_lower:
                return skill
        return None
