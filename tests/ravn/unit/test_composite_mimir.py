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
    MimirMountSummary,
    MimirPage,
    MimirPageMeta,
    MimirQueryResult,
    MimirSource,
    compute_content_hash,
)
from ravn.adapters.mimir.composite import CompositeMimirAdapter
from ravn.domain.exceptions import MimirUnavailableError
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


def test_ingest_targets_prefers_explicit_or_default_write_routing() -> None:
    local = _make_mount("local")
    shared = _make_mount("shared")
    adapter = CompositeMimirAdapter(
        mounts=[local, shared],
        write_routing=WriteRouting(default=["shared", "missing"]),
    )

    assert adapter.ingest_targets("local") == ["local"]
    assert adapter.ingest_targets("missing") == []
    assert adapter.ingest_targets() == ["shared"]


def test_ingest_targets_falls_back_to_all_mounts_when_no_default_is_configured() -> None:
    local = _make_mount("local")
    shared = _make_mount("shared")
    adapter = CompositeMimirAdapter(mounts=[local, shared], write_routing=WriteRouting(default=[]))

    assert adapter.ingest_targets() == ["local", "shared"]


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


@pytest.mark.asyncio
async def test_ingest_to_targets_only_named_mount() -> None:
    source = MimirSource(
        source_id="src-1",
        title="Test source",
        content="hello",
        source_type="research",
        ingested_at=datetime.now(UTC),
        content_hash=compute_content_hash("hello"),
    )
    local = _make_mount("local")
    shared = _make_mount("shared")
    local.port.ingest = AsyncMock(return_value=["local/page.md"])
    shared.port.ingest = AsyncMock(return_value=["shared/page.md"])

    adapter = CompositeMimirAdapter(mounts=[local, shared])
    result = await adapter.ingest_to(source, "shared")

    assert result == ["shared/page.md"]
    shared.port.ingest.assert_awaited_once_with(source)
    local.port.ingest.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_to_rejects_unknown_mount() -> None:
    source = MimirSource(
        source_id="src-1",
        title="Test source",
        content="hello",
        source_type="research",
        ingested_at=datetime.now(UTC),
        content_hash=compute_content_hash("hello"),
    )
    adapter = CompositeMimirAdapter(mounts=[_make_mount("local")])

    with pytest.raises(ValueError, match="Unknown Mimir mount: missing"):
        await adapter.ingest_to(source, "missing")


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


@pytest.mark.asyncio
async def test_read_source_from_mount_returns_only_from_named_mount() -> None:
    source = MimirSource(
        source_id="src-1",
        title="Test source",
        content="hello",
        source_type="research",
        ingested_at=datetime.now(UTC),
        content_hash=compute_content_hash("hello"),
    )
    local = _make_mount("local")
    shared = _make_mount("shared")
    local.port.read_source = AsyncMock(return_value=None)
    shared.port.read_source = AsyncMock(return_value=source)

    adapter = CompositeMimirAdapter(mounts=[local, shared])

    assert await adapter.read_source_from_mount("src-1", "shared") == source
    assert await adapter.read_source_from_mount("src-1", "missing") is None


@pytest.mark.asyncio
async def test_read_source_raises_when_no_mount_could_answer() -> None:
    """An unreachable Mímir is not an absent source.

    Swallowing the failure returned None, which the research-page validator
    reported as "references missing source_ids: ...; ingest those sources
    before writing the page". A live campaign hit that on a 503: the page was
    correct, the sources existed, and the agent was sent to re-ingest them.
    """
    local = _make_mount("local")
    shared = _make_mount("shared", role="shared")
    local.port.read_source = AsyncMock(side_effect=RuntimeError("503 Service Unavailable"))
    shared.port.read_source = AsyncMock(side_effect=RuntimeError("503 Service Unavailable"))

    adapter = CompositeMimirAdapter(mounts=[local, shared])

    with pytest.raises(MimirUnavailableError, match="could not read source"):
        await adapter.read_source("src-1")


