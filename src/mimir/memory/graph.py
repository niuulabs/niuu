"""The knowledge graph of the layers a person can read.

Nodes are the pages of those layers and the sources their timelines cite; edges are
derived **at read time from page text**, never from gbrain's own links:

- a ``[[slug]] — rel: <verb> — …`` line of a page's compiled truth is an edge with
  relation ``<verb>``; any other ``[[slug]]`` in it is ``mentions``;
- a timeline row whose evidence cites ``source:<id>`` is a ``cites`` edge from the
  page to that source, dated by the row.

A link resolves to the nearest readable layer that holds the page (the page's own
layer first, then by layer distance: project before org). A link to no readable
page is not an edge; it is listed under ``unresolved``, so a reader can see what the
text points at and the graph cannot show.

Reads are bounded by ``memory.graph_concurrency`` and a graph is kept per (org,
layer set) for ``memory.graph_cache_seconds``. A layer that cannot be read fails the
whole build naming the layer: a partial graph is never presented as the whole one.
A layer with more pages (or sources) than ``memory.graph_max_records_per_layer``
fails it too, counted on the cap counter.
"""

from __future__ import annotations

import asyncio
import posixpath
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from mimir.compiled_truth import extract_wikilinks
from mimir.memory.models import (
    EvidenceKind,
    Layer,
    MemoryStoreError,
    PageRecord,
    SourceKind,
    SourceRecord,
    SourceRef,
)
from mimir.memory.person_layers import ReadableLayer
from mimir.memory.systems import MemorySystems
from niuu.cap_telemetry import record_cap_hit

#: The cap_telemetry knob a layer larger than the graph reads is counted under.
MAX_RECORDS_KNOB = "memory.graph_max_records_per_layer"

MENTIONS = "mentions"
CITES = "cites"
#: A source's path inside its layer. Public page slugs never start with ``_``.
SOURCE_FOLDER = "_sources"

