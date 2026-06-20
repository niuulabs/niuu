"""Domain models for the wakeful resident runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from ravn.domain.resident_continuation import ResidentBudgetSnapshot
from ravn.domain.resident_expert import ResidentDomainExpertRun


class WakefulResidentDecisionKind(StrEnum):
    CONTINUE = "continue"
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
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class WakefulResidentRun:
    """Transparent result of one bounded wakeful runtime invocation."""

    mandate: str
    cycles: tuple[WakefulResidentCycleRecord, ...]
    final_decision: WakefulResidentDecisionKind
    final_reason: str
    budget: ResidentBudgetSnapshot


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
