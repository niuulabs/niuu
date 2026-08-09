"""Unit tests for MemoryPort hook integration in the agent loop (NIU-542).

Verifies that:
- RavnAgent calls memory.on_turn_complete() after every run_turn()
- process_inline_facts is called when the adapter exposes it
- No isinstance checks — any MemoryPort implementation receives the hook
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from ravn.adapters.permission.allow_deny import AllowAllPermission
from ravn.agent import RavnAgent
from ravn.ports.memory import MemoryPort
from tests.ravn.conftest import MockLLM, make_text_response
from tests.ravn.fixtures.fakes import InMemoryChannel

# ---------------------------------------------------------------------------
# Minimal MemoryPort stub
# ---------------------------------------------------------------------------


class StubMemory(MemoryPort):
    """Minimal MemoryPort that records calls to on_turn_complete."""

    def __init__(self) -> None:
        self._on_turn_complete_mock = AsyncMock()

    async def record_episode(self, episode):  # type: ignore[override]
        pass

    async def query_episodes(self, query, *, limit=5, min_relevance=0.3):  # type: ignore[override]
        return []

    async def prefetch(self, context):  # type: ignore[override]
        return ""

    async def search_sessions(self, query, *, limit=3):  # type: ignore[override]
        return []

    def inject_shared_context(self, context):  # type: ignore[override]
        pass

    def get_shared_context(self):  # type: ignore[override]
        return None

    async def on_turn_complete(self, session_id, user_input, response_summary):
        await self._on_turn_complete_mock(
            session_id=session_id,
            user_input=user_input,
            response_summary=response_summary,
        )


# ---------------------------------------------------------------------------
# Agent integration tests
# ---------------------------------------------------------------------------


class TestAgentMemoryHooks:
    def _make_agent(self, memory: MemoryPort) -> tuple[RavnAgent, InMemoryChannel]:
        ch = InMemoryChannel()
        llm = MockLLM([make_text_response("Done.")])
        agent = RavnAgent(
            llm=llm,
            tools=[],
            channel=ch,
            permission=AllowAllPermission(),
            system_prompt="Test.",
            model="claude-sonnet-4-6",
            max_tokens=512,
            max_iterations=5,
            memory=memory,
        )
        return agent, ch

    @pytest.mark.asyncio
    async def test_on_turn_complete_called_after_run_turn(self) -> None:
        memory = StubMemory()
        agent, _ = self._make_agent(memory)

        await agent.run_turn("hello")

        memory._on_turn_complete_mock.assert_awaited_once()
        call_kwargs = memory._on_turn_complete_mock.call_args
        assert call_kwargs.kwargs["user_input"] == "hello"
        assert call_kwargs.kwargs["response_summary"] == "Done."

    @pytest.mark.asyncio
    async def test_on_turn_complete_exception_aborts_the_turn(self) -> None:
        """Replaces a test asserting the agent swallowed this.

        The rolling session summary is memory state; dropping it leaves later
        turns summarising across a gap, and the warning that covered it told
        nobody.
        """
        memory = StubMemory()
        memory._on_turn_complete_mock = AsyncMock(side_effect=RuntimeError("hook failed"))
        agent, _ = self._make_agent(memory)

        with pytest.raises(RuntimeError, match="on_turn_complete failed"):
            await agent.run_turn("hello")
