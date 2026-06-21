"""Domain models for resident continuation.

The continuation kernel is intentionally backend-neutral.  It reasons about
turn outcomes, budget, memory, and policy observations, while execution stays
behind ``ExecutionAgentPort``.
"""

from __future__ import annotations

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
    selected_next_action: ResidentActionCandidate | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


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


@dataclass(frozen=True)
class ResidentContinuationDecision:
    kind: ContinuationDecisionKind
    reason: str
    action: ResidentActionCandidate | None = None
    prompt: str = ""
    question: str = ""


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

    async def write_turn(self, record: ResidentTurnRecord) -> str:
        """Persist one compact turn record and return its reference."""

    async def write_budget(self, snapshot: ResidentBudgetSnapshot) -> str:
        """Persist budget accounting and return its reference."""

    async def write_policy_observation(self, observation: ResidentPolicyObservation) -> str:
        """Persist an operator-derived policy/preference candidate."""

    async def write_operator_needed(
        self,
        *,
        question: str,
        reason: str,
        turn: ResidentTurnRecord,
    ) -> str:
        """Persist the latest pending operator question."""

    async def read_operator_needed(self) -> ResidentMemoryEntry | None:
        """Return the latest pending operator question when one exists."""

    async def write_operator_answer(self, answer: str) -> str:
        """Persist the latest operator answer and mark the pending question answered."""

    async def read_operator_answer(self) -> ResidentMemoryEntry | None:
        """Return the latest operator answer when one exists."""


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
    if not text:
        return None
    return ResidentActionCandidate(
        title=text[:80],
        action=text,
        reason=str(fields.get("rationale") or "").strip(),
    )
