"""Slim typed resident memory: budget tracking and durable memory records."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ravn.domain.models import TokenUsage, TurnResult
from ravn.domain.resident_continuation import (
    ResidentA2ATaskRecord,
    ResidentBudgetDecision,
    ResidentBudgetLimits,
    ResidentBudgetSnapshot,
    ResidentDecisionStreakRecord,
    ResidentMemoryEntry,
    ResidentMemoryPort,
    ResidentPolicyDecisionRecord,
    ResidentPolicyObservation,
    ResidentScheduledWakeRecord,
    ResidentTurnRecord,
    ResidentWorkingStateRecord,
)
from ravn.resident_text import (
    compact_line as _compact_line,
)
from ravn.resident_text import (
    slug as _slug,
)
from ravn.resident_text import (
    timestamp_slug as _timestamp_slug,
)

logger = logging.getLogger(__name__)

_OPERATOR_NEEDED_PATH = "operator-needed/latest.md"
_OPERATOR_ANSWER_PATH = "operator-answers/latest.md"
_SCHEDULED_WAKE_PATH = "scheduled-wake/latest.md"
_A2A_TASKS_PATH = Path("a2a-tasks")
_DECISION_STREAK_DIR = "decision-streak"


def _case_path(case_id: str, leaf: str) -> Path:
    case_slug = _slug(case_id)
    return Path("cases") / case_slug / leaf if case_slug else Path(leaf)


def _a2a_task_path(task_id: str) -> Path:
    slug = (_slug(task_id) or "task")[:80]
    digest = hashlib.sha256(task_id.encode()).hexdigest()[:16]
    return _A2A_TASKS_PATH / f"{slug}-{digest}.md"


class ResidentRunBudget:
    """In-memory run budget with persistable snapshots."""

    def __init__(
        self,
        limits: ResidentBudgetLimits,
        *,
        clock: Any = time.monotonic,
    ) -> None:
        self._limits = limits
        self._clock = clock
        self._started_at = float(clock())
        self._turns_used = 0
        self._usage = TokenUsage(input_tokens=0, output_tokens=0)
        self._cost_usd = 0.0

    @property
    def limits(self) -> ResidentBudgetLimits:
        return self._limits

    def snapshot(self) -> ResidentBudgetSnapshot:
        return ResidentBudgetSnapshot(
            turns_used=self._turns_used,
            elapsed_seconds=max(0.0, float(self._clock()) - self._started_at),
            usage=self._usage,
            cost_usd=self._cost_usd,
        )

    def can_continue(self) -> ResidentBudgetDecision:
        snapshot = self.snapshot()
        if self._limits.max_turns > 0 and snapshot.turns_used >= self._limits.max_turns:
            return ResidentBudgetDecision(False, f"max turns reached: {self._limits.max_turns}")
        if (
            self._limits.max_wall_clock_seconds > 0
            and snapshot.elapsed_seconds >= self._limits.max_wall_clock_seconds
        ):
            return ResidentBudgetDecision(
                False,
                f"max wall-clock seconds reached: {self._limits.max_wall_clock_seconds:g}",
            )
        if self._limits.max_tokens > 0 and snapshot.total_tokens >= self._limits.max_tokens:
            return ResidentBudgetDecision(
                False,
                f"max token budget reached: {self._limits.max_tokens}",
            )
        if self._limits.max_cost_usd > 0 and snapshot.cost_usd >= self._limits.max_cost_usd:
            return ResidentBudgetDecision(
                False,
                f"max cost budget reached: ${self._limits.max_cost_usd:.2f}",
            )
        return ResidentBudgetDecision(True, "budget available")

    def record_turn(self, result: TurnResult) -> ResidentBudgetSnapshot:
        self._turns_used += 1
        self._usage = self._usage + result.usage
        return self.snapshot()

    def record_usage(self, usage: TokenUsage) -> ResidentBudgetSnapshot:
        self._turns_used += 1
        self._usage = self._usage + usage
        return self.snapshot()


class NullResidentMemory(ResidentMemoryPort):
    """No-op memory used when persistence is unavailable."""

    async def recall(self, mandate: str, *, limit: int = 5) -> list[ResidentMemoryEntry]:
        return []

    async def read(self, ref: str) -> ResidentMemoryEntry | None:
        return None

    async def read_working_state(self, resident_id: str) -> ResidentMemoryEntry | None:
        return None

    async def write_turn(self, record: ResidentTurnRecord) -> str:
        return ""

    async def write_working_state(self, record: ResidentWorkingStateRecord) -> str:
        return ""

    async def read_a2a_task(self, task_id: str) -> ResidentMemoryEntry | None:
        return None

    async def write_a2a_task(self, record: ResidentA2ATaskRecord) -> str:
        return ""

    async def list_a2a_tasks(self) -> list[ResidentMemoryEntry]:
        return []

    async def write_budget(self, snapshot: ResidentBudgetSnapshot) -> str:
        return ""

    async def write_policy_observation(self, observation: ResidentPolicyObservation) -> str:
        return ""

    async def list_policy_observations(self) -> list[ResidentPolicyObservation]:
        return []

    async def write_policy_decision(self, decision: ResidentPolicyDecisionRecord) -> str:
        return ""

    async def write_operator_needed(
        self,
        *,
        question: str,
        reason: str,
        turn: ResidentTurnRecord,
        case_id: str = "",
        turn_ref: str = "",
    ) -> str:
        return ""

    async def read_operator_needed(self, case_id: str = "") -> ResidentMemoryEntry | None:
        return None

    async def write_operator_answer(self, answer: str, *, case_id: str = "") -> str:
        return ""

    async def read_operator_answer(self, case_id: str = "") -> ResidentMemoryEntry | None:
        return None

    async def consume_operator_answer(self, answer: ResidentMemoryEntry) -> str:
        return ""

    async def list_operator_needed(self) -> list[ResidentMemoryEntry]:
        return []

    async def list_operator_answers(self) -> list[ResidentMemoryEntry]:
        return []


class LocalResidentMemory(ResidentMemoryPort):
    """Filesystem fallback that mirrors the Mimir memory shape."""

    def __init__(
        self,
        root: Path,
        *,
        prefix: str = "resident/continuation",
        retention_max_cases: int = 0,
        retention_max_age_days: float = 0.0,
        retention_sweep_interval_seconds: float = 900.0,
    ) -> None:
        self._root = Path(root)
        self._prefix = Path(prefix.strip("/").strip() or "resident/continuation")
        self._retention_max_cases = max(0, retention_max_cases)
        self._retention_max_age_days = max(0.0, retention_max_age_days)
        self._retention_sweep_interval_seconds = max(0.0, retention_sweep_interval_seconds)
        self._last_retention_sweep: float | None = None

    # ------------------------------------------------------------------
    # Case retention
    # ------------------------------------------------------------------

    def _case_is_resumable(self, case_dir: Path) -> bool:
        """Whether any mechanism can still bring this case back.

        Exactly two things resume a case: a pending scheduled wake, and an
        unanswered operator question. Both already record ``- status: pending``
        under a known leaf — the same convention ``_list_case_entries`` reads —
        so this needs no new state to answer.
        """
        for leaf in (_SCHEDULED_WAKE_PATH, _OPERATOR_NEEDED_PATH):
            marker = case_dir / leaf
            if not marker.is_file():
                continue
            try:
                if _operator_marker_is_pending(marker.read_text(encoding="utf-8")):
                    return True
            except OSError:
                # Unreadable marker: assume the case is live rather than delete it.
                return True
        return False

    async def prune_cases(self) -> int:
        """Delete unresumable cases beyond the retention policy; return the count."""
        return await asyncio.to_thread(self._prune_cases_sync)

    def _prune_cases_sync(self) -> int:
        base = self._root / self._prefix / "cases"
        if not base.is_dir():
            return 0
        if self._retention_max_cases <= 0 and self._retention_max_age_days <= 0:
            return 0

        eligible: list[tuple[float, Path]] = []
        live = 0
        for case_dir in base.iterdir():
            if not case_dir.is_dir():
                continue
            if self._case_is_resumable(case_dir):
                live += 1
                continue
            try:
                eligible.append((case_dir.stat().st_mtime, case_dir))
            except OSError:
                continue

        eligible.sort(reverse=True)
        doomed: list[Path] = []
        if self._retention_max_age_days > 0:
            cutoff = time.time() - self._retention_max_age_days * 86400
            doomed.extend(path for mtime, path in eligible if mtime < cutoff)
        if self._retention_max_cases > 0:
            # The cap counts every case on disk, live ones included, so a
            # resident holding many open cases keeps them and trims further
            # into its dead tail rather than pruning nothing.
            surplus = len(eligible) + live - self._retention_max_cases
            if surplus > 0:
                doomed.extend(path for _mtime, path in eligible[-surplus:])

        removed = 0
        for case_dir in dict.fromkeys(doomed):
            try:
                shutil.rmtree(case_dir)
            except OSError:
                logger.warning("resident cases: could not prune %s", case_dir, exc_info=True)
                continue
            removed += 1
        if removed:
            logger.info(
                "resident cases: pruned %d unresumable case(s); %d live, %d retained",
                removed,
                live,
                len(eligible) + live - removed,
            )
        return removed

    async def _maybe_prune_cases(self) -> None:
        """Sweep when one is due. Never blocking, never on the read path."""
        if self._retention_max_cases <= 0 and self._retention_max_age_days <= 0:
            return
        now = time.monotonic()
        if (
            self._last_retention_sweep is not None
            and now - self._last_retention_sweep < self._retention_sweep_interval_seconds
        ):
            return
        self._last_retention_sweep = now
        try:
            await self.prune_cases()
        except Exception:  # noqa: BLE001 — bookkeeping must never break a turn
            logger.warning("resident cases: retention sweep failed", exc_info=True)

    async def recall(self, mandate: str, *, limit: int = 5) -> list[ResidentMemoryEntry]:
        base = self._root / self._prefix
        if not base.exists():
            return []
        files = sorted(base.rglob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
        terms = {
            term
            for term in re.findall(r"[a-z0-9][a-z0-9._:-]+", mandate.casefold())
            if len(term) >= 3
        }
        if terms:
            matching: list[Path] = []
            for path in files:
                content = path.read_text(encoding="utf-8").casefold()
                if any(term in content for term in terms):
                    matching.append(path)
            if matching:
                files = matching
        entries: list[ResidentMemoryEntry] = []
        for path in files[:limit]:
            content = path.read_text(encoding="utf-8")
            entries.append(
                ResidentMemoryEntry(
                    path=str(path.relative_to(self._root)),
                    summary=_first_heading_or_line(content),
                    content=content,
                )
            )
        return entries

    async def read(self, ref: str) -> ResidentMemoryEntry | None:
        path = self._root / ref
        if not path.is_file():
            return None
        content = path.read_text(encoding="utf-8")
        return ResidentMemoryEntry(
            path=ref,
            summary=_first_heading_or_line(content),
            content=content,
        )

    async def read_working_state(self, resident_id: str) -> ResidentMemoryEntry | None:
        return await self.read(str(self._working_state_path(resident_id)))

    async def write_turn(self, record: ResidentTurnRecord) -> str:
        stamp = record.created_at.strftime("%Y%m%dT%H%M%SZ")
        rel = self._prefix / _case_path(
            record.case_id,
            f"turns/{stamp}-{record.turn_index}.md",
        )
        ref = self._write(rel, _render_turn_record(record))
        # Every turn writes here, so this is the natural sweep tick. The
        # interval throttle keeps it off all but one write in fifteen minutes.
        await self._maybe_prune_cases()
        return ref

    async def write_working_state(self, record: ResidentWorkingStateRecord) -> str:
        return self._write(
            self._working_state_path(record.resident_id), _render_working_state(record)
        )

    async def read_decision_streak(self, resident_id: str) -> ResidentDecisionStreakRecord | None:
        entry = await self.read(str(self._decision_streak_path(resident_id)))
        if entry is None:
            return None
        return _parse_decision_streak(resident_id, entry.content)

    async def write_decision_streak(self, record: ResidentDecisionStreakRecord) -> str:
        return self._write(
            self._decision_streak_path(record.resident_id), _render_decision_streak(record)
        )

    async def read_a2a_task(self, task_id: str) -> ResidentMemoryEntry | None:
        return await self.read(str(self._prefix / _a2a_task_path(task_id)))

    async def write_a2a_task(self, record: ResidentA2ATaskRecord) -> str:
        return self._write(self._prefix / _a2a_task_path(record.task_id), _render_a2a_task(record))

    async def list_a2a_tasks(self) -> list[ResidentMemoryEntry]:
        base = self._root / self._prefix / _A2A_TASKS_PATH
        if not base.exists():
            return []
        entries: list[ResidentMemoryEntry] = []
        for path in sorted(base.glob("*.md")):
            content = path.read_text(encoding="utf-8")
            entries.append(
                ResidentMemoryEntry(
                    path=str(path.relative_to(self._root)),
                    summary=_first_heading_or_line(content),
                    content=content,
                )
            )
        return entries

    async def write_budget(self, snapshot: ResidentBudgetSnapshot) -> str:
        rel = self._prefix / _case_path(snapshot.case_id, "budget/latest.md")
        return self._write(rel, _render_budget_snapshot(snapshot, updated_at=datetime.now(UTC)))

    async def write_policy_observation(self, observation: ResidentPolicyObservation) -> str:
        rel = self._prefix / "policy" / f"{_slug(observation.subject) or 'policy-observation'}.md"
        return self._write(rel, _render_policy_observation(observation))

    async def list_policy_observations(self) -> list[ResidentPolicyObservation]:
        base = self._root / self._prefix / "policy"
        if not base.exists():
            return []
        observations: list[ResidentPolicyObservation] = []
        for path in sorted(base.glob("*.md")):
            parsed = _parse_policy_observation(path.read_text(encoding="utf-8"))
            if parsed is not None:
                observations.append(parsed)
        return observations

    async def write_policy_decision(self, decision: ResidentPolicyDecisionRecord) -> str:
        stamp = decision.created_at.strftime("%Y%m%dT%H%M%SZ")
        slug = _slug(decision.action_title) or "policy-decision"
        rel = self._prefix / "policy-decisions" / f"{stamp}-{decision.turn_index}-{slug}.md"
        return self._write(rel, _render_policy_decision(decision))

    async def write_operator_needed(
        self,
        *,
        question: str,
        reason: str,
        turn: ResidentTurnRecord,
        case_id: str = "",
        turn_ref: str = "",
    ) -> str:
        resolved_case = case_id or turn.case_id
        rel = self._prefix / _case_path(resolved_case, _OPERATOR_NEEDED_PATH)
        return self._write(
            rel,
            _render_operator_needed(
                question=question,
                reason=reason,
                turn=turn,
                status="pending",
                turn_ref=turn_ref,
            ),
        )

    async def read_operator_needed(self, case_id: str = "") -> ResidentMemoryEntry | None:
        rel = self._prefix / _case_path(case_id, _OPERATOR_NEEDED_PATH)
        path = self._root / rel
        if not path.exists():
            return None
        content = path.read_text(encoding="utf-8")
        if not _operator_marker_is_pending(content):
            return None
        return ResidentMemoryEntry(
            path=str(rel),
            summary=_first_heading_or_line(content),
            content=content,
        )

    async def write_operator_answer(self, answer: str, *, case_id: str = "") -> str:
        now = datetime.now(UTC)
        answer_rel = self._prefix / _case_path(case_id, _OPERATOR_ANSWER_PATH)
        marker_rel = self._prefix / _case_path(case_id, _OPERATOR_NEEDED_PATH)
        marker_path = self._root / marker_rel
        prior = marker_path.read_text(encoding="utf-8") if marker_path.exists() else ""
        answer_ref = self._write(
            answer_rel,
            _render_operator_answer(
                answer,
                answered_at=now,
                case_id=case_id,
                pending_context=prior,
            ),
        )
        history_rel = self._prefix / _case_path(
            case_id,
            f"operator-answers/{_timestamp_slug(now)}.md",
        )
        self._write(
            history_rel,
            _render_operator_answer(
                answer,
                answered_at=now,
                case_id=case_id,
                pending_context=prior,
            ),
        )
        self._write(
            marker_rel,
            _render_answered_operator_needed(prior, answer_path=answer_ref, answered_at=now),
        )
        return answer_ref

    async def read_operator_answer(self, case_id: str = "") -> ResidentMemoryEntry | None:
        rel = self._prefix / _case_path(case_id, _OPERATOR_ANSWER_PATH)
        path = self._root / rel
        if not path.exists():
            return None
        content = path.read_text(encoding="utf-8")
        if _operator_answer_is_consumed(content):
            return None
        return ResidentMemoryEntry(
            path=str(rel),
            summary=_first_heading_or_line(content),
            content=content,
        )

    async def consume_operator_answer(self, answer: ResidentMemoryEntry) -> str:
        rel = Path(answer.path) if answer.path else self._prefix / _OPERATOR_ANSWER_PATH
        path = self._root / rel
        prior = path.read_text(encoding="utf-8") if path.exists() else answer.content
        return self._write(
            rel,
            _render_consumed_operator_answer(prior, consumed_at=datetime.now(UTC)),
        )

    async def list_operator_needed(self) -> list[ResidentMemoryEntry]:
        return self._list_case_entries(_OPERATOR_NEEDED_PATH, pending=True)

    async def list_operator_answers(self) -> list[ResidentMemoryEntry]:
        return self._list_case_entries(_OPERATOR_ANSWER_PATH, pending=False)

    def _list_case_entries(self, leaf: str, *, pending: bool) -> list[ResidentMemoryEntry]:
        base = self._root / self._prefix / "cases"
        if not base.exists():
            return []
        entries: list[ResidentMemoryEntry] = []
        for path in sorted(base.glob(f"*/{leaf}")):
            content = path.read_text(encoding="utf-8")
            available = (
                _operator_marker_is_pending(content)
                if pending
                else not _operator_answer_is_consumed(content)
            )
            if available:
                entries.append(
                    ResidentMemoryEntry(
                        path=str(path.relative_to(self._root)),
                        summary=_first_heading_or_line(content),
                        content=content,
                    )
                )
        return entries

    def _write(self, rel: Path, content: str) -> str:
        path = self._root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        # Local resident pages are deliberately operator-inspectable Markdown,
        # not a credential store. Keep them private to the owning OS account.
        path.write_text(content, encoding="utf-8")
        path.chmod(0o600)
        return str(rel)

    def _working_state_path(self, resident_id: str) -> Path:
        resident_slug = _slug(resident_id) or "resident"
        return self._prefix / "working-state" / f"{resident_slug}.md"

    def _decision_streak_path(self, resident_id: str) -> Path:
        resident_slug = _slug(resident_id) or "resident"
        return self._prefix / _DECISION_STREAK_DIR / f"{resident_slug}.md"


def _render_turn_record(record: ResidentTurnRecord) -> str:
    action = record.selected_next_action
    action_text = action.action if action is not None else ""
    fields = "\n".join(
        f"- {key}: {value!r}" for key, value in sorted(record.outcome_fields.items())
    )
    tools = ", ".join(record.tool_names) if record.tool_names else "none"
    tool_results = "\n\n".join(record.tool_results) or "none"
    evidence = "\n".join(f"- {ref}" for ref in record.evidence_refs) or "- none"
    inbox = "\n".join(f"- {ref}" for ref in record.inbox_refs) or "- none"
    return (
        f"# Resident Turn {record.turn_index}\n\n"
        f"- updated_at: {record.created_at.isoformat()}\n"
        f"- case_id: {record.case_id}\n"
        f"- root_correlation_id: {record.root_correlation_id}\n"
        f"- task_id: {record.task_id}\n"
        f"- triggered_by: {record.triggered_by}\n"
        f"- persona: {record.persona}\n"
        f"- tools_used: {tools}\n"
        f"- input_tokens: {record.usage.input_tokens}\n"
        f"- output_tokens: {record.usage.output_tokens}\n\n"
        f"- case_input_tokens: {record.cumulative_usage.input_tokens}\n"
        f"- case_output_tokens: {record.cumulative_usage.output_tokens}\n\n"
        "## Prompt\n\n"
        f"{record.prompt}\n\n"
        "## Response\n\n"
        f"{record.response}\n\n"
        "## Tool Results\n\n"
        f"{tool_results}\n\n"
        "## Mandate\n\n"
        f"{record.mandate[:4000]}\n\n"
        "## Outcome Fields\n\n"
        f"{fields or '- none'}\n\n"
        "## Selected Next Action\n\n"
        f"{action_text or 'none'}\n\n"
        "## Evidence References\n\n"
        f"{evidence}\n\n"
        "## Inbox References\n\n"
        f"{inbox}\n"
    )


def _render_working_state(record: ResidentWorkingStateRecord) -> str:
    state_json = json.dumps(record.state, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    signal_refs = "\n".join(f"- {ref}" for ref in record.signal_refs) or "- none"
    evidence_refs = "\n".join(f"- {ref}" for ref in record.evidence_refs) or "- none"
    return (
        "# Resident Working State\n\n"
        f"- updated_at: {record.updated_at.isoformat()}\n"
        f"- source_turn_ref: {record.source_turn_ref}\n"
        f"- source_case_id: {record.source_case_id}\n"
        f"- source_task_id: {record.source_task_id}\n\n"
        "## State\n\n"
        "```json\n"
        f"{state_json}\n"
        "```\n\n"
        "## Source Signal References\n\n"
        f"{signal_refs}\n\n"
        "## Evidence References\n\n"
        f"{evidence_refs}\n"
    )


def _render_decision_streak(record: ResidentDecisionStreakRecord) -> str:
    return (
        "# Resident Decision Streak\n\n"
        f"- resident_id: {record.resident_id}\n"
        f"- fingerprint: {record.fingerprint}\n"
        f"- count: {record.count}\n"
        f"- decision: {_compact_line(record.decision, limit=200)}\n"
        f"- case_id: {record.case_id}\n"
        f"- first_seen_at: {record.first_seen_at.isoformat()}\n"
        f"- updated_at: {record.updated_at.isoformat()}\n\n"
        "## Rationale\n\n"
        f"{_compact_line(record.rationale, limit=1000)}\n"
    )


def _parse_decision_streak(resident_id: str, content: str) -> ResidentDecisionStreakRecord | None:
    fingerprint = _marker_field(content, "fingerprint")
    if not fingerprint:
        return None
    try:
        count = int(_marker_field(content, "count") or "0")
    except ValueError:
        return None
    return ResidentDecisionStreakRecord(
        resident_id=resident_id,
        fingerprint=fingerprint,
        count=count,
        decision=_marker_field(content, "decision"),
        rationale=_section_body(content, "Rationale"),
        case_id=_marker_field(content, "case_id"),
        first_seen_at=_marker_datetime(content, "first_seen_at") or datetime.now(UTC),
        updated_at=_marker_datetime(content, "updated_at") or datetime.now(UTC),
    )


def _marker_field(content: str, key: str) -> str:
    match = re.search(rf"^- {re.escape(key)}:\s*(.*?)\s*$", content, flags=re.MULTILINE)
    return _unquote(match.group(1).strip()) if match else ""


def _marker_datetime(content: str, key: str) -> datetime | None:
    raw = _marker_field(content, key)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _section_body(content: str, heading: str) -> str:
    match = re.search(
        rf"^## {re.escape(heading)}\s*$\n+(.*?)(?=\n## |\Z)",
        content,
        flags=re.MULTILINE | re.DOTALL,
    )
    return match.group(1).strip() if match else ""


_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ][\d:.+\-]+")


def decision_fingerprint(
    *,
    decision: str,
    objectives: Any,
    tool_results: tuple[str, ...],
) -> str:
    """Fingerprint the evidence a turn acted on, not the prose it wrote about it.

    Keyed on what the resident was trying to do and what its tools actually
    returned. Everything the model narrates — rationale, state summary, its own
    observations — is excluded, because a resident stuck on one belief restates
    it differently every turn: across 55 real stuck turns the rationale took 40
    distinct forms and the attempts list grew by one entry each time, so any
    fingerprint including them matched nothing and the guard never fired.

    Tool results are the honest signal. A resident re-reading one unchanged fact
    gets byte-identical results; one watching a real condition gets new numbers
    every turn and never trips. Objectives keep separate subjects apart while
    staying stable within one — and a frozen objective is itself the symptom.

    Validated against two live residents: a stuck one reached runs of 8 identical
    turns, a busy one watching genuine etcd latency peaked at 4.
    """
    payload = json.dumps(
        {
            "decision": decision.strip().casefold(),
            "objectives": objectives,
            # Timestamps differ on every read of the same thing; they are the
            # clock moving, not the world changing.
            "evidence": [_TIMESTAMP_RE.sub("<ts>", str(item)) for item in tool_results],
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _render_a2a_task(record: ResidentA2ATaskRecord) -> str:
    payload = {
        "task_id": record.task_id,
        "agent_id": record.agent_id,
        "skill_id": record.skill_id,
        "state": record.state,
        "operation": record.operation,
        "prompt": record.prompt,
        "status_message": record.status_message,
        "question": record.question,
        "case_id": record.case_id,
        "root_correlation_id": record.root_correlation_id,
        "parent_task_id": record.parent_task_id,
        "mandate": record.mandate,
        "turn_index": record.turn_index,
        "case_input_tokens": record.case_input_tokens,
        "case_output_tokens": record.case_output_tokens,
        "case_started_at": record.case_started_at,
        "push_registered": record.push_registered,
        "update_fingerprint": record.update_fingerprint,
        "updated_at": record.updated_at.isoformat(),
    }
    return (
        "# Resident A2A Task\n\n"
        "```json\n"
        f"{json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)}\n"
        "```\n"
    )


def _parse_a2a_task(content: str) -> ResidentA2ATaskRecord | None:
    match = re.search(r"```json\s*(\{.*?\})\s*```", content, flags=re.DOTALL)
    if match is None:
        return None
    try:
        payload = json.loads(match.group(1))
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or not str(payload.get("task_id") or "").strip():
        return None
    raw_updated_at = str(payload.get("updated_at") or "").strip()
    try:
        updated_at = datetime.fromisoformat(raw_updated_at)
    except ValueError:
        updated_at = datetime.now(UTC)
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    push_registered = payload.get("push_registered")
    if not isinstance(push_registered, bool):
        push_registered = None
    return ResidentA2ATaskRecord(
        task_id=str(payload["task_id"]),
        agent_id=str(payload.get("agent_id") or ""),
        skill_id=str(payload.get("skill_id") or ""),
        state=str(payload.get("state") or "TASK_STATE_UNSPECIFIED"),
        operation=str(payload.get("operation") or ""),
        prompt=str(payload.get("prompt") or ""),
        status_message=str(payload.get("status_message") or ""),
        question=str(payload.get("question") or ""),
        case_id=str(payload.get("case_id") or ""),
        root_correlation_id=str(payload.get("root_correlation_id") or ""),
        parent_task_id=str(payload.get("parent_task_id") or ""),
        mandate=str(payload.get("mandate") or ""),
        turn_index=int(payload.get("turn_index") or 0),
        case_input_tokens=int(payload.get("case_input_tokens") or 0),
        case_output_tokens=int(payload.get("case_output_tokens") or 0),
        case_started_at=str(payload.get("case_started_at") or ""),
        push_registered=push_registered,
        update_fingerprint=str(payload.get("update_fingerprint") or ""),
        updated_at=updated_at,
    )


def _render_operator_needed(
    *,
    question: str,
    reason: str,
    turn: ResidentTurnRecord,
    status: str,
    turn_ref: str = "",
) -> str:
    return (
        "# Operator Input Needed\n\n"
        f"- status: {status}\n"
        f"- case_id: {turn.case_id}\n"
        f"- root_correlation_id: {turn.root_correlation_id}\n"
        f"- task_id: {turn.task_id}\n"
        f"- persona: {turn.persona}\n"
        f"- turn: {turn.turn_index}\n"
        f"- turn_ref: {turn_ref}\n"
        f"- input_tokens: {turn.usage.input_tokens}\n"
        f"- output_tokens: {turn.usage.output_tokens}\n"
        f"- case_input_tokens: {turn.cumulative_usage.input_tokens}\n"
        f"- case_output_tokens: {turn.cumulative_usage.output_tokens}\n"
        f"- reason: {_compact_line(reason, limit=500)}\n"
        f"- question: {_compact_line(question, limit=1000)}\n"
        f"- created_at: {datetime.now(UTC).isoformat()}\n\n"
        "## Selected Next Action\n\n"
        f"{turn.selected_next_action.action if turn.selected_next_action else 'none'}\n\n"
        "## Mandate\n\n"
        f"{turn.mandate[:4000]}\n"
    )


def _render_operator_answer(
    answer: str,
    *,
    answered_at: datetime,
    case_id: str = "",
    pending_context: str = "",
) -> str:
    return (
        "# Operator Answer\n\n"
        "- status: available\n"
        f"- case_id: {case_id}\n"
        f"- answered_at: {answered_at.isoformat()}\n\n"
        "## Answer\n\n"
        f"{str(answer).strip()}\n"
        + (
            f"\n## Pending Context\n\n{pending_context.strip()}\n"
            if pending_context.strip()
            else ""
        )
    )


def _render_consumed_operator_answer(content: str, *, consumed_at: datetime) -> str:
    if content.strip():
        rendered = re.sub(r"^- status: .*$", "- status: consumed", content, flags=re.MULTILINE)
        if "- status: consumed" not in rendered:
            # No status line to replace; inject one (after the canonical header if
            # present) so the answer is reliably recognized as consumed and not
            # re-applied on every poll.
            if "# Operator Answer\n" in rendered:
                rendered = rendered.replace(
                    "# Operator Answer\n",
                    "# Operator Answer\n\n- status: consumed\n",
                    1,
                )
            else:
                rendered = "- status: consumed\n" + rendered
    else:
        rendered = "# Operator Answer\n\n- status: consumed\n"
    return rendered.rstrip() + "\n" + f"- consumed_at: {consumed_at.isoformat()}\n"


def _render_answered_operator_needed(
    prior: str,
    *,
    answer_path: str,
    answered_at: datetime,
) -> str:
    if prior.strip():
        content = re.sub(r"^- status: .*$", "- status: answered", prior, flags=re.MULTILINE)
        if "- status: answered" not in content:
            if "# Operator Input Needed\n" in content:
                content = content.replace(
                    "# Operator Input Needed\n",
                    "# Operator Input Needed\n\n- status: answered\n",
                    1,
                )
            else:
                content = "- status: answered\n" + content
    else:
        content = "# Operator Input Needed\n\n- status: answered\n"
    return (
        content.rstrip()
        + "\n"
        + f"- answered_at: {answered_at.isoformat()}\n"
        + f"- answer_ref: {answer_path}\n"
    )


def _render_scheduled_wake(record: ResidentScheduledWakeRecord) -> str:
    """Render a pending wake using the same status convention as operator markers.

    Sharing ``- status: pending`` lets both adapters list due wakes through the
    existing ``_list_case_entries`` helper instead of a second listing path.
    """
    return (
        "# Resident Scheduled Wake\n\n"
        "- status: pending\n"
        f"- case_id: {record.case_id}\n"
        f"- root_correlation_id: {record.root_correlation_id}\n"
        f"- task_id: {record.task_id}\n"
        f"- persona: {record.persona}\n"
        f"- turn: {record.turn_index}\n"
        f"- turn_ref: {record.turn_ref}\n"
        f"- case_input_tokens: {record.case_input_tokens}\n"
        f"- case_output_tokens: {record.case_output_tokens}\n"
        f"- case_started_at: {record.case_started_at}\n"
        f"- wake_at: {record.wake_at.isoformat()}\n"
        f"- reason: {_compact_line(record.reason, limit=500)}\n"
        f"- created_at: {record.created_at.isoformat()}\n\n"
        "## Mandate\n\n"
        f"{record.mandate[:4000]}\n"
    )


def _render_consumed_scheduled_wake(content: str, *, consumed_at: datetime) -> str:
    if content.strip():
        rendered = re.sub(r"^- status: .*$", "- status: consumed", content, flags=re.MULTILINE)
        if "- status: consumed" not in rendered:
            # No status line to replace; inject one so a fired wake is never
            # re-enqueued on the next poll.
            if "# Resident Scheduled Wake\n" in rendered:
                rendered = rendered.replace(
                    "# Resident Scheduled Wake\n",
                    "# Resident Scheduled Wake\n\n- status: consumed\n",
                    1,
                )
            else:
                rendered = "- status: consumed\n" + rendered
    else:
        rendered = "# Resident Scheduled Wake\n\n- status: consumed\n"
    return rendered.rstrip() + "\n" + f"- consumed_at: {consumed_at.isoformat()}\n"


def _scheduled_wake_at(content: str) -> datetime | None:
    """Parse the durable wake time, or None when it is absent or malformed."""
    match = re.search(r"^- wake_at:\s*(.+?)\s*$", content, flags=re.MULTILINE)
    if match is None:
        return None
    try:
        parsed = datetime.fromisoformat(_unquote(match.group(1).strip()))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _operator_marker_is_pending(content: str) -> bool:
    return bool(re.search(r"^- status:\s*pending\s*$", content, flags=re.MULTILINE))


def _operator_answer_is_consumed(content: str) -> bool:
    return bool(re.search(r"^- status:\s*consumed\s*$", content, flags=re.MULTILINE))


def _operator_marker_question(content: str) -> str:
    match = re.search(r"^- question:\s*(.+?)\s*$", content, flags=re.MULTILINE)
    return _unquote(match.group(1).strip()) if match else ""


def _unquote(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1].strip()
    return text


def _render_budget_snapshot(snapshot: ResidentBudgetSnapshot, *, updated_at: datetime) -> str:
    return (
        "# Resident Continuation Budget\n\n"
        f"- updated_at: {updated_at.isoformat()}\n"
        f"- case_id: {snapshot.case_id}\n"
        f"- root_correlation_id: {snapshot.root_correlation_id}\n"
        f"- turns_used: {snapshot.turns_used}\n"
        f"- elapsed_seconds: {snapshot.elapsed_seconds:.2f}\n"
        f"- input_tokens: {snapshot.usage.input_tokens}\n"
        f"- output_tokens: {snapshot.usage.output_tokens}\n"
        f"- total_tokens: {snapshot.total_tokens}\n"
        f"- cost_usd: {snapshot.cost_usd:.6f}\n"
    )


def _render_policy_observation(observation: ResidentPolicyObservation) -> str:
    return (
        f"# Resident Policy Observation: {observation.subject}\n\n"
        f"- status: {observation.status}\n"
        f"- source: {observation.source}\n"
        f"- created_at: {observation.created_at.isoformat()}\n\n"
        f"{observation.observation}\n"
    )


def _render_policy_decision(decision: ResidentPolicyDecisionRecord) -> str:
    boundaries = ", ".join(decision.risk_boundaries) if decision.risk_boundaries else "none"
    notes = "\n".join(f"- {note}" for note in decision.calibration_notes) or "- none"
    return (
        f"# Resident Policy Decision: {decision.action_title}\n\n"
        f"- created_at: {decision.created_at.isoformat()}\n"
        f"- turn: {decision.turn_index}\n"
        f"- decision_kind: {decision.decision_kind}\n"
        f"- allowed: {str(decision.allowed).lower()}\n"
        f"- needs_approval: {str(decision.needs_approval).lower()}\n"
        f"- risk_boundaries: {boundaries}\n"
        f"- reason: {_compact_line(decision.reason, limit=700)}\n"
        f"- question: {_compact_line(decision.question, limit=700)}\n\n"
        "## Action\n\n"
        f"{decision.action}\n\n"
        "## Calibration Notes\n\n"
        f"{notes}\n"
    )


def _parse_policy_observation(content: str) -> ResidentPolicyObservation | None:
    subject_match = re.search(
        r"^#\s*Resident Policy Observation:\s*(.+?)\s*$",
        content,
        flags=re.MULTILINE,
    )
    if not subject_match:
        return None
    status_match = re.search(r"^-\s*status:\s*(.+?)\s*$", content, flags=re.MULTILINE)
    source_match = re.search(r"^-\s*source:\s*(.+?)\s*$", content, flags=re.MULTILINE)
    created_match = re.search(r"^-\s*created_at:\s*(.+?)\s*$", content, flags=re.MULTILINE)
    created_at = datetime.now(UTC)
    if created_match:
        try:
            created_at = datetime.fromisoformat(created_match.group(1).strip())
        except ValueError:
            created_at = datetime.now(UTC)
    return ResidentPolicyObservation(
        subject=subject_match.group(1).strip(),
        observation=_policy_observation_body(content),
        source=(source_match.group(1).strip() if source_match else "memory"),
        status=(status_match.group(1).strip() if status_match else "candidate"),
        created_at=created_at,
    )


def _policy_observation_body(content: str) -> str:
    body_started = False
    lines: list[str] = []
    for line in content.splitlines():
        if not body_started:
            if line.strip() == "":
                body_started = True
            continue
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("#"):
            continue
        if stripped:
            lines.append(stripped)
    return _compact_line(" ".join(lines), limit=1000)


def _first_heading_or_line(content: str) -> str:
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        return line.removeprefix("#").strip() or line
    return ""
