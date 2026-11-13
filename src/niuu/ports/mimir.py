"""MimirPort — abstract interface for the Mímir knowledge base.

Both the Mímir service (``src/mimir/``) and the clients that call it
depend on this interface.  Neither module depends on the other.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace

from niuu.domain.knowledge_graph import KnowledgeGraph
from niuu.domain.mimir import (
    MimirMountSummary,
    MimirPage,
    MimirPageMeta,
    MimirQueryResult,
    MimirSource,
    MimirSourceMeta,
)


class MimirPort(ABC):
    """Abstract interface for the Mímir wiki knowledge base.

    Mímir maintains a persistent, LLM-written wiki that accumulates synthesised
    knowledge between agent sessions.  Raw sources flow in via ``ingest()``;
    the wiki layer is queried via ``query()`` and ``search()``.

    Adapters may expose optional capabilities beyond this interface (link
    graph traversal, entity index); callers discover them via
    ``getattr`` and degrade gracefully (the HTTP router answers 501).
    """

    async def inspect_instance(self) -> dict:
        return {
            "backend": "unknown",
            "metrics": {},
            "unavailable": ["Instance inspection is not supported by this adapter"],
        }

    async def get_graph(self) -> KnowledgeGraph:
        """Project stored pages into the common exploration graph.

        This complete implementation reads page bodies for explicit links.
        Indexed backends may override it with an equivalent bulk projection.
        Errors propagate: a partial corpus must not appear to be a full graph.
        """
        from niuu.domain.knowledge_graph import project_pages

        pages = [await self.get_page(meta.path) for meta in await self.list_pages()]
        return project_pages(pages)

    async def summarize(self) -> MimirMountSummary:
        """Return a cheap scale/health summary of this mount.

        Mount listings and the stats endpoint are polled continuously, so this
        must not depend on corpus size the way ``list_pages()`` does.  Adapters
        that can answer from filesystem metadata or a remote summary endpoint
        should override it; this fallback keeps naive implementations correct
        at the cost of walking the corpus.

        """
        pages = await self.list_pages()
        sources = await self.list_sources()
        timestamps = [page.updated_at for page in pages]
        timestamps.extend(source.ingested_at for source in sources)
        return MimirMountSummary(
            page_count=len(pages),
            source_count=len(sources),
            categories=sorted({page.category for page in pages}),
            last_write=max(timestamps) if timestamps else None,
        )

    @abstractmethod
    async def ingest(self, source: MimirSource) -> list[str]:
        """Ingest a raw source and update relevant wiki pages.

        Returns a list of wiki page paths (relative to wiki root) that were
        created or updated.  Appends an entry to ``wiki/log.md``.
        """
        raise NotImplementedError

    @abstractmethod
    async def query(self, question: str) -> MimirQueryResult:
        """Answer *question* from wiki knowledge.

        The adapter performs ranking; full synthesis is performed by the caller.
        """
        raise NotImplementedError

    @abstractmethod
    async def search(self, query: str) -> list[MimirPage]:
        """Full-text search over wiki pages, ranked by relevance."""
        raise NotImplementedError

    @abstractmethod
    async def upsert_page(
        self,
        path: str,
        content: str,
        mimir: str | None = None,
        meta: MimirPageMeta | None = None,
    ) -> None:
        """Create or replace a wiki page at *path*.

        *path* is relative to the wiki root (e.g. ``"technical/ravn/tools.md"``).
        Updates ``wiki/index.md`` if the page is new.

        The optional *mimir* parameter is used by ``CompositeMimirAdapter`` to
        route writes to a specific named Mímir instance, bypassing the default
        category-based routing.

        The optional *meta* parameter carries updated page metadata.  Adapters
        that support it will persist the metadata alongside the content; others
        may ignore it.
        """
        raise NotImplementedError

    async def delete_page(self, path: str, mimir: str | None = None) -> bool:
        """Delete a wiki page and its derived indexes.

        Returns ``True`` when a page was removed and ``False`` when it did not
        exist. Adapters that cannot delete pages should raise
        ``NotImplementedError``.
        """
        raise NotImplementedError

    @abstractmethod
    async def read_page(self, path: str) -> str:
        """Return the raw Markdown content of the wiki page at *path*.

        Raises ``FileNotFoundError`` if the page does not exist.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_page(self, path: str) -> MimirPage:
        """Return content and metadata for the wiki page at *path* in one call.

        More efficient than calling ``read_page`` and ``list_pages`` separately.
        Raises ``FileNotFoundError`` if the page does not exist.
        """
        raise NotImplementedError

    @abstractmethod
    async def list_pages(
        self,
        category: str | None = None,
        prefix: str | None = None,
    ) -> list[MimirPageMeta]:
        """List all wiki pages, optionally filtered to *category* and/or *prefix*.

        Returns lightweight metadata records — does not read full page content.
        """
        raise NotImplementedError

    @abstractmethod
    async def read_source(self, source_id: str) -> MimirSource | None:
        """Return the full raw source by ID, or None if not found."""
        raise NotImplementedError

    async def read_source_excerpt(
        self,
        source_id: str,
        max_chars: int,
    ) -> MimirSource | None:
        """Return a raw source whose content is bounded to *max_chars*.

        Callers that only ever use a bounded prefix — the synthesis trigger
        truncates to its context budget — should ask for that prefix rather
        than pull the whole blob and throw most of it away.  Raw sources reach
        several megabytes, so over HTTP this is the difference between
        transferring the corpus and transferring what will actually be read.

        A non-positive *max_chars* means no bound.  This fallback fetches in
        full and truncates locally, which is correct but saves nothing;
        adapters that can bound it at the source override it.
        """
        source = await self.read_source(source_id)
        if source is None or max_chars <= 0 or len(source.content) <= max_chars:
            return source
        return replace(source, content=source.content[:max_chars])

    @abstractmethod
    async def list_sources(self, *, unprocessed_only: bool = False) -> list[MimirSourceMeta]:
        """List ingested raw sources.

        When *unprocessed_only* is True, returns only sources awaiting
        synthesis: not yet referenced in any wiki page (no page carries a
        matching source_id) and not of an operational type
        (``OPERATIONAL_SOURCE_TYPES``) — exhaust is never synthesis work, so
        it must not read as backlog.
        """
        raise NotImplementedError
