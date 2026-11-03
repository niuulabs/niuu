"""Telemetry for every System One decision Ravn asks for.

``ObservedDecisionPort`` wraps whichever decision adapter is configured, so every call site gets
the same visibility without each adapter re-implementing it:

* span ``ravn.decision`` per request — purpose, model, question count, input tokens, errors;
* a ``ravn.decision.request`` span event carrying exactly what was asked — the state and every
  question's instructions and answer space — recorded before the call, so a failed request still
  shows what it sent (content capture rules apply: redacted, bounded, off unless enabled);
* metrics ``ravn.decision.requests`` / ``.failures`` / ``.input_tokens`` and the
  ``ravn.decision.latency`` histogram, by purpose and model;
* per answer: ``ravn.decision.answers`` counted by question and (for a Choice) the chosen option,
  ``ravn.decision.confidence`` / ``.probability`` / ``.score`` histograms, and a
  ``ravn.decision.answer`` span event carrying the full distribution when content capture is on;
* one INFO log line per request summarising its latency, input tokens and every answer, and one
  ERROR line per failed request. These lines are the exact per-event record: decisions are sparse
  (often hourly) and residents restart on every rollout, so counter ``increase()`` drops a new
  series' first event and any restart that lands on the same value — dashboards count from logs.

Metric attributes are bounded by construction: purposes, question ids and option names are
defined in code, never taken from state.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from time import monotonic
from typing import Any

from niuu.observability import get_observability
from ravn.ports.decision import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionAnswer,
    DecisionPort,
    DecisionQuestion,
    DecisionResult,
    NoulAnswer,
    NoulQuestion,
)

logger = logging.getLogger(__name__)

#: Histogram resolution for decision telemetry. The SDK default buckets (0, 5, 10 … 10000) put
#: every sub-second latency and every value in [0, 1] into one bucket, which makes quantiles
#: meaningless. These are reporting resolution, not behaviour.
LATENCY_BUCKETS_S = (0.025, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 0.75, 1.0, 2.0, 5.0, 10.0)
UNIT_BUCKETS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
#: Score levels are 0..9 at most; a boundary between each pair of levels.
SCORE_BUCKETS = (0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5)


class ObservedDecisionPort(DecisionPort):
    """Decorate a decision port with spans, metrics and a log line per decision."""

    def __init__(self, inner: DecisionPort) -> None:
        self._inner = inner

    @property
    def inner(self) -> DecisionPort:
        return self._inner

    async def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if close is not None:
            await close()

    async def decide(
        self,
        state: Any,
        questions: Mapping[str, DecisionQuestion],
        *,
        purpose: str,
    ) -> DecisionResult:
        telemetry = get_observability()
        span_attributes = {
            "ravn.decision.purpose": purpose,
            "ravn.decision.question_count": len(questions),
        }
        started = monotonic()
        with telemetry.span("ravn.decision", attributes=span_attributes) as span:
            telemetry.event(
                "ravn.decision.request",
                attributes=span_attributes,
                content={
                    "state": state,
                    "questions": {qid: describe_question(q) for qid, q in questions.items()},
                },
            )
            try:
                result = await self._inner.decide(state, questions, purpose=purpose)
            except Exception as exc:
                telemetry.mark_error(span, type(exc).__name__, str(exc))
                telemetry.count(
                    "ravn.decision.failures",
                    attributes={
                        "ravn.decision.purpose": purpose,
                        "error.type": type(exc).__name__,
                    },
                )
                logger.error(
                    "decision[%s] failed %.0fms error=%s: %s",
                    purpose,
                    (monotonic() - started) * 1000,
                    type(exc).__name__,
                    exc,
                )
                raise
            elapsed = monotonic() - started
            base = {"ravn.decision.purpose": purpose, "ravn.decision.model": result.model}
            request_attributes: dict[str, Any] = {"ravn.decision.model": result.model}
            if result.input_tokens is not None:
                request_attributes["ravn.decision.input_tokens"] = result.input_tokens
            telemetry.set_attributes(request_attributes)
            telemetry.count("ravn.decision.requests", attributes=base)
            telemetry.duration(
                "ravn.decision.latency", elapsed, attributes=base, buckets=LATENCY_BUCKETS_S
            )
            if result.input_tokens is not None:
                telemetry.count("ravn.decision.input_tokens", result.input_tokens, attributes=base)
            for qid, answer in result.answers.items():
                _record_answer(telemetry, base, qid, answer)
        logger.info(
            "decision[%s] model=%s %.0fms input_tokens=%s %s",
            purpose,
            result.model,
            elapsed * 1000,
            "unknown" if result.input_tokens is None else result.input_tokens,
            " ".join(f"{qid}={describe_answer(a)}" for qid, a in result.answers.items()),
        )
        return result


def describe_question(question: DecisionQuestion) -> dict[str, Any]:
    """The question as asked: its type, instructions and full answer space."""
    if isinstance(question, NoulQuestion):
        return {
            "type": "noul",
            "instructions": question.instructions,
            "criteria": dict(question.criteria) if question.criteria is not None else None,
        }
    if isinstance(question, ChoiceQuestion):
        return {
            "type": "choice",
            "instructions": question.instructions,
            "options": dict(question.options),
        }
    return {
        "type": "score",
        "instructions": question.instructions,
        "levels": list(question.levels),
    }


def describe_answer(answer: DecisionAnswer) -> str:
    """Compact, log-friendly rendering: P(yes), ``choice(confidence)`` or ``score(confidence)``."""
    if isinstance(answer, NoulAnswer):
        return f"{answer.probability:.2f}"
    if isinstance(answer, ChoiceAnswer):
        return f"{answer.choice}({answer.confidence:.2f})"
    return f"{answer.score:.2f}({answer.confidence:.2f})"


def _record_answer(
    telemetry: Any,
    base: dict[str, str],
    qid: str,
    answer: DecisionAnswer,
) -> None:
    attributes: dict[str, Any] = {**base, "ravn.decision.question": qid}
    if isinstance(answer, NoulAnswer):
        attributes["ravn.decision.answer_type"] = "noul"
        telemetry.record(
            "ravn.decision.probability",
            answer.probability,
            attributes=attributes,
            buckets=UNIT_BUCKETS,
        )
        content: dict[str, Any] = {"probability": answer.probability}
    elif isinstance(answer, ChoiceAnswer):
        attributes["ravn.decision.answer_type"] = "choice"
        attributes["ravn.decision.answer"] = answer.choice
        telemetry.record(
            "ravn.decision.confidence",
            answer.confidence,
            attributes=attributes,
            buckets=UNIT_BUCKETS,
        )
        content = {
            "choice": answer.choice,
            "confidence": answer.confidence,
            "probabilities": dict(answer.probabilities),
        }
    else:
        attributes["ravn.decision.answer_type"] = "score"
        telemetry.record(
            "ravn.decision.score", answer.score, attributes=attributes, buckets=SCORE_BUCKETS
        )
        telemetry.record(
            "ravn.decision.confidence",
            answer.confidence,
            attributes=attributes,
            buckets=UNIT_BUCKETS,
        )
        content = {
            "score": answer.score,
            "confidence": answer.confidence,
            "probabilities": {str(level): p for level, p in answer.probabilities.items()},
        }
    telemetry.count("ravn.decision.answers", attributes=attributes)
    telemetry.event("ravn.decision.answer", attributes=attributes, content=content)