@pytest.mark.asyncio
async def test_read_source_returns_none_when_every_mount_answered_and_lacked_it() -> None:
    """A genuine absence must stay absent — but only if every mount answered.

    This previously passed with one mount raising, which meant the broken
    mount's contents were assumed empty.
    """
    local = _make_mount("local")
    shared = _make_mount("shared", role="shared")
    local.port.read_source = AsyncMock(return_value=None)
    shared.port.read_source = AsyncMock(return_value=None)

    adapter = CompositeMimirAdapter(mounts=[local, shared])

    assert await adapter.read_source("src-1") is None


@pytest.mark.asyncio
async def test_read_source_from_mount_raises_when_that_mount_is_down() -> None:
    """The caller named this mount, so its failure is the answer to the call."""
    local = _make_mount("local")
    local.port.read_source = AsyncMock(side_effect=RuntimeError("boom"))
    adapter = CompositeMimirAdapter(mounts=[local])

    with pytest.raises(MimirUnavailableError, match="local"):
        await adapter.read_source_from_mount("src-1", "local")


@pytest.mark.asyncio
async def test_read_source_retries_until_a_mount_comes_back() -> None:
    """A restarting Mímir is unavailable for as long as it rebuilds its index.

    Session 30d0d041 failed provenance verification on a single 503 that landed
    50s into a 74s restart. The source existed the whole time.
    """
    source = MimirSource(
        source_id="src-1",
        title="Test source",
        content="hello",
        source_type="research",
        ingested_at=datetime.now(UTC),
        content_hash=compute_content_hash("hello"),
    )
    shared = _make_mount("shared", role="shared")
    shared.port.read_source = AsyncMock(
        side_effect=[
            RuntimeError("503 Service Unavailable"),
            RuntimeError("503 Service Unavailable"),
            source,
        ]
    )

    adapter = CompositeMimirAdapter(
        mounts=[shared],
        read_retry_max_seconds=30.0,
        read_retry_initial_backoff_seconds=0.001,
        read_retry_max_backoff_seconds=0.001,
    )

    assert await adapter.read_source("src-1") == source
    assert shared.port.read_source.await_count == 3


@pytest.mark.asyncio
async def test_read_source_raises_once_the_retry_budget_is_spent() -> None:
    shared = _make_mount("shared", role="shared")
    shared.port.read_source = AsyncMock(side_effect=RuntimeError("503 Service Unavailable"))

    adapter = CompositeMimirAdapter(
        mounts=[shared],
        read_retry_max_seconds=0.005,
        read_retry_initial_backoff_seconds=0.001,
        read_retry_max_backoff_seconds=0.001,
    )

    with pytest.raises(MimirUnavailableError, match="could not read source"):
        await adapter.read_source("src-1")
    assert shared.port.read_source.await_count > 1


@pytest.mark.asyncio
async def test_read_source_does_not_retry_a_genuine_absence() -> None:
    """A mount that answers "not here" is an answer — retrying only adds latency."""
    shared = _make_mount("shared", role="shared")
    shared.port.read_source = AsyncMock(return_value=None)

    adapter = CompositeMimirAdapter(
        mounts=[shared],
        read_retry_max_seconds=30.0,
        read_retry_initial_backoff_seconds=0.001,
    )

    assert await adapter.read_source("src-1") is None
    assert shared.port.read_source.await_count == 1


@pytest.mark.asyncio
async def test_read_source_retry_is_off_by_default() -> None:
    shared = _make_mount("shared", role="shared")
    shared.port.read_source = AsyncMock(side_effect=RuntimeError("503 Service Unavailable"))

    adapter = CompositeMimirAdapter(mounts=[shared])

    with pytest.raises(MimirUnavailableError):
        await adapter.read_source("src-1")
    assert shared.port.read_source.await_count == 1


