"""Unit tests for LogBasedUsageAdapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from mimir.adapters.usage_log import LogBasedUsageAdapter


class TestLogBasedUsageAdapter:
    def _make_adapter(self, tmp_path: Path) -> LogBasedUsageAdapter:
        return LogBasedUsageAdapter(tmp_path)

    def _write_log(self, tmp_path: Path, content: str) -> None:
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir(parents=True, exist_ok=True)
        (wiki_dir / "log.md").write_text(content)

    @pytest.mark.asyncio
    async def test_top_pages_no_log_returns_empty(self, tmp_path: Path) -> None:
        adapter = self._make_adapter(tmp_path)
        assert await adapter.top_pages() == []

    @pytest.mark.asyncio
    async def test_record_access_included_in_top_pages(self, tmp_path: Path) -> None:
        adapter = self._make_adapter(tmp_path)
        await adapter.record_access("wiki/page-a.md")
        await adapter.record_access("wiki/page-a.md")
        await adapter.record_access("wiki/page-b.md")
        top = await adapter.top_pages()
        assert top[0] == ("wiki/page-a.md", 2)
        assert top[1] == ("wiki/page-b.md", 1)

    @pytest.mark.asyncio
    async def test_parse_log_counts_links(self, tmp_path: Path) -> None:
        self._write_log(
            tmp_path,
            """\
## [2024-01-01] query | cloud infrastructure
Found results: [AWS Guide](wiki/aws.md) and [GCP Guide](wiki/gcp.md)

## [2024-01-02] query | kubernetes
Found: [K8s Basics](wiki/k8s.md)
""",
        )
        adapter = self._make_adapter(tmp_path)
        top = await adapter.top_pages()
        paths = [p for p, _ in top]
        assert "wiki/aws.md" in paths
        assert "wiki/gcp.md" in paths
        assert "wiki/k8s.md" in paths

    @pytest.mark.asyncio
    async def test_parse_log_skips_non_query_lines(self, tmp_path: Path) -> None:
        self._write_log(
            tmp_path,
            """\
## [2024-01-01] index | page listing
- [Some page](wiki/index.md)

## [2024-01-02] query | real query
Results: [Target page](wiki/result.md)
""",
        )
        adapter = self._make_adapter(tmp_path)
        top = await adapter.top_pages()
        paths = [p for p, _ in top]
        # index section links should NOT be counted
        assert "wiki/index.md" not in paths
        assert "wiki/result.md" in paths

    @pytest.mark.asyncio
    async def test_pages_above_threshold(self, tmp_path: Path) -> None:
        adapter = self._make_adapter(tmp_path)
        await adapter.record_access("wiki/hot.md")
        await adapter.record_access("wiki/hot.md")
        await adapter.record_access("wiki/hot.md")
        await adapter.record_access("wiki/cold.md")
        above = await adapter.pages_above_threshold(2)
        assert "wiki/hot.md" in above
        assert "wiki/cold.md" not in above

    @pytest.mark.asyncio
    async def test_pages_above_threshold_no_data(self, tmp_path: Path) -> None:
        adapter = self._make_adapter(tmp_path)
        assert await adapter.pages_above_threshold(1) == []

    @pytest.mark.asyncio
    async def test_log_and_session_counts_combined(self, tmp_path: Path) -> None:
        self._write_log(
            tmp_path,
            """\
## [2024-01-01] query | test
Found: [Page A](wiki/a.md)
""",
        )
        adapter = self._make_adapter(tmp_path)
        await adapter.record_access("wiki/a.md")  # adds 1 more to log's 1
        top = await adapter.top_pages()
        count_a = next((c for p, c in top if p == "wiki/a.md"), 0)
        assert count_a == 2

    @pytest.mark.asyncio
    async def test_top_pages_respects_n(self, tmp_path: Path) -> None:
        adapter = self._make_adapter(tmp_path)
        for i in range(10):
            await adapter.record_access(f"wiki/page-{i}.md")
        top = await adapter.top_pages(n=5)
        assert len(top) <= 5

    @pytest.mark.asyncio
    async def test_unreadable_log_returns_empty(self, tmp_path: Path) -> None:
        # Create log as a directory so reading it fails
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir(parents=True)
        (wiki_dir / "log.md").mkdir()  # directory, not file → OSError on read
        adapter = self._make_adapter(tmp_path)
        top = await adapter.top_pages()
        assert top == []
