"""Meaning-based matching of learned tools, on the lesson-matching machinery.

The same two steps that decide whether a new lesson restates a recorded one:
an embedding shortlist (cheap, says two things are *about* the same topic) and
a small-model judgment on the shortlist (says whether they are the same
*capability*). Embedding alone cannot separate ``k8s_pod_logs_since`` from
``k8s_pod_logs_previous``; the judge can.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from ravn.adapters.reflection.lesson_judge import LessonJudgeError
from ravn.adapters.reflection.lessons import cosine
from ravn.ports.capability_matching import CapabilityDescriptor, CapabilityVerdict
from ravn.resident_text import compact_line

if TYPE_CHECKING:
    from ravn.adapters.reflection.lesson_judge import LessonJudge
    from ravn.adapters.reflection.lessons import LessonIndex

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
        self._lessons = lessons
        self._judge = judge
        self._similarity_threshold = similarity_threshold
        self._max_candidates = max_candidates
        self._excerpt_chars = excerpt_chars
        # A curation pass compares every tool with every other; embedding the
        # same descriptions once per comparison would repeat that work N times.
        self._vectors: dict[str, list[float]] = {}

    async def same_capability(
        self,
        subject: CapabilityDescriptor,
        candidates: Sequence[CapabilityDescriptor],
        *,
        as_revision: bool = False,
    ) -> list[CapabilityVerdict]:
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
        shortlist = [pair for pair in scored if pair[1] >= self._similarity_threshold][
            : self._max_candidates
        ]
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

    async def _vectors_for(self, texts: list[str]) -> dict[str, list[float]]:
        missing = list(dict.fromkeys(text for text in texts if text not in self._vectors))
        if missing:
            embedded = await self._lessons.embed_texts(missing)
            self._vectors.update(zip(missing, embedded, strict=True))
        return {text: self._vectors[text] for text in texts}


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
