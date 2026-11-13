"""Tests for the ``MimirPort`` defaults that mount listings and stats depend on.

``/mimir/mounts`` and ``/mimir/stats`` are polled continuously by dashboards, so
the port's default ``summarize`` must answer from page and source metadata and
``read_source_excerpt`` must bound what a caller receives.

Covers:
- summarize() counts pages, sources and categories and reports the latest write
- list_sources(unprocessed_only=True) excludes cited sources and operational exhaust
- read_source_excerpt() bounds content, and treats a non-positive bound as none
- list_pages() filters by prefix and category
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from niuu.domain.mimir import MimirSource, compute_content_hash
from tests.test_mimir.unit.fakes import InMemoryMimirAdapter


def _make_source(
    source_id: str, content: str = "body", source_type: str = "research"
) -> MimirSource:
    return MimirSource(
        source_id=source_id,
        title=f"Source {source_id}",
        content=content,
        source_type=source_type,  # type: ignore[arg-type]
        ingested_at=datetime.now(UTC),
        content_hash=compute_content_hash(content),
    )


@pytest.mark.asyncio
async def test_summarize_counts_pages_and_categories() -> None:
    adapter = InMemoryMimirAdapter()
    await adapter.upsert_page("technical/alpha.md", "# Alpha\n")
    await adapter.upsert_page("technical/beta.md", "# Beta\n")
    await adapter.upsert_page("research/gamma.md", "# Gamma\n")

    summary = await adapter.summarize()

    assert summary.page_count == 3
    assert summary.categories == ["research", "technical"]


@pytest.mark.asyncio
async def test_summarize_counts_sources_and_reports_the_latest_write() -> None:
    adapter = InMemoryMimirAdapter()
    await adapter.upsert_page("technical/alpha.md", "# Alpha\n")
    await adapter.ingest(_make_source("src-1"))

    summary = await adapter.summarize()

    assert summary.source_count == 1
    assert summary.last_write is not None
    assert summary.last_write.year >= 2020


@pytest.mark.asyncio
async def test_summarize_of_an_empty_store_has_no_last_write() -> None:
    summary = await InMemoryMimirAdapter().summarize()

    assert summary.page_count == 0
    assert summary.source_count == 0
    assert summary.last_write is None


@pytest.mark.asyncio
async def test_list_sources_unprocessed_on_an_empty_store() -> None:
    assert await InMemoryMimirAdapter().list_sources(unprocessed_only=True) == []


@pytest.mark.asyncio
async def test_list_sources_unprocessed_excludes_referenced_sources() -> None:
    adapter = InMemoryMimirAdapter()
    await adapter.ingest(_make_source("src-cited", content="cited body"))
    await adapter.ingest(_make_source("src-loose", content="loose body"))
    await adapter.upsert_page("technical/alpha.md", "# Alpha\n\n<!-- sources: src-cited -->\n")

    unprocessed = await adapter.list_sources(unprocessed_only=True)

    assert [s.source_id for s in unprocessed] == ["src-loose"]


@pytest.mark.asyncio
async def test_list_sources_unprocessed_excludes_operational_exhaust() -> None:
    """No synthesis will ever cite a tool transcript or a probe log."""
    adapter = InMemoryMimirAdapter()
    await adapter.ingest(_make_source("src-real", content="knowledge"))
    await adapter.ingest(
        _make_source("src-tool", content="probe output", source_type="tool_output")
    )
    await adapter.ingest(_make_source("src-diag", content="run log", source_type="diagnostic"))

    unprocessed = await adapter.list_sources(unprocessed_only=True)

    assert [s.source_id for s in unprocessed] == ["src-real"]
    assert len(await adapter.list_sources()) == 3


@pytest.mark.asyncio
async def test_read_source_excerpt_bounds_content() -> None:
    adapter = InMemoryMimirAdapter()
    await adapter.ingest(_make_source("src-1", content="x" * 5_000))

    excerpt = await adapter.read_source_excerpt("src-1", 100)

    assert excerpt is not None
    assert len(excerpt.content) == 100
    assert excerpt.source_id == "src-1"


@pytest.mark.asyncio
async def test_read_source_excerpt_leaves_short_content_alone() -> None:
    adapter = InMemoryMimirAdapter()
    await adapter.ingest(_make_source("src-1", content="short"))

    excerpt = await adapter.read_source_excerpt("src-1", 10_000)

    assert excerpt is not None
    assert excerpt.content == "short"


@pytest.mark.asyncio
async def test_read_source_excerpt_treats_non_positive_as_unbounded() -> None:
    adapter = InMemoryMimirAdapter()
    await adapter.ingest(_make_source("src-1", content="x" * 500))

    excerpt = await adapter.read_source_excerpt("src-1", 0)

    assert excerpt is not None
    assert len(excerpt.content) == 500


@pytest.mark.asyncio
async def test_read_source_excerpt_of_a_missing_source() -> None:
    assert await InMemoryMimirAdapter().read_source_excerpt("src-nope", 100) is None


@pytest.mark.asyncio
async def test_list_pages_prefix_and_category_filters() -> None:
    adapter = InMemoryMimirAdapter()
    await adapter.upsert_page("technical/a/one.md", "# One\n")
    await adapter.upsert_page("technical/b/two.md", "# Two\n")
    await adapter.upsert_page("research/a/three.md", "# Three\n")

    assert len(await adapter.list_pages()) == 3
    assert [m.path for m in await adapter.list_pages(prefix="technical/a/")] == [
        "technical/a/one.md"
    ]
    assert [
        m.path for m in await adapter.list_pages(category="technical", prefix="technical/b/")
    ] == ["technical/b/two.md"]
