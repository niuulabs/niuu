"""Dream-time curation of the learnings and candidates themselves.

Reflection records one lesson per session; left alone the record only grows.
The same lesson piles up under different wordings, a newer verified finding
sits next to the older claim it disproves, one-off speculation never confirmed
lingers forever, and promoted learnings stay in the long narrative form they
were recorded in. This pass, run from the resident consolidation dream, fixes
those in place:

* **merge** — lessons the judge calls the same are folded into one, keeping
  every observation's session and wording as provenance;
* **supersede** — of two contradicting lessons, the one with stronger evidence
  (verified, then promoted, then observations, then recency) stands and the
  other is retired with the reason recorded;
* **prune** — a single unverified observation never confirmed within
  ``prune_after_days`` is retired;
* **rewrite** — a promoted learning gains a short ``When X, do Y`` procedure,
  which is what injection puts in the prompt.

Nothing is deleted. Retired pages keep their content with a ``status`` of
``merged``, ``superseded`` or ``pruned`` and a ``curation_reason``, and every
pass that changed anything writes an audit page under ``audit_prefix``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from ravn.adapters.reflection.lessons import (
    CANDIDATE_CATEGORY,
    Lesson,
    LessonIndex,
    frontmatter_values,
    parse_lesson,
)
from ravn.adapters.reflection.post_session import (
    _CONFIDENCE_HIGH_THRESHOLD,
    _CONFIDENCE_MEDIUM_THRESHOLD,
    _append_related_observation,
    _escape_yaml,
    _insert_timeline_entry,
    _matchable,
    _promotion_reason,
    _reflection_owns,
    _set_frontmatter_value,
    evidence_is_new_by_meaning,
    promote_candidate,
    set_procedure,
)
from ravn.resident_text import compact_line, timestamp_slug

if TYPE_CHECKING:
    from niuu.ports.mimir import MimirPort
    from ravn.adapters.reflection.lesson_judge import LessonJudge
    from ravn.config import LearningCurationConfig

logger = logging.getLogger(__name__)

_ACTIONS = ("merged", "superseded", "pruned", "promoted", "rewritten")


@dataclass(frozen=True)
class CurationAction:
    action: str
    path: str
    target: str
    reason: str


@dataclass
class CurationReport:
    started_at: datetime
    lessons_reviewed: int = 0
    pairs_judged: int = 0
    actions: list[CurationAction] = field(default_factory=list)
    audit_path: str = ""
    error: str = ""

    def record(self, action: str, path: str, target: str, reason: str) -> None:
        self.actions.append(CurationAction(action, path, target, reason))

    def paths(self, action: str) -> list[str]:
        return [item.path for item in self.actions if item.action == action]

    def summary(self) -> dict:
        return {
            "lessons_reviewed": self.lessons_reviewed,
            "pairs_judged": self.pairs_judged,
            **{action: self.paths(action) for action in _ACTIONS},
            "audit_path": self.audit_path,
        }


class LearningCurator:
    """Merge, supersede, prune, and rewrite recorded lessons on a schedule."""

    def __init__(
        self,
        *,
        mimir: MimirPort,
        lessons: LessonIndex,
        judge: LessonJudge,
        config: LearningCurationConfig,
        similarity_threshold: float,
        evidence_duplicate_similarity: float,
        min_repetitions: int,
        verified_recovery_promotes: bool,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._mimir = mimir
        self._lessons = lessons
        self._judge = judge
        self._config = config
        self._similarity_threshold = similarity_threshold
        self._evidence_duplicate_similarity = evidence_duplicate_similarity
        self._min_repetitions = min_repetitions
        self._verified_recovery_promotes = verified_recovery_promotes
        self._clock = clock
        self._last_run: datetime | None = None
        # Pairs the judge already called distinct, by lesson fingerprint, so a
        # pass spends its judgment budget on pairs it has not seen yet.
        self._distinct: set[tuple[str, str]] = set()

    def due(self) -> bool:
        if self._last_run is None:
            return True
        elapsed = (self._clock() - self._last_run).total_seconds()
        return elapsed >= self._config.min_interval_seconds

    async def curate(
        self,
        *,
        max_judgments: int | None = None,
        max_rewrites: int | None = None,
    ) -> CurationReport:
        """Run one curation pass. Raises after auditing whatever it changed."""
        now = self._clock()
        self._last_run = now
        report = CurationReport(started_at=now)
        try:
            await self._run(
                report,
                now=now,
                max_judgments=self._config.max_judgments
                if max_judgments is None
                else max_judgments,
                max_rewrites=self._config.max_rewrites if max_rewrites is None else max_rewrites,
            )
        except Exception as exc:
            report.error = f"{type(exc).__name__}: {exc}"
            await self._write_audit(report)
            raise
        await self._write_audit(report)
        return report

    async def _run(
        self,
        report: CurationReport,
        *,
        now: datetime,
        max_judgments: int,
        max_rewrites: int,
    ) -> None:
        pool = [
            lesson
            for lesson in await self._lessons.load(self._mimir)
            if _matchable(lesson) and _reflection_owns(lesson.path, lesson.content)
        ]
        report.lessons_reviewed = len(pool)
        state = _PassState({lesson.path: lesson for lesson in pool})

        # Judge first, act after. Contradictions are settled only once every
        # paraphrase has been merged, so a lesson is weighed on all of its
        # evidence: judged pair by pair, a one-off negative claim could beat
        # one observation of a lesson that three sessions independently found.
        same: list[tuple[str, str, str]] = []
        contradicts: list[tuple[str, str, str]] = []
        pairs = await self._lessons.similar_pairs(pool, threshold=self._similarity_threshold)
        for left, right, _score in pairs:
            if report.pairs_judged >= max_judgments:
                break
            key = (
                min(left.fingerprint, right.fingerprint),
                max(left.fingerprint, right.fingerprint),
            )
            if key in self._distinct:
                continue
            verdict = await self._judge.relate(left, right)
            report.pairs_judged += 1
            if verdict.relation == "same":
                same.append((left.path, right.path, verdict.reason))
            elif verdict.relation == "contradicts":
                contradicts.append((left.path, right.path, verdict.reason))
            else:
                self._distinct.add(key)

        merged_into: set[str] = set()
        for left_path, right_path, reason in same:
            pair = state.pair(left_path, right_path)
            if pair is not None:
                merged_into.add(await self._merge(*pair, reason, state, report, now=now))
        for path in sorted(merged_into):
            lesson = state.current(path)
            if lesson is not None and lesson.category == CANDIDATE_CATEGORY:
                await self._maybe_promote(lesson, state, report, now=now)
        for left_path, right_path, reason in contradicts:
            pair = state.pair(left_path, right_path)
            if pair is not None:
                await self._supersede(*pair, reason, state, report)

        await self._prune(state.live, report, now=now)
        await self._rewrite(state.live, report, now=now, limit=max_rewrites)

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    async def _merge(
        self,
        left: Lesson,
        right: Lesson,
        reason: str,
        state: _PassState,
        report: CurationReport,
        *,
        now: datetime,
    ) -> str:
        """Fold the weaker of two same lessons into the stronger; return the survivor."""
        keep, retire = sorted((left, right), key=_canonical_key, reverse=True)
        content = keep.content
        for entry in _timeline_entries(retire.content):
            session_id = entry.get("session_id", "")
            if (
                not session_id
                or session_id in parse_lesson(keep.path, content, updated_at=now).session_ids
            ):
                continue
            note = entry.get("note", "")
            # The same fact re-read in another session is one observation
            # repeated, not corroboration — the guard reflection applies too.
            if not await evidence_is_new_by_meaning(
                self._lessons, content, note, threshold=self._evidence_duplicate_similarity
            ):
                continue
            content = _insert_timeline_entry(
                content,
                "  - source: curation_merge\n"
                f"    session_id: {session_id}\n"
                f"    date: {entry.get('date') or retire.last_observed.isoformat()}\n"
                f'    merged_from: "{_escape_yaml(retire.path)}"\n'
                # note last: the timeline writer inserts after the final note line.
                f'    note: "{_escape_yaml(note)}"',
            )
        content = _append_related_observation(
            content,
            title=retire.title,
            claim=retire.claim,
            session_id=", ".join(sorted(retire.session_ids)) or "unknown",
            date=retire.last_observed,
        )
        if retire.verified and not keep.verified:
            content = _set_frontmatter_value(content, "verified", "true")
            verification = frontmatter_values(retire.content).get("verification", "")
            if verification:
                content = _set_frontmatter_value(
                    content, "verification", f'"{_escape_yaml(verification)}"'
                )
        sessions = len(parse_lesson(keep.path, content, updated_at=now).session_ids)
        content = _set_frontmatter_value(content, "evidence_count", str(sessions))
        content = _set_frontmatter_value(content, "confidence", _confidence(sessions))
        await self._mimir.upsert_page(keep.path, content)

        why = f"same lesson as {keep.path}: {compact_line(reason)}"
        await self._retire(retire, status="merged", field="merged_into", target=keep.path, why=why)
        state.move(retire.path, keep.path)
        report.record("merged", retire.path, keep.path, why)
        state.live[keep.path] = parse_lesson(keep.path, content, updated_at=now)
        return keep.path

    async def _maybe_promote(
        self,
        lesson: Lesson,
        state: _PassState,
        report: CurationReport,
        *,
        now: datetime,
    ) -> None:
        reason = _promotion_reason(
            {},
            evidence_count=len(lesson.session_ids),
            min_repetitions=self._min_repetitions,
            verified_recovery=lesson.verified and self._verified_recovery_promotes,
        )
        if not reason:
            return
        learning_path = await promote_candidate(
            self._mimir,
            candidate_path=lesson.path,
            candidate_content=lesson.content,
            title=lesson.title,
            repo_slug=frontmatter_values(lesson.content).get("repo_slug", ""),
            reason=reason,
        )
        report.record("promoted", lesson.path, learning_path, reason)
        # The candidate is now represented by its learning page.
        promoted = await self._mimir.read_page(learning_path)
        state.live[learning_path] = parse_lesson(learning_path, promoted, updated_at=now)
        state.move(lesson.path, learning_path)

    # ------------------------------------------------------------------
    # Supersede
    # ------------------------------------------------------------------

    async def _supersede(
        self,
        left: Lesson,
        right: Lesson,
        reason: str,
        state: _PassState,
        report: CurationReport,
    ) -> None:
        winner, loser = sorted((left, right), key=lambda lesson: lesson.strength(), reverse=True)
        why = (
            f"contradicted by {winner.path}, which stands on {_decided_by(winner, loser)}: "
            f"{compact_line(reason)}"
        )
        await self._retire(
            loser, status="superseded", field="superseded_by", target=winner.path, why=why
        )
        state.retire(loser.path)
        report.record("superseded", loser.path, winner.path, why)

    # ------------------------------------------------------------------
    # Prune
    # ------------------------------------------------------------------

    async def _prune(
        self,
        live: dict[str, Lesson],
        report: CurationReport,
        *,
        now: datetime,
    ) -> None:
        cutoff = now - timedelta(days=self._config.prune_after_days)
        stale = [
            lesson
            for lesson in live.values()
            if lesson.category == CANDIDATE_CATEGORY
            and lesson.status == "candidate"
            and not lesson.verified
            and lesson.evidence_count <= 1
            and lesson.last_observed < cutoff
        ]
        for lesson in stale:
            why = (
                "observed once, never verified, and not confirmed again within "
                f"{self._config.prune_after_days:g} days "
                f"(last observed {lesson.last_observed.date().isoformat()})"
            )
            await self._retire(lesson, status="pruned", field="", target="", why=why)
            live.pop(lesson.path, None)
            report.record("pruned", lesson.path, "", why)

    # ------------------------------------------------------------------
    # Rewrite
    # ------------------------------------------------------------------

    async def _rewrite(
        self,
        live: dict[str, Lesson],
        report: CurationReport,
        *,
        now: datetime,
        limit: int,
    ) -> None:
        pending = sorted(
            (lesson for lesson in live.values() if lesson.is_learning and not lesson.procedure),
            key=lambda lesson: lesson.last_observed,
            reverse=True,
        )
        for lesson in pending[:limit]:
            procedure = await self._judge.procedure(
                lesson, max_chars=self._config.procedure_max_chars
            )
            content = set_procedure(lesson.content, procedure)
            content = _set_frontmatter_value(
                content, "procedure_rewritten", now.strftime("%Y-%m-%dT%H:%M:%SZ")
            )
            await self._mimir.upsert_page(lesson.path, content)
            live[lesson.path] = parse_lesson(lesson.path, content, updated_at=now)
            report.record("rewritten", lesson.path, "", procedure)

    # ------------------------------------------------------------------
    # Shared
    # ------------------------------------------------------------------

    async def _retire(
        self, lesson: Lesson, *, status: str, field: str, target: str, why: str
    ) -> None:
        content = _set_frontmatter_value(lesson.content, "status", status)
        if field:
            content = _set_frontmatter_value(content, field, f'"{_escape_yaml(target)}"')
        content = _set_frontmatter_value(content, "curation_reason", f'"{_escape_yaml(why)}"')
        if frontmatter_values(content).get("status") != status:
            raise RuntimeError(
                f"cannot retire lesson {lesson.path!r}: its page has no frontmatter "
                "this pass knows how to mark"
            )
        await self._mimir.upsert_page(lesson.path, content)

    async def _write_audit(self, report: CurationReport) -> None:
        if not report.actions and not report.error:
            return
        stamp = report.started_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        path = f"{self._config.audit_prefix.strip('/')}/{timestamp_slug(report.started_at)}.md"
        lines = [
            "---",
            f'title: "Learning curation {stamp}"',
            "type: decision",
            f"date: {stamp}",
            "---",
            "",
            f"# Learning curation — {stamp}",
            "",
            f"Lessons reviewed: {report.lessons_reviewed}. Pairs judged: {report.pairs_judged}.",
        ]
        for action in _ACTIONS:
            items = [item for item in report.actions if item.action == action]
            if not items:
                continue
            lines += ["", f"## {action.capitalize()}"]
            for item in items:
                target = f" → `{item.target}`" if item.target else ""
                lines.append(f"- `{item.path}`{target}: {item.reason}")
        if report.error:
            lines += ["", "## Error", f"Pass stopped: {report.error}"]
        await self._mimir.upsert_page(path, "\n".join(lines) + "\n")
        report.audit_path = path
        logger.info(
            "LearningCurator: %s (%s)",
            ", ".join(f"{len(report.paths(a))} {a}" for a in _ACTIONS),
            path,
        )


class _PassState:
    """The lessons still standing in one pass, and where retired ones went."""

    def __init__(self, live: dict[str, Lesson]) -> None:
        self.live = live
        self._moved: dict[str, str] = {}

    def move(self, path: str, successor: str) -> None:
        self.live.pop(path, None)
        self._moved[path] = successor

    def retire(self, path: str) -> None:
        self.live.pop(path, None)

    def current(self, path: str) -> Lesson | None:
        """The live lesson *path* now lives on as, following merges and promotions."""
        seen: set[str] = set()
        while path in self._moved and path not in seen:
            seen.add(path)
            path = self._moved[path]
        return self.live.get(path)

    def pair(self, left: str, right: str) -> tuple[Lesson, Lesson] | None:
        """Both lessons as they stand now, or None if they are gone or now one."""
        current_left, current_right = self.current(left), self.current(right)
        if current_left is None or current_right is None:
            return None
        if current_left.path == current_right.path:
            return None
        return current_left, current_right


def _timeline_entries(content: str) -> list[dict[str, str]]:
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


def _canonical_key(lesson: Lesson) -> tuple[bool, bool, int, datetime]:
    """Which of two same lessons survives a merge: the trusted page, then evidence."""
    return (lesson.is_learning, lesson.verified, lesson.evidence_count, lesson.last_observed)


def _decided_by(winner: Lesson, loser: Lesson) -> str:
    if winner.verified != loser.verified:
        return "verified evidence from a recovered tool route"
    if winner.is_learning != loser.is_learning:
        return "being a promoted learning"
    if winner.evidence_count != loser.evidence_count:
        return f"more observations ({winner.evidence_count} vs {loser.evidence_count})"
    return (
        f"newer evidence ({winner.last_observed.date().isoformat()} vs "
        f"{loser.last_observed.date().isoformat()})"
    )


def _confidence(observations: int) -> str:
    if observations >= _CONFIDENCE_HIGH_THRESHOLD:
        return "high"
    if observations >= _CONFIDENCE_MEDIUM_THRESHOLD:
        return "medium"
    return "low"
