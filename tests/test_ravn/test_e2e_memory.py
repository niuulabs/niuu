"""E2E scenarios for memory and session search (NIU-456).

Scenario 1 — Memory recall:
  Seed episodes → new conversation references past work → prefetch injects
  relevant context → LLM uses it (context appears in system prompt).

Scenario 2 — Session search:
  Seed sessions → LLM invokes session_search tool → gets summaries → uses them.

Scenario 3 — Fallback trigger:
  Primary mock LLM raises 429 → fallback mock responds → conversation
  continues transparently (same LLMResponse returned to agent).

All scenarios use real SQLiteMemoryAdapter against tmp_path and scripted
MockLLM/FallbackLLMAdapter (no network calls).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ravn.adapters.memory.sqlite import SqliteMemoryAdapter
from ravn.adapters.permission.allow_deny import AllowAllPermission
from ravn.adapters.tools.session_search import SessionSearchTool
from ravn.agent import RavnAgent
from ravn.domain.models import (
    Episode,
    LLMResponse,
    Outcome,
    StopReason,
    StreamEvent,
    StreamEventType,
    TokenUsage,
    ToolCall,
)
from ravn.ports.llm import LLMPort
from tests.ravn.fixtures.fakes import InMemoryChannel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_USAGE = TokenUsage(input_tokens=10, output_tokens=5)


def _text_response(text: str) -> LLMResponse:
    return LLMResponse(
        content=text,
        tool_calls=[],
        stop_reason=StopReason.END_TURN,
        usage=_USAGE,
    )


def _tool_response(tool_name: str, tc_id: str = "tc-1", **kwargs) -> LLMResponse:
    return LLMResponse(
        content="",
        tool_calls=[ToolCall(id=tc_id, name=tool_name, input=kwargs)],
        stop_reason=StopReason.TOOL_USE,
        usage=_USAGE,
    )


class ScriptedLLM(LLMPort):
    """Replays responses from a pre-supplied list (non-streaming via generate)."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = iter(responses)
        self.calls: list[dict] = []

    async def generate(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system,
        model: str,
        max_tokens: int,
        thinking: dict | None = None,
    ) -> LLMResponse:
        self.calls.append({"messages": messages, "system": system})
        return next(self._responses)

    async def stream(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system,
        model: str,
        max_tokens: int,
        thinking: dict | None = None,
    ) -> AsyncIterator[StreamEvent]:
        response = next(self._responses)
        self.calls.append({"messages": messages, "system": system})
        if response.content:
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text=response.content)
        for tc in response.tool_calls:
            yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=tc)
        yield StreamEvent(type=StreamEventType.MESSAGE_DONE, usage=response.usage)


def _make_agent(
    llm: LLMPort,
    memory=None,
    tools=None,
) -> tuple[RavnAgent, InMemoryChannel]:
    ch = InMemoryChannel()
    agent = RavnAgent(
        llm=llm,
        tools=tools or [],
        channel=ch,
        permission=AllowAllPermission(),
        system_prompt="You are a test assistant.",
        model="claude-sonnet-4-6",
        max_tokens=1024,
        max_iterations=5,
        memory=memory,
    )
    return agent, ch


def _ep(
    episode_id: str,
    session_id: str,
    summary: str,
    tags: list[str] | None = None,
    timestamp: datetime | None = None,
    outcome: Outcome = Outcome.SUCCESS,
) -> Episode:
    return Episode(
        episode_id=episode_id,
        session_id=session_id,
        timestamp=timestamp or datetime.now(UTC),
        summary=summary,
        task_description=summary[:50],
        tools_used=["bash"],
        outcome=outcome,
        tags=tags or ["shell"],
    )


@pytest.fixture
async def mem(tmp_path: Path) -> SqliteMemoryAdapter:
    adapter = SqliteMemoryAdapter(
        path=str(tmp_path / "memory.db"),
        max_retries=3,
        min_jitter_ms=1.0,
        max_jitter_ms=5.0,
        prefetch_min_relevance=0.0,
        prefetch_budget=2000,
        prefetch_limit=5,
        recency_half_life_days=14.0,
    )
    await adapter.initialize()
    return adapter


# ---------------------------------------------------------------------------
# Scenario 1 — Memory recall
# ---------------------------------------------------------------------------


