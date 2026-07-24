"""Slim typed resident memory: budget tracking and durable memory records."""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ravn.domain.models import TokenUsage, TurnResult
from ravn.domain.resident_continuation import (
    ResidentBudgetDecision,
    ResidentBudgetLimits,
    ResidentBudgetSnapshot,
    ResidentMemoryEntry,
    ResidentMemoryPort,
    ResidentPolicyDecisionRecord,
    ResidentPolicyObservation,
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

_OPERATOR_NEEDED_PATH = "operator-needed/latest.md"
_OPERATOR_ANSWER_PATH = "operator-answers/latest.md"


def _case_path(case_id: str, leaf: str) -> Path:
    case_slug = _slug(case_id)
    return Path("cases") / case_slug / leaf if case_slug else Path(leaf)


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

    def __init__(self, root: Path, *, prefix: str = "resident/continuation") -> None:
        self._root = Path(root)
        self._prefix = Path(prefix.strip("/").strip() or "resident/continuation")

    async def recall(self, mandate: str, *, limit: int = 5) -> list[ResidentMemoryEntry]:
        base = self._root / self._prefix
        if not base.exists():
            return []
        files = sorted(base.rglob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
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
        return self._write(rel, _render_turn_record(record))

    async def write_working_state(self, record: ResidentWorkingStateRecord) -> str:
        return self._write(
            self._working_state_path(record.resident_id), _render_working_state(record)
        )

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
