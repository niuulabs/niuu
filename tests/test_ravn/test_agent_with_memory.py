"""Tests for RavnAgent memory integration (prefetch + episode recording)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from ravn.adapters.permission.allow_deny import AllowAllPermission
from ravn.agent import (
    RavnAgent,
    _determine_outcome,
    _extract_episode,
    _infer_tags,
)
from ravn.domain.models import (
    Episode,
    EpisodeMatch,
    Outcome,
    SessionSummary,
    SharedContext,
    StreamEvent,
    StreamEventType,
    TokenUsage,
    ToolCall,
    ToolResult,
    TurnResult,
)
from ravn.ports.llm import LLMPort
from ravn.ports.memory import MemoryPort
from tests.ravn.fixtures.fakes import InMemoryChannel

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class RecordingMemory(MemoryPort):
    """Memory stub that records all calls for assertion."""

    def __init__(self, prefetch_result: str = "") -> None:
        self.recorded_episodes: list[Episode] = []
        self.prefetch_calls: list[str] = []
        self._prefetch_result = prefetch_result
        self._shared: SharedContext | None = None

    async def record_episode(self, episode: Episode) -> None:
        self.recorded_episodes.append(episode)

    async def query_episodes(
        self, query: str, *, limit: int = 5, min_relevance: float = 0.3
    ) -> list[EpisodeMatch]:
        return []

    async def prefetch(self, context: str) -> str:
        self.prefetch_calls.append(context)
        return self._prefetch_result

    async def search_sessions(self, query: str, *, limit: int = 3) -> list[SessionSummary]:
        return []

    def inject_shared_context(self, context: SharedContext) -> None:
        self._shared = context

    def get_shared_context(self) -> SharedContext | None:
        return self._shared


def make_simple_llm(response_text: str = "Done!") -> LLMPort:
    async def _stream(*args, **kwargs) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(type=StreamEventType.TEXT_DELTA, text=response_text)
        yield StreamEvent(
            type=StreamEventType.MESSAGE_DONE,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
        )

    llm = AsyncMock(spec=LLMPort)
    llm.stream = _stream
    return llm


def make_agent(
    llm: LLMPort,
    memory: MemoryPort | None = None,
) -> tuple[RavnAgent, InMemoryChannel]:
    ch = InMemoryChannel()
    agent = RavnAgent(
        llm=llm,
        tools=[],
        channel=ch,
        permission=AllowAllPermission(),
        system_prompt="You are a test assistant.",
        model="claude-sonnet-4-6",
        max_tokens=1024,
        max_iterations=5,
        memory=memory,
    )
    return agent, ch


# ---------------------------------------------------------------------------
# _infer_tags
# ---------------------------------------------------------------------------


class TestInferTags:
    def test_file_tool(self) -> None:
        tags = _infer_tags(["read_file", "write_file"])
        assert "file_operations" in tags

    def test_git_tool(self) -> None:
        tags = _infer_tags(["git_commit", "git_push"])
        assert "git" in tags

    def test_bash_tool(self) -> None:
        tags = _infer_tags(["bash"])
        assert "shell" in tags

    def test_web_tools(self) -> None:
        tags = _infer_tags(["web_search", "web_fetch"])
        assert "web" in tags

    def test_no_tools_gives_general(self) -> None:
        tags = _infer_tags([])
        assert tags == ["general"]

    def test_unknown_tool_gives_general(self) -> None:
        tags = _infer_tags(["custom_tool_xyz"])
        assert "general" in tags

    def test_no_duplicate_tags(self) -> None:
        tags = _infer_tags(["bash", "terminal"])
        assert tags.count("shell") == 1

    def test_session_search_tag(self) -> None:
        tags = _infer_tags(["session_search"])
        assert "memory" in tags


# ---------------------------------------------------------------------------
# _determine_outcome
# ---------------------------------------------------------------------------


class TestDetermineOutcome:
    def test_no_tools_is_success(self) -> None:
        assert _determine_outcome([]) == Outcome.SUCCESS

    def test_all_success_is_success(self) -> None:
        results = [
            ToolResult(tool_call_id="1", content="ok"),
            ToolResult(tool_call_id="2", content="ok"),
        ]
        assert _determine_outcome(results) == Outcome.SUCCESS

    def test_all_errors_is_failure(self) -> None:
        results = [
            ToolResult(tool_call_id="1", content="err", is_error=True),
            ToolResult(tool_call_id="2", content="err", is_error=True),
        ]
        assert _determine_outcome(results) == Outcome.FAILURE

    def test_mixed_is_partial(self) -> None:
        results = [
            ToolResult(tool_call_id="1", content="ok"),
            ToolResult(tool_call_id="2", content="err", is_error=True),
        ]
        assert _determine_outcome(results) == Outcome.PARTIAL


# ---------------------------------------------------------------------------
# _extract_episode
# ---------------------------------------------------------------------------


class TestExtractEpisode:
    def _make_turn_result(
        self,
        response: str = "Done!",
        tool_calls: list[ToolCall] | None = None,
        tool_results: list[ToolResult] | None = None,
    ) -> TurnResult:
        return TurnResult(
            response=response,
            tool_calls=tool_calls or [],
            tool_results=tool_results or [],
            usage=TokenUsage(input_tokens=10, output_tokens=5),
        )

    def test_episode_id_is_uuid(self) -> None:
        import re

        ep = _extract_episode("sess-1", "do something", self._make_turn_result())
        uuid_re = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        assert uuid_re.match(ep.episode_id)

    def test_session_id_preserved(self) -> None:
        ep = _extract_episode("my-session", "task", self._make_turn_result())
        assert ep.session_id == "my-session"

    def test_task_description_truncated(self) -> None:
        long_input = "a" * 300
        ep = _extract_episode("s", long_input, self._make_turn_result())
        assert len(ep.task_description) <= 203  # 200 chars + ellipsis

    def test_summary_truncated(self) -> None:
        long_response = "b" * 600
        ep = _extract_episode("s", "task", self._make_turn_result(response=long_response))
        assert len(ep.summary) <= 503  # 500 chars + ellipsis

    def test_empty_response_uses_fallback(self) -> None:
        ep = _extract_episode("s", "task", self._make_turn_result(response=""))
        assert ep.summary  # not empty

    def test_tools_used_deduplicated(self) -> None:
        calls = [
            ToolCall(id="1", name="bash", input={}),
            ToolCall(id="2", name="bash", input={}),
            ToolCall(id="3", name="read_file", input={}),
        ]
        ep = _extract_episode("s", "task", self._make_turn_result(tool_calls=calls))
        assert ep.tools_used.count("bash") == 1

    def test_outcome_determined_from_results(self) -> None:
        results = [ToolResult(tool_call_id="1", content="err", is_error=True)]
        ep = _extract_episode("s", "task", self._make_turn_result(tool_results=results))
        assert ep.outcome == Outcome.FAILURE

    def test_embedding_is_none(self) -> None:
        ep = _extract_episode("s", "task", self._make_turn_result())
        assert ep.embedding is None

    def test_timestamp_is_utc(self) -> None:
        ep = _extract_episode("s", "task", self._make_turn_result())
        assert ep.timestamp.tzinfo is not None


# ---------------------------------------------------------------------------
# Agent integration
# ---------------------------------------------------------------------------


class TestAgentMemoryIntegration:
    async def test_run_turn_records_episode(self) -> None:
        mem = RecordingMemory()
        agent, _ = make_agent(make_simple_llm(), memory=mem)
        await agent.run_turn("hello world")
        assert len(mem.recorded_episodes) == 1
        assert mem.recorded_episodes[0].session_id == str(agent.session.id)

    async def test_run_turn_calls_prefetch(self) -> None:
        mem = RecordingMemory(prefetch_result="## Past Context\n\npast stuff")
        agent, _ = make_agent(make_simple_llm(), memory=mem)
        await agent.run_turn("do something")
        assert mem.prefetch_calls == ["do something"]

    async def test_prefetch_context_passed_to_llm(self) -> None:
        mem = RecordingMemory(prefetch_result="## Past Context\n\npast data")
        llm = make_simple_llm()
        agent, _ = make_agent(llm, memory=mem)

        # Capture the system prompt used in the LLM call.
        captured_system: list[str] = []
        original_stream = llm.stream

        async def capturing_stream(*args, **kwargs):
            captured_system.append(kwargs.get("system", ""))
            async for event in original_stream(*args, **kwargs):
                yield event

        llm.stream = capturing_stream
        await agent.run_turn("help me")
        assert any("Past Context" in s for s in captured_system)

    async def test_no_memory_works_normally(self) -> None:
        agent, _ = make_agent(make_simple_llm(), memory=None)
        result = await agent.run_turn("hello")
        assert result.response == "Done!"

    async def test_memory_prefetch_failure_is_fatal(self) -> None:
        """Replaces a test that asserted the turn continued regardless.

        Running the turn anyway meant reasoning with no history at all while
        every outward signal looked normal. Configuring memory is a statement
        that it is required.
        """
        mem = RecordingMemory()
        mem.prefetch = AsyncMock(side_effect=RuntimeError("prefetch failed"))
        agent, _ = make_agent(make_simple_llm(), memory=mem)

        with pytest.raises(RuntimeError, match="memory prefetch failed"):
            await agent.run_turn("hello")

    async def test_memory_record_failure_is_fatal(self) -> None:
        """Replaces a test that asserted the turn continued regardless.

        A swallowed record_episode loses that turn permanently and the corpus
        silently stops growing — how noatun lost episodes to a broken
        embedding endpoint while looking healthy.
        """
        mem = RecordingMemory()
        mem.record_episode = AsyncMock(side_effect=RuntimeError("record failed"))
        agent, _ = make_agent(make_simple_llm(), memory=mem)

        with pytest.raises(RuntimeError, match="recording the episode"):
            await agent.run_turn("hello")

    async def test_episode_task_description_matches_user_input(self) -> None:
        mem = RecordingMemory()
        agent, _ = make_agent(make_simple_llm(), memory=mem)
        await agent.run_turn("deploy to production server")
        ep = mem.recorded_episodes[0]
        assert "deploy to production server" in ep.task_description

    async def test_multiple_turns_record_multiple_episodes(self) -> None:
        mem = RecordingMemory()
        agent, _ = make_agent(make_simple_llm(), memory=mem)
        await agent.run_turn("first task")
        await agent.run_turn("second task")
        assert len(mem.recorded_episodes) == 2
