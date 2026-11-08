"""Hitting a configured limit is loud and counted, wherever it happens.

Every row here was a silent cap once: a model answer cut off mid-sentence read
as the answer, and a cut-off tool call ran with half its arguments.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from niuu.cap_telemetry import (
    CAP_HITS,
    HEALTH_TOTAL_KEY,
    RETRIES,
    OutputTruncatedError,
    cap_hit_counts,
    degraded_counts,
    health_counts,
    record_cap_hit,
    record_degraded,
    record_retry,
    record_truncation,
    require_complete,
    retry_counts,
)

END_TURN = "end_turn"
MAX_TOKENS = "max_tokens"


@dataclass
class _Answer:
    stop_reason: str
    content: str = "half an"
    reasoning: str = ""
    tool_calls: list[Any] = field(default_factory=list)


def _response(stop_reason: str, content: str = "half an") -> _Answer:
    return _Answer(stop_reason=stop_reason, content=content)


class TestRecord:
    def test_counts_per_knob_and_logs_an_error_naming_it(self, caplog) -> None:
        with caplog.at_level(logging.ERROR, logger="niuu.cap_telemetry"):
            record_cap_hit("llm.max_tokens", "turn cut off; raise llm.max_tokens")
            record_cap_hit("llm.max_tokens", "again")
            record_cap_hit("tools.max_result_chars", "tool result cut")

        assert cap_hit_counts() == {"llm.max_tokens": 2, "tools.max_result_chars": 1}
        assert [r.levelno for r in caplog.records] == [logging.ERROR] * 3
        assert "llm.max_tokens" in caplog.records[0].getMessage()

    def test_emits_the_counter_with_the_knob(self, monkeypatch) -> None:
        telemetry = MagicMock()
        monkeypatch.setattr("niuu.cap_telemetry.get_observability", lambda: telemetry)

        record_cap_hit("wakefulness", "cut")

        name = telemetry.count.call_args.args[0]
        assert name == CAP_HITS
        assert telemetry.count.call_args.kwargs["attributes"] == {"ravn.cap.knob": "wakefulness"}

    def test_health_counts_total_and_per_knob(self) -> None:
        assert health_counts() == {HEALTH_TOTAL_KEY: 0, "degraded_total": 0, "retries_total": 0}
        record_cap_hit("a", "x")
        record_cap_hit("b", "x")
        record_cap_hit("b", "x")
        record_degraded("operator_reply.persist", "x")
        record_retry("llm.anthropic.stream", "x")
        record_retry("llm.anthropic.stream", "x")
        assert health_counts() == {
            "cap_hits.a": 1,
            "cap_hits.b": 2,
            HEALTH_TOTAL_KEY: 3,
            "degraded.operator_reply.persist": 1,
            "degraded_total": 1,
            "retries.llm.anthropic.stream": 2,
            "retries_total": 2,
        }

    def test_a_retry_logs_a_warning_and_counts_without_being_a_cap_hit(self, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="niuu.cap_telemetry"):
            record_retry("llm.openai.connect", "refused; retrying in 1.0s")
        assert retry_counts() == {"llm.openai.connect": 1}
        assert cap_hit_counts() == {}
        assert [r.levelno for r in caplog.records] == [logging.WARNING]
        assert "retry [llm.openai.connect]" in caplog.records[0].getMessage()

    def test_a_retry_emits_the_retries_counter_with_its_path(self, monkeypatch) -> None:
        telemetry = MagicMock()
        monkeypatch.setattr("niuu.cap_telemetry.get_observability", lambda: telemetry)

        record_retry("llm.anthropic.stream", "cut")

        assert telemetry.count.call_args.args[0] == RETRIES
        assert telemetry.count.call_args.kwargs["attributes"] == {
            "ravn.retry.path": "llm.anthropic.stream"
        }

    def test_a_degraded_path_logs_an_error_and_counts(self, caplog) -> None:
        with caplog.at_level(logging.ERROR, logger="niuu.cap_telemetry"):
            record_degraded("directed_message.persist", "not written; delivered live")
        assert degraded_counts() == {"directed_message.persist": 1}
        assert "degraded [directed_message.persist]" in caplog.records[0].getMessage()

    def test_truncation_names_what_was_cut(self, caplog) -> None:
        with caplog.at_level(logging.ERROR, logger="niuu.cap_telemetry"):
            record_truncation("tools.bash.max_output_bytes", what="output", kept=10, total=25)
        message = caplog.records[0].getMessage()
        assert "output was cut from 25 to 10" in message
        assert "raise tools.bash.max_output_bytes" in message


class TestRequireComplete:
    def test_a_finished_answer_passes_uncounted(self) -> None:
        response = _response(END_TURN, "done")
        assert require_complete(response, knob="k", budget=10, model="m", purpose="p") is response
        assert cap_hit_counts() == {}

    def test_a_capped_cut_names_the_budget_and_the_remedy(self) -> None:
        with pytest.raises(OutputTruncatedError) as excinfo:
            require_complete(
                _response(MAX_TOKENS),
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
            require_complete(_response(MAX_TOKENS), knob="k", budget=None, model="m", purpose="p")
