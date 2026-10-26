"""Introspection tools — let Ravn inspect its own runtime state.

Three read-only tools that expose internal agent state to the agent itself:

* ravn_state        — snapshot of budget, tools, permission mode, model, persona
* ravn_memory_search — semantic search over the agent's own episodic memory
* ravn_reflect      — mid-task reflection pass on progress so far

Permission model
----------------
All three tools use ``introspect:read`` as their required permission.  In every
standard mode (read_only, workspace_write, full_access) the permission enforcer
allows this permission because the string contains no write/delete/execute
markers.  These tools are read-only and touch only the agent's own internal
state — never external systems.
"""

from __future__ import annotations

import logging

from ravn.budget import IterationBudget
from ravn.cap_telemetry import require_complete
from ravn.domain.models import Session, ToolResult
from ravn.ports.llm import LLMPort
from ravn.ports.memory import MemoryPort
from ravn.ports.tool import ToolPort

logger = logging.getLogger(__name__)

_INTROSPECT_PERMISSION = "introspect:read"

_REFLECT_TASK_EXCERPT_MAX = 300
_REFLECT_HEADER_DESC_MAX = 100

_REFLECT_SYSTEM = (
    "You are Ravn performing a mid-task self-assessment. "
    "Review the information provided and identify: what has been completed, "
    "what remains, whether the current approach is working, and any course "
    "corrections that would improve the outcome. Be concise and specific. No preamble."
)

_REFLECT_PROMPT_TEMPLATE = (
    "Mid-task reflection requested.\n\n"
    "Task description: {task_description}\n\n"
    "Conversation turns so far: {turn_count}\n"
    "Tools used so far: {tools_used}\n\n"
    "Provide a brief mid-task reflection:\n"
    "1. What has been accomplished?\n"
    "2. What still needs to be done?\n"
    "3. Is the current approach working? Any signs of getting stuck?\n"
    "4. What is the recommended next action?\n"
)


# ---------------------------------------------------------------------------
# ravn_state
# ---------------------------------------------------------------------------


class RavnStateTool(ToolPort):
    """Return a snapshot of the agent's current runtime state.

    Exposes iteration budget, active tool list, permission mode, memory
    status, active persona, and the current model alias.  All values are
    captured at construction time except the iteration budget counters, which
    are read live on each ``execute()`` call so the agent sees up-to-date
    figures.
    """

    def __init__(
        self,
        *,
        tool_names: list[str],
        permission_mode: str,
        model: str,
        persona: str = "",
        iteration_budget: IterationBudget | None = None,
        memory: MemoryPort | None = None,
        discovery: object | None = None,
    ) -> None:
        self._tool_names = list(tool_names)
        self._permission_mode = permission_mode
        self._model = model
        self._persona = persona
        self._iteration_budget = iteration_budget
        self._memory = memory
        self._discovery = discovery

    @property
    def name(self) -> str:
        return "ravn_state"

    @property
    def description(self) -> str:
        return (
            "Return a snapshot of your own runtime state: iteration budget "
            "(used / remaining), active tools, permission mode, memory status, "
            "active persona, and current model. "
            "Use this to reason about your own constraints before attempting "
            "long or risky operations."
        )

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def required_permission(self) -> str:
        return _INTROSPECT_PERMISSION

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        lines: list[str] = ["## Ravn Runtime State"]

        # Iteration budget
        if self._iteration_budget is not None:
            b = self._iteration_budget
            lines.append(
                f"\n**Iteration budget**: {b.consumed} used / {b.remaining} remaining"
                f" (total: {b.total})"
            )
            if b.task_ceiling is not None:
                lines.append(f"  Task ceiling: {b.task_ceiling}")
            lines.append(f"  Near limit: {b.near_limit} | Exhausted: {b.exhausted}")
        else:
            lines.append("\n**Iteration budget**: not configured")

        # Active tools
        sorted_names = sorted(self._tool_names)
        tool_list = ", ".join(sorted_names) if sorted_names else "(none)"
        lines.append(f"\n**Active tools** ({len(self._tool_names)}): {tool_list}")

        # Permission mode
        lines.append(f"\n**Permission mode**: {self._permission_mode}")

        # Memory status
        if self._memory is not None:
            lines.append("\n**Memory**: episodic memory active")
        else:
            lines.append("\n**Memory**: not configured")

        # Persona
        lines.append(f"\n**Active persona**: {self._persona or '(default)'}")

        # Model
        lines.append(f"\n**Model**: {self._model}")

        # Flock peers
        if self._discovery is not None:
            try:
                peers = self._discovery.peers()  # dict[str, RavnPeer]
                if peers:
                    lines.append(f"\n**Flock peers** ({len(peers)}):")
                    for p in peers.values():
                        caps = ", ".join(p.capabilities) if p.capabilities else "unknown"
                        lines.append(
                            f"  - {p.peer_id}  persona={p.persona}  "
                            f"status={p.status}  caps=[{caps}]"
                        )
                else:
                    lines.append("\n**Flock peers**: none discovered yet")
            except Exception as exc:
                lines.append(f"\n**Flock peers**: unavailable ({exc})")

        return ToolResult(tool_call_id="", content="\n".join(lines))


