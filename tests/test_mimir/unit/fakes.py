"""An in-memory ``MimirPort`` for tests that need a store but not a particular backend.

It keeps pages and sources in dictionaries and answers ``search`` by counting
keyword occurrences. It is a test double, not an adapter: nothing here is shipped.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import yaml

from niuu.domain.mimir import (
    OPERATIONAL_SOURCE_TYPES,
    EntityType,
    MimirPage,
    MimirPageMeta,
    MimirQueryResult,
    MimirSource,
    MimirSourceMeta,
    PageConfidence,
    PageType,
)
from niuu.ports.mimir import MimirPort

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)
_FOOTER_SOURCES_RE = re.compile(r"<!--\s*sources:\s*([^<]+?)\s*-->\s*$")


def _frontmatter(content: str) -> dict:
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}
    try:
        parsed = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _title(content: str, path: str) -> str:
    title = str(_frontmatter(content).get("title", "")).strip()
    if title:
        return title
    for line in content.splitlines():
        if line.strip().startswith("# "):
            return line.strip()[2:].strip()
    return re.sub(r"[-_]+", " ", Path(path).stem).strip().title() or "Untitled"


def _summary(content: str) -> str:
    heading_seen = False
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            heading_seen = True
            continue
        if heading_seen:
            return stripped[:120]
    return ""


def _enum_or_none(enum_cls, value):  # noqa: ANN001, ANN202
    try:
        return enum_cls(value) if value else None
    except ValueError:
        return None


def _source_ids(content: str) -> list[str]:
    declared = _frontmatter(content).get("source_ids")
    if isinstance(declared, list):
        return [str(item).strip() for item in declared if str(item).strip()]
    match = _FOOTER_SOURCES_RE.search(content)
    if not match:
        return []
    return [item.strip() for item in match.group(1).split(",") if item.strip()]


class InMemoryMimirAdapter(MimirPort):
    """A ``MimirPort`` over dictionaries. ``ingest`` stores the source and creates no pages."""

    def __init__(self) -> None:
        self.pages: dict[str, tuple[str, datetime]] = {}
        self.sources: dict[str, MimirSource] = {}
        self.ingests: list[MimirSource] = []

    async def ingest(self, source: MimirSource) -> list[str]:
        self.sources[source.source_id] = source
        self.ingests.append(source)
        return []

    async def query(self, question: str) -> MimirQueryResult:
        return MimirQueryResult(question=question, answer="", sources=await self.search(question))

    async def search(self, query: str) -> list[MimirPage]:
        keywords = set(re.split(r"\W+", query.lower())) - {""}
        scored: list[tuple[int, MimirPage]] = []
        for path in self.pages:
            page = await self.get_page(path)
            haystack = "\n".join((page.meta.title, path, page.meta.summary, page.content)).lower()
            score = sum(haystack.count(keyword) for keyword in keywords)
            if score:
                scored.append((score, page))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [page for _, page in scored]

    async def upsert_page(
        self,
        path: str,
        content: str,
        mimir: str | None = None,
        meta: MimirPageMeta | None = None,
    ) -> None:
        self.pages[path] = (content, datetime.now(UTC))

    async def delete_page(self, path: str, mimir: str | None = None) -> bool:
        return self.pages.pop(path, None) is not None

    async def read_page(self, path: str) -> str:
        if path not in self.pages:
            raise FileNotFoundError(f"Mímir page not found: {path}")
        return self.pages[path][0]

    async def get_page(self, path: str) -> MimirPage:
        content = await self.read_page(path)
        parts = path.split("/")
        front = _frontmatter(content)
        related = front.get("related_entities")
        meta = MimirPageMeta(
            path=path,
            title=_title(content, path),
            summary=_summary(content),
            category=parts[0] if len(parts) > 1 else "uncategorised",
            updated_at=self.pages[path][1],
            source_ids=_source_ids(content),
            page_type=_enum_or_none(PageType, front.get("type")),
            confidence=_enum_or_none(PageConfidence, front.get("confidence")),
            entity_type=_enum_or_none(EntityType, front.get("entity_type")),
            related_entities=[str(slug) for slug in related] if isinstance(related, list) else [],
        )
        return MimirPage(meta=meta, content=content)

    async def list_pages(
        self,
        category: str | None = None,
        prefix: str | None = None,
    ) -> list[MimirPageMeta]:
        metas = [(await self.get_page(path)).meta for path in sorted(self.pages)]
        return [
            meta
            for meta in metas
            if (category is None or meta.category == category)
            and (prefix is None or meta.path.startswith(prefix))
        ]

    async def read_source(self, source_id: str) -> MimirSource | None:
        return self.sources.get(source_id)

    async def list_sources(self, *, unprocessed_only: bool = False) -> list[MimirSourceMeta]:
        referenced = {
            source_id for content, _ in self.pages.values() for source_id in _source_ids(content)
        }
        return [
            MimirSourceMeta(
                source_id=source.source_id,
                title=source.title,
                ingested_at=source.ingested_at,
                source_type=source.source_type,
                origin_url=source.origin_url,
            )
            for source in self.sources.values()
            if not unprocessed_only
            or (
                source.source_id not in referenced
                and source.source_type not in OPERATIONAL_SOURCE_TYPES
            )
        ]
