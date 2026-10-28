"""Consolidating lessons: one neighbourhood judgment, one survivor.

Reflection (on write) and curation (in the dream) both answer the same
question about a lesson — which recorded lessons say the same thing, and which
contradict it — and both act on the answer the same way: every lesson that says
the same thing folds into one survivor. This module is that shared mechanism.

The survivor is chosen, never taken from whichever match happened to be listed
first: a promoted learning beats a candidate, then verified evidence, then more
observations, then recency. Letting a paraphrase feed a sibling *candidate*
while an equivalent learning already existed is how residents ended up with
seven learnings for "inspect_pod 404 → check the owning workload".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from ravn.adapters.reflection.lessons import Lesson, frontmatter_values, parse_lesson
from ravn.adapters.reflection.post_session import (
    _CONFIDENCE_HIGH_THRESHOLD,
    _CONFIDENCE_MEDIUM_THRESHOLD,
    _append_related_observation,
    _escape_yaml,
    _insert_timeline_entry,
    _set_frontmatter_value,
    evidence_is_new_by_meaning,
)

if TYPE_CHECKING:
    from niuu.ports.mimir import MimirPort
    from ravn.adapters.reflection.lesson_judge import LessonJudge
    from ravn.adapters.reflection.lessons import LessonIndex

#: Frontmatter key marking a lesson whose neighbourhood the dream has judged.
CURATED_KEY = "curated"


@dataclass(frozen=True)
class Neighbourhood:
    """The lessons one subject was judged against, and the judge's verdict."""

    same: list[Lesson]
    contradicts: list[Lesson]
    reason: str
    judged: bool


async def judge_neighbourhood(
    *,
    lessons: LessonIndex,
    judge: LessonJudge,
    title: str,
    claim: str,
    pool: list[Lesson],
    threshold: float,
    per_category: int,
) -> Neighbourhood:
    """Judge one lesson against its nearest recorded lessons.

    The nearest learnings and the nearest candidates above *threshold* are
    shortlisted separately, *per_category* of each, so the learnings a lesson
    most likely restates are always in front of the judge.
    """
    if not pool:
        return Neighbourhood(same=[], contradicts=[], reason="nothing recorded", judged=False)
    ranked = await lessons.rank(f"{title}. {claim}".strip(), pool)
    above = [lesson for lesson, score in ranked if score >= threshold]
    shown = [lesson for lesson in above if lesson.is_learning][:per_category] + [
        lesson for lesson in above if not lesson.is_learning
    ][:per_category]
    if not shown:
        return Neighbourhood(same=[], contradicts=[], reason="no similar lesson", judged=False)
    verdict = await judge.classify(title=title, claim=claim, lessons=shown)
    return Neighbourhood(
        same=[shown[index] for index in verdict.same],
        contradicts=[shown[index] for index in verdict.contradicts],
        reason=verdict.reason,
        judged=True,
    )


def canonical_key(lesson: Lesson) -> tuple[bool, bool, int, datetime]:
    """Which of several same lessons survives: the trusted page, then evidence."""
    return (lesson.is_learning, lesson.verified, lesson.evidence_count, lesson.last_observed)


