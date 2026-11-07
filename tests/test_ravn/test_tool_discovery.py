"""Tests for the ToolDiscovery index and ToolSearchTool.

All embedding calls are handled by an in-memory fake — no real models or
network calls required.
"""

from __future__ import annotations

import math

import pytest

from niuu.domain.tool import ToolResult
from niuu.ports.embedding import EmbeddingPort
from niuu.ports.tool import ToolPort
from ravn.adapters.memory.scoring import cosine_similarity
from ravn.adapters.tools.discovery import ToolDiscovery, ToolSearchTool
from ravn.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeEmbeddingAdapter(EmbeddingPort):
    """Deterministic fake embedding adapter.

    Each unique text receives a distinct unit vector so cosine similarity
    between different texts is a fixed known value and between the same
    text is 1.0.

    The fake encodes text by hashing it into a 4-dimensional vector whose
    first component is different for each unique text seen.  This is
    sufficient for ordering tests — we don't need numerically accurate
    similarity, just reproducible, predictable ordering.
    """

    def __init__(self, dim: int = 4) -> None:
        self._dim = dim
        self._calls: list[str] = []
        # Map each unique text to a deterministic unit vector.
        self._memo: dict[str, list[float]] = {}
        self._counter = 0

    def _make_vec(self, text: str) -> list[float]:
        if text in self._memo:
            return self._memo[text]
        # Build a simple distinct vector: [counter, 0, 0, …, 0] (unit-normalised).
        raw = [0.0] * self._dim
        raw[self._counter % self._dim] = 1.0
        self._counter += 1
        self._memo[text] = raw
        return raw

    async def embed(self, text: str) -> list[float]:
        self._calls.append(text)
        return self._make_vec(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]

    @property
    def dimension(self) -> int:
        return self._dim


class SimpleTool(ToolPort):
    """Minimal tool that echoes a name."""

    def __init__(self, tool_name: str, tool_description: str = "A simple tool.") -> None:
        self._tool_name = tool_name
        self._tool_description = tool_description

    @property
    def name(self) -> str:
        return self._tool_name

    @property
    def description(self) -> str:
        return self._tool_description

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def required_permission(self) -> str:
        return f"tool:{self._tool_name}"

    async def execute(self, input: dict) -> ToolResult:
        return ToolResult(tool_call_id="", content=f"ok:{self._tool_name}")


# ---------------------------------------------------------------------------
# cosine_similarity tests
# ---------------------------------------------------------------------------


def test_cosine_similarity_identical_vectors():
    v = [1.0, 0.0, 0.0]
    assert cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_similarity_opposite_vectors():
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert cosine_similarity(a, b) == pytest.approx(-1.0)


def test_cosine_similarity_zero_vector_a():
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_cosine_similarity_zero_vector_b():
    assert cosine_similarity([1.0, 0.0], [0.0, 0.0]) == 0.0


def test_cosine_similarity_both_zero():
    assert cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0


def test_cosine_similarity_known_value():
    # 45 degrees → cos(45°) = 1/√2
    a = [1.0, 1.0]
    b = [1.0, 0.0]
    expected = 1.0 / math.sqrt(2)
    assert cosine_similarity(a, b) == pytest.approx(expected, rel=1e-6)


# ---------------------------------------------------------------------------
# ToolDiscovery.index() tests
# ---------------------------------------------------------------------------


async def test_index_empty_registry_produces_empty_index():
    reg = ToolRegistry()
    emb = FakeEmbeddingAdapter()
    discovery = ToolDiscovery(reg, emb)

    await discovery.index()

    assert discovery._index == []
    assert emb._calls == []


async def test_index_populates_entries_for_each_tool():
    reg = ToolRegistry()
    reg.register(SimpleTool("alpha", "Alpha description"))
    reg.register(SimpleTool("beta", "Beta description"))

    emb = FakeEmbeddingAdapter()
    discovery = ToolDiscovery(reg, emb)
    await discovery.index()

    assert len(discovery._index) == 2
    names = {e.name for e in discovery._index}
    assert names == {"alpha", "beta"}


