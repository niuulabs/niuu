"""Domain models for resident continuation.

The continuation kernel is intentionally backend-neutral.  It reasons about
turn outcomes, budget, memory, and policy observations, while execution stays
behind ``ExecutionAgentPort``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from ravn.domain.models import TokenUsage, TurnResult


class ContinuationDecisionKind(StrEnum):
    CONTINUE = "continue"
    ASK_OPERATOR = "ask_operator"
    SLEEP = "sleep"
    STOP = "stop"


class RiskBoundary(StrEnum):
    SPENDING = "spending"
    PHYSICAL_OPERATION = "physical_operation"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    DESTRUCTIVE_CHANGE = "destructive_change"
    PRODUCTION_CHANGE = "production_change"
    CREDENTIAL_USE = "credential_use"


@dataclass(frozen=True)
class ResidentActionCandidate:
    """A self-authored action extracted from a resident outcome."""

    title: str
    action: str
    reason: str
    source: str = "selected_next_action"
    risk_boundaries: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        return " ".join(part for part in (self.title, self.action, self.reason) if part)


@dataclass(frozen=True)
class ResidentPolicyObservation:
    """A learned preference or permission candidate to persist for review."""

    subject: str
    observation: str
    source: str
    status: str = "candidate"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class ResidentPolicyDecisionRecord:
    """Persistable audit record for one resident policy decision."""

    turn_index: int
    action_title: str
    action: str
    decision_kind: str
    allowed: bool
    needs_approval: bool
    reason: str
    risk_boundaries: tuple[str, ...] = ()
    question: str = ""
    calibration_notes: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class ResidentMemoryEntry:
    """Compact resident memory recalled before a continuation decision."""

    path: str
    summary: str
    content: str = ""


@dataclass(frozen=True)
class ResidentBudgetLimits:
    """Generic budget caps for one resident continuation run."""

    max_turns: int = 3
    max_wall_clock_seconds: float = 900.0
    max_tokens: int = 0
    max_cost_usd: float = 0.0


@dataclass(frozen=True)
class ResidentBudgetSnapshot:
    """Budget state at a decision point."""

    turns_used: int = 0
    elapsed_seconds: float = 0.0
    usage: TokenUsage = field(default_factory=lambda: TokenUsage(input_tokens=0, output_tokens=0))
    cost_usd: float = 0.0
    case_id: str = ""
    root_correlation_id: str = ""

    @property
    def total_tokens(self) -> int:
        return self.usage.total_tokens


@dataclass(frozen=True)
class ResidentBudgetDecision:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class ResidentTurnRecord:
    """Persistable record of one resident turn."""

    turn_index: int
    prompt: str
    response: str
    outcome_fields: dict[str, Any]
    tool_names: tuple[str, ...]
    usage: TokenUsage
    tool_results: tuple[str, ...] = ()
    mandate: str = ""
    cumulative_usage: TokenUsage = field(
        default_factory=lambda: TokenUsage(input_tokens=0, output_tokens=0)
    )
    selected_next_action: ResidentActionCandidate | None = None
    case_id: str = ""
    root_correlation_id: str = ""
    task_id: str = ""
    triggered_by: str = ""
    persona: str = ""
    evidence_refs: tuple[str, ...] = ()
    inbox_refs: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class ResidentWorkingStateRecord:
    """The resident's explicit, revisable model of its current reality."""

    resident_id: str
    state: dict[str, Any]
    source_turn_ref: str
    source_case_id: str
    source_task_id: str
    signal_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class ResidentA2ATaskRecord:
    """Durable handle for a peer task started or observed by a resident."""

    task_id: str
    agent_id: str = ""
    skill_id: str = ""
    state: str = "TASK_STATE_UNSPECIFIED"
    operation: str = ""
    prompt: str = ""
    status_message: str = ""
    question: str = ""
    case_id: str = ""
    root_correlation_id: str = ""
    parent_task_id: str = ""
    mandate: str = ""
    turn_index: int = 0
    case_input_tokens: int = 0
    case_output_tokens: int = 0
    case_started_at: str = ""
    push_registered: bool | None = None
    update_fingerprint: str = ""
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class ResidentScheduledWakeRecord:
    """A resident-requested wake for one durable case at a future time.

    Written when a turn selects ``sleep`` with ``next_action_timing:
    scheduled_time``.  Without it the case has no wake source and the runtime
    would silently forget a decision the model actually made.
    """

    case_id: str
    root_correlation_id: str
    wake_at: datetime
    reason: str
    mandate: str = ""
    turn_index: int = 0
    turn_ref: str = ""
    persona: str = ""
    task_id: str = ""
    case_input_tokens: int = 0
    case_output_tokens: int = 0
    case_started_at: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class ResidentDecisionStreakRecord:
    """How many turns in a row reached the same conclusion without moving.

    Per-case budgets cannot see this: a resident that re-derives one verdict in
    a fresh case every cron tick never accumulates turns in any single case, so
    it can repeat itself indefinitely while every individual case looks healthy.
    The streak is therefore keyed by the resident and the shape of what it
    decided, and survives both case boundaries and restarts.
    """

    resident_id: str
    fingerprint: str
    count: int
    decision: str = ""
    rationale: str = ""
    case_id: str = ""
    first_seen_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


