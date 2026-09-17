"""What memory is searched with, when it differs from what the model is shown.

A room message reaches the agent wrapped in framing plus up to 4,000 characters of prior room
context. Recalling on all of that embeds an average of the room's recent chatter rather than the
question — and searches documents averaging 158 characters with a 4,000-character vector.
"""

from __future__ import annotations

from tests.test_ravn.test_agent_with_memory import RecordingMemory, make_agent, make_simple_llm


class TestRecallQuery:
    async def test_recall_uses_the_query_when_one_is_given(self) -> None:
        mem = RecordingMemory()
        agent, _ = make_agent(make_simple_llm(), memory=mem)
        envelope = (
            "This is a directed message from a human.\n"
            "Most recent message from this participant in the room (context only):\n"
            + ("prior room chatter about ports and brokers. " * 80)
            + "\nHuman message: which commit is dev-integration on?"
        )

        await agent.run_turn(envelope, recall_query="which commit is dev-integration on?")

        assert mem.prefetch_calls == ["which commit is dev-integration on?"]

    async def test_only_recall_narrows_not_the_turn(self) -> None:
        """The agent still needs the room context to answer well — only the SEARCH narrows."""
        mem = RecordingMemory()
        agent, _ = make_agent(make_simple_llm(), memory=mem)
        envelope = "framing and prior room context\nHuman message: what port?"

        result = await agent.run_turn(envelope, recall_query="what port?")

        assert mem.prefetch_calls == ["what port?"]
        assert result.response  # the turn ran on the full envelope, not the query
        assert len(mem.prefetch_calls[0]) < len(envelope)

    async def test_without_a_query_the_input_is_the_query(self) -> None:
        """Every non-room trigger: the prompt IS what was asked."""
        mem = RecordingMemory()
        agent, _ = make_agent(make_simple_llm(), memory=mem)

        await agent.run_turn("which commit is dev-integration on?")

        assert mem.prefetch_calls == ["which commit is dev-integration on?"]
