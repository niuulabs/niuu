"""Meaning-based matching of learned tools, on the lesson-matching machinery.

Two steps: an embedding shortlist (cheap, says two things are *about* the same
topic) and a judgment on the shortlist (says whether they are the same
*capability*). Embedding alone cannot separate ``k8s_pod_logs_since`` from
``k8s_pod_logs_previous``; the judge can.

The judge is either the small model that decides whether a new lesson restates
a recorded one, or a System One decision model, which answers each candidate
with a probability.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from ravn.adapters.reflection.lesson_judge import LessonJudgeError
from ravn.adapters.reflection.lessons import cosine
from ravn.ports.capability_matching import (
    CapabilityDescriptor,
    CapabilityMatchError,
    CapabilityVerdict,
)
from ravn.ports.decision import DecisionPort, NoulAnswer, NoulQuestion
from ravn.resident_text import compact_line

if TYPE_CHECKING:
    from ravn.adapters.reflection.lesson_judge import LessonJudge
    from ravn.adapters.reflection.lessons import LessonIndex

logger = logging.getLogger(__name__)

_SAME_CAPABILITY_PROMPT = """\
An agent keeps a library of tools it built for itself. Decide which of the \
candidate tools below provide the SAME capability as the subject: a caller \
that needs the subject could use the candidate instead (or a fixed or \
extended version of it), because both answer the same question about the same \
thing. Tools that only share a topic are not the same — listing pods and \
reading pod logs are different capabilities, and so are logs since a time and \
logs of the previous container. A candidate that does strictly more than the \
subject, including everything the subject does, IS the same capability. \
Descriptions may be shortened.{revision}

Subject:
{subject}

Candidates:
{candidates}