# ---------------------------------------------------------------------------
# ravn_memory_search
# ---------------------------------------------------------------------------


_MEMORY_SEARCH_DEFAULT_LIMIT = 5
_MEMORY_SEARCH_MAX_LIMIT = 20


class RavnMemorySearchTool(ToolPort):
    """Semantic search over the agent's own episodic memory.

    Calls ``MemoryPort.query_episodes`` which performs hybrid retrieval
    (keyword + embedding similarity) weighted by recency and outcome.
    Requires the semantic memory adapter (NIU-436).
    """

    def __init__(
        self,
        memory: MemoryPort,
        *,
        default_limit: int = _MEMORY_SEARCH_DEFAULT_LIMIT,
    ) -> None:
        self._memory = memory
        self._default_limit = default_limit

    @property
    def name(self) -> str:
        return "ravn_memory_search"

    @property
    def description(self) -> str:
        return (
            "Semantic search over your own episodic memory. "
            "Returns the top N most relevant past episodes — with outcome, tools used, "
            "and a summary — for a given natural-language query. "
            "Use this before attempting a risky or unfamiliar task to check if you have "
            "prior experience and whether that experience was successful."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Natural-language description of what you want to recall "
                        "(e.g. 'deploy to kubernetes', 'fix async test failure')."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        f"Number of episodes to return "
                        f"(default {_MEMORY_SEARCH_DEFAULT_LIMIT}, "
                        f"max {_MEMORY_SEARCH_MAX_LIMIT})."
                    ),
                    "minimum": 1,
                    "maximum": _MEMORY_SEARCH_MAX_LIMIT,
                },
            },
            "required": ["query"],
        }

    @property
    def required_permission(self) -> str:
        return _INTROSPECT_PERMISSION

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        query = input.get("query", "").strip()
        if not query:
            return ToolResult(
                tool_call_id="",
                content="Error: query must not be empty.",
                is_error=True,
            )

        limit = min(int(input.get("limit", self._default_limit)), _MEMORY_SEARCH_MAX_LIMIT)

        try:
            matches = await self._memory.query_episodes(query, limit=limit)
        except Exception as exc:
            logger.warning("ravn_memory_search: query_episodes failed: %s", exc)
            return ToolResult(
                tool_call_id="",
                content=f"Memory search failed: {exc}",
                is_error=True,
            )

        if not matches:
            return ToolResult(
                tool_call_id="",
                content=f"No episodes found matching: {query!r}",
            )

        parts: list[str] = [f"Found {len(matches)} episode(s) matching {query!r}:\n"]
        for i, match in enumerate(matches, 1):
            ep = match.episode
            ts = ep.timestamp.strftime("%Y-%m-%d %H:%M UTC")
            tools_str = ", ".join(ep.tools_used) if ep.tools_used else "none"
            tags_str = ", ".join(ep.tags) if ep.tags else "none"
            parts.append(
                f"### Episode {i} (relevance: {match.relevance:.2f})\n"
                f"**When**: {ts} | **Outcome**: {ep.outcome}"
                f" | **Session**: {ep.session_id[:8]}…\n"
                f"**Tools**: {tools_str} | **Tags**: {tags_str}\n\n"
                f"{ep.summary}"
            )

        return ToolResult(tool_call_id="", content="\n\n".join(parts))


