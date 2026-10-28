"""Small-model judgments over recorded lessons.

Embedding similarity says two lessons are *about* the same thing; it cannot say
whether they assert the same thing or opposite things ("the .diff route works
when the API returns 403" and "every GitHub route returns 403" embed close
together). These judgments make that call, one bounded model request each.

A judge that cannot answer raises :class:`LessonJudgeError`. Treating an
unparseable answer as "different" would quietly bring back the failure this
exists to fix: every paraphrase recorded as a new one-observation candidate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ravn.adapters.reflection.post_session import _parse_reflection_json
from ravn.cap_telemetry import record_cap_hit, require_complete
from ravn.domain.exceptions import OutputTruncatedError
from ravn.resident_text import compact_line

if TYPE_CHECKING:
    from ravn.adapters.reflection.lessons import Lesson
    from ravn.ports.llm import LLMPort

logger = logging.getLogger(__name__)

_JUDGE_SYSTEM = (
    "You compare operational lessons an AI agent recorded about its environment. "
    "Respond only with one valid JSON object. No markdown fences, no commentary."
)

_CLASSIFY_PROMPT = """\
An agent recorded the lesson below (the subject). For each numbered lesson \
that follows, decide how it relates to the subject:

- SAME: it states the same operational lesson — the same situation leading to \
the same action or conclusion — however it is worded, and whether it is more \
or less specific.
- CONTRADICTS: acting on one means acting against the other, for example one \
says a route is unreachable and the other says it works.
- distinct: anything else. Lessons that only share a topic are distinct.

Subject:
{subject}

Lessons:
{lessons}

Respond with a single JSON object listing lesson numbers:
{{"same": [numbers], "contradicts": [numbers], "reason": "one sentence"}}\
"""

_PROCEDURE_PROMPT = """\
Rewrite this learning as one short procedural rule a future agent can act on \
directly. Use the form "When <situation>, <action>." and optionally append \
"Because <reason>." Keep concrete names such as hosts, tools, and routes. Do not \
add anything the learning does not say. At most {max_chars} characters.

Learning:
{lesson}