RESIDENT_WORKING_STATE_FIELDS = (
    "objectives",
    "observations",
    "hypotheses",
    "unknowns",
    "capability_gaps",
    "attempts",
)
RESIDENT_WORKING_STATE_MAX_ENTRIES = 5
RESIDENT_WORKING_STATE_MAX_ENTRY_CHARS = 500


def validate_resident_working_state(value: Any) -> list[str]:
    """Validate snapshot structure without judging or rewriting model-authored content."""
    if not isinstance(value, Mapping):
        return ["working_state must be a mapping"]
    errors: list[str] = []
    for field_name in RESIDENT_WORKING_STATE_FIELDS:
        if field_name == "objectives" and field_name not in value:
            continue
        entries = value.get(field_name)
        if not isinstance(entries, list):
            errors.append(f"working_state.{field_name} must be a list")
            continue
        for index, entry in enumerate(entries):
            if isinstance(entry, str):
                valid = bool(entry.strip())
            else:
                valid = isinstance(entry, Mapping) and bool(entry)
            if not valid:
                errors.append(
                    f"working_state.{field_name}[{index}] must be a non-empty string or mapping"
                )
                continue
            rendered = (
                entry
                if isinstance(entry, str)
                else json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str)
            )
            if len(rendered) > RESIDENT_WORKING_STATE_MAX_ENTRY_CHARS:
                errors.append(
                    f"working_state.{field_name}[{index}] exceeds "
                    f"{RESIDENT_WORKING_STATE_MAX_ENTRY_CHARS} characters"
                )
        if len(entries) > RESIDENT_WORKING_STATE_MAX_ENTRIES:
            errors.append(
                f"working_state.{field_name} has {len(entries)} entries; "
                f"maximum is {RESIDENT_WORKING_STATE_MAX_ENTRIES}"
            )
    return errors


