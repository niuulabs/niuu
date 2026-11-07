"""The remaining character limits on resident context count when they cut.

Each of these shortened something a resident carries forward — an operator
question, an A2A result — without anything counting it. The cut is still made
(the bound is real), but it now shows on ``ravn.cap.hits`` and the resident's
health snapshot. Content whose real bound is the model's context window (the
recorded tool results, the prior working state, the reply being answered) is
not capped at all, and a test below asserts it reaches the resident whole.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from ravn.adapters.tools.a2a_task import _append_while_fits
from ravn.adapters.tools.introspection import RavnReflectTool
from ravn.cap_telemetry import cap_hit_counts
from ravn.domain.models import (
    LLMResponse,
    Session,
    StopReason,
    TokenUsage,
)


class TestA2AResult:
    def test_items_beyond_the_budget_are_counted_and_marked(self) -> None:
        payload: dict = {"artifacts": []}
        values = [{"id": str(index), "body": "a" * 200} for index in range(40)]

        _append_while_fits(payload, "artifacts", values, 1_000)

        assert payload["artifacts"][-1] == {"truncated": "additional artifacts omitted"}
        assert cap_hit_counts() == {"gateway.platform.a2a_result_max_chars": 1}

    def test_items_that_fit_are_all_kept_past_the_old_sixteen(self) -> None:
        payload: dict = {"artifacts": []}
        values = [{"id": str(index)} for index in range(20)]

        _append_while_fits(payload, "artifacts", values, 100_000)

        assert len(payload["artifacts"]) == 20
        assert cap_hit_counts() == {}


class TestReflectTool:
    async def test_a_cut_off_reflection_is_a_failed_tool_call(self) -> None:
        llm = AsyncMock()
        llm.generate.return_value = LLMResponse(
            content="Progress so f",
            tool_calls=[],
            stop_reason=StopReason.MAX_TOKENS,
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )
        tool = RavnReflectTool(llm, Session(), model="m")

        result = await tool.execute({"task_description": "ship it"})

        assert result.is_error
        assert "ravn_reflect" in result.content
        assert llm.generate.await_args.kwargs["max_tokens"] is None
        assert cap_hit_counts() == {"ravn_reflect": 1}
