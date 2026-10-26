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
from typing import TYPE_CHECKING, Literal

from ravn.adapters.reflection.post_session import _parse_reflection_json
from ravn.cap_telemetry import require_complete
from ravn.domain.exceptions import OutputTruncatedError
from ravn.resident_text import compact_line

if TYPE_CHECKING:
    from ravn.adapters.reflection.lessons import Lesson
    from ravn.ports.llm import LLMPort

logger = logging.getLogger(__name__)

Relation = Literal["same", "contradicts", "distinct"]
_RELATIONS: frozenset[str] = frozenset({"same", "contradicts", "distinct"})

_JUDGE_SYSTEM = (
    "You compare operational lessons an AI agent recorded about its environment. "
    "Respond only with one valid JSON object. No markdown fences, no commentary."
)

_MATCH_PROMPT = """\
An agent just recorded a possible lesson. Decide whether one of the already \
recorded lessons below states the SAME operational lesson: the same situation \
leading to the same action or conclusion, even when it is worded differently \
or is more or less specific. Lessons that only share a topic are not the same, \
and a lesson that contradicts the new one is not the same.

New lesson:
{new}

Recorded lessons:
{recorded}

Respond with a single JSON object:
{{"same": <number of the recorded lesson that states the same lesson, or null>, \
"reason": "one sentence"}}\
"""

_RELATE_PROMPT = """\
Compare two lessons an agent recorded about the same environment.

- "same": they state the same operational lesson (same situation, same action \
or conclusion), however worded.
- "contradicts": acting on one means acting against the other, for example one \
says a route is unreachable and the other says it works.
- "distinct": anything else.

A:
{left}

B:
{right}

Respond with a single JSON object:
{{"relation": "same" | "contradicts" | "distinct", "reason": "one sentence"}}\
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


class LessonJudgeError(RuntimeError):
    """The judge model gave no usable answer."""


@dataclass(frozen=True)
class LessonMatch:
    """Which shortlisted lesson, if any, states the same lesson as a new one."""

    index: int | None
    reason: str


@dataclass(frozen=True)
class LessonRelation:
    relation: Relation
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

    async def match(self, *, title: str, claim: str, recorded: list[Lesson]) -> LessonMatch:
        """Return the 0-based index into *recorded* stating the same lesson, if any."""
        if not recorded:
            return LessonMatch(index=None, reason="nothing recorded to compare")
        listing = "\n".join(
            f"[{number}] {self._describe(lesson.title, lesson.claim)}"
            for number, lesson in enumerate(recorded, start=1)
        )
        answer = await self._ask(
            _MATCH_PROMPT.format(new=self._describe(title, claim), recorded=listing)
        )
        raw = answer.get("same")
        reason = str(answer.get("reason") or "")
        if raw is None:
            return LessonMatch(index=None, reason=reason)
        if isinstance(raw, bool) or not isinstance(raw, int | str) or not str(raw).isdigit():
            raise LessonJudgeError(f"judge named a non-numeric match: {raw!r}")
        number = int(raw)
        if not 1 <= number <= len(recorded):
            raise LessonJudgeError(
                f"judge named recorded lesson {number}, but only {len(recorded)} were shown"
            )
        return LessonMatch(index=number - 1, reason=reason)

    async def relate(self, left: Lesson, right: Lesson) -> LessonRelation:
        """Classify two lessons as the same, contradictory, or distinct."""
        answer = await self._ask(
            _RELATE_PROMPT.format(
                left=self._describe(left.title, left.claim),
                right=self._describe(right.title, right.claim),
            )
        )
        relation = str(answer.get("relation") or "").strip().casefold()
        if relation not in _RELATIONS:
            raise LessonJudgeError(f"judge returned unknown relation {relation!r}")
        return LessonRelation(relation=relation, reason=str(answer.get("reason") or ""))  # type: ignore[arg-type]

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
            raise LessonJudgeError(
                f"judge rewrite is {len(procedure)} characters; the limit is {max_chars}"
            )
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