Respond with a single JSON object:
{{"procedure": "When ..., ..."}}\
"""


#: What bounds a procedure rewrite: injection shows one learning in at most this
#: many characters, so a longer rule would be cut where it is used.
PROCEDURE_KNOB = "reflection.learning_entry_max_chars"


class LessonJudgeError(RuntimeError):
    """The judge model gave no usable answer."""


@dataclass(frozen=True)
class LessonVerdict:
    """How each lesson in a neighbourhood relates to one subject lesson.

    ``same`` and ``contradicts`` are 0-based indices into the lessons shown;
    every other lesson shown is distinct.
    """

    same: tuple[int, ...]
    contradicts: tuple[int, ...]
    reason: str


class LessonJudge:
    """Ask a small model whether lessons agree, conflict, or differ."""

    def __init__(
        self,
        llm: LLMPort,
        *,
        model: str,
        max_tokens: int | None,
        attempts: int,
        excerpt_chars: int,
    ) -> None:
        if attempts < 1:
            raise ValueError("LessonJudge needs at least one attempt per judgment")
        self._llm = llm
        self._model = model
        self._max_tokens = max_tokens
        self._attempts = attempts
        self._excerpt_chars = excerpt_chars

    async def classify(self, *, title: str, claim: str, lessons: list[Lesson]) -> LessonVerdict:
        """Say which of *lessons* state the same lesson as the subject, and which contradict it.

        One call for the whole neighbourhood: a lesson restated five ways is
        recognised as one in one judgment, and the caller can choose which of
        the matches survives instead of taking whichever was listed first.
        """
        if not lessons:
            return LessonVerdict(same=(), contradicts=(), reason="nothing to compare")
        listing = "\n".join(
            f"[{number}] {self._describe(lesson.title, lesson.claim)}"
            for number, lesson in enumerate(lessons, start=1)
        )
        answer = await self._ask(
            _CLASSIFY_PROMPT.format(subject=self._describe(title, claim), lessons=listing)
        )
        same = _indices(answer.get("same"), "same", len(lessons))
        contradicts = _indices(answer.get("contradicts"), "contradicts", len(lessons))
        if set(same) & set(contradicts):
            raise LessonJudgeError(
                f"judge called lessons {sorted(set(same) & set(contradicts))} both the same "
                "as the subject and contradicting it"
            )
        return LessonVerdict(
            same=same, contradicts=contradicts, reason=str(answer.get("reason") or "")
        )

    async def procedure(self, lesson: Lesson, *, max_chars: int) -> str:
        """Rewrite *lesson* as one ``When X, do Y`` rule."""
        evidence = f"\nEvidence: {lesson.evidence_count} observation(s)"
        if lesson.verified:
            evidence += ", verified by a recovered tool route"
        answer = await self._ask(
            _PROCEDURE_PROMPT.format(
                max_chars=max_chars,
                lesson=self._describe(lesson.title, lesson.claim) + evidence,
            )
        )
        procedure = " ".join(str(answer.get("procedure") or "").split())
        if not procedure.casefold().startswith("when "):
            raise LessonJudgeError(
                f"judge rewrite is not a 'When X, do Y' rule: {compact_line(procedure)!r}"
            )
        if len(procedure) > max_chars:
            message = (
                f"judge rewrite is {len(procedure)} characters; the limit is {max_chars}, "
                f"the room one injected learning has. Raise {PROCEDURE_KNOB} if rules that "
                "long are worth injecting whole"
            )
            record_cap_hit(PROCEDURE_KNOB, message)
            raise LessonJudgeError(message)
        return procedure

    async def ask_json(self, prompt: str) -> dict:
        """Ask the judge model a structured question; the answer is a JSON object."""
        return await self._ask(prompt)

    def _describe(self, title: str, claim: str) -> str:
        return f"title: {compact_line(title, limit=self._excerpt_chars)}\n" + (
            f"claim: {compact_line(claim, limit=self._excerpt_chars)}"
        )

    async def _ask(self, prompt: str) -> dict:
        last_problem = ""
        for attempt in range(1, self._attempts + 1):
            response = await self._llm.generate(
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                system=_JUDGE_SYSTEM,
                model=self._model,
                max_tokens=self._max_tokens,
            )
            try:
                require_complete(
                    response,
                    knob="reflection.matching.judge_max_tokens",
                    budget=self._max_tokens,
                    model=self._model,
                    purpose="the lesson judge",
                )
            except OutputTruncatedError as exc:
                raise LessonJudgeError(str(exc)) from exc
            raw = (response.content or "").strip()
            found, parsed = _parse_reflection_json(raw) if raw else (False, None)
            if found and isinstance(parsed, dict):
                return parsed
            last_problem = f"unusable judge answer {compact_line(raw, limit=200)!r}"
            if attempt < self._attempts:
                logger.info("LessonJudge: %s; asking again", last_problem)
        raise LessonJudgeError(
            f"{last_problem} after {self._attempts} attempt(s) with model {self._model!r}; "
            "set reflection.matching.judge_llm_alias to a model that follows JSON "
            "instructions, or reflection.matching.mode: lexical to stop judging"
        )


def _indices(raw: object, field: str, shown: int) -> tuple[int, ...]:
    """0-based indices from a judge's list of 1-based lesson numbers."""
    if raw is None:
        return ()
    items = raw if isinstance(raw, list) else [raw]
    indices: list[int] = []
    for item in items:
        if isinstance(item, bool) or not str(item).strip().isdigit():
            raise LessonJudgeError(f"judge listed a non-numeric lesson in {field!r}: {item!r}")
        number = int(str(item).strip())
        if not 1 <= number <= shown:
            raise LessonJudgeError(
                f"judge listed lesson {number} in {field!r}, but only {shown} were shown"
            )
        if number - 1 not in indices:
            indices.append(number - 1)
    return tuple(indices)