# ---------------------------------------------------------------------------
# ravn_reflect
# ---------------------------------------------------------------------------


class RavnReflectTool(ToolPort):
    """Trigger a mid-task reflection pass on progress so far.

    Sends a compact prompt to the LLM (using the supplied model alias) asking
    it to assess what has been done, what remains, and whether the current
    approach is working.  The session reference is read at execution time so
    tool-use history from the current conversation is always up to date.
    """

    def __init__(
        self,
        llm: LLMPort,
        session: Session,
        *,
        model: str,
        # None sends no cap: a reasoning model thinks before the assessment,
        # and the old 512 ended inside that thinking.
        max_tokens: int | None = None,
    ) -> None:
        self._llm = llm
        self._session = session
        self._model = model
        self._max_tokens = max_tokens

    @property
    def name(self) -> str:
        return "ravn_reflect"

    @property
    def description(self) -> str:
        return (
            "Trigger a mid-task reflection on progress so far. "
            "Summarises what has been accomplished, what remains, and whether the "
            "current approach is working — then returns a structured self-assessment. "
            "Use this when stuck, before a major irreversible action, or when "
            "approaching the iteration limit."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_description": {
                    "type": "string",
                    "description": "Brief description of the current task or goal (≤300 chars).",
                },
            },
            "required": ["task_description"],
        }

    @property
    def required_permission(self) -> str:
        return _INTROSPECT_PERMISSION

    @property
    def parallelisable(self) -> bool:
        return False

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        task_description = input.get("task_description", "").strip()
        if not task_description:
            return ToolResult(
                tool_call_id="",
                content="Error: task_description must not be empty.",
                is_error=True,
            )

        # Collect tool names used so far from session history.
        tools_mentioned: set[str] = set()
        for msg in self._session.messages:
            if not isinstance(msg.content, list):
                continue
            for block in msg.content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tools_mentioned.add(block.get("name", "unknown"))

        tools_str = ", ".join(sorted(tools_mentioned)) if tools_mentioned else "none yet"
        turn_count = self._session.turn_count

        task_excerpt = task_description[:_REFLECT_TASK_EXCERPT_MAX]
        prompt = _REFLECT_PROMPT_TEMPLATE.format(
            task_description=task_excerpt,
            turn_count=turn_count,
            tools_used=tools_str,
        )

        try:
            response = await self._llm.generate(
                [{"role": "user", "content": prompt}],
                tools=[],
                system=_REFLECT_SYSTEM,
                model=self._model,
                max_tokens=self._max_tokens,
            )
            require_complete(
                response,
                knob="ravn_reflect",
                budget=self._max_tokens,
                model=self._model,
                purpose="the mid-task reflection",
            )
        except Exception as exc:
            # Returned to the model as a failed tool call, not swallowed.
            logger.error("ravn_reflect: reflection failed: %s", exc)
            return ToolResult(
                tool_call_id="",
                content=f"Reflection failed: {exc}",
                is_error=True,
            )

        short_desc = task_description[:_REFLECT_HEADER_DESC_MAX]
        if len(task_description) > _REFLECT_HEADER_DESC_MAX:
            short_desc += "…"

        header = (
            f"## Mid-Task Reflection\n"
            f"**Task**: {short_desc}\n"
            f"**Turn**: {turn_count} | **Tools used**: {tools_str}\n\n"
        )
        return ToolResult(tool_call_id="", content=header + response.content)
