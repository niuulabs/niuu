"""Web search port — interface for pluggable search providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SearchResult:
    """A single search result entry."""

    title: str
    url: str
    snippet: str


class WebSearchPort(ABC):
    """Abstract interface for a web search provider."""

    @abstractmethod
    async def search(self, query: str, *, num_results: int) -> list[SearchResult]:
        """Search the web and return a list of results.

        Args:
            query: The search query string.
            num_results: Maximum number of results to return.

        Returns:
            Ordered list of search results (may be fewer than num_results).
        """
        raise NotImplementedError