async def test_index_stores_correct_metadata():
    reg = ToolRegistry()
    reg.register(SimpleTool("mytool", "Does something useful"))
    emb = FakeEmbeddingAdapter()
    discovery = ToolDiscovery(reg, emb)
    await discovery.index()

    entry = discovery._index[0]
    assert entry.name == "mytool"
    assert entry.description == "Does something useful"
    assert entry.required_permission == "tool:mytool"


async def test_index_calls_embed_batch_with_descriptions():
    reg = ToolRegistry()
    reg.register(SimpleTool("t1", "First tool desc"))
    reg.register(SimpleTool("t2", "Second tool desc"))

    emb = FakeEmbeddingAdapter()
    discovery = ToolDiscovery(reg, emb)
    await discovery.index()

    assert "First tool desc" in emb._calls
    assert "Second tool desc" in emb._calls


async def test_index_rebuilds_on_second_call():
    reg = ToolRegistry()
    reg.register(SimpleTool("only"))
    emb = FakeEmbeddingAdapter()
    discovery = ToolDiscovery(reg, emb)

    await discovery.index()
    assert len(discovery._index) == 1

    # Calling index() again replaces the index.
    await discovery.index()
    assert len(discovery._index) == 1


# ---------------------------------------------------------------------------
# ToolDiscovery.search() tests
# ---------------------------------------------------------------------------


async def test_search_empty_index_returns_empty_list():
    reg = ToolRegistry()
    emb = FakeEmbeddingAdapter()
    discovery = ToolDiscovery(reg, emb)
    # No index() call — index is empty.

    results = await discovery.search("find files")
    assert results == []


async def test_search_returns_tuples_of_entry_and_score():
    reg = ToolRegistry()
    reg.register(SimpleTool("file_search", "Search files by pattern"))
    emb = FakeEmbeddingAdapter()
    discovery = ToolDiscovery(reg, emb)
    await discovery.index()

    results = await discovery.search("find files")

    assert len(results) == 1
    entry, score = results[0]
    assert entry.name == "file_search"
    assert isinstance(score, float)


async def test_search_limits_results_to_top_n():
    reg = ToolRegistry()
    for i in range(10):
        reg.register(SimpleTool(f"tool_{i}", f"Tool {i} description"))

    emb = FakeEmbeddingAdapter()
    discovery = ToolDiscovery(reg, emb)
    await discovery.index()

    results = await discovery.search("some query", top_n=3)
    assert len(results) == 3


async def test_search_default_top_n_is_five():
    reg = ToolRegistry()
    for i in range(10):
        reg.register(SimpleTool(f"t_{i}", f"Tool {i}"))
    emb = FakeEmbeddingAdapter()
    discovery = ToolDiscovery(reg, emb)
    await discovery.index()

    results = await discovery.search("query")
    assert len(results) == 5


async def test_search_returns_all_when_fewer_than_top_n():
    reg = ToolRegistry()
    reg.register(SimpleTool("only_one", "The only tool"))
    emb = FakeEmbeddingAdapter()
    discovery = ToolDiscovery(reg, emb)
    await discovery.index()

    results = await discovery.search("anything", top_n=10)
    assert len(results) == 1


async def test_search_returns_highest_score_first():
    """The tool whose description most closely matches the query scores highest."""
    reg = ToolRegistry()
    # Both tools get the same unit-vector embedding direction as the query
    # via the fake adapter — but we test ordering is descending.
    reg.register(SimpleTool("a", "run shell commands"))
    reg.register(SimpleTool("b", "read a file"))
    emb = FakeEmbeddingAdapter()
    discovery = ToolDiscovery(reg, emb)
    await discovery.index()

    results = await discovery.search("run shell commands", top_n=2)
    assert len(results) == 2
    # Scores must be in descending order.
    assert results[0][1] >= results[1][1]


