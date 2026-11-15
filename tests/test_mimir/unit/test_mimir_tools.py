"""Unit tests for Mímir knowledge base (NIU-540).

Tests cover:
- MimirSource, MimirPageMeta, MimirQueryResult domain models
- the mimir_* tool wrappers over an in-memory MimirPort: execute() paths, error handling
- MimirConfig defaults and the no-implicit-store rule
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mimir.client_config import MimirConfig
from mimir.client_settings import MimirClientSettings
from mimir.tools import (
    MimirIngestTool,
    MimirPublishArtifactTool,
    MimirPublishFilesTool,
    MimirReadSourceTool,
    MimirReadTool,
    MimirSearchTool,
    MimirWriteTool,
)
from niuu.domain.mimir import (
    MimirPageMeta,
    MimirQueryResult,
    MimirSource,
    compute_source_id,
)
from tests.test_mimir.unit.fakes import InMemoryMimirAdapter

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


def _make_adapter(tmp_path: Path) -> InMemoryMimirAdapter:
    return InMemoryMimirAdapter()


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
        {"content": "Some content.", "title": "My Source", "source_type": "research"}
    )

    assert not result.is_error
    event_emitter.assert_awaited_once()
    event_type, fields = event_emitter.await_args.args
    assert event_type == "mimir.source.ingested"
    assert fields["source_id"].startswith("src_")
    assert fields["source_title"] == "My Source"
    assert fields["source_type"] == "research"


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", ["tool_output", "diagnostic"])
async def test_mimir_ingest_tool_refuses_operational_sources(
    tmp_path: Path, source_type: str
) -> None:
    """The agent tool refuses what REST /ingest refuses with a 422: nothing is stored."""
    adapter = _make_adapter(tmp_path)
    event_emitter = AsyncMock()
    tool = MimirIngestTool(adapter, event_emitter=event_emitter)

    result = await tool.execute(
        {"content": "probe output", "title": "Run log", "source_type": source_type}
    )

    assert result.is_error
    assert f"Refusing to ingest source_type {source_type!r}" in result.content
    assert await adapter.list_sources() == []
    event_emitter.assert_not_awaited()


@pytest.mark.asyncio
async def test_mimir_ingest_tool_does_not_swallow_a_failed_target_lookup() -> None:
    from mimir.exceptions import MimirConfigurationError

    class _Unrouted(InMemoryMimirAdapter):
        def ingest_targets(self, explicit: str | None = None) -> list[str]:
            raise MimirConfigurationError("write routing selects no mount")

    tool = MimirIngestTool(_Unrouted(), event_emitter=AsyncMock())

    with pytest.raises(MimirConfigurationError, match="selects no mount"):
        await tool.execute({"content": "Some content.", "title": "My Source"})


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
    assert "research/new.md" in adapter.pages


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
async def test_mimir_publish_artifact_writes_and_verifies_json_capability(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    tool = MimirPublishArtifactTool(adapter)
    path = "capabilities/build-widget/learned_tool.json"
    content = json.dumps(
        {
            "manifest": {"name": "widget"},
            "tool_code": "def run(): pass",
            "test_code": "def test_run(): pass",
            "requirements": [],
        }
    )

    result = await tool.execute({"path": path, "content": content})

    assert not result.is_error
    assert await adapter.read_page(path) == content


@pytest.mark.asyncio
async def test_mimir_publish_artifact_rejects_non_json_capability_path() -> None:
    tool = MimirPublishArtifactTool(MagicMock())

    result = await tool.execute(
        {"path": "capabilities/build-widget/learned_tool.md", "content": "{}"}
    )

    assert result.is_error
    assert "capabilities/*.json" in result.content


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
    assert "research/campaigns/demo/final.md" in adapter.pages


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
    assert "deliveries/demo/40-manifest.md" in adapter.pages


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


# ---------------------------------------------------------------------------
# MimirConfig defaults
# ---------------------------------------------------------------------------


def test_mimir_config_defaults() -> None:
    cfg = MimirConfig()
    assert cfg.enabled is True
    assert cfg.instances == []


def test_an_enabled_mimir_with_no_instances_is_a_configuration_error() -> None:
    from mimir.builders import build_mimir
    from mimir.exceptions import MimirConfigurationError

    with pytest.raises(MimirConfigurationError, match="mimir.instances"):
        build_mimir(MimirClientSettings())


def test_a_disabled_mimir_builds_nothing() -> None:
    from mimir.builders import build_mimir

    settings = MimirClientSettings()
    settings.mimir.enabled = False

    assert build_mimir(settings) is None


def test_mimir_config_in_settings() -> None:
    settings = MimirClientSettings()
    assert hasattr(settings, "mimir")
    assert isinstance(settings.mimir, MimirConfig)
