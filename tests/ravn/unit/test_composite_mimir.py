"""Unit tests for CompositeMimirAdapter and WriteRouting.

Tests cover:
- Read priority ordering across mounts
- Write routing: prefix matching, default fallback, explicit override
- De-duplication of search/query results across mounts
- read_page falls through to next mount on FileNotFoundError
- lint merges results from all mounts
- ingest fans out to all mounts
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from niuu.domain.mimir import (
    LintIssue,
    MimirLintReport,
    MimirPage,
    MimirPageMeta,
    MimirQueryResult,
    MimirSource,
    compute_content_hash,
)
from ravn.adapters.mimir.composite import CompositeMimirAdapter
from ravn.domain.mimir import MimirMount, WriteRouting

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_meta(path: str, category: str = "technical") -> MimirPageMeta:
    return MimirPageMeta(
        path=path,
        title=path.split("/")[-1],
        summary="",
        category=category,
        updated_at=datetime.now(UTC),
        source_ids=[],
    )


def _make_page(path: str) -> MimirPage:
    return MimirPage(meta=_make_meta(path), content=f"content of {path}")


def _mock_port(
    pages: list[MimirPage] | None = None,
    lint_report: MimirLintReport | None = None,
    read_raises: bool = False,
) -> MagicMock:
    port = MagicMock()
    pages = pages or []

    port.search = AsyncMock(return_value=pages)
    port.query = AsyncMock(return_value=MimirQueryResult(question="q", answer="", sources=pages))
    port.list_pages = AsyncMock(return_value=[p.meta for p in pages])
    port.list_threads = AsyncMock(return_value=pages)
    port.ingest = AsyncMock(return_value=[])
    port.upsert_page = AsyncMock(return_value=None)
    port.update_thread_weight = AsyncMock(return_value=None)
    port.lint = AsyncMock(return_value=lint_report or MimirLintReport(issues=[], pages_checked=0))

    if read_raises:
        port.read_page = AsyncMock(side_effect=FileNotFoundError("not found"))
        port.get_page = AsyncMock(side_effect=FileNotFoundError("not found"))
    else:

        async def _read(path: str) -> str:
            return f"content of {path}"

        async def _get_page(path: str) -> MimirPage:
            return _make_page(path)

        port.read_page = _read
        port.get_page = _get_page

    return port


def _make_mount(
    name: str,
    role: str = "local",
    priority: int = 0,
    pages: list[MimirPage] | None = None,
    read_raises: bool = False,
    lint_report: MimirLintReport | None = None,
    categories: list[str] | None = None,
) -> MimirMount:
    return MimirMount(
        name=name,
        port=_mock_port(pages=pages, read_raises=read_raises, lint_report=lint_report),
        role=role,
        categories=categories,
        read_priority=priority,
    )


# ---------------------------------------------------------------------------
# WriteRouting tests
# ---------------------------------------------------------------------------


def test_write_routing_explicit_override() -> None:
    routing = WriteRouting(
        rules=[("self/", ["local"]), ("household/", ["shared"])],
        default=["local"],
    )
    assert routing.resolve("self/test.md", explicit="shared") == ["shared"]


def test_write_routing_prefix_match_first_wins() -> None:
    routing = WriteRouting(
        rules=[
            ("self/", ["local"]),
            ("technical/", ["local", "shared"]),
            ("household/", ["shared"]),
        ],
        default=["local"],
    )
    assert routing.resolve("technical/ravn/tools.md") == ["local", "shared"]
    assert routing.resolve("self/preferences.md") == ["local"]
    assert routing.resolve("household/finances.md") == ["shared"]


def test_write_routing_default_fallback() -> None:
    routing = WriteRouting(
        rules=[("self/", ["local"])],
        default=["local"],
    )
    assert routing.resolve("research/deep-dive.md") == ["local"]


def test_write_routing_empty_rules_uses_default() -> None:
    routing = WriteRouting(rules=[], default=["shared"])
    assert routing.resolve("anything/page.md") == ["shared"]


def test_write_routing_multi_mount_default() -> None:
    routing = WriteRouting(rules=[], default=["local", "shared"])
    assert routing.resolve("technical/x.md") == ["local", "shared"]


# ---------------------------------------------------------------------------
# CompositeMimirAdapter — read priority ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_priority_order_dedup() -> None:
    page_a = _make_page("technical/a.md")
    page_b = _make_page("technical/b.md")

    # local has page_a, shared has page_a + page_b
    local = _make_mount("local", priority=0, pages=[page_a])
    shared = _make_mount("shared", priority=1, role="shared", pages=[page_a, page_b])

    adapter = CompositeMimirAdapter(mounts=[local, shared])
    results = await adapter.search("technical")

    # page_a appears in both — should be de-duplicated; local wins (priority 0)
    paths = [p.meta.path for p in results]
    assert paths.count("technical/a.md") == 1
    assert "technical/b.md" in paths
    # local result should appear first
    assert paths[0] == "technical/a.md"


@pytest.mark.asyncio
async def test_query_merges_from_all_mounts() -> None:
    page_a = _make_page("technical/a.md")
    page_b = _make_page("projects/b.md")

    local = _make_mount("local", priority=0, pages=[page_a])
    shared = _make_mount("shared", priority=1, role="shared", pages=[page_b])

    adapter = CompositeMimirAdapter(mounts=[local, shared])
    result = await adapter.query("anything")

    paths = [p.meta.path for p in result.sources]
    assert "technical/a.md" in paths
    assert "projects/b.md" in paths


@pytest.mark.asyncio
async def test_list_pages_dedup_by_path() -> None:
    page_a = _make_page("technical/a.md")
    local = _make_mount("local", priority=0, pages=[page_a])
    shared = _make_mount("shared", priority=1, role="shared", pages=[page_a])

    adapter = CompositeMimirAdapter(mounts=[local, shared])
    pages = await adapter.list_pages()

    paths = [m.path for m in pages]
    assert paths.count("technical/a.md") == 1


# ---------------------------------------------------------------------------
# CompositeMimirAdapter — read_page fallthrough
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_page_falls_through_to_second_mount() -> None:
    local = _make_mount("local", priority=0, read_raises=True)
    shared = _make_mount("shared", priority=1, role="shared", pages=[_make_page("technical/a.md")])

    adapter = CompositeMimirAdapter(mounts=[local, shared])
    content = await adapter.read_page("technical/a.md")
    assert "technical/a.md" in content


@pytest.mark.asyncio
async def test_read_page_raises_if_all_mounts_miss() -> None:
    local = _make_mount("local", priority=0, read_raises=True)
    shared = _make_mount("shared", priority=1, role="shared", read_raises=True)

    adapter = CompositeMimirAdapter(mounts=[local, shared])
    with pytest.raises(FileNotFoundError, match="not found in any mount"):
        await adapter.read_page("missing.md")


# ---------------------------------------------------------------------------
# CompositeMimirAdapter — get_page fallthrough
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_page_falls_through_to_second_mount() -> None:
    local = _make_mount("local", priority=0, read_raises=True)
    shared = _make_mount("shared", priority=1, role="shared", pages=[_make_page("technical/a.md")])

    adapter = CompositeMimirAdapter(mounts=[local, shared])
    page = await adapter.get_page("technical/a.md")
    assert "technical/a.md" in page.content


@pytest.mark.asyncio
async def test_get_page_raises_if_all_mounts_miss() -> None:
    local = _make_mount("local", priority=0, read_raises=True)
    shared = _make_mount("shared", priority=1, role="shared", read_raises=True)

    adapter = CompositeMimirAdapter(mounts=[local, shared])
    with pytest.raises(FileNotFoundError, match="not found in any mount"):
        await adapter.get_page("missing.md")


# ---------------------------------------------------------------------------
# CompositeMimirAdapter — write routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_routes_to_default_local() -> None:
    local_port = _mock_port()
    shared_port = _mock_port()

    local = MimirMount(name="local", port=local_port, role="local", read_priority=0)
    shared = MimirMount(name="shared", port=shared_port, role="shared", read_priority=1)

    routing = WriteRouting(rules=[], default=["local"])
    adapter = CompositeMimirAdapter(mounts=[local, shared], write_routing=routing)

    await adapter.upsert_page("technical/test.md", "# Test\ncontent")

    local_port.upsert_page.assert_called_once()
    shared_port.upsert_page.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_explicit_override_bypasses_routing() -> None:
    local_port = _mock_port()
    shared_port = _mock_port()

    local = MimirMount(name="local", port=local_port, role="local", read_priority=0)
    shared = MimirMount(name="shared", port=shared_port, role="shared", read_priority=1)

    routing = WriteRouting(rules=[], default=["local"])
    adapter = CompositeMimirAdapter(mounts=[local, shared], write_routing=routing)

    await adapter.upsert_page("technical/test.md", "# Test\ncontent", mimir="shared")

    shared_port.upsert_page.assert_called_once()
    local_port.upsert_page.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_multi_mount_routing() -> None:
    local_port = _mock_port()
    shared_port = _mock_port()

    local = MimirMount(name="local", port=local_port, role="local", read_priority=0)
    shared = MimirMount(name="shared", port=shared_port, role="shared", read_priority=1)

    routing = WriteRouting(
        rules=[("technical/", ["local", "shared"])],
        default=["local"],
    )
    adapter = CompositeMimirAdapter(mounts=[local, shared], write_routing=routing)

    await adapter.upsert_page("technical/ravn.md", "# Ravn\ncontent")

    local_port.upsert_page.assert_called_once()
    shared_port.upsert_page.assert_called_once()


# ---------------------------------------------------------------------------
# CompositeMimirAdapter — lint merge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lint_merges_all_mounts() -> None:
    report_a = MimirLintReport(
        issues=[LintIssue(id="L01", severity="warning", message="orphan", page_path="a.md")],
        pages_checked=5,
    )
    report_b = MimirLintReport(
        issues=[
            LintIssue(id="L02", severity="error", message="contradiction", page_path="b.md"),
            LintIssue(id="L04", severity="info", message="concept gap: concept-x", page_path=""),
        ],
        pages_checked=3,
    )

    local = _make_mount("local", priority=0, lint_report=report_a)
    shared = _make_mount("shared", priority=1, role="shared", lint_report=report_b)

    adapter = CompositeMimirAdapter(mounts=[local, shared])
    merged = await adapter.lint()

    assert any(i.id == "L01" and i.page_path == "a.md" for i in merged.issues)
    assert any(i.id == "L02" and i.page_path == "b.md" for i in merged.issues)
    assert any(i.id == "L04" and "concept-x" in i.message for i in merged.issues)
    assert merged.pages_checked == 8


# ---------------------------------------------------------------------------
# CompositeMimirAdapter — ingest fan-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_fans_out_to_all_mounts() -> None:
    local_port = _mock_port()
    shared_port = _mock_port()

    local = MimirMount(name="local", port=local_port, role="local", read_priority=0)
    shared = MimirMount(name="shared", port=shared_port, role="shared", read_priority=1)

    adapter = CompositeMimirAdapter(mounts=[local, shared])

    source = MimirSource(
        source_id="src_abc",
        title="Test",
        content="content",
        source_type="document",
        ingested_at=datetime.now(UTC),
        content_hash=compute_content_hash("content"),
    )
    await adapter.ingest(source)

    local_port.ingest.assert_called_once()
    shared_port.ingest.assert_called_once()


# ---------------------------------------------------------------------------
# Integration: two MarkdownMimirAdapters behind CompositeMimirAdapter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_two_markdown_adapters(tmp_path: Path) -> None:
    """Write to local only; merged read returns results from both."""
    from mimir.adapters.markdown import MarkdownMimirAdapter

    local_adapter = MarkdownMimirAdapter(root=tmp_path / "local")
    shared_adapter = MarkdownMimirAdapter(root=tmp_path / "shared")

    # Write a page to shared directly
    await shared_adapter.upsert_page(
        "household/shared-fact.md",
        "# Shared Fact\nThis is a shared household fact.",
    )

    routing = WriteRouting(rules=[], default=["local"])
    adapter = CompositeMimirAdapter(
        mounts=[
            MimirMount(name="local", port=local_adapter, role="local", read_priority=0),
            MimirMount(name="shared", port=shared_adapter, role="shared", read_priority=1),
        ],
        write_routing=routing,
    )

    # Write via composite — goes to local only
    await adapter.upsert_page("technical/local-page.md", "# Local Page\nLocal content.")

    # Search should return pages from both mounts
    results = await adapter.search("fact")
    paths = [p.meta.path for p in results]
    assert "household/shared-fact.md" in paths

    results2 = await adapter.search("local content")
    paths2 = [p.meta.path for p in results2]
    assert "technical/local-page.md" in paths2

    # Verify local-page is NOT in shared
    shared_pages = await shared_adapter.list_pages()
    shared_paths = [m.path for m in shared_pages]
    assert "technical/local-page.md" not in shared_paths


# ---------------------------------------------------------------------------
# Exception isolation — one mount failing should not abort others
# ---------------------------------------------------------------------------


def _error_port(error: Exception = RuntimeError("mount down")) -> object:
    """Return a mock port where every operation raises."""
    port = AsyncMock()
    port.ingest = AsyncMock(side_effect=error)
    port.query = AsyncMock(side_effect=error)
    port.search = AsyncMock(side_effect=error)
    port.list_pages = AsyncMock(side_effect=error)
    port.list_threads = AsyncMock(side_effect=error)
    port.list_sources = AsyncMock(side_effect=error)
    port.lint = AsyncMock(side_effect=error)
    port.read_source = AsyncMock(side_effect=error)
    port.read_page = AsyncMock(side_effect=error)
    port.get_page = AsyncMock(side_effect=error)
    port.upsert_page = AsyncMock(side_effect=error)
    port.update_thread_weight = AsyncMock(side_effect=error)
    return port


@pytest.mark.asyncio
async def test_ingest_exception_in_one_mount_continues_others() -> None:
    bad_port = _error_port()
    good_port = AsyncMock()
    good_port.ingest = AsyncMock(return_value=["wiki/page.md"])
    bad = MimirMount(name="bad", port=bad_port, role="local", read_priority=0)
    good = MimirMount(name="good", port=good_port, role="shared", read_priority=1)
    adapter = CompositeMimirAdapter(mounts=[bad, good])
    source = MimirSource(
        source_id="s1",
        title="T",
        content="c",
        source_type="document",
        ingested_at=datetime.now(UTC),
        content_hash=compute_content_hash("c"),
    )
    result = await adapter.ingest(source)
    good_port.ingest.assert_called_once()
    assert result == ["wiki/page.md"]


@pytest.mark.asyncio
async def test_query_exception_in_one_mount_continues_others() -> None:
    bad_port = _error_port()
    good_port = _mock_port()
    bad = MimirMount(name="bad", port=bad_port, role="local", read_priority=0)
    good = MimirMount(name="good", port=good_port, role="shared", read_priority=1)
    adapter = CompositeMimirAdapter(mounts=[bad, good])
    result = await adapter.query("test question")
    good_port.query.assert_called_once()
    assert isinstance(result, MimirQueryResult)


@pytest.mark.asyncio
async def test_search_exception_in_one_mount_continues_others() -> None:
    bad_port = _error_port()
    good_port = _mock_port()
    bad = MimirMount(name="bad", port=bad_port, role="local", read_priority=0)
    good = MimirMount(name="good", port=good_port, role="shared", read_priority=1)
    adapter = CompositeMimirAdapter(mounts=[bad, good])
    result = await adapter.search("query")
    good_port.search.assert_called_once()
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_list_pages_exception_in_one_mount_continues_others() -> None:
    bad_port = _error_port()
    good_port = _mock_port()
    bad = MimirMount(name="bad", port=bad_port, role="local", read_priority=0)
    good = MimirMount(name="good", port=good_port, role="shared", read_priority=1)
    adapter = CompositeMimirAdapter(mounts=[bad, good])
    result = await adapter.list_pages()
    good_port.list_pages.assert_called_once()
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_list_sources_sets_mount_name() -> None:
    from niuu.domain.mimir import MimirSourceMeta

    meta = MimirSourceMeta(
        source_id="src-1", title="T", ingested_at=datetime.now(UTC), source_type="web"
    )
    port = AsyncMock()
    port.list_sources = AsyncMock(return_value=[meta])
    mount = MimirMount(name="local", port=port, role="local", read_priority=0)
    adapter = CompositeMimirAdapter(mounts=[mount])
    result = await adapter.list_sources()
    assert result[0].mount_name == "local"


@pytest.mark.asyncio
async def test_list_sources_exception_in_one_mount_continues_others() -> None:
    bad_port = _error_port()
    good_port = _mock_port()
    bad = MimirMount(name="bad", port=bad_port, role="local", read_priority=0)
    good = MimirMount(name="good", port=good_port, role="shared", read_priority=1)
    adapter = CompositeMimirAdapter(mounts=[bad, good])
    result = await adapter.list_sources()
    # Should not raise, returns whatever good_port has
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_lint_exception_in_one_mount_continues_others() -> None:
    bad_port = _error_port()
    good_port = _mock_port()
    bad = MimirMount(name="bad", port=bad_port, role="local", read_priority=0)
    good = MimirMount(name="good", port=good_port, role="shared", read_priority=1)
    adapter = CompositeMimirAdapter(mounts=[bad, good])
    result = await adapter.lint()
    good_port.lint.assert_called_once()
    assert isinstance(result, MimirLintReport)


@pytest.mark.asyncio
async def test_read_source_exception_falls_through() -> None:
    bad_port = _error_port()
    good_port = _mock_port()
    bad = MimirMount(name="bad", port=bad_port, role="local", read_priority=0)
    good = MimirMount(name="good", port=good_port, role="shared", read_priority=1)
    adapter = CompositeMimirAdapter(mounts=[bad, good])
    # good_port.read_source returns None by default (from _mock_port)
    await adapter.read_source("src-1")
    good_port.read_source.assert_called_once()


@pytest.mark.asyncio
async def test_upsert_unknown_mount_name_skipped() -> None:
    good_port = _mock_port()
    good = MimirMount(name="good", port=good_port, role="local", read_priority=0)
    routing = WriteRouting(rules=[("wiki/", ["nonexistent"])], default=["good"])
    adapter = CompositeMimirAdapter(mounts=[good], write_routing=routing)
    # Should not crash — unknown mount is silently skipped
    await adapter.upsert_page("wiki/page.md", "content")


@pytest.mark.asyncio
async def test_upsert_exception_in_mount_logged_not_raised() -> None:
    bad_port = _error_port()
    bad = MimirMount(name="bad", port=bad_port, role="local", read_priority=0)
    adapter = CompositeMimirAdapter(mounts=[bad])
    # Should not raise
    await adapter.upsert_page("wiki/page.md", "content")


# ---------------------------------------------------------------------------
# CompositeMimirAdapter — list_threads (NIU-559)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_threads_dedup_by_path() -> None:
    page_a = _make_page("threads/alpha")
    page_b = _make_page("threads/beta")

    local = _make_mount("local", priority=0, pages=[page_a])
    shared = _make_mount("shared", priority=1, role="shared", pages=[page_a, page_b])

    adapter = CompositeMimirAdapter(mounts=[local, shared])
    results = await adapter.list_threads()

    paths = [p.meta.path for p in results]
    assert paths.count("threads/alpha") == 1
    assert "threads/beta" in paths


@pytest.mark.asyncio
async def test_list_threads_respects_limit() -> None:
    pages = [_make_page(f"threads/t{i}") for i in range(5)]
    local = _make_mount("local", priority=0, pages=pages)
    adapter = CompositeMimirAdapter(mounts=[local])
    results = await adapter.list_threads(limit=3)
    assert len(results) == 3


@pytest.mark.asyncio
async def test_list_threads_exception_in_one_mount_continues_others() -> None:
    bad_port = _error_port()
    bad_port.list_threads = AsyncMock(side_effect=RuntimeError("down"))
    good_port = _mock_port(pages=[_make_page("threads/alpha")])
    bad = MimirMount(name="bad", port=bad_port, role="local", read_priority=0)
    good = MimirMount(name="good", port=good_port, role="shared", read_priority=1)
    adapter = CompositeMimirAdapter(mounts=[bad, good])
    results = await adapter.list_threads()
    assert isinstance(results, list)
    good_port.list_threads.assert_called_once()


# ---------------------------------------------------------------------------
# CompositeMimirAdapter — update_thread_weight (NIU-559)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_thread_weight_routes_to_default_mount() -> None:
    local_port = _mock_port()
    shared_port = _mock_port()

    local = MimirMount(name="local", port=local_port, role="local", read_priority=0)
    shared = MimirMount(name="shared", port=shared_port, role="shared", read_priority=1)

    routing = WriteRouting(rules=[], default=["local"])
    adapter = CompositeMimirAdapter(mounts=[local, shared], write_routing=routing)

    await adapter.update_thread_weight("threads/my-thread", 0.75)

    local_port.update_thread_weight.assert_called_once_with("threads/my-thread", 0.75, None)
    shared_port.update_thread_weight.assert_not_called()


@pytest.mark.asyncio
async def test_update_thread_weight_passes_signals() -> None:
    local_port = _mock_port()
    local = MimirMount(name="local", port=local_port, role="local", read_priority=0)
    routing = WriteRouting(rules=[], default=["local"])
    adapter = CompositeMimirAdapter(mounts=[local], write_routing=routing)

    signals = {"age_days": 2.0, "mention_count": 3}
    await adapter.update_thread_weight("threads/my-thread", 0.8, signals)

    local_port.update_thread_weight.assert_called_once_with("threads/my-thread", 0.8, signals)