#: A relationship line: ``- [[slug]] — rel: works_at — description`` (the bullet is optional).
_TYPED = re.compile(
    r"^\s*(?:[-*+]\s+)?\[\[([^\[\]]+)\]\]\s*[—–-]+\s*rel:\s*([A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)
_WIKILINK = re.compile(r"\[\[([^\[\]|#]+)(?:[|#][^\[\]]*)?\]\]")
#: A Markdown link to another page of the same memory: ``[text](../technical/x.md#part)``.
_MD_LINK = re.compile(r"\[[^\]]*\]\((?!\w+://)([^)\s#]+?\.md)(?:#[^)]*)?\)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")
_BULLET = re.compile(r"^(?:[-*+>]\s+)+")
_MARKUP = re.compile(r"(\*\*|__|`)")

#: What a page is called in the graph, from its ``type`` or its first folder.
_KIND_BY_TYPE = {
    "decision": "decision",
    "person": "person",
    "service": "service",
    "ticket": "ticket",
    "runbook": "runbook",
}
_KIND_BY_FOLDER = {
    "decisions": "decision",
    "people": "person",
    "services": "service",
    "tickets": "ticket",
    "runbooks": "runbook",
}
PAGE_KIND = "page"
SOURCE_KIND = "source"
TICKET_KIND = "ticket"


class GraphError(MemoryStoreError):
    """A graph that could not be built. ``layer`` is the layer id it could not be built for."""

    def __init__(self, layer: str, message: str) -> None:
        super().__init__(message)
        self.layer = layer


class GraphLayerError(GraphError):
    """A layer could not be read."""


class GraphTooLargeError(GraphError):
    """A layer holds more records than ``memory.graph_max_records_per_layer``."""


def normalize_slug(raw: str) -> str:
    """A link target as a page slug: no alias, anchor or surrounding slashes, lowercase."""
    return raw.split("|", 1)[0].split("#", 1)[0].strip().strip("/").lower()


def page_kind(page_type: str, slug: str) -> str:
    """``decision``, ``person``, ``service``, ``ticket``, ``runbook`` or ``page``."""
    kind = _KIND_BY_TYPE.get(page_type.strip().lower())
    if kind is not None:
        return kind
    return _KIND_BY_FOLDER.get(slug.split("/", 1)[0], PAGE_KIND)


def first_sentence(compiled_truth: str) -> str:
    """The first sentence of the compiled truth's first paragraph of prose.

    Headings and relationship lines are not prose; a link reads as its slug.
    """
    paragraph: list[str] = []
    for line in compiled_truth.splitlines():
        text = _BULLET.sub("", line.strip())
        if not text:
            if paragraph:
                break
            continue
        if text.startswith("#") or _TYPED.match(line):
            if paragraph:
                break
            continue
        paragraph.append(_WIKILINK.sub(lambda m: m.group(1).strip(), _MARKUP.sub("", text)))
    if not paragraph:
        return ""
    return _SENTENCE_END.split(" ".join(paragraph), maxsplit=1)[0].strip()


@dataclass(frozen=True)
class _Page:
    layer: Layer
    record: PageRecord

    @property
    def node_id(self) -> str:
        return node_id(self.layer, self.record.slug)


def node_id(layer: Layer, path: str) -> str:
    return f"{layer.id}/{path}"


class KnowledgeGraph:
    """Builds, and briefly keeps, the graph of a set of layers.

    Args:
        systems: the memory systems and which org lives on which.
        concurrency: reads in flight at once against the memory system.
        max_records_per_layer: pages (and, separately, sources) one layer may hold.
        cache_seconds: how stale a graph may be; 0 keeps nothing.
        clock: monotonic seconds; the tests move it.
    """

    def __init__(
        self,
        systems: MemorySystems,
        *,
        concurrency: int,
        max_records_per_layer: int,
        cache_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if concurrency < 1:
            raise ValueError("memory.graph_concurrency must be at least 1")
        if max_records_per_layer < 1:
            raise ValueError(f"{MAX_RECORDS_KNOB} must be at least 1")
        if cache_seconds < 0:
            raise ValueError("memory.graph_cache_seconds cannot be negative")
        self._systems = systems
        self._concurrency = concurrency
        self._max = max_records_per_layer
        self._cache_seconds = cache_seconds
        self._clock = clock
        self._cache: dict[tuple[str, frozenset[str]], tuple[float, dict[str, Any]]] = {}
        self._locks: dict[tuple[str, frozenset[str]], asyncio.Lock] = {}

    # -- cache -----------------------------------------------------------

    async def graph(self, tenant_id: str, layers: Sequence[ReadableLayer]) -> dict[str, Any]:
        """The graph of *layers* of org *tenant_id*: from the cache while it is fresh.

        Raises:
            TenantNotConfiguredError: the org has no memory system.
            GraphLayerError: a layer could not be read.
            GraphTooLargeError: a layer holds more than the graph reads.
        """
        key = (tenant_id, frozenset(item.layer.id for item in layers))
        async with self._locks.setdefault(key, asyncio.Lock()):
            cached = self._cache.get(key)
            if cached is not None and cached[0] > self._clock():
                return cached[1]
            built = await self._build(tenant_id, layers)
            self._cache.pop(key, None)
            if self._cache_seconds > 0:
                self._cache[key] = (self._clock() + self._cache_seconds, built)
            self._forget_stale()
            return built

    def _forget_stale(self) -> None:
        now = self._clock()
        for key in [k for k, (expires, _) in self._cache.items() if expires <= now]:
            del self._cache[key]
        for key in [
            k for k, lock in self._locks.items() if k not in self._cache and not lock.locked()
        ]:
            del self._locks[key]

    # -- reads -----------------------------------------------------------

    async def _build(self, tenant_id: str, layers: Sequence[ReadableLayer]) -> dict[str, Any]:
        _, backend = self._systems.for_tenant(tenant_id)
        nearest = sorted(layers, key=lambda item: item.layer.distance)

        async def list_pages(item: ReadableLayer):
            refs = await self._in(
                item.layer, backend.list_pages(item.layer.namespace, limit=self._max + 1)
            )
            self._within_cap(item.layer, "pages", len(refs))
            return refs

        listed = await self._all([lambda i=item: list_pages(i) for item in nearest])

        async def get_page(layer: Layer, slug: str) -> _Page:
            record = await self._in(layer, backend.get_page(layer.namespace, slug))
            if record.timeline_omitted:
                raise GraphLayerError(
                    layer.id, f"{layer.id}: page {slug}: {record.timeline_omitted}"
                )
            return _Page(layer, record)

        pages = await self._all(
            [
                lambda lyr=item.layer, s=ref.slug: get_page(lyr, s)
                for item, refs in zip(nearest, listed, strict=True)
                for ref in refs
            ]
        )
        sources = await self._sources(backend, nearest, pages)
        return _assemble(layers, nearest, pages, sources)

    async def _sources(
        self, backend: Any, nearest: Sequence[ReadableLayer], pages: Sequence[_Page]
    ) -> dict[tuple[str, str], SourceRecord]:
        """The cited sources that exist, by (layer id, source id): the nearest layer holding it."""
        cited = {ref for page in pages for ref in _cited_ids(page.record)}
        if not cited:
            return {}

        async def list_sources(item: ReadableLayer) -> list[SourceRef]:
            refs = await self._in(
                item.layer, backend.list_sources(item.layer.namespace, limit=self._max + 1)
            )
            self._within_cap(item.layer, "sources", len(refs))
            return refs

        listed = await self._all([lambda i=item: list_sources(i) for item in nearest])
        held = {
            item.layer.id: {ref.id for ref in refs}
            for item, refs in zip(nearest, listed, strict=True)
        }
        wanted = {
            (resolved, source_id)
            for page in pages
            for source_id in _cited_ids(page.record)
            for resolved in [_holder(page.layer, nearest, held, source_id)]
            if resolved is not None
        }
        by_id = {item.layer.id: item.layer for item in nearest}

        async def get_source(layer_id: str, source_id: str) -> SourceRecord:
            layer = by_id[layer_id]
            return await self._in(layer, backend.get_source(layer.namespace, source_id))

        ordered = sorted(wanted)
        records = await self._all([lambda w=w: get_source(*w) for w in ordered])
        return dict(zip(ordered, records, strict=True))

    # -- plumbing --------------------------------------------------------

    async def _in[T](self, layer: Layer, call: Awaitable[T]) -> T:
        try:
            return await call
        except MemoryStoreError as exc:
            raise GraphLayerError(layer.id, f"layer {layer.id} could not be read: {exc}") from exc

    def _within_cap(self, layer: Layer, what: str, count: int) -> None:
        if count <= self._max:
            return
        message = (
            f"layer {layer.id} holds more than {self._max} {what}, more than the knowledge graph "
            f"reads; raise {MAX_RECORDS_KNOB}"
        )
        record_cap_hit(MAX_RECORDS_KNOB, message)
        raise GraphTooLargeError(layer.id, message)

    async def _all[T](self, calls: Sequence[Callable[[], Awaitable[T]]]) -> list[T]:
        """Run *calls*, at most ``concurrency`` in flight; the first failure cancels the rest."""
        semaphore = asyncio.Semaphore(self._concurrency)

        async def run(call: Callable[[], Awaitable[T]]) -> T:
            async with semaphore:
                return await call()

        tasks = [asyncio.ensure_future(run(call)) for call in calls]
        try:
            return list(await asyncio.gather(*tasks))
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise


def _cited_ids(record: PageRecord) -> set[str]:
    return {
        evidence.value
        for row in record.timeline
        for evidence in row.attribution.evidence
        if evidence.kind is EvidenceKind.SOURCE
    }


def _holder(
    own: Layer,
    nearest: Sequence[ReadableLayer],
    held: dict[str, set[str]],
    source_id: str,
) -> str | None:
    """The id of the layer a page of *own* resolves a source to: its own, then the nearest."""
    if source_id in held[own.id]:
        return own.id
    for item in nearest:
        if source_id in held[item.layer.id]:
            return item.layer.id
    return None


# ---------------------------------------------------------------------------
# Assembly — pure: pages and sources in, the response out
# ---------------------------------------------------------------------------


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day is not None else None


def _assemble(
    layers: Sequence[ReadableLayer],
    nearest: Sequence[ReadableLayer],
    pages: Sequence[_Page],
    sources: dict[tuple[str, str], SourceRecord],
) -> dict[str, Any]:
    slugs: dict[str, set[str]] = {item.layer.id: set() for item in nearest}
    for page in pages:
        slugs[page.layer.id].add(page.record.slug)

    nodes: list[dict[str, Any]] = [_page_node(page) for page in pages]
    edges: dict[tuple[str, str, str], str | None] = {}
    unresolved: dict[tuple[str, str], None] = {}

    def link(source: str, target: str, relation: str, since: date | None) -> None:
        key = (source, target, relation)
        introduced_on = _iso(since)
        known = edges.get(key)
        if key not in edges or known is None or (introduced_on and introduced_on < known):
            edges[key] = introduced_on

    for page in pages:
        source = page.node_id
        typed = {
            normalize_slug(s): rel.lower() for s, rel in _TYPED.findall(page.record.compiled_truth)
        }
        mentioned = [
            normalize_slug(s) for s in extract_wikilinks(page.record.compiled_truth)
        ] + _markdown_links(page.record.slug, page.record.compiled_truth)
        introduced = _introduced(page.record)
        for slug in dict.fromkeys([*typed, *mentioned]):
            if not slug:
                continue
            target = _resolve(page.layer, nearest, slugs, slug)
            if target is None:
                unresolved[(source, slug)] = None
                continue
            if target == source:
                continue
            link(source, target, typed.get(slug, MENTIONS), introduced.get(slug))
        for source_id, since in _citations(page.record).items():
            holder = _holder_of(page.layer, nearest, sources, source_id)
            if holder is None:
                unresolved[(source, f"source:{source_id}")] = None
                continue
            link(source, node_id(holder, f"{SOURCE_FOLDER}/{source_id}"), CITES, since)

    by_layer = {item.layer.id: item.layer for item in nearest}
    for (layer_id, source_id), record in sorted(sources.items()):
        nodes.append(_source_node(by_layer[layer_id], source_id, record))

    return {
        "layers": [{"id": item.layer.id, "name": item.name} for item in layers],
        "nodes": nodes,
        "edges": [
            {"source": s, "target": t, "relation": rel, "since": since}
            for (s, t, rel), since in sorted(edges.items())
        ],
        "unresolved": [
            {"source": source, "target_slug": slug} for source, slug in sorted(unresolved)
        ],
    }


def _markdown_links(slug: str, text: str) -> list[str]:
    """The pages a page's relative Markdown links name, as slugs from the layer's root."""
    folder = posixpath.dirname(slug)
    found = []
    for target in _MD_LINK.findall(text):
        # A link above the layer's root names no page here; it is then listed as unresolved.
        joined = posixpath.normpath(posixpath.join(folder, target))
        found.append(normalize_slug(joined.removesuffix(".md")))
    return found


def _resolve(
    own: Layer, nearest: Sequence[ReadableLayer], slugs: dict[str, set[str]], slug: str
) -> str | None:
    """The page a link names: by its path, else by its name, nearest layer first.

    The wiki format links pages by name (`[[ines-moreau]]` for `people/ines-moreau`), so
    a link with no path matches the one page of that name in a layer. Two pages of the
    same name in one layer leave the link unresolved rather than guessing between them.
    """
    order = [own, *(item.layer for item in nearest if item.layer != own)]
    for layer in order:
        if slug in slugs[layer.id]:
            return node_id(layer, slug)
    if "/" in slug:
        return None
    for layer in order:
        named = [path for path in slugs[layer.id] if path.rsplit("/", 1)[-1] == slug]
        if len(named) == 1:
            return node_id(layer, named[0])
        if len(named) > 1:
            return None
    return None


def _holder_of(
    own: Layer,
    nearest: Sequence[ReadableLayer],
    sources: dict[tuple[str, str], SourceRecord],
    source_id: str,
) -> Layer | None:
    if (own.id, source_id) in sources:
        return own
    for item in nearest:
        if (item.layer.id, source_id) in sources:
            return item.layer
    return None


def _citations(record: PageRecord) -> dict[str, date]:
    """Each source the timeline cites, with the date of the first row that cites it."""
    cited: dict[str, date] = {}
    for row in record.timeline:
        for evidence in row.attribution.evidence:
            if evidence.kind is not EvidenceKind.SOURCE:
                continue
            known = cited.get(evidence.value)
            if known is None or row.on < known:
                cited[evidence.value] = row.on
    return cited


def _introduced(record: PageRecord) -> dict[str, date]:
    """Each slug a timeline row links, with the date of the first row that does."""
    first: dict[str, date] = {}
    for row in record.timeline:
        for raw in extract_wikilinks(f"{row.summary}\n{row.detail}"):
            slug = normalize_slug(raw)
            known = first.get(slug)
            if known is None or row.on < known:
                first[slug] = row.on
    return first


def _page_node(page: _Page) -> dict[str, Any]:
    record = page.record
    days = [row.on for row in record.timeline]
    return {
        "id": page.node_id,
        "layer": page.layer.id,
        "path": record.slug,
        "title": record.title,
        "kind": page_kind(record.page_type, record.slug),
        "summary": first_sentence(record.compiled_truth),
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "first_seen": _iso(min(days)) if days else None,
        "changed_at": _iso(max(days)) if days else None,
    }


def _source_node(layer: Layer, source_id: str, record: SourceRecord) -> dict[str, Any]:
    written = record.attribution.written_at.isoformat()
    return {
        "id": node_id(layer, f"{SOURCE_FOLDER}/{source_id}"),
        "layer": layer.id,
        "path": f"{SOURCE_FOLDER}/{source_id}",
        "title": record.title,
        "kind": TICKET_KIND if record.kind == SourceKind.TICKET.value else SOURCE_KIND,
        "summary": "",
        "updated_at": written,
        "first_seen": written,
        "changed_at": None,
    }
