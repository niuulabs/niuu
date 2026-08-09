"""Port interface for pluggable full-text and semantic search."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SearchResult:
    """A single result returned by a search query.

    ``score`` is a normalised relevance value in ``[0, 1]`` where higher is
    better.  Callers may apply domain-specific post-processing (e.g. recency
    decay, outcome weighting) on top of this base score.
    """

    id: str
    content: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class SearchPort(ABC):
    """Port for pluggable full-text and semantic search backends.

    Implementations may support keyword-only search (FTS), semantic search
    (vector similarity), or hybrid retrieval (both merged via RRF).

    The port is content-agnostic: callers pass arbitrary ``content`` strings
    and ``metadata`` dicts.  Domain-specific scoring (e.g. recency decay) is
    the caller's responsibility — the port returns base relevance scores and
    domain adapters (e.g. Mímir's markdown adapter) may apply their own
    boosts on top before surfacing results.
    """

    @abstractmethod
    async def index(
        self,
        id: str,
        content: str,
        metadata: dict[str, Any],
        *,
        embedding: list[float] | None = None,
    ) -> None:
        """Add or replace a document in the search index.

        Args:
            id: Stable unique identifier for this document.
            content: The text to index and search against.
            metadata: Arbitrary key/value data stored alongside the document
                and returned in ``SearchResult.metadata``.
            embedding: Optional pre-computed embedding vector.  When supplied,
                implementations should store it and use it for hybrid
                retrieval without recomputing it via an embed function.
        """

    async def initialize(self) -> None:
        """Set up any resources required by the adapter (e.g. connection pools).

        Default is a no-op; stateful backends override this.
        """

    async def close(self) -> None:
        """Release resources held by the adapter (e.g. connection pools).

        Default is a no-op; stateful backends override this.
        """

    @abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Return the top *limit* documents matching *query*.

        Results are ordered by descending score.  An empty list is returned
        when no documents match or the index is empty.

        Args:
            query: Free-text search query.
            limit: Maximum number of results to return.
        """

    @abstractmethod
    async def remove(self, id: str) -> None:
        """Remove a document from the index by its *id*.

        No-op if the document does not exist.
        """

    async def unembedded(self, limit: int = 500) -> list[tuple[str, str, dict[str, Any]]]:
        """Return indexed documents that carry no embedding.

        Turning embeddings on only affects new writes, so an existing corpus
        stays lexical-only until it is backfilled. Implementations without a
        vector column return an empty list.
        """
        return []

    @abstractmethod
    async def rebuild(self) -> None:
        """Rebuild the search index from the stored documents.

        Useful after bulk mutations or to repair a corrupted index.
        """
