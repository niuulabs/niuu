"""Decision port — typed judgments from a System One decision model.

A decision model answers bounded questions about a piece of state with a probability for every
allowed answer instead of generated text: a Noul gives P(yes), a Choice picks one option of a
defined set, a Score places the state on ordered levels. The judgment stays Ravn's — this port only
says how to ask for it — so whether a hosted model (TypeSafe Jev) or a self-hosted one (Cloudflare
Clef, Ollama's ``/v1/systemone``) answers is a configuration choice.

Confidence describes how concentrated an answer's distribution is. It is not calibrated
correctness and not permission to act; what a caller does with it is the caller's policy.

Configured backend failures must raise. An incomplete or malformed answer set is a failure, never
a partial result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

#: Question text: a plain string, or JSON structure when definitions, contrasts or examples make
#: the judgment clearer. Nested state is referenced by backticked paths such as ``observation``.
Instructions = str | Mapping[str, Any] | Sequence[Any]


@dataclass(frozen=True)
class NoulQuestion:
    """Whether a condition holds; answered with P(yes)."""

    instructions: Instructions
    #: Optional ``{"true": ..., "false": ...}`` descriptions of what counts as each answer.
    criteria: Mapping[str, str] | None = None


@dataclass(frozen=True)
class ChoiceQuestion:
    """One option of a defined set; option names map to descriptions (or None)."""

    instructions: Instructions
    options: Mapping[str, str | None]


@dataclass(frozen=True)
class ScoreQuestion:
    """A position on ordered levels; level 0 is the first description."""

    instructions: Instructions
    levels: Sequence[str]


DecisionQuestion = NoulQuestion | ChoiceQuestion | ScoreQuestion


@dataclass(frozen=True)
class NoulAnswer:
    probability: float


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    confidence: float
    probabilities: Mapping[str, float]


@dataclass(frozen=True)
class ScoreAnswer:
    #: Probability-weighted level; fractional. Threshold it, do not interpolate magnitudes.
    score: float
    confidence: float
    #: Level index -> probability.
    probabilities: Mapping[int, float]


DecisionAnswer = NoulAnswer | ChoiceAnswer | ScoreAnswer


@dataclass(frozen=True)
class DecisionResult:
    """Every requested question's answer, from one request."""

    #: The model that answered, which may differ from a requested alias.
    model: str
    answers: Mapping[str, DecisionAnswer]
    #: Billable input tokens, or None when the backend does not report usage.
    input_tokens: int | None


@runtime_checkable
class DecisionPort(Protocol):
    """Ask a decision model independent questions about one shared state."""

    async def decide(
        self,
        state: Any,
        questions: Mapping[str, DecisionQuestion],
        *,
        purpose: str,
    ) -> DecisionResult:
        """Answer every question about *state*.

        *purpose* names the call site (``resident.first_opinion``) so every judgment is
        attributable in telemetry. Question ids are for code and are not sent as meaning: each
        question's instructions must stand on their own.

        Raise when a configured backend cannot produce a complete, well-typed answer set.
        """
        ...
