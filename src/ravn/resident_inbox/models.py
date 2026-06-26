"""Resident inbox domain models, enums, and storage constants."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

_INBOX_SIGNAL_PREFIX = "resident/inbox/signals"
_INBOX_TRIAGE_PREFIX = "resident/inbox/triage"
_INBOX_DECISION_PREFIX = "resident/inbox/decisions"
_INBOX_SIGNAL_JSON_START = "<resident-inbox-signal-json>"
_INBOX_SIGNAL_JSON_END = "</resident-inbox-signal-json>"
_INBOX_TRIAGE_JSON_START = "<resident-inbox-triage-json>"
_INBOX_TRIAGE_JSON_END = "</resident-inbox-triage-json>"


class ResidentInboxClassification(StrEnum):
    FACT = "fact"
    PREFERENCE = "preference"
    POLICY = "policy"
    APPROVAL = "approval"
    DENIAL = "denial"
    TASK_REQUEST = "task_request"
    IDEA = "idea"
    SOURCE_EVIDENCE = "source_evidence"
    CORRECTION = "correction"
    RISK = "risk"
    STATUS_UPDATE = "status_update"
    FILE_REFERENCE = "file_reference"
    URL_REFERENCE = "url_reference"
    PHYSICAL_OBSERVATION = "physical_observation"
    UNKNOWN = "unknown"


class ResidentInboxStatus(StrEnum):
    NEW = "new"
    TRIAGED = "triaged"
    ATTACHED = "attached"
    CONVERTED = "converted"
    REMEMBERED = "remembered"
    IGNORED = "ignored"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ResidentInboxSignal:
    id: str
    source: str
    kind: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    raw_ref: str = ""
    classification: str = ResidentInboxClassification.UNKNOWN.value
    confidence: float = 0.5
    status: str = ResidentInboxStatus.NEW.value
    evidence_refs: tuple[str, ...] = ()
    target_objective_id: str = ""
    reason: str = ""
    observed_at: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    processed_at: datetime | None = None

    def with_updates(self, **updates: object) -> ResidentInboxSignal:
        return replace(self, **updates)  # type: ignore[arg-type]


@dataclass(frozen=True)
class ResidentInboxTriage:
    signal_id: str
    classification: str
    decision: str
    reason: str
    signal_ref: str
    objective_ref: str = ""
    memory_ref: str = ""
    target_objective_id: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class ResidentInboxRun:
    mandate: str
    processed: tuple[ResidentInboxTriage, ...]
    persisted_refs: tuple[str, ...]
    final_suggested_next_action: str


class ResidentInboxBackend(Protocol):
    async def write_signal(self, signal: ResidentInboxSignal) -> str: ...

    async def list_signals(
        self,
        *,
        status: str = ResidentInboxStatus.NEW.value,
        limit: int = 10,
    ) -> list[tuple[str, ResidentInboxSignal]]: ...

    async def write_triage(self, triage: ResidentInboxTriage) -> str: ...

    async def append_decision(self, entry: str) -> str: ...


@dataclass(frozen=True)
class ResidentInboxConfig:
    max_signals_per_wake: int = 5
    create_objectives: bool = True
    attach_to_existing_objectives: bool = True
    min_attach_score: int = 2
