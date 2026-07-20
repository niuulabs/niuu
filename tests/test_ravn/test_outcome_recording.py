"""Tests for episode reflection enrichment and cost accounting (NIU-574).

Replaces the old task_outcomes-based tests.  Outcomes are now stored directly
on Episode records via _enrich_episode().
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from ravn.adapters.permission.allow_deny import AllowAllPermission
from ravn.agent import RavnAgent
from ravn.domain.budget import compute_cost as _compute_cost
from ravn.domain.models import (
    Episode,
    EpisodeMatch,
    LLMResponse,
    Outcome,
    SessionSummary,
    SharedContext,
    StopReason,
    StreamEvent,
    StreamEventType,
    TokenUsage,
)
from ravn.ports.llm import LLMPort
from ravn.ports.memory import MemoryPort
from tests.ravn.fixtures.fakes import InMemoryChannel

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class RecordingMemory(MemoryPort):
    """In-memory stub that records record_episode calls."""

    def __init__(self) -> None:
        self.recorded: list[Episode] = []
        self._shared: SharedContext | None = None

    async def record_episode(self, episode: Episode) -> None:
        self.recorded.append(episode)

    async def query_episodes(
        self, query: str, *, limit: int = 5, min_relevance: float = 0.3
    ) -> list[EpisodeMatch]:
        return []

    async def prefetch(self, context: str) -> str:
        return ""

    async def search_sessions(self, query: str, *, limit: int = 3) -> list[SessionSummary]:
        return []

    def inject_shared_context(self, context: SharedContext) -> None:
        self._shared = context

    def get_shared_context(self) -> SharedContext | None:
        return self._shared


def make_simple_llm(response_text: str = "Done!") -> LLMPort:
    """Build a mock LLM that streams a simple text response and supports generate."""

    async def _stream(*args, **kwargs) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(type=StreamEventType.TEXT_DELTA, text=response_text)
        yield StreamEvent(
            type=StreamEventType.MESSAGE_DONE,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
        )

    llm = AsyncMock(spec=LLMPort)
    llm.stream = _stream
    llm.generate = AsyncMock(
        return_value=LLMResponse(
            content="Reflection: went well, nothing to change.",
            tool_calls=[],
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=5, output_tokens=10),
        )
    )
    return llm


def make_agent(
    llm: LLMPort,
    memory: MemoryPort | None = None,
    reflection_model: str = "claude-haiku-4-5-20251001",
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
        reflection_model=reflection_model,
    )
    return agent, ch


# ---------------------------------------------------------------------------
# _compute_cost
# ---------------------------------------------------------------------------


class TestComputeCost:
    def test_zero_tokens(self) -> None:
        assert _compute_cost(0, 0, 3.0, 15.0) == 0.0

    def test_one_million_input(self) -> None:
        assert _compute_cost(1_000_000, 0, 3.0, 15.0) == pytest.approx(3.0)

    def test_one_million_output(self) -> None:
        assert _compute_cost(0, 1_000_000, 3.0, 15.0) == pytest.approx(15.0)

    def test_mixed(self) -> None:
        expected = 100_000 * 3.0 / 1_000_000 + 50_000 * 15.0 / 1_000_000
        assert _compute_cost(100_000, 50_000, 3.0, 15.0) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Episode enrichment via RavnAgent
# ---------------------------------------------------------------------------


class TestAgentEpisodeEnrichment:
    async def test_episode_recorded_after_turn(self) -> None:
        mem = RecordingMemory()
        agent, _ = make_agent(make_simple_llm(), memory=mem)
        await agent.run_turn("deploy to production")
        assert len(mem.recorded) == 1

    async def test_episode_task_description_matches_input(self) -> None:
        mem = RecordingMemory()
        agent, _ = make_agent(make_simple_llm(), memory=mem)
        await agent.run_turn("run the test suite")
        assert "run the test suite" in mem.recorded[0].task_description

    async def test_episode_has_reflection(self) -> None:
        mem = RecordingMemory()
        agent, _ = make_agent(make_simple_llm(), memory=mem)
        await agent.run_turn("build the project")
        assert mem.recorded[0].reflection is not None
        assert mem.recorded[0].reflection != ""

    async def test_reflection_calls_llm_generate(self) -> None:
        mem = RecordingMemory()
        llm = make_simple_llm()
        agent, _ = make_agent(llm, memory=mem)
        await agent.run_turn("analyse logs")
        llm.generate.assert_called_once()

    async def test_reflection_uses_configured_model(self) -> None:
        mem = RecordingMemory()
        llm = make_simple_llm()
        reflection_model = "claude-haiku-4-5-20251001"
        agent, _ = make_agent(llm, memory=mem, reflection_model=reflection_model)
        await agent.run_turn("read the docs")
        call_kwargs = llm.generate.call_args.kwargs
        assert call_kwargs["model"] == reflection_model

    @pytest.mark.parametrize("disabled_value", ["off", "disabled", "none"])
    async def test_disabled_reflection_records_raw_episode_without_llm_narrative(
        self,
        disabled_value: str,
    ) -> None:
        mem = RecordingMemory()
        llm = make_simple_llm()
        agent, _ = make_agent(llm, memory=mem, reflection_model=disabled_value)

        await agent.run_turn("observe one signal")

        llm.generate.assert_not_called()
        assert mem.recorded[0].reflection == ""

    async def test_no_memory_works_normally(self) -> None:
        agent, _ = make_agent(make_simple_llm(), memory=None)
        result = await agent.run_turn("hello")
        assert result.response == "Done!"

    async def test_episode_recording_failure_does_not_crash(self) -> None:
        mem = RecordingMemory()
        mem.record_episode = AsyncMock(side_effect=RuntimeError("db error"))
        agent, _ = make_agent(make_simple_llm(), memory=mem)
        result = await agent.run_turn("hello")
        assert result.response == "Done!"

    async def test_reflection_failure_stores_fallback_message(self) -> None:
        mem = RecordingMemory()
        llm = make_simple_llm()
        llm.generate = AsyncMock(side_effect=RuntimeError("api error"))
        agent, _ = make_agent(llm, memory=mem)
        await agent.run_turn("task with failed reflection")
        assert "unavailable" in mem.recorded[0].reflection.lower()

    async def test_multiple_turns_record_multiple_episodes(self) -> None:
        mem = RecordingMemory()
        agent, _ = make_agent(make_simple_llm(), memory=mem)
        await agent.run_turn("first task")
        await agent.run_turn("second task")
        assert len(mem.recorded) == 2

    async def test_episode_duration_is_nonnegative(self) -> None:
        mem = RecordingMemory()
        agent, _ = make_agent(make_simple_llm(), memory=mem)
        await agent.run_turn("quick task")
        assert mem.recorded[0].duration_seconds is not None
        assert mem.recorded[0].duration_seconds >= 0.0

    async def test_episode_cost_usd_nonnegative(self) -> None:
        mem = RecordingMemory()
        agent, _ = make_agent(make_simple_llm(), memory=mem)
        await agent.run_turn("task")
        assert mem.recorded[0].cost_usd is not None
        assert mem.recorded[0].cost_usd >= 0.0

    async def test_episode_errors_is_list(self) -> None:
        mem = RecordingMemory()
        agent, _ = make_agent(make_simple_llm(), memory=mem)
        await agent.run_turn("task")
        assert isinstance(mem.recorded[0].errors, list)


# ---------------------------------------------------------------------------
# Episode model fields
# ---------------------------------------------------------------------------


class TestEpisodeReflectionFields:
    def test_reflection_defaults_to_none(self) -> None:
        ep = Episode(
            episode_id="ep-1",
            session_id="sess-1",
            timestamp=datetime.now(UTC),
            summary="did something",
            task_description="do something",
            tools_used=[],
            outcome=Outcome.SUCCESS,
            tags=[],
        )
        assert ep.reflection is None
        assert ep.errors == []
        assert ep.cost_usd is None
        assert ep.duration_seconds is None

    def test_can_set_all_fields(self) -> None:
        ep = Episode(
            episode_id="ep-2",
            session_id="sess-1",
            timestamp=datetime.now(UTC),
            summary="did something",
            task_description="do something",
            tools_used=["bash"],
            outcome=Outcome.PARTIAL,
            tags=["test"],
            reflection="Should use smaller steps.",
            errors=["tool error: permission denied"],
            cost_usd=0.005,
            duration_seconds=12.5,
        )
        assert ep.reflection == "Should use smaller steps."
        assert ep.errors == ["tool error: permission denied"]
        assert ep.cost_usd == pytest.approx(0.005)
        assert ep.duration_seconds == pytest.approx(12.5)
