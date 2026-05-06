"""MimirPort — abstract interface for the Mímir knowledge base.

Both the Mímir service (``src/mimir/``) and Ravn adapters (``src/ravn/``)
depend on this interface.  Neither module depends on the other.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from niuu.domain.mimir import (
    MimirLintReport,
    MimirPage,
    MimirPageMeta,
    MimirQueryResult,
    MimirSource,
    MimirSourceMeta,
    ThreadContextRef,
    ThreadState,
)


class MimirPort(ABC):
    """Abstract interface for the Mímir wiki knowledge base.

    Mímir maintains a persistent, LLM-written wiki that accumulates synthesised
    knowledge between agent sessions.  Raw sources flow in via ``ingest()``;
    the wiki layer is queried via ``query()`` and ``search()``; idle-time
    maintenance is driven by ``lint()``.
    """

    @abstractmethod
    async def ingest(self, source: MimirSource) -> list[str]:
        """Ingest a raw source and update relevant wiki pages.

        Returns a list of wiki page paths (relative to wiki root) that were
        created or updated.  Appends an entry to ``wiki/log.md``.
        """
        ...

    @abstractmethod
    async def query(self, question: str) -> MimirQueryResult:
        """Answer *question* from wiki knowledge.

        The adapter performs ranking; full synthesis is performed by the caller.
        """
        ...

    @abstractmethod
    async def lint(self, fix: bool = False) -> MimirLintReport:
        """Health-check the wiki across 12 check types (L01–L12).

        When *fix* is ``True``, auto-fixable issues (L05, L11, L12) are
        corrected in-place before the report is returned.  Appends an entry
        to ``wiki/log.md``.
        """
        ...

    @abstractmethod
    async def search(self, query: str) -> list[MimirPage]:
        """Full-text search over wiki pages, ranked by relevance."""
        ...

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

        The optional *meta* parameter carries updated page metadata (e.g. thread
        fields written by the thread enricher).  Adapters that support it will
        persist the metadata alongside the content; others may ignore it.
        """
        ...

    @abstractmethod
    async def read_page(self, path: str) -> str:
        """Return the raw Markdown content of the wiki page at *path*.

        Raises ``FileNotFoundError`` if the page does not exist.
        """
        ...

    @abstractmethod
    async def get_page(self, path: str) -> MimirPage:
        """Return content and metadata for the wiki page at *path* in one call.

        More efficient than calling ``read_page`` and ``list_pages`` separately.
        Raises ``FileNotFoundError`` if the page does not exist.
        """
        ...

    @abstractmethod
    async def list_pages(
        self,
        category: str | None = None,
    ) -> list[MimirPageMeta]:
        """List all wiki pages, optionally filtered to *category*.

        Returns lightweight metadata records — does not read full page content.
        """
        ...

    @abstractmethod
    async def read_source(self, source_id: str) -> MimirSource | None:
        """Return the full raw source by ID, or None if not found."""
        ...

    @abstractmethod
    async def list_sources(self, *, unprocessed_only: bool = False) -> list[MimirSourceMeta]:
        """List ingested raw sources.

        When *unprocessed_only* is True, returns only sources that are not yet
        referenced in any wiki page (i.e. no page carries a matching source_id).
        """
        ...

    # ------------------------------------------------------------------
    # Thread methods — optional extension point
    # ------------------------------------------------------------------
    # Not declared abstract so that existing adapters (HttpMimirAdapter,
    # CompositeMimirAdapter) do not need to implement them in this ticket.
    # Only MarkdownMimirAdapter provides a real implementation.
    # ------------------------------------------------------------------

    async def create_thread(
        self,
        title: str,
        weight: float = 0.5,
        context_refs: list[ThreadContextRef] | None = None,
        next_action_hint: str | None = None,
    ) -> MimirPage:
        """Create a new thread with the given title and initial metadata.

        Creates ``threads/{slug}.yaml`` and ``threads/{slug}.md`` under the
        Mímir root.  Returns a ``MimirPage`` representing the new thread.
        Raises ``FileExistsError`` if a thread with the same slug already exists.
        """
        raise NotImplementedError

    async def get_thread(self, path: str) -> MimirPage:
        """Return full thread data including the Markdown working notes.

        *path* is the stem path, e.g. ``"threads/retrieval-architecture"``.
        Raises ``FileNotFoundError`` if the thread YAML does not exist.
        """
        raise NotImplementedError

    async def get_thread_queue(
        self,
        owner_id: str | None = None,
        limit: int = 50,
    ) -> list[MimirPage]:
        """Return open threads sorted by weight descending.

        Hot path — only reads ``.yaml`` files, never opens ``.md`` files.
        Optionally filtered to *owner_id*.
        """
        raise NotImplementedError

    async def update_thread_state(self, path: str, state: ThreadState) -> None:
        """Transition a thread to *state*.

        Writes only the YAML file.  Raises ``FileNotFoundError`` if the thread
        does not exist.
        """
        raise NotImplementedError

    async def list_threads(
        self,
        state: ThreadState | None = None,
        limit: int = 100,
    ) -> list[MimirPage]:
        """List thread pages, optionally filtered by *state*."""
        raise NotImplementedError

    async def update_thread_weight(
        self,
        path: str,
        weight: float,
        signals: dict | None = None,
    ) -> None:
        """Update the weight score for a thread.

        Writes only the YAML file.  Raises ``FileNotFoundError`` if the thread
        does not exist.  If *signals* are provided they are stored alongside the
        weight for later recomputation.
        """
        raise NotImplementedError

    async def assign_thread_owner(self, path: str, owner_id: str | None) -> None:
        """Assign (or clear) the owner of a thread.

        Uses a ``.lock`` file for mutual exclusion.  Raises
        ``ThreadOwnershipError`` if the thread already has a different owner.
        Raises ``FileNotFoundError`` if the thread does not exist.
        """
        raise NotImplementedError
