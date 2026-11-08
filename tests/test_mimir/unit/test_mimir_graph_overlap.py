"""Unit tests for SourceOverlapGraphAdapter."""

from __future__ import annotations

import pytest

from mimir.adapters.graph_source_overlap import SourceOverlapGraphAdapter


class TestSourceOverlapGraphAdapter:
    @pytest.mark.asyncio
    async def test_empty_input_returns_empty_graph(self) -> None:
        adapter = SourceOverlapGraphAdapter()
        graph = await adapter.build_graph({})
        assert graph.nodes == []
        assert graph.edges == []

    @pytest.mark.asyncio
    async def test_single_page_no_edges(self) -> None:
        adapter = SourceOverlapGraphAdapter()
        graph = await adapter.build_graph({"wiki/a.md": ["src-1"]})
        assert "wiki/a.md" in graph.nodes
        assert graph.edges == []

    @pytest.mark.asyncio
    async def test_shared_source_creates_edge(self) -> None:
        adapter = SourceOverlapGraphAdapter()
        graph = await adapter.build_graph(
            {
                "wiki/a.md": ["src-1"],
                "wiki/b.md": ["src-1"],
            }
        )
        assert len(graph.edges) == 1
        assert set(graph.edges[0]) == {"wiki/a.md", "wiki/b.md"}

    @pytest.mark.asyncio
    async def test_no_shared_source_no_edge(self) -> None:
        adapter = SourceOverlapGraphAdapter()
        graph = await adapter.build_graph(
            {
                "wiki/a.md": ["src-1"],
                "wiki/b.md": ["src-2"],
            }
        )
        assert graph.edges == []

    @pytest.mark.asyncio
    async def test_multiple_shared_sources_single_edge(self) -> None:
        adapter = SourceOverlapGraphAdapter()
        graph = await adapter.build_graph(
            {
                "wiki/a.md": ["src-1", "src-2"],
                "wiki/b.md": ["src-1", "src-2"],
            }
        )
        # Should produce exactly one edge (deduplication)
        assert len(graph.edges) == 1

    @pytest.mark.asyncio
    async def test_three_pages_shared_source_three_edges(self) -> None:
        adapter = SourceOverlapGraphAdapter()
        graph = await adapter.build_graph(
            {
                "wiki/a.md": ["src-shared"],
                "wiki/b.md": ["src-shared"],
                "wiki/c.md": ["src-shared"],
            }
        )
        assert len(graph.edges) == 3

    @pytest.mark.asyncio
    async def test_result_is_cached(self) -> None:
        adapter = SourceOverlapGraphAdapter()
        data = {"wiki/a.md": ["src-1"], "wiki/b.md": ["src-1"]}
        g1 = await adapter.build_graph(data)
        g2 = await adapter.build_graph(data)
        assert g1 is g2  # Same object from cache

    @pytest.mark.asyncio
    async def test_cache_invalidated_on_different_input(self) -> None:
        adapter = SourceOverlapGraphAdapter()
        g1 = await adapter.build_graph({"wiki/a.md": ["s1"]})
        g2 = await adapter.build_graph({"wiki/a.md": ["s1"], "wiki/b.md": ["s1"]})
        assert g1 is not g2

    @pytest.mark.asyncio
    async def test_related_pages_returns_neighbours(self) -> None:
        adapter = SourceOverlapGraphAdapter()
        await adapter.build_graph(
            {
                "wiki/a.md": ["src-1"],
                "wiki/b.md": ["src-1"],
                "wiki/c.md": ["src-1"],
            }
        )
        related = await adapter.related_pages("wiki/a.md")
        assert set(related) == {"wiki/b.md", "wiki/c.md"}

    @pytest.mark.asyncio
    async def test_related_pages_no_graph_returns_empty(self) -> None:
        adapter = SourceOverlapGraphAdapter()
        assert await adapter.related_pages("wiki/a.md") == []

    @pytest.mark.asyncio
    async def test_related_pages_limit(self) -> None:
        adapter = SourceOverlapGraphAdapter()
        data = {f"wiki/{chr(ord('a') + i)}.md": ["src-1"] for i in range(10)}
        await adapter.build_graph(data)
        related = await adapter.related_pages("wiki/a.md", limit=3)
        assert len(related) <= 3

    @pytest.mark.asyncio
    async def test_related_pages_unknown_path_returns_empty(self) -> None:
        adapter = SourceOverlapGraphAdapter()
        await adapter.build_graph({"wiki/a.md": ["src-1"]})
        assert await adapter.related_pages("wiki/unknown.md") == []
