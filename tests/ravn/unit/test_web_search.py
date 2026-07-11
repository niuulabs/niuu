"""Unit tests for WebSearchTool and its real provider boundary."""

from __future__ import annotations

import pytest

from ravn.adapters.tools.web_search import DuckDuckGoLiteSearchProvider, WebSearchTool
from ravn.ports.web_search import SearchResult

# ---------------------------------------------------------------------------
class FakeWebSearchProvider:
    _DEFAULT_RESULTS = [
        SearchResult(title="Example Domain", url="https://example.com", snippet="Example"),
        SearchResult(title="Python", url="https://docs.python.org", snippet="Python docs"),
        SearchResult(title="GitHub", url="https://github.com", snippet="Code hosting"),
    ]

    def __init__(self, results: list[SearchResult] | None = None) -> None:
        self._results = list(self._DEFAULT_RESULTS if results is None else results)

    async def search(self, _query: str, *, num_results: int) -> list[SearchResult]:
        return self._results[:num_results]


# ---------------------------------------------------------------------------
# Test fake
# ---------------------------------------------------------------------------


class TestFakeWebSearchProvider:
    @pytest.mark.asyncio
    async def test_returns_default_results(self) -> None:
        provider = FakeWebSearchProvider()
        results = await provider.search("python", num_results=3)
        assert len(results) == 3
        assert all(isinstance(r, SearchResult) for r in results)

    @pytest.mark.asyncio
    async def test_respects_num_results(self) -> None:
        provider = FakeWebSearchProvider()
        results = await provider.search("test", num_results=1)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_num_results_zero_returns_empty(self) -> None:
        provider = FakeWebSearchProvider()
        results = await provider.search("anything", num_results=0)
        assert results == []

    @pytest.mark.asyncio
    async def test_custom_results_used(self) -> None:
        custom = [
            SearchResult(title="Custom", url="https://custom.io", snippet="custom snippet"),
        ]
        provider = FakeWebSearchProvider(results=custom)
        results = await provider.search("whatever", num_results=5)
        assert len(results) == 1
        assert results[0].title == "Custom"

    @pytest.mark.asyncio
    async def test_results_have_required_fields(self) -> None:
        provider = FakeWebSearchProvider()
        results = await provider.search("q", num_results=2)
        for r in results:
            assert r.title
            assert r.url.startswith("https://")
            assert r.snippet

    @pytest.mark.asyncio
    async def test_query_does_not_affect_mock_results(self) -> None:
        provider = FakeWebSearchProvider()
        r1 = await provider.search("query one", num_results=2)
        r2 = await provider.search("query two", num_results=2)
        assert r1 == r2


# ---------------------------------------------------------------------------
# WebSearchTool properties
# ---------------------------------------------------------------------------


class TestWebSearchToolProperties:
    def test_name(self) -> None:
        assert WebSearchTool().name == "web_search"

    def test_description_is_non_empty(self) -> None:
        assert len(WebSearchTool().description) > 10

    def test_input_schema_requires_query(self) -> None:
        schema = WebSearchTool().input_schema
        assert "query" in schema["properties"]
        assert "query" in schema["required"]

    def test_input_schema_has_num_results(self) -> None:
        schema = WebSearchTool().input_schema
        assert "num_results" in schema["properties"]

    def test_required_permission(self) -> None:
        assert WebSearchTool().required_permission == "web:search"

    def test_parallelisable_default(self) -> None:
        assert WebSearchTool().parallelisable is True

    def test_uses_real_provider_by_default(self) -> None:
        tool = WebSearchTool()
        assert isinstance(tool._provider, DuckDuckGoLiteSearchProvider)


# ---------------------------------------------------------------------------
# WebSearchTool.execute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestWebSearchToolExecute:
    async def test_empty_query_returns_error(self) -> None:
        tool = WebSearchTool()
        result = await tool.execute({"query": ""})
        assert result.is_error
        assert "query is required" in result.content

    async def test_missing_query_returns_error(self) -> None:
        tool = WebSearchTool()
        result = await tool.execute({})
        assert result.is_error

    async def test_returns_formatted_results(self) -> None:
        tool = WebSearchTool(provider=FakeWebSearchProvider())
        result = await tool.execute({"query": "python asyncio"})
        assert not result.is_error
        assert "1." in result.content
        assert "https://" in result.content

    async def test_num_results_controls_output(self) -> None:
        provider = FakeWebSearchProvider(
            results=[
                SearchResult(title=f"Result {i}", url=f"https://r{i}.io", snippet=f"Snippet {i}")
                for i in range(5)
            ]
        )
        tool = WebSearchTool(provider=provider)
        result = await tool.execute({"query": "test", "num_results": 2})
        assert not result.is_error
        assert "Result 0" in result.content
        assert "Result 2" not in result.content

    async def test_no_results_returns_friendly_message(self) -> None:
        provider = FakeWebSearchProvider(results=[])
        tool = WebSearchTool(provider=provider)
        result = await tool.execute({"query": "xyzzy"})
        assert not result.is_error
        assert "no results" in result.content.lower()

    async def test_result_contains_title_url_snippet(self) -> None:
        provider = FakeWebSearchProvider(
            results=[
                SearchResult(title="MyTitle", url="https://myurl.io", snippet="MySummary"),
            ]
        )
        tool = WebSearchTool(provider=provider)
        result = await tool.execute({"query": "find me"})
        assert "MyTitle" in result.content
        content_lines = result.content.splitlines()
        assert any(line.strip() == "https://myurl.io" for line in content_lines)
        assert "MySummary" in result.content

    async def test_uses_tool_default_num_results(self) -> None:
        results = [
            SearchResult(title=f"R{i}", url=f"https://r{i}.io", snippet="s") for i in range(10)
        ]
        provider = FakeWebSearchProvider(results=results)
        tool = WebSearchTool(provider=provider, num_results=3)
        result = await tool.execute({"query": "q"})
        assert not result.is_error
        # 3 results means 3 numbered items
        assert "3." in result.content
        assert "4." not in result.content
