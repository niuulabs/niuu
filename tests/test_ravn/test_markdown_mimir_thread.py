"""Adapter tests for MarkdownMimirAdapter thread support (NIU-564).

Uses pytest's tmp_path fixture for an isolated wiki directory.
Covers upsert_page with thread meta writing frontmatter correctly to file.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from mimir.adapters.markdown import MarkdownMimirAdapter
from niuu.domain.mimir import (
    MimirPageMeta,
    ThreadContextRef,
    ThreadState,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(tmp_path: Path) -> MarkdownMimirAdapter:
    return MarkdownMimirAdapter(root=tmp_path / "mimir")


def _thread_meta(
    path: str = "technical/open-question.md",
    title: str = "Open Question",
    summary: str = "This is an open question",
    thread_state: ThreadState = ThreadState.open,
    thread_weight: float = 1.0,
    is_thread: bool = True,
    thread_next_action_hint: str | None = "Investigate further",
    thread_context_refs: list[ThreadContextRef] | None = None,
) -> MimirPageMeta:
    return MimirPageMeta(
        path=path,
        title=title,
        summary=summary,
        category="technical",
        updated_at=datetime(2024, 6, 1, tzinfo=UTC),
        source_ids=["src-1"],
        thread_state=thread_state,
        thread_weight=thread_weight,
        is_thread=is_thread,
        thread_next_action_hint=thread_next_action_hint,
        thread_context_refs=thread_context_refs or [],
    )


# ---------------------------------------------------------------------------
# upsert_page — content written to filesystem
# ---------------------------------------------------------------------------


class TestUpsertPageWithThreadMeta:
    @pytest.mark.asyncio
    async def test_page_file_created(self, tmp_path: Path) -> None:
        adapter = _make_adapter(tmp_path)
        meta = _thread_meta()
        content = "# Open Question\nThis is an open question about the system."

        await adapter.upsert_page(meta.path, content, meta=meta)

        page_file = tmp_path / "mimir" / "wiki" / meta.path
        assert page_file.exists()

    @pytest.mark.asyncio
    async def test_content_written_to_file(self, tmp_path: Path) -> None:
        adapter = _make_adapter(tmp_path)
        meta = _thread_meta()
        content = "# Open Question\nThis is an open question about the system."

        await adapter.upsert_page(meta.path, content, meta=meta)

        page_file = tmp_path / "mimir" / "wiki" / meta.path
        written = page_file.read_text(encoding="utf-8")
        assert written == content

    @pytest.mark.asyncio
    async def test_page_with_frontmatter_content_preserved(self, tmp_path: Path) -> None:
        """Content containing YAML frontmatter is written verbatim."""
        adapter = _make_adapter(tmp_path)
        meta = _thread_meta()
        frontmatter_content = (
            "---\n"
            "thread_state: open\n"
            "thread_weight: 1.0\n"
            "is_thread: true\n"
            "---\n"
            "# Open Question\n"
            "Some content here.\n"
        )

        await adapter.upsert_page(meta.path, frontmatter_content, meta=meta)

        page_file = tmp_path / "mimir" / "wiki" / meta.path
        written = page_file.read_text(encoding="utf-8")
        assert "thread_state: open" in written
        assert "thread_weight: 1.0" in written
        assert "is_thread: true" in written
        assert "# Open Question" in written

    @pytest.mark.asyncio
    async def test_upsert_page_accepts_meta_kwarg_without_error(self, tmp_path: Path) -> None:
        """upsert_page accepts MimirPageMeta as meta kwarg without raising."""
        adapter = _make_adapter(tmp_path)
        meta = _thread_meta(thread_state=ThreadState.pulling)
        content = "# Pulling Thread\nBeing worked on."

        # Should not raise even though meta may be ignored internally
        await adapter.upsert_page(meta.path, content, meta=meta)

    @pytest.mark.asyncio
    async def test_upsert_page_creates_parent_directories(self, tmp_path: Path) -> None:
        adapter = _make_adapter(tmp_path)
        meta = _thread_meta(path="technical/deep/nested/page.md")
        content = "# Nested\nContent."

        await adapter.upsert_page(meta.path, content, meta=meta)

        page_file = tmp_path / "mimir" / "wiki" / meta.path
        assert page_file.exists()

    @pytest.mark.asyncio
    async def test_upsert_page_overwrites_existing_content(self, tmp_path: Path) -> None:
        adapter = _make_adapter(tmp_path)
        meta = _thread_meta()
        original = "# Original\nOriginal content."
        updated = "# Updated\nUpdated content with thread tags."

        await adapter.upsert_page(meta.path, original, meta=meta)
        await adapter.upsert_page(meta.path, updated, meta=meta)

        page_file = tmp_path / "mimir" / "wiki" / meta.path
        written = page_file.read_text(encoding="utf-8")
        assert written == updated
        assert "Original" not in written

    @pytest.mark.asyncio
    async def test_upsert_page_with_thread_context_refs_in_content(self, tmp_path: Path) -> None:
        """Content may embed context refs as HTML comments; adapter writes it as-is."""
        adapter = _make_adapter(tmp_path)
        refs = [ThreadContextRef(ref_type="ingest", ref_id="src-1", ref_summary="Source page")]
        meta = _thread_meta(thread_context_refs=refs)
        content = "# Open Thread\nSome open question.\n<!-- sources: src-1 -->\n"

        await adapter.upsert_page(meta.path, content, meta=meta)

        page_file = tmp_path / "mimir" / "wiki" / meta.path
        written = page_file.read_text(encoding="utf-8")
        assert "<!-- sources: src-1 -->" in written

    @pytest.mark.asyncio
    async def test_upsert_page_without_meta_still_works(self, tmp_path: Path) -> None:
        """upsert_page works without meta kwarg (plain wiki page write)."""
        adapter = _make_adapter(tmp_path)
        content = "# Plain Wiki Page\nNo thread metadata."

        await adapter.upsert_page("technical/plain.md", content)

        page_file = tmp_path / "mimir" / "wiki" / "technical" / "plain.md"
        assert page_file.exists()
        assert page_file.read_text(encoding="utf-8") == content

    @pytest.mark.asyncio
    async def test_new_page_added_to_index(self, tmp_path: Path) -> None:
        """upsert_page for a new page updates wiki/index.md."""
        adapter = _make_adapter(tmp_path)
        meta = _thread_meta()
        content = "# Open Question\nIs this a thread?"

        await adapter.upsert_page(meta.path, content, meta=meta)

        index = tmp_path / "mimir" / "wiki" / "index.md"
        assert index.exists()

    @pytest.mark.asyncio
    async def test_upsert_page_read_back_via_read_page(self, tmp_path: Path) -> None:
        """Content written via upsert_page can be read back via read_page."""
        adapter = _make_adapter(tmp_path)
        meta = _thread_meta()
        content = "# Thread\nThe thread content."

        await adapter.upsert_page(meta.path, content, meta=meta)
        read_back = await adapter.read_page(meta.path)

        assert read_back == content
