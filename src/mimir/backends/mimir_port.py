"""MimirPortBackend — an attached memory: one registered Mímir store as a layer.

An ``attached`` layer (design section 4) is a memory a project or launch spec
adds beside the session's own layers: another Mímir, a registered gbrain wiki,
a vendor knowledge base. It lives in a store someone else curates, reached
through today's :class:`~niuu.ports.mimir.MimirPort` connection from the
gateway's own registry. This backend presents that store as a single-namespace
:class:`~mimir.ports.memory_backend.MemoryBackend` so the gateway reads and
merges it like any layer.

What such a store can do is less than a memory system can, and the backend says
so instead of pretending:

- It declares ``pages`` and ``search`` only. Facts, sources, changes and
  namespaces raise :class:`~mimir.memory.models.CapabilityMissingError`; the
  gateway never asks an attached layer for them.
- A store page carries no sealed attribution, so every page reads as
  **unattested**, at the lowest trust, authored by ``external:attached:<name>``.
- Writes keep the two-zone page format (``src/mimir/FORMAT.md``): a rewrite
  replaces the Compiled Truth zone and keeps the Timeline, and every write adds
  a timeline row naming who wrote it, at what trust and from what evidence,
  because the store has nowhere else to keep that.
- The store has no revision check. A revision here is a hash of the page's
  content, compared just before the write; a writer that lands between that
  read and the write is not detected.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

import yaml

from mimir.compiled_truth import (
    append_timeline_entry,
    parse_page,
    rewrite_compiled_truth,
)
from mimir.memory.models import (
    Attribution,
    CapabilityMissingError,
    ChangeSet,
    EntityLayer,
    HitKind,
    LayeredFact,
    LayeredRecall,
    MemoryBackendError,
    MemoryStoreError,
    MemoryValidationError,
    NamespaceRemoval,
    NewSource,
    NewTimelineRow,
    PageRecord,
    PageRef,
    PageWrite,
    RecordNotFoundError,
    RememberResult,
    RevisionConflictError,
    SearchHit,
    SourceRecord,
    SourceRef,
    TimelineRow,
)
from mimir.ports.memory_backend import Capability, MemoryBackend
from niuu.domain.mimir import MimirPage
from niuu.domain.mimir_facts import NewFact, TrustLevel
from niuu.ports.mimir import MimirPort

_CAPABILITIES = frozenset({Capability.PAGES, Capability.SEARCH})


def _revision(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


class MimirPortBackend(MemoryBackend):
    """Args:
    port: the store, opened from the gateway's registry.
    namespace: the attached layer's namespace (``attached-<name>``); the only one served.
    label: the attachment's name, used in authorship and messages.
    """

    def __init__(self, port: MimirPort, *, namespace: str, label: str) -> None:
        self._port = port
        self._namespace = namespace
        self._label = label

    @property
    def capabilities(self) -> frozenset[Capability]:
        return _CAPABILITIES

    @property
    def port(self) -> MimirPort:
        return self._port

    async def close(self) -> None:
        for name in ("close", "aclose"):
            closer = getattr(self._port, name, None)
            if closer is not None:
                await closer()
                return

    # -- plumbing --------------------------------------------------------

    def _check(self, namespace: str) -> None:
        if namespace != self._namespace:
            raise MemoryValidationError(
                f"attached memory {self._label} holds only namespace {self._namespace}, "
                f"not {namespace}"
            )

    def _missing(self, what: str) -> CapabilityMissingError:
        return CapabilityMissingError(
            f"attached memory {self._label} is a registered Mímir store: it holds pages, not "
            f"{what}. Use a layer of the org's memory system for {what}"
        )

    async def _call[T](self, what: str, call: Any) -> T:
        try:
            return await call
        except FileNotFoundError as exc:
            raise RecordNotFoundError(f"attached memory {self._label} has no {what}") from exc
        except MemoryStoreError:
            raise
        except Exception as exc:
            raise MemoryBackendError(
                f"attached memory {self._label} failed to {what.split(' ', 1)[0]} "
                f"({type(exc).__name__}: {exc})"
            ) from exc

    def _unattested(self, at: datetime | None) -> Attribution:
        return Attribution(
            author=f"external:attached:{self._label}",
            trust=TrustLevel.AGENT,
            evidence=(),
            written_at=at or datetime.now(UTC),
            attested=False,
        )

    async def _raw(self, slug: str) -> MimirPage:
        return await self._call(f"page {slug}", self._port.get_page(slug))

    def _record(self, slug: str, page: MimirPage) -> PageRecord:
        parsed = parse_page(page.content)
        truth = parsed.compiled_truth.strip() or page.content.strip()
        updated = page.meta.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        rows = []
        for entry in parsed.timeline_entries:
            try:
                on = date.fromisoformat(entry.date)
            except ValueError:
                on = updated.date()
            rows.append(
                TimelineRow(
                    on=on,
                    summary=entry.description or entry.raw,
                    detail=f"[Source: {entry.source}]" if entry.source else "",
                    attribution=self._unattested(updated),
                )
            )
        return PageRecord(
            slug=slug,
            namespace=self._namespace,
            title=page.meta.title or slug,
            page_type=str(page.meta.page_type or ""),
            compiled_truth=truth,
            revision=_revision(page.content),
            attribution=self._unattested(updated),
            updated_at=updated,
            timeline=tuple(rows),
        )

    @staticmethod
    def _provenance_line(on: date, summary: str, attribution: Attribution) -> str:
        cited = ", ".join(e.ref for e in attribution.evidence) or "no evidence cited"
        return (
            f"- {on.isoformat()}: {summary} "
            f"[Source: {attribution.author} ({attribution.trust.label}); {cited}]"
        )

    # -- pages -----------------------------------------------------------

    async def get_page(self, namespace: str, slug: str) -> PageRecord:
        self._check(namespace)
        return self._record(slug, await self._raw(slug))

    async def put_page(
        self,
        namespace: str,
        slug: str,
        page: PageWrite,
        attribution: Attribution,
        *,
        expected_revision: str | None,
    ) -> PageRecord:
        self._check(namespace)
        try:
            current: MimirPage | None = await self._raw(slug)
        except RecordNotFoundError:
            current = None
        if expected_revision is None and current is not None:
            raise RevisionConflictError(
                f"page {slug} already exists in attached memory {self._label}; read it and "
                "pass its revision to rewrite it"
            )
        if expected_revision is not None:
            if current is None:
                raise RecordNotFoundError(f"attached memory {self._label} has no page {slug}")
            if _revision(current.content) != expected_revision:
                raise RevisionConflictError(
                    f"page {slug} in attached memory {self._label} is at revision "
                    f"{_revision(current.content)}, not {expected_revision}: read it again"
                )
            content = rewrite_compiled_truth(current.content, page.compiled_truth.strip())
        else:
            front: dict[str, Any] = {"title": page.title or slug}
            if page.page_type:
                front["type"] = page.page_type
            header = yaml.safe_dump(front, sort_keys=False, allow_unicode=True)
            content = (
                f"---\n{header}---\n\n## Compiled Truth\n\n{page.compiled_truth.strip()}\n\n"
                "## Timeline\n"
            )
        content = append_timeline_entry(
            content,
            self._provenance_line(
                attribution.written_at.date(), "compiled truth written", attribution
            ),
        )
        await self._call(f"write page {slug}", self._port.upsert_page(slug, content))
        return await self.get_page(namespace, slug)

    async def append_timeline(
        self, namespace: str, slug: str, row: NewTimelineRow, attribution: Attribution
    ) -> None:
        self._check(namespace)
        current = await self._raw(slug)
        summary = row.summary + (f" — {row.detail}" if row.detail else "")
        line = self._provenance_line(row.on, summary, attribution)
        content = append_timeline_entry(current.content, line)
        await self._call(f"write page {slug}", self._port.upsert_page(slug, content))

    async def delete_page(self, namespace: str, slug: str) -> None:
        self._check(namespace)
        removed = await self._call(f"delete page {slug}", self._port.delete_page(slug))
        if not removed:
            raise RecordNotFoundError(f"attached memory {self._label} has no page {slug}")

    async def list_pages(self, namespace: str, *, limit: int) -> list[PageRef]:
        self._check(namespace)
        metas = await self._call("list pages", self._port.list_pages())
        return [
            PageRef(slug=m.path, namespace=namespace, title=m.title, updated_at=m.updated_at)
            for m in metas[:limit]
        ]

    async def backlinks(self, namespace: str, slug: str) -> list[PageRef]:
        self._check(namespace)
        graph = await self._call("read its link graph", self._port.get_graph())
        titles = {node.id: node.title for node in graph.nodes}
        sources = dict.fromkeys(e.source for e in graph.edges if e.target == slug)
        return [
            PageRef(slug=source, namespace=namespace, title=titles.get(source, source))
            for source in sources
        ]

    # -- search ----------------------------------------------------------

    async def search(
        self,
        namespaces: Sequence[str],
        query: str,
        *,
        kinds: frozenset[HitKind],
        limit: int,
    ) -> list[SearchHit]:
        for namespace in namespaces:
            self._check(namespace)
        if HitKind.PAGE not in kinds or not namespaces:
            return []
        if not query.strip():
            raise MemoryValidationError("a search needs a query")
        pages = await self._call("search", self._port.search(query))
        return [
            SearchHit(
                kind=HitKind.PAGE,
                ref=page.meta.path,
                namespace=self._namespace,
                title=page.meta.title or page.meta.path,
                snippet=page.meta.summary,
            )
            for page in pages[:limit]
        ]

    # -- what an attached store does not hold ------------------------------

    async def put_source(
        self, namespace: str, source: NewSource, attribution: Attribution
    ) -> tuple[SourceRecord, bool]:
        raise self._missing("sources")

    async def get_source(self, namespace: str, source_id: str) -> SourceRecord:
        raise self._missing("sources")

    async def list_sources(self, namespace: str, *, limit: int) -> list[SourceRef]:
        raise self._missing("sources")

    async def remember(self, namespace: str, new: NewFact) -> RememberResult:
        raise self._missing("facts")

    async def get_fact(self, fact_id: str) -> LayeredFact:
        raise self._missing("facts")

    async def recall(
        self,
        namespaces: Sequence[str],
        *,
        subject: str | None,
        contains: str | None = None,
        as_of: datetime | None = None,
    ) -> LayeredRecall:
        raise self._missing("facts")

    async def retire(
        self, namespace: str, fact_id: str, *, by: str, why: str, superseded_by: str | None = None
    ) -> LayeredFact:
        raise self._missing("facts")

    async def entity(self, namespace: str, subject: str) -> EntityLayer:
        raise self._missing("facts or entity cards")

    async def delta(self, namespaces: Sequence[str], *, since: str, limit: int) -> ChangeSet:
        raise self._missing("a change feed")

    async def create_namespace(self, namespace: str) -> bool:
        raise self._missing("namespaces")

    async def delete_namespace(self, namespace: str) -> NamespaceRemoval:
        raise self._missing("namespaces")

    async def list_namespaces(self) -> list[str]:
        raise self._missing("namespaces")
