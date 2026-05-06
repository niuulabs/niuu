"""Tests for SearchPort-backed search in MarkdownMimirAdapter (NIU-577).

Covers:
- Chunking strategy (_chunk_markdown)
- Search delegates to SearchPort when configured
- Search falls back to keyword matching when no SearchPort
- upsert_page triggers re-indexing
- ingest triggers source indexing
- rebuild_search_index walks wiki/ and indexes all pages
- Chunk metadata: page_path, section_heading, category, page_type
- FTS-only mode (search_port=None) still works
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from mimir.adapters.markdown import (
    _SEARCH_RESULT_LIMIT,
    MarkdownMimirAdapter,
    _append_paragraphs,
    _chunk_markdown,
)
from niuu.domain.mimir import MimirSource
from niuu.ports.search import SearchPort, SearchResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(
    tmp_path: Path,
    search_port: SearchPort | None = None,
) -> MarkdownMimirAdapter:
    return MarkdownMimirAdapter(root=tmp_path / "mimir", search_port=search_port)


def _make_mock_search_port() -> MagicMock:
    port = MagicMock(spec=SearchPort)
    port.index = AsyncMock(return_value=None)
    port.remove = AsyncMock(return_value=None)
    port.search = AsyncMock(return_value=[])
    port.rebuild = AsyncMock(return_value=None)
    return port


def _make_source(content: str = "Some content.", title: str = "Test") -> MimirSource:
    return MimirSource(
        source_id=f"src_{hashlib.sha256(title.encode()).hexdigest()[:16]}",
        title=title,
        content=content,
        source_type="document",
        origin_url=None,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        ingested_at=datetime.now(UTC),
    )


def _wiki_meta(page_path: str, score: float = 0.9) -> dict[str, Any]:
    return {
        "page_path": page_path,
        "section_heading": "",
        "category": "technical",
        "page_type": "wiki",
    }


def _search_result(page_path: str, score: float = 0.9) -> SearchResult:
    return SearchResult(
        id=f"{page_path}::0",
        content="some content",
        score=score,
        metadata=_wiki_meta(page_path, score),
    )


# ---------------------------------------------------------------------------
# _chunk_markdown — chunking strategy
# ---------------------------------------------------------------------------


def test_chunk_markdown_single_small_section() -> None:
    content = "# Title\n\nShort intro.\n"
    chunks = _chunk_markdown(content, "wiki/test.md", "wiki")
    assert len(chunks) == 1
    text, meta = chunks[0]
    assert "Short intro" in text
    assert meta["page_path"] == "wiki/test.md"
    assert meta["category"] == "wiki"
    assert meta["page_type"] == "wiki"


def test_chunk_markdown_splits_on_h2() -> None:
    content = (
        "# Title\n\nIntro paragraph.\n\n"
        "## Section One\n\nContent of section one.\n\n"
        "## Section Two\n\nContent of section two.\n"
    )
    chunks = _chunk_markdown(content, "technical/page.md", "technical")
    assert len(chunks) == 3
    headings = [meta["section_heading"] for _, meta in chunks]
    assert "Section One" in headings
    assert "Section Two" in headings


def test_chunk_markdown_includes_section_heading_in_metadata() -> None:
    content = "# Title\n\n## My Section\n\nBody text.\n"
    chunks = _chunk_markdown(content, "projects/foo.md", "projects")
    h2_chunks = [(t, m) for t, m in chunks if m["section_heading"] == "My Section"]
    assert h2_chunks, "Expected chunk with section_heading='My Section'"


def test_chunk_markdown_large_section_splits_on_h3() -> None:
    long_body = "word " * 600  # well over 2000 chars
    content = (
        f"# Title\n\n## Big Section\n\n### Sub A\n\n{long_body}\n\n### Sub B\n\nShort content.\n"
    )
    chunks = _chunk_markdown(content, "research/big.md", "research")
    # Should have more than one chunk for the large section
    assert len(chunks) >= 2


def test_chunk_markdown_large_section_splits_on_paragraphs() -> None:
    paragraphs = "\n\n".join(["paragraph text " * 20] * 15)
    content = f"# Title\n\n## Section\n\n{paragraphs}\n"
    chunks = _chunk_markdown(content, "research/para.md", "research")
    assert len(chunks) >= 2


def test_chunk_markdown_page_type_preserved() -> None:
    content = "# Thread\n\nSome thread content.\n"
    chunks = _chunk_markdown(content, "threads/open-q.md", "threads", "thread")
    assert all(m["page_type"] == "thread" for _, m in chunks)


def test_chunk_markdown_empty_content_returns_one_chunk() -> None:
    chunks = _chunk_markdown("", "empty.md", "uncategorised")
    assert len(chunks) == 1


def test_chunk_markdown_no_h2_returns_one_chunk() -> None:
    content = "# Title\n\nJust a paragraph. No H2 headings.\n"
    chunks = _chunk_markdown(content, "page.md", "uncategorised")
    assert len(chunks) == 1


def test_append_paragraphs_small_text_appends_single_chunk() -> None:
    base_meta: dict[str, Any] = {"page_path": "p.md", "category": "c", "page_type": "wiki"}
    out: list = []
    _append_paragraphs("Short text.", "Heading", base_meta, 2000, out)
    assert len(out) == 1
    assert out[0][1]["section_heading"] == "Heading"


def test_append_paragraphs_all_empty_paragraphs_appends_nothing() -> None:
    base_meta: dict[str, Any] = {"page_path": "p.md", "category": "c", "page_type": "wiki"}
    out: list = []
    # All-whitespace text — every paragraph is empty after strip, nothing appended.
    _append_paragraphs("   \n\n   \n\n   ", "Heading", base_meta, 1, out)
    assert len(out) == 0


def test_chunk_markdown_h3_split_with_empty_sub_sections() -> None:
    long_body = "word " * 600  # > 2000 chars
    # Section with H3 headings but an empty sub-section at the start (edge case)
    content = f"# Title\n\n## Big\n\n{long_body}\n\n### Sub A\n\nContent here.\n"
    chunks = _chunk_markdown(content, "research/edge.md", "research")
    assert len(chunks) >= 1


# ---------------------------------------------------------------------------
# search() — delegates to SearchPort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_uses_search_port_when_configured(tmp_path: Path) -> None:
    port = _make_mock_search_port()
    port.search = AsyncMock(return_value=[_search_result("technical/tools.md", 0.9)])
    adapter = _make_adapter(tmp_path, search_port=port)
    await adapter.upsert_page("technical/tools.md", "# Tools\n\nBash tools.")

    results = await adapter.search("bash tools")

    port.search.assert_called_once_with("bash tools", limit=20)
    assert len(results) == 1
    assert results[0].meta.path == "technical/tools.md"


@pytest.mark.asyncio
async def test_search_returns_empty_for_blank_query_with_port(tmp_path: Path) -> None:
    port = _make_mock_search_port()
    adapter = _make_adapter(tmp_path, search_port=port)
    results = await adapter.search("   ")
    assert results == []
    port.search.assert_not_called()


@pytest.mark.asyncio
async def test_search_deduplicates_chunks_by_page(tmp_path: Path) -> None:
    port = _make_mock_search_port()
    port.search = AsyncMock(
        return_value=[
            SearchResult(
                id="technical/tools.md::0",
                content="chunk 0",
                score=0.8,
                metadata=_wiki_meta("technical/tools.md"),
            ),
            SearchResult(
                id="technical/tools.md::1",
                content="chunk 1",
                score=0.6,
                metadata=_wiki_meta("technical/tools.md"),
            ),
        ]
    )
    adapter = _make_adapter(tmp_path, search_port=port)
    await adapter.upsert_page("technical/tools.md", "# Tools\n\nContent.")

    results = await adapter.search("tools")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_search_skips_results_for_missing_pages(tmp_path: Path) -> None:
    port = _make_mock_search_port()
    port.search = AsyncMock(return_value=[_search_result("nonexistent/page.md")])
    adapter = _make_adapter(tmp_path, search_port=port)

    results = await adapter.search("something")
    assert results == []


@pytest.mark.asyncio
async def test_search_uses_search_result_limit_constant(tmp_path: Path) -> None:
    port = _make_mock_search_port()
    adapter = _make_adapter(tmp_path, search_port=port)
    await adapter.search("anything")
    port.search.assert_called_once_with("anything", limit=_SEARCH_RESULT_LIMIT)


@pytest.mark.asyncio
async def test_search_falls_back_to_keywords_without_port(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path, search_port=None)
    await adapter.upsert_page("technical/memory.md", "# Memory\n\nEpisodic storage.")
    results = await adapter.search("episodic")
    assert any(p.meta.path == "technical/memory.md" for p in results)


# ---------------------------------------------------------------------------
# upsert_page() — triggers re-indexing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_page_indexes_new_page(tmp_path: Path) -> None:
    port = _make_mock_search_port()
    adapter = _make_adapter(tmp_path, search_port=port)

    await adapter.upsert_page("technical/auth.md", "# Auth\n\nHow auth works.")

    assert port.index.called
    for c in port.index.call_args_list:
        args = c.args
        assert args[2]["page_path"] == "technical/auth.md"


@pytest.mark.asyncio
async def test_upsert_page_removes_old_chunks_on_update(tmp_path: Path) -> None:
    port = _make_mock_search_port()
    adapter = _make_adapter(tmp_path, search_port=port)

    content_v1 = "# Auth\n\nSimple content."
    await adapter.upsert_page("technical/auth.md", content_v1)

    content_v2 = "# Auth\n\nUpdated content with more detail."
    await adapter.upsert_page("technical/auth.md", content_v2)

    assert port.remove.called
    removed_ids = [c.args[0] for c in port.remove.call_args_list]
    assert any(rid.startswith("technical/auth.md::") for rid in removed_ids)


@pytest.mark.asyncio
async def test_upsert_page_chunk_ids_use_path_prefix(tmp_path: Path) -> None:
    port = _make_mock_search_port()
    adapter = _make_adapter(tmp_path, search_port=port)

    await adapter.upsert_page("projects/saga.md", "# Saga\n\nProject saga page.")

    indexed_ids = [c.args[0] for c in port.index.call_args_list]
    assert all(id_.startswith("projects/saga.md::") for id_ in indexed_ids)


@pytest.mark.asyncio
async def test_upsert_page_without_search_port_still_works(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path, search_port=None)
    await adapter.upsert_page("technical/page.md", "# Page\n\nContent.")
    page = await adapter.read_page("technical/page.md")
    assert "Page" in page


# ---------------------------------------------------------------------------
# ingest() — indexes raw source
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_does_not_call_search_port(tmp_path: Path) -> None:
    port = _make_mock_search_port()
    adapter = _make_adapter(tmp_path, search_port=port)

    src = _make_source(content="Deploying services to Kubernetes clusters.", title="K8s Deploy")
    await adapter.ingest(src)

    port.index.assert_not_called()


@pytest.mark.asyncio
async def test_ingest_without_search_port_does_not_fail(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path, search_port=None)
    src = _make_source()
    result = await adapter.ingest(src)
    assert result == []


# ---------------------------------------------------------------------------
# rebuild_search_index()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rebuild_indexes_all_wiki_pages(tmp_path: Path) -> None:
    port = _make_mock_search_port()
    adapter = _make_adapter(tmp_path, search_port=port)

    await adapter.upsert_page("technical/a.md", "# A\n\nContent A.")
    await adapter.upsert_page("projects/b.md", "# B\n\nContent B.")
    port.index.reset_mock()
    port.remove.reset_mock()
    port.rebuild.reset_mock()

    count = await adapter.rebuild_search_index()

    assert count == 2
    assert port.index.called
    port.rebuild.assert_called_once()


@pytest.mark.asyncio
async def test_rebuild_calls_port_rebuild_to_wipe_index(tmp_path: Path) -> None:
    port = _make_mock_search_port()
    adapter = _make_adapter(tmp_path, search_port=port)

    await adapter.rebuild_search_index()

    port.rebuild.assert_called_once()


@pytest.mark.asyncio
async def test_rebuild_excludes_index_and_log(tmp_path: Path) -> None:
    port = _make_mock_search_port()
    adapter = _make_adapter(tmp_path, search_port=port)
    port.index.reset_mock()

    await adapter.rebuild_search_index()

    all_indexed_ids = [c.args[0] for c in port.index.call_args_list]
    assert not any("index.md" in id_ for id_ in all_indexed_ids)
    assert not any("log.md" in id_ for id_ in all_indexed_ids)


@pytest.mark.asyncio
async def test_rebuild_returns_zero_without_search_port(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path, search_port=None)
    await adapter.upsert_page("technical/page.md", "# Page\n\nContent.")
    count = await adapter.rebuild_search_index()
    assert count == 0


@pytest.mark.asyncio
async def test_rebuild_resets_chunk_counts(tmp_path: Path) -> None:
    port = _make_mock_search_port()
    adapter = _make_adapter(tmp_path, search_port=port)

    await adapter.upsert_page("technical/page.md", "# Page\n\nContent.")
    assert "technical/page.md" in adapter._page_chunk_counts

    adapter._page_chunk_counts.clear()
    await adapter.rebuild_search_index()

    assert "technical/page.md" in adapter._page_chunk_counts


# ---------------------------------------------------------------------------
# Search result ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_results_ordered_by_descending_score(tmp_path: Path) -> None:
    port = _make_mock_search_port()
    port.search = AsyncMock(
        return_value=[
            SearchResult(
                id="a.md::0",
                content="a",
                score=0.3,
                metadata=_wiki_meta("technical/a.md"),
            ),
            SearchResult(
                id="b.md::0",
                content="b",
                score=0.9,
                metadata=_wiki_meta("technical/b.md"),
            ),
        ]
    )
    adapter = _make_adapter(tmp_path, search_port=port)
    await adapter.upsert_page("technical/a.md", "# A\n\nA content.")
    await adapter.upsert_page("technical/b.md", "# B\n\nB content.")

    results = await adapter.search("query")

    assert results[0].meta.path == "technical/b.md"
    assert results[1].meta.path == "technical/a.md"