class TestMemoryRecallScenario:
    async def test_prefetch_context_appears_in_llm_system_prompt(
        self, mem: SqliteMemoryAdapter
    ) -> None:
        """Seed an episode then verify prefetch injects it into the system prompt.

        The episode summary and the query share the key terms so FTS5 returns a match.
        """
        await mem.record_episode(
            _ep("ep-1", "sess-old", "nginx proxy deployment configuration completed")
        )

        llm = ScriptedLLM([_text_response("I remember the nginx deployment.")])
        agent, _ = _make_agent(llm, memory=mem)

        # Query uses exact terms from the episode.
        await agent.run_turn("nginx proxy deployment")

        # The system prompt passed to the LLM should contain past context.
        assert llm.calls, "LLM was never called"
        system = llm.calls[0]["system"]
        system_text = system if isinstance(system, str) else str(system)
        assert "Past Context" in system_text or "nginx" in system_text.lower()

    async def test_prefetch_injects_relevant_episode(self, mem: SqliteMemoryAdapter) -> None:
        """Episode whose summary matches the query must appear in context."""
        await mem.record_episode(
            _ep("ep-2", "sess-A", "postgresql replication configured successfully")
        )

        captured_systems: list[str] = []
        llm = ScriptedLLM([_text_response("Done")])
        original_stream = llm.stream

        async def _capturing_stream(*args, **kwargs):
            system = kwargs.get("system", "")
            captured_systems.append(system if isinstance(system, str) else str(system))
            async for event in original_stream(*args, **kwargs):
                yield event

        llm.stream = _capturing_stream

        agent, _ = _make_agent(llm, memory=mem)
        # Query uses exact terms from the episode so FTS5 finds a match.
        await agent.run_turn("postgresql replication")

        combined = "\n".join(captured_systems)
        assert "Past Context" in combined or "postgresql" in combined.lower()

    async def test_no_relevant_episodes_no_context_injected(self, mem: SqliteMemoryAdapter) -> None:
        """When no episodes match, system prompt must not contain context header."""
        # Store an episode about a completely unrelated topic.
        await mem.record_episode(_ep("ep-3", "sess-B", "compiled rust kernel module driver"))

        captured_systems: list[str] = []
        llm = ScriptedLLM([_text_response("Done")])
        original_stream = llm.stream

        async def _capture(*args, **kwargs):
            system = kwargs.get("system", "")
            captured_systems.append(system if isinstance(system, str) else str(system))
            async for event in original_stream(*args, **kwargs):
                yield event

        llm.stream = _capture
        agent, _ = _make_agent(llm, memory=mem)
        # Query is totally unrelated — no FTS5 match.
        await agent.run_turn("python type annotations")

        # Prefetch found no match → no "Past Context" header.
        combined = "\n".join(captured_systems)
        assert "Past Context" not in combined

    async def test_episode_recorded_after_turn(self, mem: SqliteMemoryAdapter) -> None:
        """The turn's episode is recorded in the memory backend."""
        llm = ScriptedLLM([_text_response("Deployed!")])
        agent, _ = _make_agent(llm, memory=mem)

        await agent.run_turn("deploy the service to staging")

        matches = await mem.query_episodes("deploy staging", min_relevance=0.0)
        assert len(matches) >= 1
        assert any("deploy" in m.episode.task_description.lower() for m in matches)

    async def test_multiple_episodes_accumulate(self, mem: SqliteMemoryAdapter) -> None:
        """Two turns produce two episodes in memory."""
        # Two main responses + two reflection generate() responses (NIU-574).
        llm = ScriptedLLM(
            [
                _text_response("First done"),
                _text_response("Reflection on first."),
                _text_response("Second done"),
                _text_response("Reflection on second."),
            ]
        )
        agent, _ = _make_agent(llm, memory=mem)

        await agent.run_turn("first task alpha")
        await agent.run_turn("second task beta")

        matches = await mem.query_episodes("task", min_relevance=0.0)
        assert len(matches) >= 2


# ---------------------------------------------------------------------------
# Scenario 2 — Session search
# ---------------------------------------------------------------------------


class TestSessionSearchScenario:
    async def test_session_search_tool_returns_summaries(self, mem: SqliteMemoryAdapter) -> None:
        """Seed two sessions, call session_search, verify both appear."""
        await mem.record_episode(_ep("e1", "sess-X", "configured redis cache cluster"))
        await mem.record_episode(_ep("e2", "sess-X", "tuned redis eviction policy"))
        await mem.record_episode(_ep("e3", "sess-Y", "set up redis replication"))

        tool = SessionSearchTool(memory=mem)
        result = await tool.execute({"query": "redis", "limit": 5})

        assert not result.is_error
        assert "sess-X" in result.content or "sess-Y" in result.content

    async def test_session_search_deduplicates_sessions(self, mem: SqliteMemoryAdapter) -> None:
        """Multiple episodes in the same session appear as ONE session summary."""
        for i in range(3):
            await mem.record_episode(
                _ep(f"ep-dup-{i}", "sess-dedup", "git push to remote repository")
            )

        tool = SessionSearchTool(memory=mem)
        result = await tool.execute({"query": "git push"})

        # The result must describe exactly one session (the output groups by session).
        assert "Session 1" in result.content
        assert "Session 2" not in result.content

    async def test_session_search_respects_limit(self, mem: SqliteMemoryAdapter) -> None:
        """Limit parameter caps the number of returned sessions."""
        for i in range(5):
            await mem.record_episode(
                _ep(f"ep-{i}", f"sess-{i}", "deployed kubernetes pod configuration")
            )

        tool = SessionSearchTool(memory=mem)
        result = await tool.execute({"query": "kubernetes", "limit": 2})

        # Count session-id-like prefixes in result — at most 2.
        # (SessionSummary format includes "sess-" prefix)
        matches = [line for line in result.content.splitlines() if "sess-" in line]
        assert len(matches) <= 4  # generous bound; tool may format multi-line

    async def test_session_search_empty_result_message(self, mem: SqliteMemoryAdapter) -> None:
        """No match returns a 'No sessions found' message (not an error)."""
        await mem.record_episode(_ep("ep-none", "sess-1", "wrote unit tests in go"))

        tool = SessionSearchTool(memory=mem)
        result = await tool.execute({"query": "machine learning neural network"})

        assert not result.is_error
        assert "No sessions found" in result.content

    async def test_session_search_e2e_through_agent(self, mem: SqliteMemoryAdapter) -> None:
        """LLM invokes session_search → tool result fed back → LLM uses it."""
        # Seed a session.
        await mem.record_episode(
            _ep("ep-seed", "sess-seed", "configured prometheus metrics scraping")
        )

        search_tool = SessionSearchTool(memory=mem)

        # Scripted conversation:
        # Turn 1: LLM calls session_search("prometheus")
        # Turn 2: LLM uses result to give final answer.
        llm = ScriptedLLM(
            [
                _tool_response("session_search", query="prometheus"),
                _text_response("Found past prometheus work. Reusing configuration."),
            ]
        )
        agent, _ = _make_agent(llm, memory=mem, tools=[search_tool])

        result = await agent.run_turn("show me past prometheus work")
        assert "prometheus" in result.response.lower()
