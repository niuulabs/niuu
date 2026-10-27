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

The pass is incremental. Each lesson's neighbourhood — its nearest learnings
and nearest candidates — is judged once, in one model call that names every
lesson saying the same thing and every one contradicting it; the lesson is
then marked ``curated`` and never judged again. Work therefore grows with new
lessons, not with the square of all of them. (A fixed budget of pairs per day is
what left noatun with dozens of duplicate learnings: the backlog only ever grew.)

A large backlog still takes many hours on a shared judge model, longer than the
gap between rollouts. A pass that could only finish all-or-nothing never
finished: valhalla's resident judged 158 lessons in ten hours, was restarted,
and showed "dreaming" with no result for a day. So the dream gives the pass a
deadline, the time its next dream is due. Judging stops there; what was judged
is settled and the rest waits for the next dream, which is reported and counted
as a cap hit rather than read as "nothing left". A lesson is marked
``curated`` only after the contradictions and promotions its judgment found are
settled, so a pass cut off by a restart leaves its lessons uncurated to be
judged again, instead of marked done with their verdicts lost.

Nothing is deleted. Retired pages keep their content with a ``status`` of
``merged``, ``superseded`` or ``pruned`` and a ``curation_reason``, and every
pass that changed anything writes an audit page under ``audit_prefix``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from ravn.adapters.reflection.lesson_consolidation import (
    canonical_key,
    fold_into,
    is_curated,
    judge_neighbourhood,
    mark_curated,
    retire,
)
from ravn.adapters.reflection.lessons import (
    CANDIDATE_CATEGORY,
    Lesson,
    LessonIndex,
    frontmatter_values,
    parse_lesson,
)
from ravn.adapters.reflection.post_session import (
    _matchable,
    _promotion_reason,
    _reflection_owns,
    _set_frontmatter_value,
    promote_candidate,
    set_procedure,
)
from ravn.cap_telemetry import record_cap_hit
from ravn.resident_text import compact_line, timestamp_slug

if TYPE_CHECKING:
    from niuu.ports.mimir import MimirPort
    from ravn.adapters.reflection.lesson_judge import LessonJudge
    from ravn.config import LearningCurationConfig

logger = logging.getLogger(__name__)

