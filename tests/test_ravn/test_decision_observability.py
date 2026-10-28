"""Every decision is visible: spans, metrics, events and a log line per request."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any

import pytest

from ravn.decision_observability import (
    LATENCY_BUCKETS_S,
    SCORE_BUCKETS,
    UNIT_BUCKETS,
    ObservedDecisionPort,
    describe_answer,
    describe_question,
)
from ravn.ports.decision import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionResult,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
)


class _Span:
    def __init__(self) -> None:
        self.attributes: dict[str, Any] = {}


class RecordingTelemetry:
    def __init__(self) -> None:
        self.spans: list[tuple[str, dict]] = []
        self.span_attributes: dict[str, Any] = {}
        self.counts: list[tuple[str, int, dict]] = []
        self.records: list[tuple[str, float, dict]] = []
        self.durations: list[tuple[str, float, dict]] = []
        self.events: list[tuple[str, dict, Any]] = []
        self.errors: list[tuple[str, str]] = []
        self.buckets: dict[str, Any] = {}

    @contextmanager
    def span(self, name: str, *, attributes: dict | None = None):
        self.spans.append((name, dict(attributes or {})))
        yield _Span()

    def set_attributes(self, attributes: dict) -> None:
        self.span_attributes.update(attributes)

    def count(self, name: str, value: int = 1, *, attributes: dict | None = None) -> None:
        self.counts.append((name, value, dict(attributes or {})))

    def record(
        self, name: str, value: float, *, attributes: dict | None = None, buckets=None
    ) -> None:
        self.records.append((name, value, dict(attributes or {})))
        self.buckets[name] = buckets

    def duration(
        self, name: str, seconds: float, *, attributes: dict | None = None, buckets=None
    ) -> None:
        self.durations.append((name, seconds, dict(attributes or {})))
        self.buckets[name] = buckets

    def event(self, name: str, *, attributes: dict | None = None, content: Any = None) -> None:
        self.events.append((name, dict(attributes or {}), content))

    def mark_error(self, span: Any, error_type: str, description: str = "") -> None:
        self.errors.append((error_type, description))


class _Port:
    def __init__(self, result: DecisionResult | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls: list[tuple[Any, dict, str]] = []

    async def decide(self, state, questions, *, purpose):
        self.calls.append((state, dict(questions), purpose))
        if self.error is not None:
            raise self.error
        return self.result


_QUESTIONS = {
    "p": NoulQuestion("x"),
    "c": ChoiceQuestion("y", {"a": None, "b": None}),
    "s": ScoreQuestion("z", ["lo", "hi"]),
}
_RESULT = DecisionResult(
    model="jev-1.14.0",
    answers={
        "p": NoulAnswer(0.25),
        "c": ChoiceAnswer("b", 0.9, {"a": 0.1, "b": 0.9}),
        "s": ScoreAnswer(0.5, 0.4, {0: 0.5, 1: 0.5}),
    },
    input_tokens=120,
)


@pytest.fixture
def telemetry(monkeypatch: pytest.MonkeyPatch) -> RecordingTelemetry:
    recorder = RecordingTelemetry()
    monkeypatch.setattr("ravn.decision_observability.get_observability", lambda: recorder)
    return recorder


async def test_passes_through_and_records_the_request(telemetry: RecordingTelemetry) -> None:
    inner = _Port(_RESULT)
    result = await ObservedDecisionPort(inner).decide({"k": 1}, _QUESTIONS, purpose="unit")

    assert result is _RESULT
    assert inner.calls == [({"k": 1}, _QUESTIONS, "unit")]
    assert telemetry.spans == [
        ("ravn.decision", {"ravn.decision.purpose": "unit", "ravn.decision.question_count": 3})
    ]
    assert telemetry.span_attributes == {
        "ravn.decision.model": "jev-1.14.0",
        "ravn.decision.input_tokens": 120,
    }
    base = {"ravn.decision.purpose": "unit", "ravn.decision.model": "jev-1.14.0"}
    assert ("ravn.decision.requests", 1, base) in telemetry.counts
    assert ("ravn.decision.input_tokens", 120, base) in telemetry.counts
    assert [name for name, _, _ in telemetry.durations] == ["ravn.decision.latency"]
    assert telemetry.buckets["ravn.decision.latency"] == LATENCY_BUCKETS_S


async def test_records_each_answer(telemetry: RecordingTelemetry) -> None:
    await ObservedDecisionPort(_Port(_RESULT)).decide("s", _QUESTIONS, purpose="unit")

    answers = [attrs for name, _, attrs in telemetry.counts if name == "ravn.decision.answers"]
    assert {(a["ravn.decision.question"], a["ravn.decision.answer_type"]) for a in answers} == {
        ("p", "noul"),
        ("c", "choice"),
        ("s", "score"),
    }
    choice = next(a for a in answers if a["ravn.decision.question"] == "c")
    assert choice["ravn.decision.answer"] == "b"
    histograms = {(name, value) for name, value, _ in telemetry.records}
    assert ("ravn.decision.probability", 0.25) in histograms
    assert ("ravn.decision.confidence", 0.9) in histograms
    assert ("ravn.decision.score", 0.5) in histograms
    assert telemetry.buckets["ravn.decision.confidence"] == UNIT_BUCKETS
    assert telemetry.buckets["ravn.decision.probability"] == UNIT_BUCKETS
    assert telemetry.buckets["ravn.decision.score"] == SCORE_BUCKETS
    events = {
        attrs["ravn.decision.question"]: content
        for name, attrs, content in telemetry.events
        if name == "ravn.decision.answer"
    }
    assert events["c"]["probabilities"] == {"a": 0.1, "b": 0.9}
    assert events["s"]["probabilities"] == {"0": 0.5, "1": 0.5}


async def test_unknown_usage_records_no_token_count(telemetry: RecordingTelemetry) -> None:
    result = DecisionResult(model="nimble", answers={"p": NoulAnswer(0.5)}, input_tokens=None)
    await ObservedDecisionPort(_Port(result)).decide("s", {"p": NoulQuestion("x")}, purpose="u")

    assert not any(name == "ravn.decision.input_tokens" for name, _, _ in telemetry.counts)
    assert "ravn.decision.input_tokens" not in telemetry.span_attributes


async def test_failure_is_marked_counted_and_raised(telemetry: RecordingTelemetry) -> None:
    port = ObservedDecisionPort(_Port(error=TimeoutError("slow")))

    with pytest.raises(TimeoutError):
        await port.decide("s", _QUESTIONS, purpose="unit")

    assert telemetry.errors == [("TimeoutError", "slow")]
    assert telemetry.counts == [
        (
            "ravn.decision.failures",
            1,
            {"ravn.decision.purpose": "unit", "error.type": "TimeoutError"},
        )
    ]


async def test_logs_one_summary_line(
    telemetry: RecordingTelemetry, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="ravn.decision_observability"):
        await ObservedDecisionPort(_Port(_RESULT)).decide("s", _QUESTIONS, purpose="unit")

    [line] = [r.getMessage() for r in caplog.records]
    assert line.startswith("decision[unit] model=jev-1.14.0 ")
    assert line.endswith("p=0.25 c=b(0.90) s=0.50(0.40)")


def test_describe_answer() -> None:
    assert describe_answer(NoulAnswer(0.5)) == "0.50"
    assert describe_answer(ChoiceAnswer("x", 0.75, {"x": 0.75})) == "x(0.75)"
    assert describe_answer(ScoreAnswer(1.25, 0.5, {0: 0.5, 1: 0.5})) == "1.25(0.50)"


async def test_close_closes_the_inner_adapter() -> None:
    class _Closable(_Port):
        closed = False

        async def close(self) -> None:
            self.closed = True

    inner = _Closable(_RESULT)
    observed = ObservedDecisionPort(inner)
    await observed.close()

    assert inner.closed
    assert observed.inner is inner
    await ObservedDecisionPort(_Port(_RESULT)).close()


async def test_records_exactly_what_was_asked(telemetry: RecordingTelemetry) -> None:
    await ObservedDecisionPort(_Port(_RESULT)).decide({"k": 1}, _QUESTIONS, purpose="unit")

    [(attrs, content)] = [
        (attrs, content)
        for name, attrs, content in telemetry.events
        if name == "ravn.decision.request"
    ]
    assert attrs == {"ravn.decision.purpose": "unit", "ravn.decision.question_count": 3}
    assert content == {
        "state": {"k": 1},
        "questions": {
            "p": {"type": "noul", "instructions": "x", "criteria": None},
            "c": {"type": "choice", "instructions": "y", "options": {"a": None, "b": None}},
            "s": {"type": "score", "instructions": "z", "levels": ["lo", "hi"]},
        },
    }


async def test_a_failed_request_still_shows_what_it_sent(telemetry: RecordingTelemetry) -> None:
    with pytest.raises(TimeoutError):
        await ObservedDecisionPort(_Port(error=TimeoutError("slow"))).decide(
            "the state", _QUESTIONS, purpose="unit"
        )

    [request] = [
        content for name, _, content in telemetry.events if name == "ravn.decision.request"
    ]
    assert request["state"] == "the state"


def test_describe_question_keeps_noul_criteria() -> None:
    assert describe_question(NoulQuestion("x", {"true": "t", "false": "f"})) == {
        "type": "noul",
        "instructions": "x",
        "criteria": {"true": "t", "false": "f"},
    }
