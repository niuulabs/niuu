"""The resident acting on its own friction, without being asked.

Learning decides *what* a resident has learned; this pass decides what to *do*
about the walls it keeps hitting. It runs in the consolidation dream right
after learning curation, and each run:

1. **Judges past improvements.** An action is ineffective when its friction
   came back ``recurrence_threshold`` times inside the evaluation window (for
   escalations: when the operator was still asked despite the resolution). An
   ineffective learning is demoted and the action is never retried for that
   friction. An action whose friction stayed quiet for the whole window is
   effective, and that is recorded as evidence on the learning it relied on.
2. **Adopts what was learned.** A friction linked to a promoted learning is
   addressed by that learning; the resident runtime will now answer an
   escalation about it with the learning instead of asking the operator.
3. **Chooses at most ``max_actions`` improvements** for recurring friction that
   nothing addresses yet, each with an explicit expected benefit: promote or
   rewrite a learning, retire a belief newer evidence contradicts, commission a
   learned tool, or file a platform-defect ticket.

Learning actions use Ravn's own evidence gates. A tool commission or a ticket
is dispatched as an ordinary resident task: it runs with the persona's tool
permissions, through ``build_tool``'s autonomy and review gates and the
persona's branch rule, exactly like any other turn. This pass holds no
privilege of its own. Every run that does anything writes a short "what I
improved and why" page, and every step emits telemetry.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from niuu.observability import get_observability
from ravn.adapters.reflection.friction import (
    ADDRESSED,
    OPEN,
    RESOLVED,
    FrictionRecord,
)
from ravn.adapters.reflection.lesson_judge import LessonJudgeError
from ravn.adapters.reflection.lessons import (
    CANDIDATE_CATEGORY,
    Lesson,
    frontmatter_values,
)
from ravn.adapters.reflection.post_session import (
    _escape_yaml,
    _insert_timeline_entry,
    _reflection_owns,
    _reflection_writes_at,
    _set_frontmatter_value,
    promote_candidate,
    set_procedure,
)
from ravn.domain.models import AgentTask, OutputMode
from ravn.resident_text import compact_line, timestamp_slug

if TYPE_CHECKING:
    from niuu.ports.mimir import MimirPort
    from ravn.adapters.reflection.friction import FrictionLedger
    from ravn.adapters.reflection.lesson_judge import LessonJudge
    from ravn.adapters.reflection.lessons import LessonIndex
    from ravn.config import SelfImprovementConfig

logger = logging.getLogger(__name__)

Dispatch = Callable[[AgentTask], Awaitable[bool | None]]

#: Actions whose effect lives on a learning page; demoted when they do not help.
_LEARNING_ACTIONS = frozenset({"learned", "promote_learning", "rewrite_learning"})
_DISPATCHED_ACTIONS = frozenset({"commission_tool", "file_defect"})

_PLANNER_PROMPT = """\
You are {resident}, a resident agent, reviewing friction you keep hitting in \
your own work. Choose at most {max_actions} improvements to make to yourself. \
Choose an improvement only when you can state a concrete expected benefit; \
choosing nothing is a valid answer.

Allowed actions:
{allowed}

Recurring friction:
{friction}

Lessons linked to that friction:
{lessons}