async def fold_into(
    mimir: MimirPort,
    lessons: LessonIndex,
    *,
    keep: Lesson,
    other: Lesson,
    now: datetime,
    evidence_duplicate_similarity: float,
    source: str,
    record_origin: bool = True,
) -> Lesson:
    """Fold *other*'s observations into *keep* and write *keep*; return it re-read.

    Each of *other*'s sessions becomes a timeline entry on *keep* unless its
    note restates one already there by meaning (the same fact re-read is one
    observation, not corroboration). *other*'s wording is kept under
    ``## Related observations``, and its verification carries over. *other*
    itself is not touched here; the caller retires it, or never wrote it —
    a fresh observation folded on write has no page, so *record_origin* is
    false and its entries carry no ``merged_from``.
    """
    content = keep.content
    for entry in timeline_entries(other.content):
        session_id = entry.get("session_id", "")
        if (
            not session_id
            or session_id in parse_lesson(keep.path, content, updated_at=now).session_ids
        ):
            continue
        note = entry.get("note", "")
        if not await evidence_is_new_by_meaning(
            lessons, content, note, threshold=evidence_duplicate_similarity
        ):
            continue
        origin = f'    merged_from: "{_escape_yaml(other.path)}"\n' if record_origin else ""
        content = _insert_timeline_entry(
            content,
            f"  - source: {source}\n"
            f"    session_id: {session_id}\n"
            f"    date: {entry.get('date') or other.last_observed.isoformat()}\n"
            f"{origin}"
            # note last: the timeline writer inserts after the final note line.
            f'    note: "{_escape_yaml(note)}"',
        )
    content = _append_related_observation(
        content,
        title=other.title,
        claim=other.claim,
        session_id=", ".join(sorted(other.session_ids)) or "unknown",
        date=other.last_observed,
    )
    if other.verified and not keep.verified:
        content = _set_frontmatter_value(content, "verified", "true")
        verification = frontmatter_values(other.content).get("verification", "")
        if verification:
            content = _set_frontmatter_value(
                content, "verification", f'"{_escape_yaml(verification)}"'
            )
    sessions = len(parse_lesson(keep.path, content, updated_at=now).session_ids)
    content = _set_frontmatter_value(content, "evidence_count", str(sessions))
    content = _set_frontmatter_value(content, "confidence", _confidence(sessions))
    if keep.is_learning:
        content = _set_frontmatter_value(
            content, "last_confirmed", now.strftime("%Y-%m-%dT%H:%M:%SZ")
        )
    await mimir.upsert_page(keep.path, content)
    return parse_lesson(keep.path, content, updated_at=now)


async def retire(
    mimir: MimirPort,
    lesson: Lesson,
    *,
    status: str,
    field: str,
    target: str,
    why: str,
) -> None:
    """Mark *lesson* retired (merged, superseded, pruned) with the reason; raise if unmarkable."""
    content = _set_frontmatter_value(lesson.content, "status", status)
    if field:
        content = _set_frontmatter_value(content, field, f'"{_escape_yaml(target)}"')
    content = _set_frontmatter_value(content, "curation_reason", f'"{_escape_yaml(why)}"')
    if frontmatter_values(content).get("status") != status:
        raise RuntimeError(
            f"cannot retire lesson {lesson.path!r}: its page has no frontmatter "
            "this pass knows how to mark"
        )
    await mimir.upsert_page(lesson.path, content)


async def mark_curated(mimir: MimirPort, lesson: Lesson, *, now: datetime) -> Lesson:
    """Record that *lesson*'s neighbourhood was judged, so no later pass judges it again."""
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    content = _set_frontmatter_value(lesson.content, CURATED_KEY, stamp)
    if frontmatter_values(content).get(CURATED_KEY) != stamp:
        raise RuntimeError(
            f"cannot mark lesson {lesson.path!r} curated: its page has no frontmatter "
            "timeline to record it on, so every pass would judge it again"
        )
    await mimir.upsert_page(lesson.path, content)
    return parse_lesson(lesson.path, content, updated_at=now)


def is_curated(lesson: Lesson) -> bool:
    return bool(frontmatter_values(lesson.content).get(CURATED_KEY))


def timeline_entries(content: str) -> list[dict[str, str]]:
    """Parse the frontmatter ``timeline`` list into one dict per entry."""
    end = content.find("\n---", 3) if content.startswith("---") else -1
    frontmatter = content[:end] if end != -1 else ""
    entries: list[dict[str, str]] = []
    for line in frontmatter.splitlines():
        item = re.match(r"^  - (\w+):\s*(.*)$", line)
        if item is not None:
            entries.append({item.group(1): _unquote(item.group(2))})
            continue
        field_match = re.match(r"^    (\w+):\s*(.*)$", line)
        if field_match is not None and entries:
            entries[-1][field_match.group(1)] = _unquote(field_match.group(2))
    return entries


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('\\"', '"')
    return value


def _confidence(observations: int) -> str:
    if observations >= _CONFIDENCE_HIGH_THRESHOLD:
        return "high"
    if observations >= _CONFIDENCE_MEDIUM_THRESHOLD:
        return "medium"
    return "low"
