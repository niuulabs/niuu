"""Tool discovery via semantic search — tool_search tool.

Stores tool description embeddings at index time and performs cosine
similarity search to surface relevant tools for a given natural-language
query.  This allows the agent to discover tools it was not explicitly told
about by searching semantically over the tool registry.

Usage::

    discovery = ToolDiscovery(registry, embedding_adapter)
    await discovery.index()           # called once after registry is populated
    tool = ToolSearchTool(discovery)
    registry.register(tool)

The index is rebuilt by calling ``index()`` again whenever the registry
changes (e.g. after dynamic tool registration).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from niuu.domain.tool import ToolResult
from niuu.ports.embedding import EmbeddingPort
from niuu.ports.tool import ToolPort
from ravn.adapters.memory.scoring import cosine_similarity
from ravn.registry import ToolRegistry

logger = logging.getLogger(__name__)

_TOOL_SEARCH_PERMISSION = "introspect:read"
_DEFAULT_TOP_N = 5
_MAX_TOP_N = 20


# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _IndexedTool:
    """An indexed tool entry with a pre-computed description embedding."""

    name: str
    description: str
    required_permission: str
    embedding: list[float]


# ---------------------------------------------------------------------------
# ToolDiscovery — index and search backend
# ---------------------------------------------------------------------------


class ToolDiscovery:
    """Index tool descriptions and serve cosine-similarity search queries.

    ``index()`` must be called (and awaited) after the registry is fully
    populated.  Until then ``search()`` returns an empty list.

    Args:
        registry: The tool registry whose tools will be indexed.
        embedding: The embedding adapter used to vectorise descriptions.
    """

    def __init__(self, registry: ToolRegistry, embedding: EmbeddingPort) -> None:
        self._registry = registry
        self._embedding = embedding
        self._index: list[_IndexedTool] = []

    async def index(self) -> None:
        """Embed all registered tool descriptions and build the search index.

        Calls ``embed_batch`` once with all descriptions for efficiency.
        Replaces any previously built index.
        """
        tools = self._registry.list()
        if not tools:
            self._index = []
            return

        texts = [t.description for t in tools]
        embeddings = await self._embedding.embed_batch(texts)

        self._index = [
            _IndexedTool(
                name=t.name,
                description=t.description,
                required_permission=t.required_permission,
                embedding=emb,
            )
            for t, emb in zip(tools, embeddings)
        ]
        logger.debug("tool_discovery: indexed %d tools", len(self._index))

    async def search(
        self, query: str, top_n: int = _DEFAULT_TOP_N
    ) -> list[tuple[_IndexedTool, float]]:
        """Return the top *top_n* tools most relevant to *query*.

        Embeds *query* and ranks indexed tools by cosine similarity.
        Results are returned sorted by descending relevance score.

        Returns an empty list when the index is empty (``index()`` has not
        been called or the registry had no tools).
        """
        if not self._index:
            return []

        query_vec = await self._embedding.embed(query)
        scored = [(entry, cosine_similarity(query_vec, entry.embedding)) for entry in self._index]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_n]


# ---------------------------------------------------------------------------
# ToolSearchTool — ToolPort implementation
# ---------------------------------------------------------------------------


class ToolSearchTool(ToolPort):
    """Semantic search over the tool registry.

    Returns the top N tools most relevant to a natural-language query,
    ranked by cosine similarity over pre-computed description embeddings.
    The index is populated at registry load time via
    ``ToolDiscovery.index()``.

    Example::

        tool_search("find files by content pattern")
        # → surfaces grep / file search tools

        tool_search("run shell commands")
        # → surfaces bash / terminal tools

    Args:
        discovery: The ``ToolDiscovery`` instance that holds the index.
        default_top_n: Number of results returned when the caller omits
            ``top_n`` (default 5).
    """

    def __init__(
        self,
        discovery: ToolDiscovery,
        *,
        default_top_n: int = _DEFAULT_TOP_N,
    ) -> None:
        self._discovery = discovery
        self._default_top_n = default_top_n

    @property
    def name(self) -> str:
        return "tool_search"

    @property
    def description(self) -> str:
        return (
            "Semantic search over the tool registry. "
            "Returns the top N tools most relevant to a natural-language query, "
            "ranked by cosine similarity, with their description and permission "
            "requirements. "
            "Use this to discover tools you are not yet aware of — "
            "for example: tool_search('find files by content pattern') "
            "surfaces file search tools; tool_search('run shell commands') "
            "surfaces terminal/bash tools."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Natural-language description of what you want to do "
                        "(e.g. 'run shell commands', 'read a file', 'search the web')."
                    ),
                },
                "top_n": {
                    "type": "integer",
                    "description": (
                        f"Number of tools to return (default {_DEFAULT_TOP_N}, max {_MAX_TOP_N})."
                    ),
                    "minimum": 1,
                    "maximum": _MAX_TOP_N,
                },
            },
            "required": ["query"],
        }

    @property
    def required_permission(self) -> str:
        return _TOOL_SEARCH_PERMISSION

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        query = input.get("query", "").strip()
        if not query:
            return ToolResult(
                tool_call_id="",
                content="Error: query must not be empty.",
                is_error=True,
            )

        top_n = min(int(input.get("top_n", self._default_top_n)), _MAX_TOP_N)

        try:
            matches = await self._discovery.search(query, top_n=top_n)
        except Exception as exc:
            logger.warning("tool_search: search failed: %s", exc)
            return ToolResult(
                tool_call_id="",
                content=f"Tool search failed: {exc}",
                is_error=True,
            )

        if not matches:
            return ToolResult(
                tool_call_id="",
                content=f"No tools found matching: {query!r}. The tool index may be empty.",
            )

        parts: list[str] = [f"Found {len(matches)} tool(s) matching {query!r}:\n"]
        for i, (entry, score) in enumerate(matches, 1):
            parts.append(
                f"### {i}. `{entry.name}` (relevance: {score:.3f})\n"
                f"**Permission**: `{entry.required_permission}`\n\n"
                f"{entry.description}"
            )

        return ToolResult(tool_call_id="", content="\n\n".join(parts))