@pytest.mark.asyncio
async def test_summarize_aggregates_every_mount() -> None:
    """Totals are only true when every mount contributed.

    This used to skip an unreachable mount, so `/mimir/mounts` reported a
    smaller corpus than exists — a shrinking page count reads as data loss,
    which is a worse signal than an outage that says so.
    """
    early = datetime(2026, 1, 1, tzinfo=UTC)
    late = datetime(2026, 6, 1, tzinfo=UTC)

    local = _make_mount("local")
    shared = _make_mount("shared", role="shared")
    local.port.summarize = AsyncMock(
        return_value=MimirMountSummary(
            page_count=3,
            source_count=1,
            categories=["technical"],
            last_write=early,
            lint_issues=2,
            lint_checked_at=early,
        )
    )
    shared.port.summarize = AsyncMock(
        return_value=MimirMountSummary(
            page_count=5,
            source_count=4,
            categories=["research"],
            last_write=late,
            lint_issues=1,
            lint_checked_at=late,
        )
    )
    adapter = CompositeMimirAdapter(mounts=[local, shared])
    summary = await adapter.summarize()

    assert summary.page_count == 8
    assert summary.source_count == 5
    assert summary.categories == ["research", "technical"]
    assert summary.last_write == late
    assert summary.lint_issues == 3
    assert summary.lint_checked_at == late


@pytest.mark.asyncio
async def test_read_source_excerpt_falls_through_to_the_mount_that_has_it() -> None:
    source = MimirSource(
        source_id="src-1",
        title="Test source",
        content="hello",
        source_type="research",
        ingested_at=datetime.now(UTC),
        content_hash=compute_content_hash("hello"),
    )
    local = _make_mount("local")
    shared = _make_mount("shared", role="shared")
    local.port.read_source_excerpt = AsyncMock(return_value=None)
    shared.port.read_source_excerpt = AsyncMock(return_value=source)

    adapter = CompositeMimirAdapter(mounts=[local, shared])

    assert await adapter.read_source_excerpt("src-1", 100) == source
    shared.port.read_source_excerpt.assert_awaited_once_with("src-1", 100)


@pytest.mark.asyncio
async def test_read_source_excerpt_shares_the_outage_semantics() -> None:
    """A bounded read of an unreachable mount is still an outage, not an absence."""
    shared = _make_mount("shared", role="shared")
    shared.port.read_source_excerpt = AsyncMock(side_effect=RuntimeError("503 Service Unavailable"))

    adapter = CompositeMimirAdapter(mounts=[shared])

    with pytest.raises(MimirUnavailableError, match="could not read source"):
        await adapter.read_source_excerpt("src-1", 100)


@pytest.mark.asyncio
async def test_summarize_handles_mounts_that_were_never_written_or_linted() -> None:
    empty = _make_mount("empty")
    empty.port.summarize = AsyncMock(
        return_value=MimirMountSummary(
            page_count=0,
            source_count=0,
            categories=[],
            last_write=None,
            lint_issues=0,
            lint_checked_at=None,
        )
    )

    summary = await CompositeMimirAdapter(mounts=[empty]).summarize()

    assert summary.page_count == 0
    assert summary.last_write is None
    assert summary.lint_checked_at is None


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
# A mount that fails fails the call — .claude/rules/no-fallbacks.md
# ---------------------------------------------------------------------------


def _error_port(error: Exception = RuntimeError("mount down")) -> object:
    """Return a mock port where every operation raises."""
    port = AsyncMock()
    port.ingest = AsyncMock(side_effect=error)
    port.query = AsyncMock(side_effect=error)
    port.search = AsyncMock(side_effect=error)
    port.list_pages = AsyncMock(side_effect=error)
    port.list_threads = AsyncMock(side_effect=error)
    port.get_thread_queue = AsyncMock(side_effect=error)
    port.list_sources = AsyncMock(side_effect=error)
    port.lint = AsyncMock(side_effect=error)
    port.summarize = AsyncMock(side_effect=error)
    port.read_source = AsyncMock(side_effect=error)
    port.read_page = AsyncMock(side_effect=error)
    port.get_page = AsyncMock(side_effect=error)
    port.upsert_page = AsyncMock(side_effect=error)
    port.delete_page = AsyncMock(side_effect=error)
    port.update_thread_weight = AsyncMock(side_effect=error)
    port.update_thread_state = AsyncMock(side_effect=error)
    return port


