"""MemoryBackend — the one contract every memory system behind the gateway implements.

Design section 7. A memory system stores layers as **namespaces** and every
method names the namespace (or namespaces) it acts on: the backend never decides
which layers a caller may see. The gateway resolves layers, applies the write
rules and trust, merges reads, and keeps proposals; a backend only stores and
returns records.

A backend declares its :class:`Capability` set. A layer needs the capabilities
its kind lists in :data:`LAYER_CAPABILITIES`; placing a layer on a system that
lacks one fails when the layer is placed (:func:`require_capabilities`), never
at the first call.

Failures raise: :class:`~mimir.memory.models.RecordNotFoundError` for a missing
record (an answer, not a fault), :class:`~mimir.memory.models.RevisionConflictError`
when a page moved on, and :class:`~mimir.memory.models.MemoryBackendError` for
everything the system could not do. An empty list is "nothing there", never
"could not look".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum

from mimir.memory.models import (
    Attribution,
    CapabilityMissingError,
    ChangeSet,
    EntityLayer,
    HitKind,
    LayeredFact,
    LayeredRecall,
    LayerKind,
    NamespaceRemoval,
    NewSource,
    NewTimelineRow,
    PageRecord,
    PageRef,
    PageWrite,
    RememberResult,
    SearchHit,
    SourceRecord,
    SourceRef,
)
from niuu.domain.mimir_facts import NewFact


class Capability(StrEnum):
    SOURCES = "sources"
    PAGES = "pages"
    FACTS = "facts"
    SEARCH = "search"
    CHANGES = "changes"
    NAMESPACES = "namespaces"


ALL_CAPABILITIES: frozenset[Capability] = frozenset(Capability)

#: What each layer kind needs from the system it is placed on. The org and
#: project layers are long-lived and answer "what changed"; the run and session
#: layers are created and removed with their run or session. An attached memory
#: is a store someone else curates: the gateway reads its pages and searches it,
#: and writes pages to it when the attachment allows; it holds no facts and is
#: never created or removed by the gateway.
LAYER_CAPABILITIES: dict[LayerKind, frozenset[Capability]] = {
    LayerKind.ORG: ALL_CAPABILITIES,
    LayerKind.PROJECT: ALL_CAPABILITIES,
    LayerKind.RUN: ALL_CAPABILITIES,
    LayerKind.SESSION: ALL_CAPABILITIES,
    LayerKind.ATTACHED: frozenset({Capability.PAGES, Capability.SEARCH}),
}


def require_capabilities(system: str, backend: MemoryBackend, kind: LayerKind) -> None:
    """Raise unless *backend* can hold a layer of *kind*."""
    missing = LAYER_CAPABILITIES[kind] - backend.capabilities
    if missing:
        names = ", ".join(sorted(c.value for c in missing))
        raise CapabilityMissingError(
            f"memory system {system!r} cannot hold a {kind.value} layer: it lacks {names}. "
            "Place the layer on a system that declares them, or extend the backend"
        )


class MemoryBackend(ABC):
    """One memory system, holding layers as namespaces."""

    @property
    @abstractmethod
    def capabilities(self) -> frozenset[Capability]:
        """What this system can do."""

    # -- sources ---------------------------------------------------------

    @abstractmethod
    async def put_source(
        self, namespace: str, source: NewSource, attribution: Attribution
    ) -> tuple[SourceRecord, bool]:
        """Store raw material. Returns the record and whether it was new.

        A source is never changed: storing the same content again returns the
        record already there (with its own attribution) and ``False``.
        """

    @abstractmethod
    async def get_source(self, namespace: str, source_id: str) -> SourceRecord: ...

    @abstractmethod
    async def list_sources(self, namespace: str, *, limit: int | None) -> list[SourceRef]:
        """Up to *limit* sources; every source when *limit* is None."""

    # -- pages -----------------------------------------------------------

    @abstractmethod
    async def get_page(self, namespace: str, slug: str) -> PageRecord: ...

    @abstractmethod
    async def put_page(
        self,
        namespace: str,
        slug: str,
        page: PageWrite,
        attribution: Attribution,
        *,
        expected_revision: str | None,
    ) -> PageRecord:
        """Set a page's compiled truth, keeping its timeline.

        ``expected_revision`` None creates the page and fails if it exists;
        otherwise the write fails with RevisionConflictError unless the page is
        still at that revision.
        """

    @abstractmethod
    async def append_timeline(
        self, namespace: str, slug: str, row: NewTimelineRow, attribution: Attribution
    ) -> None:
        """Append one cited row to an existing page's timeline."""

    @abstractmethod
    async def delete_page(self, namespace: str, slug: str) -> None:
        """Remove one page. RecordNotFoundError when there is none."""

    @abstractmethod
    async def list_pages(self, namespace: str, *, limit: int | None) -> list[PageRef]:
        """Up to *limit* pages; every page when *limit* is None."""

    @abstractmethod
    async def backlinks(self, namespace: str, slug: str) -> list[PageRef]:
        """Pages of *namespace* that link to *slug*."""

    # -- facts -----------------------------------------------------------

    @abstractmethod
    async def remember(self, namespace: str, new: NewFact) -> RememberResult: ...

    @abstractmethod
    async def get_fact(self, fact_id: str) -> LayeredFact:
        """A fact by id, with the namespace its provenance seals."""

    @abstractmethod
    async def recall(
        self,
        namespaces: Sequence[str],
        *,
        subject: str | None,
        contains: str | None = None,
        as_of: datetime | None = None,
    ) -> LayeredRecall:
        """Facts of these namespaces, current at *as_of* (now when None)."""

    @abstractmethod
    async def retire(
        self, namespace: str, fact_id: str, *, by: str, why: str, superseded_by: str | None = None
    ) -> LayeredFact: ...

    @abstractmethod
    async def entity(self, namespace: str, subject: str) -> EntityLayer: ...

    # -- search ----------------------------------------------------------

    @abstractmethod
    async def search(
        self,
        namespaces: Sequence[str],
        query: str,
        *,
        kinds: frozenset[HitKind],
        limit: int | None,
    ) -> list[SearchHit]:
        """Ranked hits from these namespaces, best first.

        *limit* None means the caller asked for no cap: a backend adds none of its own and
        returns what the system's search returns (it does not page or truncate). With a
        limit, at most that many.
        """

    # -- changes ---------------------------------------------------------

    @abstractmethod
    async def delta(self, namespaces: Sequence[str], *, since: str, limit: int) -> ChangeSet:
        """Pages and facts of these namespaces changed after *since*, oldest first.

        *since* is an ISO 8601 time or the ``cursor`` of an earlier ChangeSet.
        """

    # -- namespaces ------------------------------------------------------

    @abstractmethod
    async def create_namespace(self, namespace: str) -> bool:
        """Make *namespace* ready for writes. Returns False when it already existed."""

    @abstractmethod
    async def delete_namespace(self, namespace: str) -> NamespaceRemoval:
        """Remove every page of *namespace* and withdraw its facts.

        Removing something already gone is not an error: the result says what was removed.
        """

    @abstractmethod
    async def list_namespaces(self) -> list[str]:
        """Every namespace created on this system, all of them (the retention sweep's input)."""

    async def close(self) -> None:  # noqa: B027 — optional hook, nothing to release by default
        """Release connections."""
