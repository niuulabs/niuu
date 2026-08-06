"""Tests for MimirPort's default implementations.

The port supplies working defaults for ``summarize`` and ``read_source_excerpt``
so an adapter that cannot answer them cheaply is still correct. Adapters that
can — the Markdown store, the HTTP client — override them; these defaults are
what keeps everything else, including test doubles, honest.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from niuu.domain.mimir import (
    MimirLintReport,
    MimirPage,
    MimirPageMeta,
    MimirQueryResult,
    MimirSource,
    MimirSourceMeta,
    compute_content_hash,
)
from niuu.ports.mimir import MimirPort


class _MinimalMimir(MimirPort):
    """An adapter that implements only the abstract surface."""

    def __init__(self, sources: dict[str, MimirSource], pages: list[MimirPageMeta]) -> None:
        self._sources = sources
        self._pages = pages

    async def ingest(self, source: MimirSource) -> list[str]:
        raise NotImplementedError

    async def query(self, question: str) -> MimirQueryResult:
        raise NotImplementedError

    async def lint(self, fix: bool = False) -> MimirLintReport:
        raise AssertionError("the default summary must not lint")

    async def search(self, query: str) -> list[MimirPage]:
        raise NotImplementedError

    async def upsert_page(self, path: str, content: str, mimir: str | None = None) -> None:
        raise NotImplementedError

    async def delete_page(self, path: str, mimir: str | None = None) -> bool:
        raise NotImplementedError

    async def read_page(self, path: str) -> str:
        raise NotImplementedError

    async def get_page(self, path: str) -> MimirPage:
        raise NotImplementedError

    async def list_pages(
        self,
        category: str | None = None,
        prefix: str | None = None,
    ) -> list[MimirPageMeta]:
        return self._pages

    async def read_source(self, source_id: str) -> MimirSource | None:
        return self._sources.get(source_id)

    async def list_sources(self, *, unprocessed_only: bool = False) -> list[MimirSourceMeta]:
        return [
            MimirSourceMeta(
                source_id=s.source_id,
                title=s.title,
                ingested_at=s.ingested_at,
                source_type=s.source_type,
            )
            for s in self._sources.values()
        ]


def _page(path: str, category: str, updated_at: datetime) -> MimirPageMeta:
    return MimirPageMeta(
        path=path,
        title=path,
        summary="",
        category=category,
        updated_at=updated_at,
        source_ids=[],
    )


def _source(source_id: str, content: str, ingested_at: datetime) -> MimirSource:
    return MimirSource(
        source_id=source_id,
        title=source_id,
        content=content,
        source_type="research",
        ingested_at=ingested_at,
        content_hash=compute_content_hash(content),
    )


@pytest.mark.asyncio
async def test_default_summarize_derives_counts_from_the_listings() -> None:
    early = datetime(2026, 1, 1, tzinfo=UTC)
    late = datetime(2026, 6, 1, tzinfo=UTC)
    port = _MinimalMimir(
        sources={"src-1": _source("src-1", "body", late)},
        pages=[
            _page("technical/a.md", "technical", early),
            _page("research/b.md", "research", early),
        ],
    )

    summary = await port.summarize()

    assert summary.page_count == 2
    assert summary.source_count == 1
    assert summary.categories == ["research", "technical"]
    assert summary.last_write == late


@pytest.mark.asyncio
async def test_default_summarize_of_an_empty_store() -> None:
    summary = await _MinimalMimir(sources={}, pages=[]).summarize()

    assert summary.page_count == 0
    assert summary.last_write is None


@pytest.mark.asyncio
async def test_default_read_source_excerpt_bounds_locally() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    port = _MinimalMimir(sources={"src-1": _source("src-1", "x" * 5_000, now)}, pages=[])

    excerpt = await port.read_source_excerpt("src-1", 100)

    assert excerpt is not None
    assert len(excerpt.content) == 100


@pytest.mark.asyncio
async def test_default_read_source_excerpt_leaves_short_content_alone() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    port = _MinimalMimir(sources={"src-1": _source("src-1", "short", now)}, pages=[])

    assert (await port.read_source_excerpt("src-1", 10_000)).content == "short"
    assert (await port.read_source_excerpt("src-1", 0)).content == "short"


@pytest.mark.asyncio
async def test_default_read_source_excerpt_of_a_missing_source() -> None:
    port = _MinimalMimir(sources={}, pages=[])

    assert await port.read_source_excerpt("src-nope", 100) is None