Respond with a single JSON object:
{{"same": [<numbers of the candidates that provide the same capability>], \
"reason": "one sentence"}}\
"""

#: Added when the subject is a build request: it is what a tool's next version
#: should do, so a request that adds to a candidate belongs to that candidate.
_REVISION_CLAUSE = (
    " The subject is a request for a tool to be built. A candidate the subject "
    "only adds to (an extra output field, an extra option, a related count or "
    "summary) IS the same capability too: the subject is what that candidate's "
    "next version should do, not a new tool."
)

_DECISION_PURPOSE = "learned_tool.same_capability"

#: The shared request goes in state and each candidate in its own question: on
#: real tool pairs that found more true duplicates than both tools in state.
_REVISION_QUESTION = (
    "Is `candidate` the same capability as the tool request in `request`? The "
    "request is for a tool to be built. Same means the candidate, as it stands "
    "or as a fixed or extended next version, answers the same question about "
    "the same thing, so building a separate tool would be a duplicate."
)
_REVISION_SAME = (
    "The candidate already does this, does strictly more, or the request only "
    "adds to it: an extra output field, an extra option, a filter, or a related "
    "count or summary."
)
_STAND_IN_QUESTION = (
    "Is `candidate` the same capability as the tool in `request`? Same means a "
    "caller that needs that tool could use the candidate instead, or a fixed or "
    "extended version of it, because both answer the same question about the "
    "same thing."
)
_STAND_IN_SAME = "The candidate does this, or does strictly more and includes all of it."
_DIFFERENT = (
    "They only share a topic or a technology. Listing pods and reading pod logs "
    "are different capabilities, and so are logs since a time and logs of the "
    "previous container."
)


class _CapabilityShortlist:
    """The candidates close enough by embedding to be worth a judgment."""

    def __init__(self, *, lessons: LessonIndex, similarity_threshold: float) -> None:
        self._lessons = lessons
        self._similarity_threshold = similarity_threshold
        # A curation pass compares every tool with every other; embedding the
        # same descriptions once per comparison would repeat that work N times.
        self._vectors: dict[str, list[float]] = {}

    async def closest(
        self,
        subject: CapabilityDescriptor,
        candidates: Sequence[CapabilityDescriptor],
    ) -> list[tuple[CapabilityDescriptor, float]]:
        """Candidates at or above the threshold, closest first, with their similarity."""
        others = [candidate for candidate in candidates if candidate.key != subject.key]
        if not others:
            return []
        vectors = await self._vectors_for([subject.text, *(item.text for item in others)])
        query = vectors[subject.text]
        scored = sorted(
            ((item, cosine(query, vectors[item.text])) for item in others),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return [pair for pair in scored if pair[1] >= self._similarity_threshold]

    async def _vectors_for(self, texts: list[str]) -> dict[str, list[float]]:
        missing = list(dict.fromkeys(text for text in texts if text not in self._vectors))
        if missing:
            embedded = await self._lessons.embed_texts(missing)
            self._vectors.update(zip(missing, embedded, strict=True))
        return {text: self._vectors[text] for text in texts}


class SemanticCapabilityMatcher:
    """Shortlist candidate tools by embedding, then ask the lesson judge."""

    def __init__(
        self,
        *,
        lessons: LessonIndex,
        judge: LessonJudge,
        similarity_threshold: float,
        max_candidates: int,
        excerpt_chars: int,
    ) -> None:
        if max_candidates < 1:
            raise ValueError("SemanticCapabilityMatcher needs max_candidates >= 1")
        self._shortlist = _CapabilityShortlist(
            lessons=lessons, similarity_threshold=similarity_threshold
        )
        self._judge = judge
        self._max_candidates = max_candidates
        self._excerpt_chars = excerpt_chars

    async def same_capability(
        self,
        subject: CapabilityDescriptor,
        candidates: Sequence[CapabilityDescriptor],
        *,
        as_revision: bool = False,
    ) -> list[CapabilityVerdict]:
        shortlist = (await self._shortlist.closest(subject, candidates))[: self._max_candidates]
        if not shortlist:
            return []
        listing = "\n".join(
            f"[{number}] {self._describe(item)}" for number, (item, _) in enumerate(shortlist, 1)
        )
        answer = await self._judge.ask_json(
            _SAME_CAPABILITY_PROMPT.format(
                subject=self._describe(subject),
                candidates=listing,
                revision=_REVISION_CLAUSE if as_revision else "",
            )
        )
        chosen = _chosen_numbers(answer.get("same"), shown=len(shortlist))
        reason = str(answer.get("reason") or "")
        return [
            CapabilityVerdict(
                candidate_key=item.key,
                same=number in chosen,
                similarity=round(score, 4),
                reason=reason,
            )
            for number, (item, score) in enumerate(shortlist, 1)
        ]

    def _describe(self, item: CapabilityDescriptor) -> str:
        inputs = ", ".join(item.inputs) if item.inputs else "none"
        return (
            f"name: {item.name}\n"
            f"description: {compact_line(item.description, limit=self._excerpt_chars)}\n"
            f"inputs: {inputs}"
        )


class SystemOneCapabilityMatcher:
    """Shortlist candidate tools by embedding, then ask a decision model about each one.

    One yes/no question per candidate, in one request, so every shortlisted tool
    gets its own probability. A decision that fails raises
    :class:`CapabilityMatchError`; it never reads as "no match".
    """

    def __init__(
        self,
        *,
        lessons: LessonIndex,
        decisions: DecisionPort,
        similarity_threshold: float,
        min_same_probability: float,
        excerpt_chars: int,
    ) -> None:
        self._shortlist = _CapabilityShortlist(
            lessons=lessons, similarity_threshold=similarity_threshold
        )
        self._decisions = decisions
        self._min_same_probability = min_same_probability
        self._excerpt_chars = excerpt_chars

    async def same_capability(
        self,
        subject: CapabilityDescriptor,
        candidates: Sequence[CapabilityDescriptor],
        *,
        as_revision: bool = False,
    ) -> list[CapabilityVerdict]:
        shortlist = await self._shortlist.closest(subject, candidates)
        if not shortlist:
            return []
        questions = {
            f"candidate_{number}": self._question(item, as_revision=as_revision)
            for number, (item, _) in enumerate(shortlist, 1)
        }
        try:
            result = await self._decisions.decide(
                {"request": self._view(subject)}, questions, purpose=_DECISION_PURPOSE
            )
        except Exception as exc:
            raise CapabilityMatchError(f"decision model could not answer: {exc}") from exc
        verdicts = []
        for question_id, (item, score) in zip(questions, shortlist, strict=True):
            answer = result.answers[question_id]
            if not isinstance(answer, NoulAnswer):
                raise CapabilityMatchError(f"decision model mistyped {question_id}: {answer!r}")
            verdicts.append(
                CapabilityVerdict(
                    candidate_key=item.key,
                    same=answer.probability >= self._min_same_probability,
                    similarity=round(score, 4),
                    reason=(
                        f"{result.model} put P(same capability) at {answer.probability:.2f}; "
                        f"the bar is {self._min_same_probability:.2f}"
                    ),
                    probability=answer.probability,
                )
            )
        # Which tool each answer was about: the decision log only has question ids,
        # and this line is what calibrates the bar.
        logger.info(
            "capability match for %r (bar %.2f): %s",
            subject.name,
            self._min_same_probability,
            " ".join(
                f"{item.name}={verdict.probability:.2f}"
                for (item, _), verdict in zip(shortlist, verdicts, strict=True)
            ),
        )
        return verdicts

    def _question(self, item: CapabilityDescriptor, *, as_revision: bool) -> NoulQuestion:
        return NoulQuestion(
            instructions={
                "question": _REVISION_QUESTION if as_revision else _STAND_IN_QUESTION,
                "candidate": self._view(item),
            },
            criteria={
                "true": _REVISION_SAME if as_revision else _STAND_IN_SAME,
                "false": _DIFFERENT,
            },
        )

    def _view(self, item: CapabilityDescriptor) -> dict[str, Any]:
        return {
            "name": item.name,
            "description": compact_line(item.description, limit=self._excerpt_chars),
            "inputs": list(item.inputs),
        }


def _chosen_numbers(raw: object, *, shown: int) -> set[int]:
    if raw is None:
        return set()
    if not isinstance(raw, list):
        raise LessonJudgeError(f"judge answered 'same' with {raw!r}, not a list of numbers")
    chosen: set[int] = set()
    for item in raw:
        if isinstance(item, bool) or not str(item).strip().isdigit():
            raise LessonJudgeError(f"judge named a non-numeric candidate: {item!r}")
        number = int(str(item).strip())
        if not 1 <= number <= shown:
            raise LessonJudgeError(f"judge named candidate {number}, but only {shown} were shown")
        chosen.add(number)
    return chosen