Respond with a single JSON object:
{{"actions": [{{
  "friction": "F<number>",
  "action": one of the allowed actions,
  "lesson": "L<number>" for promote_learning, rewrite_learning and retire_belief, else null,
  "need": "the recurring need, for commission_tool",
  "title": "ticket title, for file_defect",
  "body": "ticket description with the evidence, for file_defect",
  "expected_benefit": "what will stop happening, concretely",
  "rationale": "one sentence on why this action and not another"
}}]}}\
"""

_ACTION_HELP = {
    "promote_learning": (
        "promote_learning — promote a linked candidate lesson to a trusted learning, "
        "because the friction it prevents keeps recurring"
    ),
    "rewrite_learning": (
        "rewrite_learning — rewrite a linked learning as a short 'When X, do Y' rule "
        "the next turn can act on"
    ),
    "retire_belief": (
        "retire_belief — retire a stale belief or capability-gap claim that newer "
        "evidence contradicts"
    ),
    "commission_tool": (
        "commission_tool — commission a learned tool with build_tool for a recurring "
        "need no existing capability covers"
    ),
    "file_defect": (
        "file_defect — file a platform-defect ticket when the platform itself is at "
        "fault and you cannot fix it from your own capabilities"
    ),
}


@dataclass
class ImprovementReport:
    started_at: datetime
    evaluated: list[dict[str, str]] = field(default_factory=list)
    actions: list[dict[str, str]] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)
    open_friction: list[str] = field(default_factory=list)
    audit_path: str = ""
    error: str = ""

    def summary(self) -> dict[str, Any]:
        return {
            "evaluated": [f"{e['ref']}:{e['verdict']}" for e in self.evaluated],
            "actions": [f"{a['kind']}:{a['ref']}" for a in self.actions],
            "rejected": len(self.rejected),
            "open_friction": len(self.open_friction),
            "audit_path": self.audit_path,
        }


class SelfImprovementPass:
    """Evaluate, adopt, choose, and act on recurring friction."""

    def __init__(
        self,
        *,
        mimir: MimirPort,
        ledger: FrictionLedger,
        lessons: LessonIndex,
        judge: LessonJudge,
        config: SelfImprovementConfig,
        procedure_max_chars: int,
        resident_id: str,
        dispatch: Dispatch | None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if dispatch is None and _DISPATCHED_ACTIONS & set(config.allowed_actions):
            raise ValueError(
                "reflection.self_improvement allows commission_tool/file_defect but no "
                "drive loop is available to dispatch them; run the resident with its drive "
                "loop, or remove those actions from self_improvement.allowed_actions"
            )
        self._mimir = mimir
        self._ledger = ledger
        self._lessons = lessons
        self._judge = judge
        self._config = config
        self._procedure_max_chars = procedure_max_chars
        self._resident_id = resident_id
        self._dispatch = dispatch
        self._clock = clock

    async def run(self) -> ImprovementReport:
        """One pass. Raises after writing whatever it did to the brief."""
        now = self._clock()
        report = ImprovementReport(started_at=now)
        try:
            records = await self._ledger.load()
            by_path = {lesson.path: lesson for lesson in await self._lessons.load(self._mimir)}
            await self._evaluate(records, by_path, report, now=now)
            await self._adopt_learned(records, by_path, report, now=now)
            await self._plan_and_act(records, by_path, report, now=now)
        except Exception as exc:
            report.error = f"{type(exc).__name__}: {exc}"
            await self._write_brief(report)
            raise
        await self._write_brief(report)
        return report

    # ------------------------------------------------------------------
    # 1. Did earlier improvements help?
    # ------------------------------------------------------------------

    async def _evaluate(
        self,
        records: list[FrictionRecord],
        by_path: dict[str, Lesson],
        report: ImprovementReport,
        *,
        now: datetime,
    ) -> None:
        window = self._config.evaluation_window_seconds
        for record in records:
            changed = False
            for action in record.actions:
                if action.get("status") != "pending":
                    continue
                acted_at = action.get("acted_at", "")
                recurrences = record.recurrences_since(acted_at)
                if recurrences >= self._config.recurrence_threshold:
                    verdict = "ineffective"
                    reason = (
                        f"the friction came back {recurrences} time(s) after "
                        f"{action.get('kind')} on {acted_at[:10]}"
                    )
                    record.status = OPEN
                    if action.get("kind") in _LEARNING_ACTIONS:
                        await self._demote(action.get("target", ""), by_path, reason)
                elif (now - _parse(acted_at)).total_seconds() >= window:
                    verdict = "effective"
                    reason = (
                        f"no recurrence in {window / 86400:g} day(s) after {action.get('kind')}"
                    )
                    record.status = RESOLVED
                    await self._credit(action, record, by_path, reason, now=now)
                else:
                    continue
                action["status"] = verdict
                action["evaluated_at"] = now.isoformat()
                action["evaluation"] = reason
                changed = True
                report.evaluated.append(
                    {
                        "ref": record.path,
                        "kind": action.get("kind", ""),
                        "verdict": verdict,
                        "reason": reason,
                    }
                )
                _telemetry_count(
                    "ravn.self_improvement.evaluated", verdict=verdict, kind=action.get("kind", "")
                )
            if changed:
                await self._ledger.save(record)

    async def _demote(self, path: str, by_path: dict[str, Lesson], reason: str) -> None:
        lesson = by_path.get(path)
        if lesson is None or not lesson.active or not _reflection_writes_at(path):
            return
        content = _set_frontmatter_value(lesson.content, "status", "demoted")
        content = _set_frontmatter_value(
            content, "curation_reason", f'"{_escape_yaml("demoted: " + reason)}"'
        )
        if frontmatter_values(content).get("status") != "demoted":
            raise RuntimeError(f"cannot demote learning {path!r}: no frontmatter to mark")
        await self._mimir.upsert_page(path, content)

    async def _credit(
        self,
        action: dict[str, str],
        record: FrictionRecord,
        by_path: dict[str, Lesson],
        reason: str,
        *,
        now: datetime,
    ) -> None:
        """Record an improvement that worked as evidence on the lesson it relied on."""
        lesson = by_path.get(action.get("target", ""))
        if lesson is None or not lesson.active or not _reflection_owns(lesson.path, lesson.content):
            return
        note = f"friction {record.path} stopped recurring: {reason}"
        content = _insert_timeline_entry(
            lesson.content,
            "  - source: improvement_outcome\n"
            f"    session_id: improvement:{action.get('id', '')}\n"
            f"    date: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f'    note: "{_escape_yaml(note)}"',
        )
        count = int(frontmatter_values(content).get("evidence_count") or 0) + 1
        content = _set_frontmatter_value(content, "evidence_count", str(count))
        await self._mimir.upsert_page(lesson.path, content)

    # ------------------------------------------------------------------
    # 2. Friction a promoted learning already answers
    # ------------------------------------------------------------------

    async def _adopt_learned(
        self,
        records: list[FrictionRecord],
        by_path: dict[str, Lesson],
        report: ImprovementReport,
        *,
        now: datetime,
    ) -> None:
        for record in records:
            if record.status != OPEN or record.live_action() is not None:
                continue
            learning = next(
                (
                    lesson
                    for lesson in (_resolve(path, by_path) for path in record.linked_lessons)
                    if lesson is not None and lesson.is_learning and "learned" not in record.tried()
                ),
                None,
            )
            if learning is None:
                continue
            await self._record_action(
                record,
                report,
                kind="learned",
                target=learning.path,
                expected_benefit=(
                    "the promoted learning is in my prompt, so I apply it instead of "
                    "hitting this again"
                ),
                rationale=f"linked lesson {learning.path} was promoted",
                instruction=_learned_instruction(learning),
                now=now,
            )

    # ------------------------------------------------------------------
    # 3. Choose and act
    # ------------------------------------------------------------------

    async def _plan_and_act(
        self,
        records: list[FrictionRecord],
        by_path: dict[str, Lesson],
        report: ImprovementReport,
        *,
        now: datetime,
    ) -> None:
        recurring = sorted(
            (
                record
                for record in records
                if record.status == OPEN
                and record.live_action() is None
                and record.total >= self._config.min_occurrences
            ),
            key=lambda record: (record.total, record.last_seen),
            reverse=True,
        )
        report.open_friction = [record.path for record in recurring]
        candidates = recurring[: self._config.max_candidates]
        if not candidates:
            return
        shown: list[Lesson] = []
        for record in candidates:
            for path in record.linked_lessons:
                lesson = _resolve(path, by_path)
                if lesson is not None and lesson not in shown:
                    shown.append(lesson)
        answer = await self._judge.ask_json(self._prompt(candidates, shown))
        proposals = answer.get("actions")
        if not isinstance(proposals, list):
            raise LessonJudgeError(f"planner answer has no 'actions' list: {answer!r}")
        for proposal in proposals[: self._config.max_actions]:
            problem = await self._act(proposal, candidates, shown, report, now=now)
            if problem:
                report.rejected.append({"proposal": compact_line(str(proposal)), "reason": problem})
                _telemetry_count(
                    "ravn.self_improvement.rejected",
                    kind=str(proposal.get("action") if isinstance(proposal, dict) else ""),
                )

    async def _act(
        self,
        proposal: object,
        candidates: list[FrictionRecord],
        shown: list[Lesson],
        report: ImprovementReport,
        *,
        now: datetime,
    ) -> str:
        """Carry out one proposal; return why it was rejected, or ``""``."""
        if not isinstance(proposal, dict):
            return "not an object"
        record = _numbered(proposal.get("friction"), "F", candidates)
        if record is None:
            return f"names no shown friction: {proposal.get('friction')!r}"
        kind = str(proposal.get("action") or "")
        if kind not in self._config.allowed_actions:
            return f"action {kind!r} is not allowed"
        if kind in record.tried():
            return f"{kind} was already tried for this friction without effect"
        if record.live_action() is not None:
            return "this friction was already acted on in this pass"
        benefit = str(proposal.get("expected_benefit") or "").strip()
        if not benefit:
            return "no expected benefit"
        rationale = str(proposal.get("rationale") or "").strip()
        _telemetry_count("ravn.self_improvement.chosen", kind=kind)
        lesson = _numbered(proposal.get("lesson"), "L", shown)

        if kind == "promote_learning":
            if lesson is None or lesson.category != CANDIDATE_CATEGORY:
                return "promote_learning needs a shown candidate lesson"
            if lesson.path not in record.linked_lessons:
                return "the candidate is not linked to this friction"
            target = await promote_candidate(
                self._mimir,
                candidate_path=lesson.path,
                candidate_content=lesson.content,
                title=lesson.title,
                repo_slug=frontmatter_values(lesson.content).get("repo_slug", ""),
                reason=f"recurring_friction:{record.total}:{record.path}",
            )
            promoted = await self._mimir.read_page(target)
            instruction = _learned_instruction(lesson, body=promoted)
        elif kind == "rewrite_learning":
            if (
                lesson is None
                or not lesson.is_learning
                or not _reflection_owns(lesson.path, lesson.content)
            ):
                return "rewrite_learning needs a shown learning this resident owns"
            procedure = await self._judge.procedure(lesson, max_chars=self._procedure_max_chars)
            content = set_procedure(lesson.content, procedure)
            content = _set_frontmatter_value(
                content, "procedure_rewritten", now.strftime("%Y-%m-%dT%H:%M:%SZ")
            )
            await self._mimir.upsert_page(lesson.path, content)
            target = lesson.path
            instruction = f"You already learned this: {procedure} Apply it before asking."
        elif kind == "retire_belief":
            if lesson is None or not _reflection_owns(lesson.path, lesson.content):
                return "retire_belief needs a shown lesson this resident owns"
            content = _set_frontmatter_value(lesson.content, "status", "superseded")
            content = _set_frontmatter_value(content, "superseded_by", f'"{record.path}"')
            content = _set_frontmatter_value(
                content,
                "curation_reason",
                f'"{_escape_yaml("retired by self-improvement: " + (rationale or benefit))}"',
            )
            await self._mimir.upsert_page(lesson.path, content)
            target = lesson.path
            instruction = (
                f"The belief '{compact_line(lesson.title, limit=160)}' was retired because "
                f"newer evidence contradicts it: {compact_line(rationale or benefit)}. "
                "Do not rely on it; check the current state instead of asking."
            )
        elif kind == "commission_tool":
            need = str(proposal.get("need") or "").strip()
            if not need:
                return "commission_tool needs the recurring need"
            task = self._task(
                f"Self-improvement: commission a tool for {compact_line(need, limit=80)}",
                record,
                benefit,
                rationale,
                f"Recurring need: {need}\n\n"
                "Commission a learned tool for this need with `build_tool`, following its "
                "normal review, canary and install policy for your autonomy mode. Do not ask "
                "the operator about this need; if build_tool refuses or the build fails, "
                "record why in your outcome.",
            )
            await self._send(task)
            target = task.task_id
            instruction = (
                f"A tool for this need was commissioned (task {task.task_id}). Use it if it is "
                "installed; otherwise continue other work instead of asking the operator again."
            )
        else:  # file_defect
            title = str(proposal.get("title") or "").strip()
            body = str(proposal.get("body") or "").strip()
            if not title or not body:
                return "file_defect needs a ticket title and body"
            labels = ", ".join(self._config.defect_labels)
            task = self._task(
                f"Self-improvement: file platform defect — {compact_line(title, limit=80)}",
                record,
                benefit,
                rationale,
                "File a platform-defect ticket with `tracker_issue` (action create): "
                f"team {self._config.defect_team}, labels [{labels}], title {title!r}.\n"
                f"Description:\n{body}\n\nCite the friction record `{record.path}` as "
                "evidence. Search the tracker for an open ticket about the same defect first "
                "and comment on it instead of filing a duplicate.",
            )
            await self._send(task)
            target = task.task_id
            instruction = (
                f"This is a known platform defect (ticket task {task.task_id}). Do not ask the "
                "operator again; continue other work or sleep until the fix lands."
            )

        await self._record_action(
            record,
            report,
            kind=kind,
            target=target,
            expected_benefit=benefit,
            rationale=rationale,
            instruction=instruction,
            now=now,
        )
        return ""

    def _task(
        self,
        title: str,
        record: FrictionRecord,
        benefit: str,
        rationale: str,
        instructions: str,
    ) -> AgentTask:
        context = (
            f"Self-improvement action chosen from your own friction record `{record.path}`.\n"
            f"Friction ({record.kind}, seen {record.total} time(s)): {record.subject}\n"
            f"Expected benefit: {benefit}\n"
            f"Why this action: {rationale or 'see expected benefit'}\n\n"
            f"{instructions}\n"
        )
        return AgentTask(
            task_id=f"task_{int(time.time() * 1000):x}_self_improvement_{uuid4().hex[:6]}",
            title=title,
            initiative_context=context,
            triggered_by="self_improvement:dream",
            output_mode=OutputMode.AMBIENT,
            persona=self._config.task_persona or None,
            priority=self._config.task_priority,
        )

    async def _send(self, task: AgentTask) -> None:
        assert self._dispatch is not None  # noqa: S101 — constructor guard
        if await self._dispatch(task) is False:
            raise RuntimeError(
                f"the drive loop refused self-improvement task {task.task_id!r}; "
                "the improvement was not made"
            )

    async def _record_action(
        self,
        record: FrictionRecord,
        report: ImprovementReport,
        *,
        kind: str,
        target: str,
        expected_benefit: str,
        rationale: str,
        instruction: str,
        now: datetime,
    ) -> None:
        action = {
            "id": uuid4().hex[:12],
            "kind": kind,
            "target": target,
            "expected_benefit": expected_benefit,
            "rationale": rationale,
            "instruction": compact_line(instruction, limit=600),
            "acted_at": now.isoformat(),
            "status": "pending",
        }
        record.actions.append(action)
        record.status = ADDRESSED
        await self._ledger.save(record)
        report.actions.append({"ref": record.path, "subject": record.subject, **action})
        _telemetry_count("ravn.self_improvement.acted", kind=kind)
        get_observability().event(
            "ravn.self_improvement.acted",
            attributes={"ravn.self_improvement.kind": kind, "ravn.friction.ref": record.path},
            content={"expected_benefit": expected_benefit, "rationale": rationale},
        )

    def _prompt(self, candidates: list[FrictionRecord], shown: list[Lesson]) -> str:
        friction_lines = []
        for number, record in enumerate(candidates, start=1):
            linked = [
                f"L{shown.index(lesson) + 1}"
                for lesson in shown
                if lesson.path in record.linked_lessons
                or frontmatter_values(lesson.content).get("promoted_from", "")
                in record.linked_lessons
            ]
            tried = sorted(record.tried())
            friction_lines.append(
                f"[F{number}] {record.kind}, seen {record.total} time(s), last "
                f"{record.last_seen[:10]}: {record.subject}"
                + (f"\n      linked lessons: {', '.join(linked)}" if linked else "")
                + (f"\n      tried without effect: {', '.join(tried)}" if tried else "")
            )
        lesson_lines = [
            f"[L{number}] ({_lesson_state(lesson)}) {compact_line(lesson.title, limit=160)} — "
            f"{compact_line(lesson.procedure or lesson.claim, limit=300)}"
            for number, lesson in enumerate(shown, start=1)
        ]
        return _PLANNER_PROMPT.format(
            resident=self._resident_id,
            max_actions=self._config.max_actions,
            allowed="\n".join(f"- {_ACTION_HELP[kind]}" for kind in self._config.allowed_actions),
            friction="\n".join(friction_lines),
            lessons="\n".join(lesson_lines) or "(none)",
        )

    async def _write_brief(self, report: ImprovementReport) -> None:
        if not (report.evaluated or report.actions or report.rejected or report.error):
            return
        stamp = report.started_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        path = f"{self._config.audit_prefix.strip('/')}/{timestamp_slug(report.started_at)}.md"
        lines = [
            "---",
            f'title: "What I improved and why — {stamp}"',
            "type: decision",
            f"date: {stamp}",
            "---",
            "",
            f"# What I improved and why — {stamp}",
        ]
        if report.evaluated:
            lines += ["", "## Did earlier improvements help?"]
            lines += [
                f"- `{item['ref']}` — {item['kind']}: **{item['verdict']}** ({item['reason']})"
                for item in report.evaluated
            ]
        if report.actions:
            lines += ["", "## What I changed"]
            for action in report.actions:
                lines.append(
                    f"- **{action['kind']}** for `{action['ref']}` "
                    f"({compact_line(action['subject'], limit=160)}) → `{action['target']}`. "
                    f"Expected benefit: {action['expected_benefit']}. "
                    f"Why: {action['rationale'] or 'see expected benefit'}."
                )
        if report.rejected:
            lines += ["", "## Proposals I did not act on"]
            lines += [f"- {item['reason']}: {item['proposal']}" for item in report.rejected]
        if report.open_friction:
            lines += ["", "## Recurring friction still open"]
            lines += [f"- `{path}`" for path in report.open_friction]
        if report.error:
            lines += ["", "## Error", f"Pass stopped: {report.error}"]
        await self._mimir.upsert_page(path, "\n".join(lines) + "\n")
        report.audit_path = path
        logger.info(
            "SelfImprovementPass: %d evaluated, %d acted, %d rejected (%s)",
            len(report.evaluated),
            len(report.actions),
            len(report.rejected),
            path,
        )


def _resolve(path: str, by_path: dict[str, Lesson]) -> Lesson | None:
    """The active lesson *path* now stands for, following a candidate's promotion."""
    lesson = by_path.get(path)
    if lesson is not None and lesson.category == CANDIDATE_CATEGORY and lesson.status == "promoted":
        lesson = by_path.get(frontmatter_values(lesson.content).get("promoted_to", ""))
    return lesson if lesson is not None and lesson.active else None


def _numbered(raw: object, prefix: str, items: list) -> Any | None:
    text = str(raw or "").strip().upper().removeprefix(prefix)
    if not text.isdigit():
        return None
    number = int(text)
    return items[number - 1] if 1 <= number <= len(items) else None


def _learned_instruction(lesson: Lesson, *, body: str = "") -> str:
    rule = lesson.procedure or lesson.claim
    if body:
        rule = rule or compact_line(body)
    return (
        f"You already learned this: {compact_line(rule, limit=400)} "
        f"(learning `{lesson.path}`). Apply it; ask the operator only if it fails, and say "
        "what failed."
    )


def _lesson_state(lesson: Lesson) -> str:
    if lesson.is_learning:
        return "promoted learning" + (", has procedure" if lesson.procedure else "")
    verified = ", verified" if lesson.verified else ""
    return f"candidate, {lesson.evidence_count} observation(s){verified}"


def _parse(stamp: str) -> datetime:
    parsed = datetime.fromisoformat(stamp)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _telemetry_count(name: str, **attributes: str) -> None:
    get_observability().count(
        name,
        attributes={f"ravn.self_improvement.{key}": value for key, value in attributes.items()},
        description="Resident self-improvement loop activity.",
    )
