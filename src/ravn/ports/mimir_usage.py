"""MimirUsagePort — abstract interface for tracking wiki page access frequency.

Implementations record which pages are read and surface the most-frequently-accessed
pages so the staleness trigger can prioritise refresh work.

Hexagonal design: swap implementations without touching business logic.
Initial implementation: ``LogBasedUsageAdapter`` (parses log.md query entries).
Future: ``RedisUsageAdapter``, ``PostgresUsageAdapter``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class MimirUsagePort(ABC):
    """Track how often wiki pages are accessed and surface hot pages."""

    @abstractmethod
    async def record_access(self, path: str) -> None:
        """Record a single access to the wiki page at *path*."""
        raise NotImplementedError

    @abstractmethod
    async def top_pages(self, n: int = 20) -> list[tuple[str, int]]:
        """Return the *n* most-accessed page paths with their access counts.

        Returns a list of ``(path, count)`` tuples, sorted descending by count.
        """
        raise NotImplementedError

    @abstractmethod
    async def pages_above_threshold(self, min_accesses: int) -> list[str]:
        """Return paths of pages accessed at least *min_accesses* times."""
        raise NotImplementedError
