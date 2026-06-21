"""Domain models for the wakeful resident runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from ravn.domain.resident_continuation import ResidentBudgetSnapshot
from ravn.domain.resident_expert import ResidentDomainExpertRun
from ravn.domain.resident_portfolio import (
    ResidentObjectiveDryRunPreview,
    ResidentPortfolioStewardRun,
)


class WakefulResidentDecisionKind(StrEnum):
    CONTINUE = "continue"
    ASK_OPERATOR = "ask_operator"
    SLEEP = "sleep"
    STOP = "stop"


class WakefulPortfolioStewardActionKind(StrEnum):
    RUN_STEWARD = "run_steward"
    ASK_OPERATOR = "ask_operator"
    SLEEP = "sleep"
    STOP = "stop"


@dataclass(frozen=True)
class WakefulResidentCycleRecord:
    """Compact persisted record of one wake cycle."""

    cycle_number: int
    mandate: str
    prior_domain_model_ref: str
    attention_reason: str
    selected_action: str
    work_created_or_advanced: tuple[str, ...]
    artifact_refs: tuple[str, ...]
    finding_summaries: tuple[str, ...]
    decision: WakefulResidentDecisionKind
    decision_reason: str
    budget: ResidentBudgetSnapshot
    runtime_audit: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class WakefulResidentRun:
    """Transparent result of one bounded wakeful runtime invocation."""

    mandate: str
    cycles: tuple[WakefulResidentCycleRecord, ...]
    final_decision: WakefulResidentDecisionKind
    final_reason: str
    budget: ResidentBudgetSnapshot


@dataclass(frozen=True)
class WakefulPortfolioStewardRecord:
    """Persisted record of one wakeful portfolio stewardship pass."""

    wake_number: int
    mandate: str
    attention_reason: str
    validation_verdict: str
    selected_objective: ResidentObjectiveDryRunPreview | None
    action_taken: WakefulPortfolioStewardActionKind
    steward_pass_count: int = 0
    steward_final_action: str = ""
    steward_summary: tuple[str, ...] = ()
    operator_questions: tuple[str, ...] = ()
    persisted_refs: tuple[str, ...] = ()
    budget: ResidentBudgetSnapshot = field(default_factory=ResidentBudgetSnapshot)
    final_suggested_next_action: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class WakefulPortfolioStewardRun:
    """Transparent result of bounded wakeful portfolio stewardship."""

    mandate: str
    records: tuple[WakefulPortfolioStewardRecord, ...]
    final_action: WakefulPortfolioStewardActionKind
    final_reason: str
    budget: ResidentBudgetSnapshot = field(default_factory=ResidentBudgetSnapshot)


class WakefulResidentMemoryPort(Protocol):
    """Persistence boundary for wake cycle records."""

    async def list_wake_records(
        self,
        mandate: str,
        *,
        limit: int = 5,
    ) -> list[WakefulResidentCycleRecord]:
        """Return recent wake cycle records."""

    async def write_wake_record(self, record: WakefulResidentCycleRecord) -> str:
        """Persist one wake cycle record and return its reference."""


class ResidentExpertLoopPort(Protocol):
    """Boundary for running one bounded resident expert pass."""

    async def run(self, mandate: str) -> ResidentDomainExpertRun:
        """Run one bounded resident expert pass from the mandate."""


class WakefulPortfolioStewardMemoryPort(Protocol):
    """Persistence boundary for wakeful portfolio stewardship records."""

    async def list_records(
        self,
        mandate: str,
        *,
        limit: int = 5,
    ) -> list[WakefulPortfolioStewardRecord]:
        """Return recent wakeful portfolio stewardship records."""

    async def write_record(self, record: WakefulPortfolioStewardRecord) -> str:
        """Persist one wakeful portfolio stewardship record and return its reference."""


class ResidentPortfolioStewardPort(Protocol):
    """Boundary for running bounded portfolio stewardship from wakefulness."""

    async def run(self, mandate: str) -> ResidentPortfolioStewardRun:
        """Run bounded resident portfolio stewardship."""
