"""Unit tests for Mímir knowledge base (NIU-540).

Tests cover:
- MimirSource, MimirPage, MimirPageMeta, MimirQueryResult, MimirLintReport domain models
- MarkdownMimirAdapter: ingest, upsert_page, read_page, search, list_pages, lint
- Staleness detection (content_hash comparison)
- Log and index maintenance
- MIMIR.md seeding on first run
- Seven mimir_* tool wrappers: execute() paths, error handling
- MimirConfig defaults
- Auto-distillation trigger criteria (post-session drive loop)
- Integration: session → ingest → wiki page created with log entry
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from niuu.domain.mimir import compute_source_id
from ravn.adapters.mimir.markdown import (
    MarkdownMimirAdapter,
    _extract_summary,
    _extract_title,
)
from ravn.adapters.tools.mimir_tools import (
    MimirIngestTool,
    MimirLintTool,
    MimirPublishFilesTool,
    MimirQueryTool,
    MimirReadSourceTool,
    MimirReadTool,
    MimirSearchTool,
    MimirWriteTool,
    build_mimir_tools,
)
from ravn.config import MimirConfig, Settings
from ravn.domain.models import (
    AgentTask,
    MimirLintReport,
    MimirPageMeta,
    MimirQueryResult,
    MimirSource,
    OutputMode,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _make_source(
    title: str = "Test Source",
    content: str = "Hello world.",
    source_type: str = "document",
    origin_url: str | None = None,
) -> MimirSource:
    return MimirSource(
        source_id=f"src_{_sha256(title)[:16]}",
        title=title,
        content=content,
        source_type=source_type,
        origin_url=origin_url,
        content_hash=_sha256(content),
        ingested_at=datetime.now(UTC),
    )


def _make_adapter(tmp_path: Path) -> MarkdownMimirAdapter:
    return MarkdownMimirAdapter(root=tmp_path / "mimir")


# ---------------------------------------------------------------------------
# Domain model tests
# ---------------------------------------------------------------------------


def test_mimir_source_fields() -> None:
    src = _make_source(content="some content")
    assert src.source_id.startswith("src_")
    assert src.content == "some content"
    assert src.content_hash == _sha256("some content")
    assert src.ingested_at.tzinfo is not None


def test_mimir_page_meta_fields() -> None:
    meta = MimirPageMeta(
        path="technical/ravn/tools.md",
        title="Ravn Tools",
        summary="Overview of agent tools.",
        category="technical",
        updated_at=datetime.now(UTC),
        source_ids=["src_abc"],
    )
    assert meta.path == "technical/ravn/tools.md"
    assert meta.category == "technical"
    assert meta.source_ids == ["src_abc"]


def test_mimir_query_result_empty_answer() -> None:
    result = MimirQueryResult(question="What is X?", answer="", sources=[])
    assert result.question == "What is X?"
    assert result.answer == ""
    assert result.sources == []


def test_mimir_lint_report_issues_found() -> None:
    from niuu.domain.mimir import LintIssue

    report = MimirLintReport(
        issues=[LintIssue(id="L01", severity="warning", message="Orphan", page_path="a.md")],
        pages_checked=5,
    )
    assert report.issues_found is True


def test_mimir_lint_report_no_issues() -> None:
    report = MimirLintReport(issues=[], pages_checked=3)
    assert report.issues_found is False


# ---------------------------------------------------------------------------
# MarkdownMimirAdapter — layout bootstrap
# ---------------------------------------------------------------------------


def test_adapter_creates_directories(tmp_path: Path) -> None:
    _make_adapter(tmp_path)
    root = tmp_path / "mimir"
    assert (root / "wiki").is_dir()
    assert (root / "raw").is_dir()
    assert (root / "MIMIR.md").is_file()
    assert (root / "wiki" / "index.md").is_file()
    assert (root / "wiki" / "log.md").is_file()


def test_mimir_md_seeded_on_first_run(tmp_path: Path) -> None:
    _make_adapter(tmp_path)
    schema = (tmp_path / "mimir" / "MIMIR.md").read_text()
    assert "self/ conventions" in schema
    assert "third person" in schema


def test_mimir_md_not_overwritten_on_second_run(tmp_path: Path) -> None:
    _make_adapter(tmp_path)
    custom = "custom content"
    (tmp_path / "mimir" / "MIMIR.md").write_text(custom)
    # Re-initialising should NOT overwrite existing file
    MarkdownMimirAdapter(root=tmp_path / "mimir")
    assert (tmp_path / "mimir" / "MIMIR.md").read_text() == custom


# ---------------------------------------------------------------------------
# MarkdownMimirAdapter — ingest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_stores_raw_source(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    src = _make_source(title="My Doc", content="Important content.")
    await adapter.ingest(src)

    raw_file = tmp_path / "mimir" / "raw" / f"{src.source_id}.json"
    assert raw_file.exists()
    data = json.loads(raw_file.read_text())
    assert data["title"] == "My Doc"
    assert data["content_hash"] == src.content_hash


@pytest.mark.asyncio
async def test_ingest_appends_to_log(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    src = _make_source(title="Log Test")
    await adapter.ingest(src)

    log = (tmp_path / "mimir" / "wiki" / "log.md").read_text()
    assert "ingest" in log
    assert "Log Test" in log


@pytest.mark.asyncio
async def test_ingest_returns_empty_list_by_default(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    result = await adapter.ingest(_make_source())
    # Page creation is delegated to the agent; ingest() returns empty list
    assert result == []


# ---------------------------------------------------------------------------
# MarkdownMimirAdapter — upsert_page / read_page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_page_creates_file(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    content = "# Ravn Tools\n\nOverview of agent tools."
    await adapter.upsert_page("technical/ravn/tools.md", content)

    page_file = tmp_path / "mimir" / "wiki" / "technical" / "ravn" / "tools.md"
    assert page_file.exists()
    assert page_file.read_text() == content


def test_extract_source_ids_only_counts_footer_comment() -> None:
    content = (
        "# Task Prompt\n\n"
        "The instruction mentions <!-- sources: src_prompt --> in the body.\n\n"
        "No source footer follows."
    )
    assert MarkdownMimirAdapter._extract_source_ids(content) == []
    assert MarkdownMimirAdapter._extract_source_ids(
        "# Synthesised Page\n\nUseful summary.\n\n<!-- sources: src_footer -->\n"
    ) == ["src_footer"]


@pytest.mark.asyncio
async def test_upsert_page_adds_to_index(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    content = "# Auth Overview\n\nHow authentication works."
    await adapter.upsert_page("technical/volundr/auth.md", content)

    index = (tmp_path / "mimir" / "wiki" / "index.md").read_text()
    assert "technical/volundr/auth.md" in index
    assert "Auth Overview" in index


@pytest.mark.asyncio
async def test_upsert_page_updates_existing(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.upsert_page("research/topic.md", "# Old Title\n\nOld summary.")
    await adapter.upsert_page("research/topic.md", "# New Title\n\nNew summary.")

    index = (tmp_path / "mimir" / "wiki" / "index.md").read_text()
    assert "New Title" in index


@pytest.mark.asyncio
async def test_get_page_derives_title_from_path_when_heading_missing(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.upsert_page("runs/NIU-911-postmortem.md", "## Compiled Truth\n\nBody text.")

    page = await adapter.get_page("runs/NIU-911-postmortem.md")

    assert page.meta.title == "NIU-911 Postmortem"


@pytest.mark.asyncio
async def test_get_page_works_when_adapter_root_is_symlink(tmp_path: Path) -> None:
    real_root = tmp_path / "real-mimir"
    alias_root = tmp_path / "alias-mimir"
    real_root.mkdir(parents=True, exist_ok=True)
    alias_root.symlink_to(real_root, target_is_directory=True)

    adapter = MarkdownMimirAdapter(root=alias_root)
    await adapter.upsert_page("runs/NIU-912-postmortem.md", "## Compiled Truth\n\nBody text.")

    page = await adapter.get_page("runs/NIU-912-postmortem.md")

    assert page.meta.path == "runs/NIU-912-postmortem.md"
    assert page.meta.title == "NIU-912 Postmortem"


@pytest.mark.asyncio
async def test_read_page_returns_content(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    content = "# Test\n\nBody text."
    await adapter.upsert_page("projects/test.md", content)
    result = await adapter.read_page("projects/test.md")
    assert result == content


@pytest.mark.asyncio
async def test_read_page_raises_for_missing(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    with pytest.raises(FileNotFoundError):
        await adapter.read_page("nonexistent/page.md")


# ---------------------------------------------------------------------------
# MarkdownMimirAdapter — list_pages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pages_returns_all(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.upsert_page("technical/ravn/tools.md", "# Tools\n\nA.")
    await adapter.upsert_page("projects/saga.md", "# Saga\n\nB.")
    pages = await adapter.list_pages()
    paths = [p.path for p in pages]
    assert "technical/ravn/tools.md" in paths
    assert "projects/saga.md" in paths


@pytest.mark.asyncio
async def test_list_pages_filtered_by_category(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.upsert_page("technical/ravn/tools.md", "# Tools\n\nA.")
    await adapter.upsert_page("research/k8s.md", "# K8s\n\nB.")
    pages = await adapter.list_pages(category="technical")
    paths = [p.path for p in pages]
    assert "technical/ravn/tools.md" in paths
    assert all("technical" in p for p in paths)


@pytest.mark.asyncio
async def test_list_pages_excludes_index_and_log(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.upsert_page("technical/page.md", "# Page\n\nContent.")
    pages = await adapter.list_pages()
    paths = [p.path for p in pages]
    assert "index.md" not in paths
    assert "log.md" not in paths


# ---------------------------------------------------------------------------
# MarkdownMimirAdapter — search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_finds_matching_pages(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.upsert_page("technical/ravn/memory.md", "# Memory\n\nEpisodic storage.")
    await adapter.upsert_page("technical/ravn/tools.md", "# Tools\n\nBash and file tools.")
    results = await adapter.search("episodic storage")
    titles = [p.meta.title for p in results]
    assert "Memory" in titles


@pytest.mark.asyncio
async def test_search_returns_empty_for_no_match(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.upsert_page("technical/ravn/tools.md", "# Tools\n\nBash tools.")
    results = await adapter.search("kubernetes longhorn ceph")
    assert results == []


@pytest.mark.asyncio
async def test_search_matches_path_title_when_body_omits_query(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.upsert_page("runs/NIU-911-postmortem.md", "## Compiled Truth\n\nBody text.")

    results = await adapter.search("postmortem")

    assert [p.meta.path for p in results] == ["runs/NIU-911-postmortem.md"]


@pytest.mark.asyncio
async def test_search_excludes_index_and_log(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    # Write a keyword directly to index and log
    index = tmp_path / "mimir" / "wiki" / "index.md"
    index.write_text("# Catalog\n\nspecial_keyword here.", encoding="utf-8")
    results = await adapter.search("special_keyword")
    paths = [p.meta.path for p in results]
    assert "index.md" not in paths
    assert "log.md" not in paths


# ---------------------------------------------------------------------------
# MarkdownMimirAdapter — query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_appends_to_log(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.upsert_page("technical/page.md", "# Page\n\nSome content about Ravn.")
    await adapter.query("what is Ravn?")
    log = (tmp_path / "mimir" / "wiki" / "log.md").read_text()
    assert "query" in log
    assert "what is Ravn?" in log


@pytest.mark.asyncio
async def test_query_returns_result_struct(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.upsert_page("technical/ravn/agent.md", "# Agent\n\nCore loop.")
    result = await adapter.query("how does the agent work?")
    assert isinstance(result, MimirQueryResult)
    assert result.question == "how does the agent work?"
    assert isinstance(result.sources, list)


# ---------------------------------------------------------------------------
# MarkdownMimirAdapter — lint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lint_finds_orphan_pages(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    # Write a page directly without going through upsert_page (so it's not in index)
    (tmp_path / "mimir" / "wiki" / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "mimir" / "wiki" / "research" / "orphan.md").write_text(
        "# Orphan\n\nNot in index.", encoding="utf-8"
    )
    report = await adapter.lint()
    orphan_paths = [i.page_path for i in report.issues if i.id == "L01"]
    assert "research/orphan.md" in orphan_paths


@pytest.mark.asyncio
async def test_lint_finds_contradictions(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    content = "# Auth\n\n[CONTRADICTION] This conflicts with the session model."
    await adapter.upsert_page("technical/auth.md", content)
    report = await adapter.lint()
    contradiction_paths = [i.page_path for i in report.issues if i.id == "L02"]
    assert "technical/auth.md" in contradiction_paths


@pytest.mark.asyncio
async def test_lint_pages_checked_count(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.upsert_page("technical/a.md", "# A\n\nContent A.")
    await adapter.upsert_page("technical/b.md", "# B\n\nContent B.")
    report = await adapter.lint()
    assert report.pages_checked == 2


@pytest.mark.asyncio
async def test_lint_appends_to_log(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.lint()
    log = (tmp_path / "mimir" / "wiki" / "log.md").read_text()
    assert "lint" in log
    assert "pages checked" in log


@pytest.mark.asyncio
async def test_lint_no_issues(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.upsert_page(
        "technical/page.md",
        "---\ntype: topic\n---\n# Page\n\nClean content.",
    )
    report = await adapter.lint()
    # page was indexed by upsert_page, so no L01 orphan
    orphan_paths = [i.page_path for i in report.issues if i.id == "L01"]
    assert "technical/page.md" not in orphan_paths
    assert report.pages_checked == 1


# ---------------------------------------------------------------------------
# Staleness detection
# ---------------------------------------------------------------------------


def test_compute_content_hash_is_sha256() -> None:
    text = "hello world"
    expected = hashlib.sha256(text.encode()).hexdigest()
    assert MarkdownMimirAdapter.compute_content_hash(text) == expected


@pytest.mark.asyncio
async def test_is_source_stale_returns_false_when_unchanged(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    src = _make_source(content="original")
    await adapter.ingest(src)
    assert adapter.is_source_stale(src.source_id, "original") is False


@pytest.mark.asyncio
async def test_is_source_stale_returns_true_when_changed(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    src = _make_source(content="original")
    await adapter.ingest(src)
    assert adapter.is_source_stale(src.source_id, "completely different content") is True


def test_is_source_stale_returns_false_for_unknown_source(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    # No source stored → not stale (nothing to compare)
    assert adapter.is_source_stale("nonexistent_id", "content") is False


@pytest.mark.asyncio
async def test_lint_stale_always_empty(tmp_path: Path) -> None:
    """Lint never populates L03 (stale sources).

    Staleness requires re-fetching source URLs, which the lint pass does not do.
    Use is_source_stale() before re-ingesting a source to detect changes.
    """
    adapter = _make_adapter(tmp_path)
    src = _make_source(title="Stale Doc", content="original")
    await adapter.ingest(src)

    page_content = f"# Stale\n\nDerived from source.\n<!-- sources: {src.source_id} -->"
    await adapter.upsert_page("research/stale.md", page_content)

    # Even if the raw JSON hash is tampered with, lint never emits L03
    raw_path = tmp_path / "mimir" / "raw" / f"{src.source_id}.json"
    data = json.loads(raw_path.read_text())
    data["content_hash"] = "badhash"
    raw_path.write_text(json.dumps(data))

    report = await adapter.lint()
    l03_issues = [i for i in report.issues if i.id == "L03"]
    assert l03_issues == []


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def test_extract_title_from_h1() -> None:
    assert _extract_title("# My Title\n\nContent.") == "My Title"


def test_extract_title_fallback() -> None:
    assert _extract_title("No heading here.") == "Untitled"


def test_extract_summary_returns_first_body_line() -> None:
    content = "# Title\n\nThis is the summary.\n\nMore text."
    assert _extract_summary(content) == "This is the summary."


def test_extract_summary_skips_headings() -> None:
    content = "# Title\n## Subtitle\n\nActual summary."
    assert _extract_summary(content) == "Actual summary."


# ---------------------------------------------------------------------------
# Tool wrapper tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mimir_ingest_tool_success(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    tool = MimirIngestTool(adapter)
    result = await tool.execute(
        {"content": "Some content.", "title": "My Source", "source_type": "document"}
    )
    assert not result.is_error
    assert "Ingested source" in result.content
    assert f"source_id: {compute_source_id('Some content.')}" in result.content
    assert "source_type: document" in result.content


@pytest.mark.asyncio
async def test_mimir_ingest_tool_emits_source_ingested_event(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    event_emitter = AsyncMock()
    tool = MimirIngestTool(adapter, event_emitter=event_emitter)

    result = await tool.execute(
        {"content": "Some content.", "title": "My Source", "source_type": "tool_output"}
    )

    assert not result.is_error
    event_emitter.assert_awaited_once()
    event_type, fields = event_emitter.await_args.args
    assert event_type == "mimir.source.ingested"
    assert fields["source_id"].startswith("src_")
    assert fields["source_title"] == "My Source"
    assert fields["source_type"] == "tool_output"


@pytest.mark.asyncio
async def test_mimir_ingest_tool_missing_content() -> None:
    adapter = MagicMock(spec=["ingest"])
    tool = MimirIngestTool(adapter)
    result = await tool.execute({"title": "no content"})
    assert result.is_error


@pytest.mark.asyncio
async def test_mimir_ingest_tool_missing_title() -> None:
    adapter = MagicMock(spec=["ingest"])
    tool = MimirIngestTool(adapter)
    result = await tool.execute({"content": "some content"})
    assert result.is_error


@pytest.mark.asyncio
async def test_mimir_query_tool_with_results(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.upsert_page("technical/ravn/agent.md", "# Agent\n\nCore loop details.")
    tool = MimirQueryTool(adapter)
    result = await tool.execute({"question": "how does agent core loop work?"})
    assert not result.is_error
    assert "Agent" in result.content


@pytest.mark.asyncio
async def test_mimir_query_tool_no_results(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    tool = MimirQueryTool(adapter)
    result = await tool.execute({"question": "something completely unknown"})
    assert not result.is_error
    assert "No wiki pages found" in result.content


@pytest.mark.asyncio
async def test_mimir_query_tool_missing_question() -> None:
    adapter = MagicMock()
    tool = MimirQueryTool(adapter)
    result = await tool.execute({})
    assert result.is_error


@pytest.mark.asyncio
async def test_mimir_read_tool_success(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.upsert_page("projects/saga.md", "# Saga\n\nContent.")
    tool = MimirReadTool(adapter)
    result = await tool.execute({"path": "projects/saga.md"})
    assert not result.is_error
    assert "Saga" in result.content


@pytest.mark.asyncio
async def test_mimir_read_tool_not_found(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    tool = MimirReadTool(adapter)
    result = await tool.execute({"path": "missing/page.md"})
    assert result.is_error


@pytest.mark.asyncio
async def test_mimir_read_tool_missing_path() -> None:
    adapter = MagicMock()
    tool = MimirReadTool(adapter)
    result = await tool.execute({})
    assert result.is_error


@pytest.mark.asyncio
async def test_mimir_read_source_tool_success(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    source = _make_source(title="Source Title", content="Source content.")
    await adapter.ingest(source)

    tool = MimirReadSourceTool(adapter)
    result = await tool.execute({"source_id": source.source_id})

    assert not result.is_error
    assert "Source ID:" in result.content
    assert "Source Title" in result.content
    assert "Source content." in result.content


@pytest.mark.asyncio
async def test_mimir_write_tool_success(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    tool = MimirWriteTool(adapter)
    source = _make_source(
        title="Reference",
        content="Original source material.",
        source_type="research",
    )
    await adapter.ingest(source)
    result = await tool.execute(
        {
            "path": "research/new.md",
            "content": (
                "---\n"
                "type: research\n"
                "confidence: high\n"
                "produced_by_thread: true\n"
                f"source_ids: [{source.source_id}]\n"
                "---\n\n"
                "# New Page\n\n"
                "## Compiled Truth\n\n"
                "Body."
            ),
        }
    )
    assert not result.is_error
    assert "research/new.md" in result.content

    # Verify the page was written
    page_path = tmp_path / "mimir" / "wiki" / "research" / "new.md"
    assert page_path.exists()


@pytest.mark.asyncio
async def test_mimir_write_tool_research_page_requires_source_ids(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    tool = MimirWriteTool(adapter)
    result = await tool.execute(
        {
            "path": "research/new.md",
            "content": (
                "---\n"
                "type: research\n"
                "confidence: high\n"
                "produced_by_thread: true\n"
                "source_ids: []\n"
                "---\n\n"
                "# New Page\n\n"
                "## Compiled Truth\n\n"
                "Body."
            ),
        }
    )
    assert result.is_error
    assert "source_ids" in result.content


@pytest.mark.asyncio
async def test_mimir_write_tool_research_page_rejects_self_ingested_content(
    tmp_path: Path,
) -> None:
    adapter = _make_adapter(tmp_path)
    tool = MimirWriteTool(adapter)
    content = (
        "---\n"
        "type: research\n"
        "confidence: high\n"
        "produced_by_thread: true\n"
        "source_ids: [src_self]\n"
        "---\n\n"
        "# New Page\n\n"
        "## Compiled Truth\n\n"
        "Body."
    )
    await adapter.ingest(
        MimirSource(
            source_id="src_self",
            title="New Page",
            content=content,
            source_type="document",
            content_hash=_sha256(content),
            ingested_at=datetime.now(UTC),
        )
    )

    result = await tool.execute({"path": "research/new.md", "content": content})
    assert result.is_error
    assert "research page provenance only points to the page content itself" in result.content


@pytest.mark.asyncio
async def test_mimir_write_tool_non_md_extension() -> None:
    adapter = MagicMock()
    tool = MimirWriteTool(adapter)
    result = await tool.execute({"path": "research/bad.txt", "content": "# Title"})
    assert result.is_error
    assert ".md" in result.content


@pytest.mark.asyncio
async def test_mimir_write_tool_missing_path() -> None:
    adapter = MagicMock()
    tool = MimirWriteTool(adapter)
    result = await tool.execute({"content": "content"})
    assert result.is_error


@pytest.mark.asyncio
async def test_mimir_write_tool_missing_content() -> None:
    adapter = MagicMock()
    tool = MimirWriteTool(adapter)
    result = await tool.execute({"path": "test.md"})
    assert result.is_error


@pytest.mark.asyncio
async def test_mimir_publish_files_tool_publishes_workspace_markdown(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    research_dir = workspace / "research" / "campaigns" / "demo"
    research_dir.mkdir(parents=True)
    source = _make_source(
        title="Reference",
        content="Original source material.",
        source_type="research",
    )
    await adapter.ingest(source)
    content = (
        "---\n"
        "type: research\n"
        "confidence: medium\n"
        f"source_ids: [{source.source_id}]\n"
        "---\n\n"
        "# Demo\n\n"
        "## Compiled Truth\n\n"
        "Body."
    )
    (research_dir / "final.md").write_text(content, encoding="utf-8")

    tool = MimirPublishFilesTool(adapter, workspace)
    result = await tool.execute({"paths": ["research/campaigns/demo/final.md"]})

    assert not result.is_error
    assert "research/campaigns/demo/final.md" in result.content
    page_path = tmp_path / "mimir" / "wiki" / "research" / "campaigns" / "demo" / "final.md"
    assert page_path.exists()


@pytest.mark.asyncio
async def test_mimir_publish_files_tool_rejects_missing_workspace_file(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool = MimirPublishFilesTool(adapter, workspace)
    result = await tool.execute({"paths": ["research/campaigns/demo/final.md"]})
    assert result.is_error
    assert "Workspace file not found" in result.content


@pytest.mark.asyncio
async def test_mimir_publish_files_tool_reports_failed_publish_verification(
    tmp_path: Path,
) -> None:
    adapter = MagicMock()
    adapter.upsert_page = AsyncMock(return_value=None)
    adapter.get_page = AsyncMock(side_effect=FileNotFoundError("missing after write"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    notes_dir = workspace / "notes"
    notes_dir.mkdir()
    (notes_dir / "demo.md").write_text("# Demo\n\nBody.", encoding="utf-8")

    tool = MimirPublishFilesTool(adapter, workspace)
    result = await tool.execute({"paths": ["notes/demo.md"]})

    assert result.is_error
    assert "Failed to publish notes/demo.md" in result.content


@pytest.mark.asyncio
async def test_mimir_publish_files_tool_publishes_repo_relative_markdown(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    workspace = tmp_path / "workspace"
    repo = workspace / "repo"
    repo.mkdir(parents=True)
    delivery_dir = repo / "deliveries" / "demo"
    delivery_dir.mkdir(parents=True)
    content = "# Delivery\n\nPublished from repo-relative path."
    (delivery_dir / "40-manifest.md").write_text(content, encoding="utf-8")

    tool = MimirPublishFilesTool(adapter, workspace)
    result = await tool.execute({"paths": ["deliveries/demo/40-manifest.md"]})

    assert not result.is_error
    assert "deliveries/demo/40-manifest.md" in result.content
    page_path = tmp_path / "mimir" / "wiki" / "deliveries" / "demo" / "40-manifest.md"
    assert page_path.exists()


@pytest.mark.asyncio
async def test_mimir_search_tool_success(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.upsert_page("technical/ravn/memory.md", "# Memory\n\nEpisodic storage.")
    tool = MimirSearchTool(adapter)
    result = await tool.execute({"query": "episodic"})
    assert not result.is_error
    assert "Memory" in result.content


@pytest.mark.asyncio
async def test_mimir_search_tool_no_results(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    tool = MimirSearchTool(adapter)
    result = await tool.execute({"query": "zzz_nonexistent_term_zzz"})
    assert not result.is_error
    assert "No pages found" in result.content


@pytest.mark.asyncio
async def test_mimir_search_tool_missing_query() -> None:
    adapter = MagicMock()
    tool = MimirSearchTool(adapter)
    result = await tool.execute({})
    assert result.is_error


@pytest.mark.asyncio
async def test_mimir_lint_tool_clean(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.upsert_page("technical/page.md", "# Page\n\nClean content.")
    tool = MimirLintTool(adapter)
    result = await tool.execute({})
    assert not result.is_error
    assert "pages checked" in result.content


@pytest.mark.asyncio
async def test_mimir_lint_tool_reports_issues(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    # Add a page with contradiction marker
    await adapter.upsert_page("technical/x.md", "# X\n\n[CONTRADICTION] Something wrong.")
    tool = MimirLintTool(adapter)
    result = await tool.execute({})
    assert not result.is_error
    assert "Contradictions" in result.content


# ---------------------------------------------------------------------------
# build_mimir_tools factory
# ---------------------------------------------------------------------------


def test_build_mimir_tools_returns_seven(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    tools = build_mimir_tools(adapter, workspace=tmp_path)
    assert len(tools) == 8
    names = {t.name for t in tools}
    assert names == {
        "mimir_ingest",
        "mimir_query",
        "mimir_read",
        "mimir_read_source",
        "mimir_write",
        "mimir_publish_files",
        "mimir_search",
        "mimir_lint",
    }


# ---------------------------------------------------------------------------
# MimirConfig defaults
# ---------------------------------------------------------------------------


def test_mimir_config_defaults() -> None:
    cfg = MimirConfig()
    assert cfg.enabled is True
    assert cfg.path == "~/.ravn/mimir"
    assert cfg.auto_distill is True
    assert cfg.distill_min_session_minutes == 5
    assert cfg.idle_lint_threshold_minutes == 60
    assert cfg.continuation_threshold_minutes == 30
    assert "technical" in cfg.categories
    assert "self" in cfg.categories
    assert cfg.search.backend == "fts"


def test_mimir_config_in_settings() -> None:
    settings = Settings()
    assert hasattr(settings, "mimir")
    assert isinstance(settings.mimir, MimirConfig)


# ---------------------------------------------------------------------------
# Integration: session → ingest → wiki page created with log entry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_ingest_then_write_creates_page_with_log(tmp_path: Path) -> None:
    """Simulate: ingest a source, agent writes a wiki page, log updated."""
    adapter = _make_adapter(tmp_path)

    # 1. Ingest a raw source
    src = _make_source(
        title="Kubernetes Pod Lifecycle",
        content="Pods go through Pending, Running, Succeeded, Failed states.",
        source_type="web",
        origin_url="https://example.com/k8s-pods",
    )
    await adapter.ingest(src)

    # 2. Agent writes a wiki page derived from the source
    page_content = (
        f"# Kubernetes Pod Lifecycle\n\n"
        f"Pods transition through four states: Pending, Running, Succeeded, Failed.\n\n"
        f"<!-- sources: {src.source_id} -->"
    )
    await adapter.upsert_page("technical/k8s/pod-lifecycle.md", page_content)

    # 3. Verify page exists
    written = await adapter.read_page("technical/k8s/pod-lifecycle.md")
    assert "Pod Lifecycle" in written

    # 4. Verify index updated
    index = (tmp_path / "mimir" / "wiki" / "index.md").read_text()
    assert "technical/k8s/pod-lifecycle.md" in index

    # 5. Verify log has ingest entry
    log = (tmp_path / "mimir" / "wiki" / "log.md").read_text()
    assert "ingest" in log
    assert "Kubernetes Pod Lifecycle" in log


@pytest.mark.asyncio
async def test_integration_distillation_trigger_creates_agent_task() -> None:
    """Post-session distillation: verify AgentTask structure is correct."""
    task = AgentTask(
        task_id="task_abc_0001",
        title="Mímir distillation — post-session",
        initiative_context=(
            "Review the conversation that just ended. Extract:\n"
            "1. Any new factual knowledge worth preserving as Mímir wiki pages\n"
            "2. Any findings that update or contradict existing pages\n"
            "3. Decisions or patterns worth recording for future reference\n\n"
            "Write or update wiki pages using mimir_write(). "
            "Update index.md. Append to log.md. "
            "Be concise — the wiki is for retrieval, not transcription."
        ),
        triggered_by="post_session:distillation",
        output_mode=OutputMode.SILENT,
        priority=15,
    )
    assert task.output_mode == OutputMode.SILENT
    assert task.session_id == "daemon_task_abc_0001"
    assert "mimir_write" in task.initiative_context
    assert "log.md" in task.initiative_context