# ---------------------------------------------------------------------------
# ToolSearchTool property tests
# ---------------------------------------------------------------------------


def test_tool_search_tool_name():
    reg = ToolRegistry()
    emb = FakeEmbeddingAdapter()
    tool = ToolSearchTool(ToolDiscovery(reg, emb))
    assert tool.name == "tool_search"


def test_tool_search_tool_required_permission():
    reg = ToolRegistry()
    emb = FakeEmbeddingAdapter()
    tool = ToolSearchTool(ToolDiscovery(reg, emb))
    assert tool.required_permission == "introspect:read"


def test_tool_search_tool_input_schema_has_query():
    reg = ToolRegistry()
    emb = FakeEmbeddingAdapter()
    tool = ToolSearchTool(ToolDiscovery(reg, emb))
    schema = tool.input_schema
    assert "query" in schema["properties"]
    assert "query" in schema["required"]


def test_tool_search_tool_input_schema_has_top_n():
    reg = ToolRegistry()
    emb = FakeEmbeddingAdapter()
    tool = ToolSearchTool(ToolDiscovery(reg, emb))
    schema = tool.input_schema
    assert "top_n" in schema["properties"]


def test_tool_search_tool_description_is_non_empty():
    reg = ToolRegistry()
    emb = FakeEmbeddingAdapter()
    tool = ToolSearchTool(ToolDiscovery(reg, emb))
    assert len(tool.description) > 20


def test_tool_search_tool_parallelisable_defaults_true():
    reg = ToolRegistry()
    emb = FakeEmbeddingAdapter()
    tool = ToolSearchTool(ToolDiscovery(reg, emb))
    assert tool.parallelisable is True


# ---------------------------------------------------------------------------
# ToolSearchTool.execute() tests
# ---------------------------------------------------------------------------


async def test_execute_empty_query_returns_error():
    reg = ToolRegistry()
    emb = FakeEmbeddingAdapter()
    tool = ToolSearchTool(ToolDiscovery(reg, emb))

    result = await tool.execute({"query": ""})
    assert result.is_error
    assert "empty" in result.content.lower()


async def test_execute_whitespace_only_query_returns_error():
    reg = ToolRegistry()
    emb = FakeEmbeddingAdapter()
    tool = ToolSearchTool(ToolDiscovery(reg, emb))

    result = await tool.execute({"query": "   "})
    assert result.is_error


async def test_execute_missing_query_key_returns_error():
    reg = ToolRegistry()
    emb = FakeEmbeddingAdapter()
    tool = ToolSearchTool(ToolDiscovery(reg, emb))

    result = await tool.execute({})
    assert result.is_error


async def test_execute_with_empty_index_returns_empty_message():
    reg = ToolRegistry()
    emb = FakeEmbeddingAdapter()
    discovery = ToolDiscovery(reg, emb)
    tool = ToolSearchTool(discovery)

    result = await tool.execute({"query": "find files"})
    assert not result.is_error
    assert "No tools found" in result.content


async def test_execute_returns_match_with_tool_name():
    reg = ToolRegistry()
    reg.register(SimpleTool("grep_tool", "Search file contents by regex pattern"))
    emb = FakeEmbeddingAdapter()
    discovery = ToolDiscovery(reg, emb)
    await discovery.index()
    tool = ToolSearchTool(discovery)

    result = await tool.execute({"query": "search files"})
    assert not result.is_error
    assert "grep_tool" in result.content


async def test_execute_includes_permission_in_output():
    reg = ToolRegistry()
    reg.register(SimpleTool("my_tool", "Does stuff"))
    emb = FakeEmbeddingAdapter()
    discovery = ToolDiscovery(reg, emb)
    await discovery.index()
    tool = ToolSearchTool(discovery)

    result = await tool.execute({"query": "stuff"})
    assert "tool:my_tool" in result.content


