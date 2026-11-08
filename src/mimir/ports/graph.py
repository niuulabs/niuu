"""MimirGraphPort — abstract interface for computing document relationship graphs.

Implementations derive connection edges between wiki pages and expose related-page
lookups so the staleness trigger and synthesis persona can follow concept clusters.

Hexagonal design: swap implementations without touching business logic.
Initial implementation: ``SourceOverlapGraphAdapter`` (shared source_id → edge).
Future: ``SemanticSimilarityGraphAdapter`` (embeddings), ``TypedRelationGraphAdapter``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class MimirGraph:
    """A lightweight in-memory representation of the wiki's document graph."""

    nodes: list[str] = field(default_factory=list)  # page paths
    edges: list[tuple[str, str]] = field(default_factory=list)  # (src_path, tgt_path)


class MimirGraphPort(ABC):
    """Compute and query document relationship graphs over Mímir wiki pages."""

    @abstractmethod
    async def build_graph(self, page_source_ids: dict[str, list[str]]) -> MimirGraph:
        """Build a graph from a mapping of page_path → source_ids.

        *page_source_ids* is a ``{path: [source_id, ...]}`` dict derived from
        ``list_pages()`` metadata.  Returns a ``MimirGraph`` with nodes (page
        paths) and edges (page pairs that share at least one source_id).
        """
        raise NotImplementedError

    @abstractmethod
    async def related_pages(self, path: str, limit: int = 5) -> list[str]:
        """Return up to *limit* page paths most strongly related to *path*.

        Relatedness is defined by the adapter's edge model (e.g. shared
        source_ids, semantic similarity, typed relations).
        """
        raise NotImplementedError