_ACTIONS = ("merged", "superseded", "pruned", "promoted", "rewritten")
#: The knob that bounds a pass: it runs until the resident's next dream is due.
DEADLINE_KNOB = "resident_wakefulness.dream_interval_seconds"


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
    neighbourhoods_judged: int = 0
    #: Uncurated lessons left unjudged, and promoted learnings left without a
    #: procedure, when the pass reached its deadline. The next dream continues.
    lessons_remaining: int = 0
    rewrites_remaining: int = 0
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
            "neighbourhoods_judged": self.neighbourhoods_judged,
            "lessons_remaining": self.lessons_remaining,
            "rewrites_remaining": self.rewrites_remaining,
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
        max_related: int,
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
        self._max_related = max_related
        self._evidence_duplicate_similarity = evidence_duplicate_similarity
        self._min_repetitions = min_repetitions
        self._verified_recovery_promotes = verified_recovery_promotes
        self._clock = clock
        self._last_run: datetime | None = None

    def due(self) -> bool:
        if self._last_run is None:
            return True
        elapsed = (self._clock() - self._last_run).total_seconds()
        return elapsed >= self._config.min_interval_seconds

    async def curate(self, *, budget_seconds: float | None = None) -> CurationReport:
        """Run one curation pass. Raises after auditing whatever it changed.

        With *budget_seconds*, no lesson is judged and no procedure rewritten
        once that long has passed; the report says how many are left for the
        next pass. Without it the pass runs until the backlog is empty.
        """
        now = self._clock()
        self._last_run = now
        report = CurationReport(started_at=now)
        deadline = None if budget_seconds is None else now + timedelta(seconds=budget_seconds)
        try:
            await self._run(report, now=now, deadline=deadline)
        except Exception as exc:
            report.error = f"{type(exc).__name__}: {exc}"
            await self._write_audit(report)
            raise
        await self._write_audit(report)
        return report

    def _past(self, deadline: datetime | None) -> bool:
        return deadline is not None and self._clock() >= deadline

    async def _run(
        self, report: CurationReport, *, now: datetime, deadline: datetime | None
    ) -> None:
        pool = [
            lesson
            for lesson in await self._lessons.load(self._mimir)
            if _matchable(lesson) and _reflection_owns(lesson.path, lesson.content)
        ]
        report.lessons_reviewed = len(pool)
        state = _PassState({lesson.path: lesson for lesson in pool})

        # Strongest first, so the lessons most likely to survive are the ones
        # whose neighbourhoods are judged, and weaker paraphrases fold into them.
        fresh = sorted(
            (lesson for lesson in pool if not is_curated(lesson)), key=canonical_key, reverse=True
        )
        merged_into: set[str] = set()
        contradictions: list[tuple[str, str, str]] = []
        judged: list[str] = []
        for index, lesson in enumerate(fresh):
            if self._past(deadline):
                report.lessons_remaining = sum(
                    1 for rest in fresh[index:] if rest.path in state.live
                )
                break
            subject = state.live.get(lesson.path)
            if subject is None:  # already folded into a lesson judged earlier
                continue
            others = [other for other in state.live.values() if other.path != subject.path]
            verdict = await judge_neighbourhood(
                lessons=self._lessons,
                judge=self._judge,
                title=subject.title,
                claim=subject.claim,
                pool=others,
                threshold=self._similarity_threshold,
                per_category=self._max_related,
            )
            report.neighbourhoods_judged += verdict.judged
            survivor = subject
            if verdict.same:
                survivor = await self._merge_group(
                    [subject, *verdict.same], verdict.reason, state, report, now=now
                )
                merged_into.add(survivor.path)
            contradictions.extend(
                (survivor.path, other.path, verdict.reason) for other in verdict.contradicts
            )
            judged.append(survivor.path)

        # Promote after every merge, so promotion weighs all the evidence and a
        # candidate never promotes beside a learning that says the same thing.
        for path in sorted(merged_into):
            lesson = state.current(path)
            if lesson is not None and lesson.category == CANDIDATE_CATEGORY:
                await self._maybe_promote(lesson, state, report, now=now)
        # Settle contradictions last, between the survivors, on all their evidence.
        # A pass cut off by its deadline has not merged the paraphrases of the
        # lessons it did not reach, so a contradiction with one of those waits:
        # the judged side stays uncurated and the next pass finds it again.
        judged_now = {lesson.path for path in judged if (lesson := state.current(path))}
        held: set[str] = set()
        for left_path, right_path, reason in contradictions:
            other = state.current(right_path)
            if other is not None and not is_curated(other) and other.path not in judged_now:
                held.add(left_path)
                continue
            pair = state.pair(left_path, right_path)
            if pair is not None:
                await self._supersede(*pair, reason, state, report)
        report.lessons_remaining += len(held)
        # Only now, with every verdict acted on, is a judged lesson done.
        for path in judged:
            lesson = state.current(path)
            if lesson is None or path in held or is_curated(lesson):
                continue
            state.live[lesson.path] = await mark_curated(self._mimir, lesson, now=now)

        await self._prune(state.live, report, now=now)
        await self._rewrite(state.live, report, now=now, deadline=deadline)
        self._report_deferred(report)

    def _report_deferred(self, report: CurationReport) -> None:
        """A pass that stopped at its deadline holds work back: say so and count it."""
        if not report.lessons_remaining and not report.rewrites_remaining:
            return
        record_cap_hit(
            DEADLINE_KNOB,
            f"learning curation reached its deadline with {report.lessons_remaining} "
            f"uncurated lesson(s) unjudged and {report.rewrites_remaining} learning(s) "
            f"without a procedure after judging {report.neighbourhoods_judged} "
            "neighbourhood(s); the next consolidation dream continues them. A backlog "
            "that does not shrink across dreams needs more judge throughput "
            f"(the model behind reflection.matching.judge_llm_alias) or a longer "
            f"{DEADLINE_KNOB}.",
        )

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    async def _merge_group(
        self,
        group: list[Lesson],
        reason: str,
        state: _PassState,
        report: CurationReport,
        *,
        now: datetime,
    ) -> Lesson:
        """Fold every lesson in *group* into the strongest one; return the survivor."""
        keep, *others = sorted(group, key=canonical_key, reverse=True)
        why = f"same lesson as {keep.path}: {compact_line(reason)}"
        for other in others:
            keep = await fold_into(
                self._mimir,
                self._lessons,
                keep=keep,
                other=other,
                now=now,
                evidence_duplicate_similarity=self._evidence_duplicate_similarity,
                source="curation_merge",
            )
            await retire(
                self._mimir, other, status="merged", field="merged_into", target=keep.path, why=why
            )
            state.move(other.path, keep.path)
            report.record("merged", other.path, keep.path, why)
        state.live[keep.path] = keep
        return keep

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
        await retire(
            self._mimir,
            loser,
            status="superseded",
            field="superseded_by",
            target=winner.path,
            why=why,
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
            await retire(self._mimir, lesson, status="pruned", field="", target="", why=why)
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
        deadline: datetime | None,
    ) -> None:
        """Give every promoted learning without one a procedure; each is rewritten once."""
        pending = sorted(
            (lesson for lesson in live.values() if lesson.is_learning and not lesson.procedure),
            key=lambda lesson: lesson.last_observed,
            reverse=True,
        )
        for index, lesson in enumerate(pending):
            if self._past(deadline):
                report.rewrites_remaining = len(pending) - index
                return
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
    # Audit
    # ------------------------------------------------------------------

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
            f"Lessons reviewed: {report.lessons_reviewed}. "
            f"Neighbourhoods judged: {report.neighbourhoods_judged}.",
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
