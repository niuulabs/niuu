"""SourceOverlapGraphAdapter — MimirGraphPort backed by shared source_id edges.

Pages that reference the same raw source_id are considered related.  This is
the same logic used by ``GET /mimir/graph`` in ``mimir.router``, extracted into
a proper hexagonal adapter so it can be swapped without touching the router.

Future alternatives: ``SemanticSimilarityGraphAdapter`` (embeddings),
``TypedRelationGraphAdapter``.
"""

from __future__ import annotations

import logging

from mimir.ports.graph import MimirGraph, MimirGraphPort

logger = logging.getLogger(__name__)


class SourceOverlapGraphAdapter(MimirGraphPort):
    """MimirGraphPort that connects pages sharing at least one source_id.

    The graph is computed on demand from ``page_source_ids`` — no persistent
    state is maintained.
    """

    def __init__(self) -> None:
        # Cached graph keyed by frozenset of page paths for invalidation
        self._cached_graph: MimirGraph | None = None
        self._cached_key: frozenset[str] | None = None

    async def build_graph(self, page_source_ids: dict[str, list[str]]) -> MimirGraph:
        """Build edges between pages that share at least one source_id."""
        cache_key = frozenset(page_source_ids)
        if cache_key == self._cached_key and self._cached_graph is not None:
            return self._cached_graph

        nodes = list(page_source_ids)

        # Invert: source_id → list of pages that reference it
        source_to_pages: dict[str, list[str]] = {}
        for path, source_ids in page_source_ids.items():
            for sid in source_ids:
                source_to_pages.setdefault(sid, []).append(path)

        edges: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for page_paths in source_to_pages.values():
            for i, src in enumerate(page_paths):
                for tgt in page_paths[i + 1 :]:
                    key = (min(src, tgt), max(src, tgt))
                    if key not in seen:
                        seen.add(key)
                        edges.append(key)

        graph = MimirGraph(nodes=nodes, edges=edges)
        self._cached_graph = graph
        self._cached_key = cache_key
        return graph

    async def related_pages(self, path: str, limit: int = 5) -> list[str]:
        """Return pages sharing at least one source_id with *path*.

        Requires that ``build_graph`` has been called first with data that
        includes *path*.  Returns an empty list if the graph has not been built
        or *path* is not in the graph.
        """
        if self._cached_graph is None:
            return []

        related: list[str] = []
        for src, tgt in self._cached_graph.edges:
            if src == path and tgt not in related:
                related.append(tgt)
            elif tgt == path and src not in related:
                related.append(src)

        return related[:limit]
