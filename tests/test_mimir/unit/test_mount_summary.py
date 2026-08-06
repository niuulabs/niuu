"""Tests for the cheap mount summary path.

``/mimir/mounts`` and ``/mimir/stats`` are polled continuously by dashboards and
by every mount listing. They used to answer by walking the whole corpus — every
page body, every raw source blob, plus a full lint pass — inside an async
handler with no thread offload. On the shared Mímir that took 6–8s per call on a
half-core budget, which starved the liveness probe, and each kill cost a further
74s of 503s while the search index rebuilt. A research campaign lost its
provenance check to exactly that window.

Covers:
- summarize() counts pages and sources without reading their bodies
- summarize() never triggers a lint pass
- summarize() reports the last recorded lint result, and says when that was
- list_sources() re-parses a raw source only when the file actually changes
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mimir.adapters.markdown import MarkdownMimirAdapter
from niuu.domain.mimir import MimirSource, compute_content_hash

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(tmp_path: Path) -> MarkdownMimirAdapter:
    return MarkdownMimirAdapter(root=tmp_path / "mimir")


def _write_page(adapter: MarkdownMimirAdapter, rel_path: str, content: str) -> Path:
    page = adapter._wiki / rel_path
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(content, encoding="utf-8")
    return page


def _make_source(source_id: str, content: str = "body") -> MimirSource:
    return MimirSource(
        source_id=source_id,
        title=f"Source {source_id}",
        content=content,
        source_type="research",
        ingested_at=datetime.now(UTC),
        content_hash=compute_content_hash(content),
    )


# ---------------------------------------------------------------------------
# summarize()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarize_counts_pages_and_categories(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    _write_page(adapter, "technical/alpha.md", "# Alpha\n")
    _write_page(adapter, "technical/beta.md", "# Beta\n")
    _write_page(adapter, "research/gamma.md", "# Gamma\n")

    summary = await adapter.summarize()

    assert summary.page_count == 3
    assert summary.categories == ["research", "technical"]


@pytest.mark.asyncio
async def test_summarize_skips_index_and_log_like_list_pages(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    _write_page(adapter, "technical/alpha.md", "# Alpha\n")

    summary = await adapter.summarize()
    pages = await adapter.list_pages()

    assert summary.page_count == len(pages) == 1


@pytest.mark.asyncio
async def test_summarize_counts_sources_without_parsing_them(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    adapter._write_raw_source(_make_source("src-1"))
    # A blob that would raise if the summary tried to parse it: the count comes
    # from directory entries, so malformed content must not matter here.
    (adapter._raw / "src-broken.json").write_text("{not json", encoding="utf-8")

    summary = await adapter.summarize()

    assert summary.source_count == 2


@pytest.mark.asyncio
async def test_summarize_reports_latest_write_across_pages_and_sources(
    tmp_path: Path,
) -> None:
    adapter = _make_adapter(tmp_path)
    page = _write_page(adapter, "technical/alpha.md", "# Alpha\n")
    old = datetime(2020, 1, 1, tzinfo=UTC).timestamp()
    os.utime(page, (old, old))
    adapter._write_raw_source(_make_source("src-1"))

    summary = await adapter.summarize()

    assert summary.last_write is not None
    assert summary.last_write.year > 2020


@pytest.mark.asyncio
async def test_summarize_of_an_empty_store_has_no_last_write(tmp_path: Path) -> None:
    summary = await _make_adapter(tmp_path).summarize()

    assert summary.page_count == 0
    assert summary.source_count == 0
    assert summary.last_write is None


@pytest.mark.asyncio
async def test_summarize_never_runs_lint(tmp_path: Path, monkeypatch) -> None:
    """The corpus-wide lint pass is what made a mount listing cost seconds."""
    adapter = _make_adapter(tmp_path)
    _write_page(adapter, "technical/alpha.md", "# Alpha\n")

    def _explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("summarize must not trigger a lint pass")

    monkeypatch.setattr(adapter, "_lint_sync", _explode)

    summary = await adapter.summarize()

    assert summary.page_count == 1


@pytest.mark.asyncio
async def test_summarize_reports_unknown_lint_before_any_lint_has_run(
    tmp_path: Path,
) -> None:
    """Zero issues with no timestamp means "never checked", not "clean"."""
    adapter = _make_adapter(tmp_path)
    _write_page(adapter, "technical/alpha.md", "# Alpha\n")

    summary = await adapter.summarize()

    assert summary.lint_issues == 0
    assert summary.lint_checked_at is None


@pytest.mark.asyncio
async def test_summarize_reports_the_last_recorded_lint_result(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    # A page with no source attribution and too little content trips lint.
    _write_page(adapter, "technical/alpha.md", "# Alpha\n\nthin.\n")

    report = await adapter.lint()
    summary = await adapter.summarize()

    assert summary.lint_issues == len(report.issues)
    assert summary.lint_checked_at is not None


@pytest.mark.asyncio
async def test_summarize_survives_an_unparseable_lint_timestamp(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    _write_page(adapter, "technical/alpha.md", "# Alpha\n")
    cache = adapter._load_lint_cache()
    cache["last_checked_at"] = "not-a-timestamp"
    adapter._save_lint_cache(cache)

    summary = await adapter.summarize()

    assert summary.lint_checked_at is None


# ---------------------------------------------------------------------------
# Raw-source metadata caching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sources_does_not_reparse_unchanged_files(tmp_path: Path) -> None:
    """Raw sources run to megabytes; re-parsing them per poll is the hot cost."""
    adapter = _make_adapter(tmp_path)
    adapter._write_raw_source(_make_source("src-1"))

    first = await adapter.list_sources()
    parsed: list[str] = []
    original_read_text = Path.read_text

    def _tracking_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.suffix == ".json":
            parsed.append(self.name)
        return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    Path.read_text = _tracking_read_text  # type: ignore[method-assign]
    try:
        second = await adapter.list_sources()
    finally:
        Path.read_text = original_read_text  # type: ignore[method-assign]

    assert [s.source_id for s in first] == [s.source_id for s in second]
    assert parsed == []


@pytest.mark.asyncio
async def test_list_sources_picks_up_a_rewritten_source(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    adapter._write_raw_source(_make_source("src-1"))
    await adapter.list_sources()

    path = adapter._raw / "src-1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["title"] = "Renamed"
    path.write_text(json.dumps(data), encoding="utf-8")
    # stat() resolution is finite, so make the change unambiguous.
    future = datetime.now(UTC).timestamp() + 10
    os.utime(path, (future, future))

    refreshed = await adapter.list_sources()

    assert [s.title for s in refreshed] == ["Renamed"]


@pytest.mark.asyncio
async def test_list_sources_forgets_deleted_sources(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    adapter._write_raw_source(_make_source("src-1"))
    adapter._write_raw_source(_make_source("src-2"))
    await adapter.list_sources()

    (adapter._raw / "src-1.json").unlink()
    remaining = await adapter.list_sources()

    assert [s.source_id for s in remaining] == ["src-2"]
    assert "src-1.json" not in adapter._source_meta_cache


@pytest.mark.asyncio
async def test_list_sources_drops_a_malformed_source_without_failing(
    tmp_path: Path,
) -> None:
    """One corrupt blob must not make the whole mount look empty."""
    adapter = _make_adapter(tmp_path)
    adapter._write_raw_source(_make_source("src-1"))
    (adapter._raw / "src-broken.json").write_text("{not json", encoding="utf-8")

    sources = await adapter.list_sources()

    assert [s.source_id for s in sources] == ["src-1"]


@pytest.mark.asyncio
async def test_list_sources_unprocessed_on_an_empty_store(tmp_path: Path) -> None:
    assert await _make_adapter(tmp_path).list_sources(unprocessed_only=True) == []


@pytest.mark.asyncio
async def test_list_sources_unprocessed_excludes_referenced_sources(
    tmp_path: Path,
) -> None:
    adapter = _make_adapter(tmp_path)
    adapter._write_raw_source(_make_source("src-cited", content="cited body"))
    adapter._write_raw_source(_make_source("src-loose", content="loose body"))
    _write_page(adapter, "technical/alpha.md", "# Alpha\n\n<!-- sources: src-cited -->\n")

    unprocessed = await adapter.list_sources(unprocessed_only=True)

    assert [s.source_id for s in unprocessed] == ["src-loose"]


@pytest.mark.asyncio
async def test_list_sources_unprocessed_excludes_a_re_ingest_of_cited_content(
    tmp_path: Path,
) -> None:
    """Same content under a new id is already synthesised — it is not new work."""
    adapter = _make_adapter(tmp_path)
    adapter._write_raw_source(_make_source("src-cited", content="same body"))
    adapter._write_raw_source(_make_source("src-again", content="same body"))
    _write_page(adapter, "technical/alpha.md", "# Alpha\n\n<!-- sources: src-cited -->\n")

    unprocessed = await adapter.list_sources(unprocessed_only=True)

    assert unprocessed == []


# ---------------------------------------------------------------------------
# Racing writers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarize_skips_entries_that_vanish_mid_walk(tmp_path: Path, monkeypatch) -> None:
    """Ingest and lint write while a summary walks; a deleted entry is not fatal."""
    adapter = _make_adapter(tmp_path)
    _write_page(adapter, "technical/alpha.md", "# Alpha\n")
    _write_page(adapter, "technical/beta.md", "# Beta\n")
    adapter._write_raw_source(_make_source("src-1"))
    adapter._write_raw_source(_make_source("src-2"))

    original_stat = Path.stat

    def _stat_with_a_vanishing_entry(self: Path, *args: object, **kwargs: object):
        if self.name in {"beta.md", "src-2.json"}:
            raise FileNotFoundError(self)
        return original_stat(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", _stat_with_a_vanishing_entry)

    summary = await adapter.summarize()

    assert summary.page_count == 1
    assert summary.source_count == 1


@pytest.mark.asyncio
async def test_list_sources_skips_an_entry_that_vanishes_mid_walk(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = _make_adapter(tmp_path)
    adapter._write_raw_source(_make_source("src-1"))
    adapter._write_raw_source(_make_source("src-2"))

    original_stat = Path.stat

    def _stat_with_a_vanishing_entry(self: Path, *args: object, **kwargs: object):
        if self.name == "src-2.json":
            raise FileNotFoundError(self)
        return original_stat(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", _stat_with_a_vanishing_entry)

    sources = await adapter.list_sources()

    assert [s.source_id for s in sources] == ["src-1"]
