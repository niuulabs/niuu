"""The remaining character limits on resident context count when they cut.

Each of these shortened something a resident carries forward — its working
state, an operator question, an A2A result, the reply it is answering —
without anything counting it. The cut is still made (the bound is real), but
it now shows on ``ravn.cap.hits`` and the resident's health snapshot.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from niuu.domain.notifications import MAX_BODY_CHARS
from ravn.adapters.tools.a2a_task import _append_while_fits
from ravn.adapters.tools.introspection import RavnReflectTool
from ravn.cap_telemetry import HEALTH_TOTAL_KEY, cap_hit_counts, record_cap_hit
from ravn.domain.models import (
    LLMResponse,
    Session,
    StopReason,
    TokenUsage,
    ToolCall,
    ToolResult,
    TurnResult,
)
from ravn.drive_loop import _bounded_room_context, _build_resident_valkyrie_schema_repair_prompt
from ravn.resident_continuation import _operator_question_line
from ravn.resident_runtime import ResidentRuntime, _bounded, _tool_result_summaries


class TestResidentContext:
    def test_a_bound_with_a_knob_counts_its_cut(self) -> None:
        text = _bounded(
            "x" * 50, 10, knob="resident_state.continuation_context_max_chars", what="s"
        )
        assert text.startswith("x" * 10)
        assert text.endswith("(truncated)")
        assert cap_hit_counts() == {"resident_state.continuation_context_max_chars": 1}

    def test_text_within_the_bound_is_untouched(self) -> None:
        assert _bounded("short", 10, knob="k", what="w") == "short"
        assert cap_hit_counts() == {}

    def test_recorded_tool_results_count_their_cut(self) -> None:
        result = TurnResult(
            response="",
            tool_calls=[ToolCall(id="c1", name="kubernetes_inspect", input={})],
            tool_results=[ToolResult(tool_call_id="c1", content="y" * 500)],
            usage=TokenUsage(input_tokens=0, output_tokens=0),
        )

        (summary,) = _tool_result_summaries(result, max_chars=100)

        assert summary.startswith("### kubernetes_inspect (ok)")
        assert cap_hit_counts() == {"resident_state.continuation_tool_result_max_chars": 1}

    def test_a_long_reply_context_counts(self) -> None:
        bounded = _bounded_room_context("z" * 30, 10)
        assert "20 characters omitted" in bounded
        assert cap_hit_counts() == {"resident_state.directed_message_context_max_chars": 1}


class TestOperatorQuestion:
    def test_a_question_within_the_notification_bound_is_kept_whole(self) -> None:
        question = "Which cluster should I drain first?\nThe two are equally loaded."
        assert _operator_question_line(question) == " ".join(question.split())
        assert cap_hit_counts() == {}

    def test_a_question_longer_than_a_notification_counts(self) -> None:
        line = _operator_question_line("q" * (MAX_BODY_CHARS + 100))
        assert len(line) == MAX_BODY_CHARS
        assert cap_hit_counts() == {"niuu.notifications.MAX_BODY_CHARS": 1}


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


class TestSchemaRepair:
    def test_the_whole_previous_answer_is_repaired(self) -> None:
        # The outcome block sits at the end; keeping only the head cut it off.
        original = "reasoning " * 1_000 + "---outcome---\ndecision: watch\n---end---"

        prompt = _build_resident_valkyrie_schema_repair_prompt(
            original_response=original, validation_errors=["bad"], outcome_fields={}
        )

        assert "decision: watch\n---end---\n</previous_response>" in prompt


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


class TestHealthSnapshot:
    def test_cap_hits_ride_on_the_health_snapshot(self) -> None:
        runtime = ResidentRuntime(state=MagicMock(), resident_id="regin")
        record_cap_hit("llm.max_tokens", "cut")
        record_cap_hit("llm.max_tokens", "cut")

        snapshot = runtime.health_snapshot()

        assert snapshot["cap_hits.llm.max_tokens"] == 2
        assert snapshot[HEALTH_TOTAL_KEY] == 2

    def test_gauges_restate_the_total(self, monkeypatch) -> None:
        telemetry = MagicMock()
        monkeypatch.setattr("ravn.resident_runtime.get_observability", lambda: telemetry)
        runtime = ResidentRuntime(state=MagicMock(), resident_id="regin")
        record_cap_hit("wakefulness", "cut")

        runtime._emit_health_gauges({})

        names = {call.args[0]: call.args[1] for call in telemetry.gauge.call_args_list}
        assert names["ravn.resident.cap_hits"] == 1