def resident_working_state_from_outcome(fields: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return a complete model-authored snapshot, or None when absent/invalid."""
    state = fields.get("working_state")
    if validate_resident_working_state(state):
        return None
    assert isinstance(state, Mapping)
    return {str(key): value for key, value in state.items() if str(key).strip()}


@dataclass(frozen=True)
class ResidentContinuationContext:
    """Inputs used to decide whether/how a resident should continue."""

    mandate: str
    turn_record: ResidentTurnRecord
    budget: ResidentBudgetSnapshot
    recent_memory: tuple[ResidentMemoryEntry, ...] = ()
    available_tools: tuple[str, ...] = ()
    policy_observations: tuple[ResidentPolicyObservation, ...] = ()


@dataclass(frozen=True)
class ResidentPolicyDecision:
    allowed: bool
    needs_approval: bool
    reason: str
    risk_boundaries: tuple[str, ...] = ()
    question: str = ""
    calibration_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResidentContinuationDecision:
    kind: ContinuationDecisionKind
    reason: str
    action: ResidentActionCandidate | None = None
    prompt: str = ""
    question: str = ""
    risk_boundaries: tuple[str, ...] = ()
    calibration_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResidentContinuationRun:
    """Transparent result of a bounded continuation run."""

    mandate: str
    decisions: tuple[ResidentContinuationDecision, ...]
    turns: tuple[ResidentTurnRecord, ...]
    budget: ResidentBudgetSnapshot
    policy_observations: tuple[ResidentPolicyObservation, ...] = ()

    @property
    def final_decision(self) -> ResidentContinuationDecision | None:
        return self.decisions[-1] if self.decisions else None


class ResidentMemoryPort(Protocol):
    """Persistence boundary for resident continuation state."""

    async def recall(self, mandate: str, *, limit: int = 5) -> list[ResidentMemoryEntry]:
        """Return recent relevant resident memory."""

    async def read(self, ref: str) -> ResidentMemoryEntry | None:
        """Read one exact durable resident record by reference."""

    async def write_turn(self, record: ResidentTurnRecord) -> str:
        """Persist one compact turn record and return its reference."""

    async def write_budget(self, snapshot: ResidentBudgetSnapshot) -> str:
        """Persist budget accounting and return its reference."""

    async def write_policy_observation(self, observation: ResidentPolicyObservation) -> str:
        """Persist an operator-derived policy/preference candidate."""

    async def list_policy_observations(self) -> list[ResidentPolicyObservation]:
        """Return persisted policy/preference observations."""

    async def write_policy_decision(self, decision: ResidentPolicyDecisionRecord) -> str:
        """Persist an auditable policy decision."""

    async def write_operator_needed(
        self,
        *,
        question: str,
        reason: str,
        turn: ResidentTurnRecord,
        case_id: str = "",
        turn_ref: str = "",
    ) -> str:
        """Persist the latest pending operator question."""

    async def read_operator_needed(self, case_id: str = "") -> ResidentMemoryEntry | None:
        """Return the latest pending operator question when one exists."""

    async def write_operator_answer(self, answer: str, *, case_id: str = "") -> str:
        """Persist the latest operator answer and mark the pending question answered."""

    async def read_operator_answer(self, case_id: str = "") -> ResidentMemoryEntry | None:
        """Return the latest operator answer when one exists."""

    async def consume_operator_answer(self, answer: ResidentMemoryEntry) -> str:
        """Mark an operator answer as consumed after it has resumed resident work."""

    async def list_operator_needed(self) -> list[ResidentMemoryEntry]:
        """Return every pending operator question."""

    async def list_operator_answers(self) -> list[ResidentMemoryEntry]:
        """Return every unconsumed operator answer."""


class ResidentPolicyPort(Protocol):
    """Policy boundary for action approval and learned preferences."""

    async def assess(
        self,
        action: ResidentActionCandidate,
        *,
        context: ResidentContinuationContext,
    ) -> ResidentPolicyDecision:
        """Assess whether an action may proceed."""


class ResidentBudgetPort(Protocol):
    """Budget boundary for continuation decisions."""

    def snapshot(self) -> ResidentBudgetSnapshot:
        """Return current budget usage."""

    def can_continue(self) -> ResidentBudgetDecision:
        """Return whether another turn may be started."""

    def record_turn(self, result: TurnResult) -> ResidentBudgetSnapshot:
        """Record model usage for a completed turn."""


def selected_action_from_outcome(fields: dict[str, Any]) -> ResidentActionCandidate | None:
    """Extract a generic selected action from parsed outcome fields."""
    raw = fields.get("selected_next_action")
    if raw is None:
        return None
    if isinstance(raw, dict):
        title = str(raw.get("title") or raw.get("name") or "Selected next action").strip()
        action = str(
            raw.get("action") or raw.get("next_step") or raw.get("description") or ""
        ).strip()
        reason = str(
            raw.get("reason") or raw.get("rationale") or fields.get("rationale") or ""
        ).strip()
        boundaries = tuple(
            str(item).strip() for item in raw.get("risk_boundaries", []) if str(item).strip()
        )
        capabilities = tuple(
            str(item).strip() for item in raw.get("required_capabilities", []) if str(item).strip()
        )
        return ResidentActionCandidate(
            title=title or "Selected next action",
            action=action or title,
            reason=reason,
            risk_boundaries=boundaries,
            required_capabilities=capabilities,
        )

    text = str(raw).strip()
    if not text or text.casefold() in {"none", "n/a", "no action", "stop"}:
        return None
    return ResidentActionCandidate(
        title=text[:80],
        action=text,
        reason=str(fields.get("rationale") or "").strip(),
    )