async def test_execute_includes_relevance_score():
    reg = ToolRegistry()
    reg.register(SimpleTool("foo", "foo description"))
    emb = FakeEmbeddingAdapter()
    discovery = ToolDiscovery(reg, emb)
    await discovery.index()
    tool = ToolSearchTool(discovery)

    result = await tool.execute({"query": "foo"})
    assert "relevance" in result.content


async def test_execute_top_n_parameter_limits_results():
    reg = ToolRegistry()
    for i in range(10):
        reg.register(SimpleTool(f"t{i}", f"Tool {i} does something"))
    emb = FakeEmbeddingAdapter()
    discovery = ToolDiscovery(reg, emb)
    await discovery.index()
    tool = ToolSearchTool(discovery)

    result = await tool.execute({"query": "do something", "top_n": 2})
    assert not result.is_error
    # Two tools → two "###" headings in the formatted output.
    assert result.content.count("###") == 2


async def test_execute_top_n_capped_at_max():
    reg = ToolRegistry()
    for i in range(5):
        reg.register(SimpleTool(f"t{i}", f"Tool {i}"))
    emb = FakeEmbeddingAdapter()
    discovery = ToolDiscovery(reg, emb)
    await discovery.index()
    tool = ToolSearchTool(discovery)

    # Passing top_n=999 should be capped at _MAX_TOP_N=20 → all 5 returned.
    result = await tool.execute({"query": "tool", "top_n": 999})
    assert not result.is_error
    assert result.content.count("###") == 5


async def test_execute_search_exception_returns_error_result():
    """When the embedding backend raises, execute() returns an error result."""

    class BrokenEmbeddingAdapter(EmbeddingPort):
        async def embed(self, text: str) -> list[float]:
            raise RuntimeError("embedding service unavailable")

        async def embed_batch(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("embedding service unavailable")

        @property
        def dimension(self) -> int:
            return 4

    reg = ToolRegistry()
    reg.register(SimpleTool("some_tool", "some description"))

    # Build a broken discovery whose embed() will raise on search().
    broken_emb = FakeEmbeddingAdapter()
    discovery = ToolDiscovery(reg, broken_emb)
    await discovery.index()

    # Replace the embedding adapter with a broken one after indexing.
    discovery._embedding = BrokenEmbeddingAdapter()

    tool = ToolSearchTool(discovery)
    result = await tool.execute({"query": "anything"})

    assert result.is_error
    assert "Tool search failed" in result.content


async def test_execute_default_top_n_configurable():
    reg = ToolRegistry()
    for i in range(10):
        reg.register(SimpleTool(f"t{i}", f"Tool {i}"))
    emb = FakeEmbeddingAdapter()
    discovery = ToolDiscovery(reg, emb)
    await discovery.index()

    tool = ToolSearchTool(discovery, default_top_n=3)
    result = await tool.execute({"query": "tool"})
    assert result.content.count("###") == 3


# ---------------------------------------------------------------------------
# Integration: ToolSearchTool registered in ToolRegistry
# ---------------------------------------------------------------------------


async def test_tool_search_can_be_registered_and_dispatched():
    """ToolSearchTool can be added to the registry and invoked via dispatch."""
    reg = ToolRegistry()
    reg.register(SimpleTool("file_read", "Read a file from disk"))
    reg.register(SimpleTool("bash_run", "Execute a shell command"))

    emb = FakeEmbeddingAdapter()
    discovery = ToolDiscovery(reg, emb)
    await discovery.index()

    search_tool = ToolSearchTool(discovery)
    reg.register(search_tool)

    result = await reg.dispatch("tool_search", {"query": "read files"}, call_id="test-1")
    assert not result.is_error
    assert result.tool_call_id == "test-1"
    assert "file_read" in result.content or "bash_run" in result.content
