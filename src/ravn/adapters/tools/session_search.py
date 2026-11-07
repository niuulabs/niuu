"""session_search tool — lets the LLM search its own episodic memory.

Two-stage search:
1. FTS5 keyword search across all stored episodes.
2. Group results by session and format per-session summaries.
"""

from __future__ import annotations

from niuu.domain.tool import ToolResult
from niuu.ports.tool import ToolPort
from ravn.ports.memory import MemoryPort


class SessionSearchTool(ToolPort):
    """Search episodic memory for sessions matching a keyword query."""

    def __init__(self, memory: MemoryPort, *, limit: int = 3) -> None:
        self._memory = memory
        self._limit = limit

    @property
    def name(self) -> str:
        return "session_search"

    @property
    def description(self) -> str:
        return (
            "Search your episodic memory for past sessions and tasks matching a query. "
            "Returns per-session summaries with outcome, tags, and episode details. "
            "Use this when you need to recall what you did in a previous session, "
            "look up how you solved a similar problem, or check past outcomes."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords to search for across past session summaries.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of sessions to return (default 3, max 10).",
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["query"],
        }

    @property
    def required_permission(self) -> str:
        return "memory:read"

    async def execute(self, input: dict) -> ToolResult:
        query = input.get("query", "").strip()
        if not query:
            return ToolResult(
                tool_call_id="",
                content="Error: query must not be empty.",
                is_error=True,
            )

        limit = min(int(input.get("limit", self._limit)), 10)
        summaries = await self._memory.search_sessions(query, limit=limit)

        if not summaries:
            return ToolResult(
                tool_call_id="",
                content=f"No sessions found matching: {query!r}",
            )

        parts: list[str] = [f"Found {len(summaries)} session(s) matching {query!r}:\n"]
        for i, s in enumerate(summaries, 1):
            last = s.last_active.strftime("%Y-%m-%d %H:%M UTC")
            tags_str = ", ".join(s.tags) if s.tags else "none"
            parts.append(
                f"### Session {i} (ID: {s.session_id[:8]}…)\n"
                f"Last active: {last} | Episodes: {s.episode_count} | Tags: {tags_str}\n\n"
                f"{s.summary}"
            )

        return ToolResult(tool_call_id="", content="\n\n".join(parts))