def _a_source() -> MimirSource:
    return MimirSource(
        source_id="s1",
        title="T",
        content="c",
        source_type="document",
        ingested_at=datetime.now(UTC),
        content_hash=compute_content_hash("c"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "call"),
    [
        ("ingest", lambda a: a.ingest(_a_source())),
        ("query", lambda a: a.query("test question")),
        ("search", lambda a: a.search("query")),
        ("list_pages", lambda a: a.list_pages()),
        ("list_sources", lambda a: a.list_sources()),
        ("lint", lambda a: a.lint()),
        ("list_threads", lambda a: a.list_threads()),
        ("get_thread_queue", lambda a: a.get_thread_queue()),
        ("summarize", lambda a: a.summarize()),
    ],
)
async def test_a_failing_mount_fails_the_whole_fan_out(operation: str, call) -> None:
    """One unreachable mount must not be papered over by the others.

    Every one of these used to catch, log a warning and carry on, returning
    whatever the surviving mounts held. That is indistinguishable from a small
    corpus, which is how the GBrain resident-state adapter stayed silently
    demoted on all nine residents for months. The caller has to be able to tell
    "the corpus does not contain this" from "I could not read the corpus".
    """
    bad = MimirMount(name="bad", port=_error_port(), role="local", read_priority=0)
    good = MimirMount(name="good", port=_mock_port(), role="shared", read_priority=1)
    adapter = CompositeMimirAdapter(mounts=[bad, good])

    with pytest.raises(MimirUnavailableError, match="'bad'"):
        await call(adapter)


@pytest.mark.asyncio
async def test_the_failing_mount_is_named_with_a_remedy() -> None:
    """An operator reading the log needs to know which mount and what to do."""
    bad = MimirMount(name="shared-mimir", port=_error_port(), role="shared", read_priority=0)
    adapter = CompositeMimirAdapter(mounts=[bad])

    with pytest.raises(MimirUnavailableError) as caught:
        await adapter.search("anything")

    message = str(caught.value)
    assert "shared-mimir" in message
    assert "search" in message
    assert "Fix or remove that mount" in message


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
async def test_a_broken_mount_cannot_be_read_as_a_missing_source() -> None:
    """One mount answering "not here" does not rule the source out.

    The old rule concluded absence whenever *some* mount answered, so a single
    broken mount turned a source that exists into a provenance defect — and
    prescribed a remedy (re-ingest) that cannot fix an outage.
    """
    bad_port = _error_port()
    good_port = _mock_port()
    good_port.read_source = AsyncMock(return_value=None)
    bad = MimirMount(name="bad", port=bad_port, role="local", read_priority=0)
    good = MimirMount(name="good", port=good_port, role="shared", read_priority=1)
    adapter = CompositeMimirAdapter(mounts=[bad, good])

    with pytest.raises(MimirUnavailableError, match="bad"):
        await adapter.read_source("src-1")


@pytest.mark.asyncio
async def test_write_routing_naming_an_unmounted_mimir_is_fatal() -> None:
    """Routing to a mount that does not exist writes nowhere.

    This used to log a warning and return normally, so the caller was told the
    page had been written.
    """
    good = MimirMount(name="good", port=_mock_port(), role="local", read_priority=0)
    routing = WriteRouting(rules=[("wiki/", ["nonexistent"])], default=["good"])
    adapter = CompositeMimirAdapter(mounts=[good], write_routing=routing)

    with pytest.raises(MimirUnavailableError) as caught:
        await adapter.upsert_page("wiki/page.md", "content")

    assert "nonexistent" in str(caught.value)
    assert "mounted: ['good']" in str(caught.value)


@pytest.mark.asyncio
async def test_a_failed_write_is_never_reported_as_written() -> None:
    bad = MimirMount(name="bad", port=_error_port(), role="local", read_priority=0)
    adapter = CompositeMimirAdapter(mounts=[bad])

    with pytest.raises(MimirUnavailableError, match="upsert_page"):
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
async def test_list_threads_does_not_hide_a_broken_mount_behind_a_partial_queue() -> None:
    """A thread queue missing a mount's threads looks like an idle resident."""
    bad_port = _error_port()
    bad_port.list_threads = AsyncMock(side_effect=RuntimeError("down"))
    good_port = _mock_port(pages=[_make_page("threads/alpha")])
    bad = MimirMount(name="bad", port=bad_port, role="local", read_priority=0)
    good = MimirMount(name="good", port=good_port, role="shared", read_priority=1)
    adapter = CompositeMimirAdapter(mounts=[bad, good])

    with pytest.raises(MimirUnavailableError, match="list_threads"):
        await adapter.list_threads()


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
