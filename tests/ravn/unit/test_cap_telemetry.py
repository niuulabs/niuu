"""Hitting a configured limit is loud and counted, wherever it happens.

Every row here was a silent cap once: a turn cut off mid-answer read as the
answer, a cut-off tool call ran with half its arguments, a cut-off compaction
document replaced the history it summarised.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from ravn.agent import RavnAgent
from ravn.budget import IterationBudget
from ravn.cap_telemetry import (
    CAP_HITS,
    HEALTH_TOTAL_KEY,
    cap_hit_counts,
    degraded_counts,
    health_counts,
    record_cap_hit,
    record_degraded,
    record_truncation,
    require_complete,
)
from ravn.compression import ContextCompressor
from ravn.domain.exceptions import (
    ContextCompressionError,
    MaxIterationsError,
    OutputTruncatedError,
)
from ravn.domain.models import (
    LLMResponse,
    Message,
    StopReason,
    TokenUsage,
    ToolCall,
    ToolResult,
)
from tests.ravn.conftest import MockLLM, make_agent, make_text_response
from tests.ravn.fixtures.fakes import EchoTool, InMemoryChannel


def _response(stop_reason: StopReason, content: str = "half an", **kwargs) -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=kwargs.pop("tool_calls", []),
        stop_reason=stop_reason,
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        **kwargs,
    )


class TestRecord:
    def test_counts_per_knob_and_logs_an_error_naming_it(self, caplog) -> None:
        with caplog.at_level(logging.ERROR, logger="ravn.cap_telemetry"):
            record_cap_hit("llm.max_tokens", "turn cut off; raise llm.max_tokens")
            record_cap_hit("llm.max_tokens", "again")
            record_cap_hit("tools.max_result_chars", "tool result cut")

        assert cap_hit_counts() == {"llm.max_tokens": 2, "tools.max_result_chars": 1}
        assert [r.levelno for r in caplog.records] == [logging.ERROR] * 3
        assert "llm.max_tokens" in caplog.records[0].getMessage()

    def test_emits_the_counter_with_the_knob(self, monkeypatch) -> None:
        telemetry = MagicMock()
        monkeypatch.setattr("ravn.cap_telemetry.get_observability", lambda: telemetry)

        record_cap_hit("wakefulness", "cut")

        name = telemetry.count.call_args.args[0]
        assert name == CAP_HITS
        assert telemetry.count.call_args.kwargs["attributes"] == {"ravn.cap.knob": "wakefulness"}

    def test_health_counts_total_and_per_knob(self) -> None:
        assert health_counts() == {HEALTH_TOTAL_KEY: 0, "degraded_total": 0}
        record_cap_hit("a", "x")
        record_cap_hit("b", "x")
        record_cap_hit("b", "x")
        record_degraded("operator_reply.persist", "x")
        assert health_counts() == {
            "cap_hits.a": 1,
            "cap_hits.b": 2,
            HEALTH_TOTAL_KEY: 3,
            "degraded.operator_reply.persist": 1,
            "degraded_total": 1,
        }

    def test_a_degraded_path_logs_an_error_and_counts(self, caplog) -> None:
        with caplog.at_level(logging.ERROR, logger="ravn.cap_telemetry"):
            record_degraded("directed_message.persist", "not written; delivered live")
        assert degraded_counts() == {"directed_message.persist": 1}
        assert "degraded [directed_message.persist]" in caplog.records[0].getMessage()

    def test_truncation_names_what_was_cut(self, caplog) -> None:
        with caplog.at_level(logging.ERROR, logger="ravn.cap_telemetry"):
            record_truncation("tools.bash.max_output_bytes", what="output", kept=10, total=25)
        message = caplog.records[0].getMessage()
        assert "output was cut from 25 to 10" in message
        assert "raise tools.bash.max_output_bytes" in message


class TestRequireComplete:
    def test_a_finished_answer_passes_uncounted(self) -> None:
        response = _response(StopReason.END_TURN, "done")
        assert require_complete(response, knob="k", budget=10, model="m", purpose="p") is response
        assert cap_hit_counts() == {}

    def test_a_capped_cut_names_the_budget_and_the_remedy(self) -> None:
        with pytest.raises(OutputTruncatedError) as excinfo:
            require_complete(
                _response(StopReason.MAX_TOKENS),
                knob="memory.reflection_max_tokens",
                budget=512,
                model="Qwen/Qwen3.8-27B",
                purpose="the reflection",
            )
        message = str(excinfo.value)
        assert "[memory.reflection_max_tokens]" in message
        assert "512-token budget" in message
        assert "raise memory.reflection_max_tokens" in message
        assert excinfo.value.knob == "memory.reflection_max_tokens"
        assert cap_hit_counts() == {"memory.reflection_max_tokens": 1}

    def test_an_uncapped_cut_names_the_providers_limit(self) -> None:
        with pytest.raises(OutputTruncatedError, match="no cap sent"):
            require_complete(
                _response(StopReason.MAX_TOKENS), knob="k", budget=None, model="m", purpose="p"
            )


class TestAgentTurn:
    async def test_a_cut_off_turn_raises_instead_of_answering(self) -> None:
        llm = MockLLM([_response(StopReason.MAX_TOKENS, "The answer is")])
        agent, _ = make_agent(llm)

        with pytest.raises(OutputTruncatedError, match="llm.max_tokens"):
            await agent.run_turn("question")
        assert cap_hit_counts() == {"llm.max_tokens": 1}

    async def test_a_cut_off_tool_call_is_never_run(self) -> None:
        tool = EchoTool()
        tool.execute = AsyncMock(wraps=tool.execute)  # type: ignore[method-assign]
        call = ToolCall(id="c1", name="echo", input={})
        llm = MockLLM([_response(StopReason.MAX_TOKENS, "", tool_calls=[call])])
        agent, _ = make_agent(llm, tools=[tool])

        with pytest.raises(OutputTruncatedError):
            await agent.run_turn("go")
        tool.execute.assert_not_awaited()

    async def test_running_out_of_iterations_is_counted(self) -> None:
        call = ToolCall(id="c1", name="echo", input={"message": "again"})
        looping = [_response(StopReason.TOOL_USE, "", tool_calls=[call]) for _ in range(3)]
        agent, _ = make_agent(MockLLM(looping), tools=[EchoTool()], max_iterations=2)

        with pytest.raises(MaxIterationsError):
            await agent.run_turn("loop")
        assert cap_hit_counts() == {"agent.max_iterations": 1}

    async def test_an_exhausted_iteration_budget_is_counted(self) -> None:
        agent, _ = make_agent(MockLLM([]))
        agent._iteration_budget = IterationBudget(total=1, consumed=1)

        with pytest.raises(MaxIterationsError):
            await agent.run_turn("anything")
        with pytest.raises(MaxIterationsError):
            await agent.repair_outcome("fix it")
        assert cap_hit_counts() == {"iteration_budget.total": 2}

    async def test_outcome_repair_refuses_a_cut_off_repair(self) -> None:
        agent, _ = make_agent(MockLLM([_response(StopReason.MAX_TOKENS, "---outcome---")]))
        with pytest.raises(OutputTruncatedError, match="outcome repair"):
            await agent.repair_outcome("fix it")

    async def test_a_finished_turn_is_unaffected(self) -> None:
        agent, _ = make_agent(MockLLM([make_text_response("fine")]))
        result = await agent.run_turn("hello")
        assert result.response == "fine"
        assert cap_hit_counts() == {}

    def test_no_cap_with_a_window_uses_the_window_as_the_ceiling(self) -> None:
        """The window is the real bound; an answer that does not fit raises when cut."""
        agent = RavnAgent(
            llm=MockLLM([]),
            tools=[],
            channel=InMemoryChannel(),
            permission=MagicMock(),
            system_prompt="s",
            model="m",
            max_tokens=None,
            max_iterations=1,
            context_window_tokens=131072,
        )
        assert agent._max_prompt_tokens == 131072

    def test_compaction_target_leaves_tool_results_bounded_by_the_window(self) -> None:
        """A latency target compacts history; it no longer cuts the newest result."""
        agent = RavnAgent(
            llm=MockLLM([]),
            tools=[],
            channel=InMemoryChannel(),
            permission=MagicMock(),
            system_prompt="s",
            model="m",
            max_tokens=None,
            max_iterations=1,
            context_window_tokens=1048576,
            compaction_target_tokens=48000,
        )
        assert agent._max_prompt_tokens == 1048576
        assert agent._compaction_target_tokens == 48000
        # 48000 used to leave ~33k tokens for one result; the window leaves far more.
        assert agent._budget_tool_result_char_limit() > 1_000_000

    def test_no_cap_with_an_explicit_prompt_budget_is_accepted(self) -> None:
        agent = RavnAgent(
            llm=MockLLM([]),
            tools=[],
            channel=InMemoryChannel(),
            permission=MagicMock(),
            system_prompt="s",
            model="m",
            max_tokens=None,
            max_iterations=1,
            context_window_tokens=131072,
            max_prompt_tokens=100000,
        )
        assert agent._max_prompt_tokens == 100000

    def test_a_cap_still_reserves_its_share_of_the_window(self) -> None:
        agent = RavnAgent(
            llm=MockLLM([]),
            tools=[],
            channel=InMemoryChannel(),
            permission=MagicMock(),
            system_prompt="s",
            model="m",
            max_tokens=32000,
            max_iterations=1,
            context_window_tokens=131072,
        )
        assert agent._max_prompt_tokens == 131072 - 32000


class TestAgentToolResult:
    def test_a_truncated_tool_result_counts(self) -> None:
        agent, _ = make_agent(MockLLM([]))
        agent._max_tool_result_chars = 10
        call = ToolCall(id="c1", name="echo", input={})

        bounded = agent._truncate_oversized_tool_result(
            call, ToolResult(tool_call_id="c1", content="x" * 50), batch_size=1
        )
        assert bounded.content.startswith("x" * 10)
        assert "truncated" in bounded.content
        assert cap_hit_counts() == {"tools.max_result_chars": 1}


class TestCompaction:
    async def test_a_cut_off_state_document_never_replaces_history(self) -> None:
        llm = MockLLM([_response(StopReason.MAX_TOKENS, "## State\n- half")])
        compressor = ContextCompressor(
            llm, model="m", protect_first=1, protect_last=1, context_window=200, output_reserve=0
        )
        messages = [Message(role="user", content="word " * 200) for _ in range(6)]

        with pytest.raises(ContextCompressionError, match="compression_max_tokens"):
            await compressor.maybe_compress(messages)
        assert cap_hit_counts() == {"context_management.compression_max_tokens": 1}

    async def test_no_output_reserve_means_no_window_derived_budget(self) -> None:
        compressor = ContextCompressor(
            MockLLM([]), model="m", context_window=200, output_reserve=None
        )
        messages = [Message(role="user", content="word " * 200) for _ in range(6)]

        kept, result = await compressor.maybe_compress(messages)

        assert kept is messages
        assert not result.was_compressed
