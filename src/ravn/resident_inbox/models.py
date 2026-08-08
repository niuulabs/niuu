"""Resident inbox domain models, enums, and storage constants."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from .shape import ShapeAggregate

_INBOX_SIGNAL_PREFIX = "resident/inbox/signals"
#: Coalescing queue: at most one slot per structural shape awaiting judgment.
_INBOX_PENDING_PREFIX = "resident/inbox/signals/pending"
#: Judged slots, subject to normal retention.
_INBOX_PROCESSED_PREFIX = "resident/inbox/signals/processed"
_INBOX_TRIAGE_PREFIX = "resident/inbox/triage"
_INBOX_DECISION_PREFIX = "resident/inbox/decisions"
_INBOX_SIGNAL_JSON_START = "<resident-inbox-signal-json>"
_INBOX_SIGNAL_JSON_END = "</resident-inbox-signal-json>"
_INBOX_TRIAGE_JSON_START = "<resident-inbox-triage-json>"
_INBOX_TRIAGE_JSON_END = "</resident-inbox-triage-json>"

# Signal kind marking a directed operator message (the only kind allowed to
# resolve a pending operator objective).
_OPERATOR_DIRECTED_MESSAGE_KIND = "operator.directed_message"


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
    trace_context: dict[str, str] = field(default_factory=dict)
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
    #: Structural slot identity. Observations sharing a shape share a slot.
    shape_key: str = ""
    #: How many raw observations this slot covers. 1 for an uncoalesced record.
    observation_count: int = 1
    #: Exact raw archive range this slot covers, inclusive.
    first_archive_ref: str = ""
    last_archive_ref: str = ""
    #: Oldest observation in the slot; ``observed_at`` tracks the newest.
    first_observed_at: str = ""
    #: Variation across the slot's observations. Never merges across shapes.
    aggregate: ShapeAggregate = field(default_factory=ShapeAggregate)
    #: Resident turns that ended with an invalid outcome for this slot.
    attempts: int = 0

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
    async def write_event(self, event: Any) -> str: ...

    async def load_seen_signal_keys(self) -> list[str]:
        """Identities this resident has already ingested, oldest first.

        Optional: a backend that keeps no durable dedupe record returns nothing
        and the caller falls back to an in-process cache, which is what every
        resident did before this existed.
        """
        return []

    async def record_seen_signal_keys(self, keys: Sequence[str]) -> None:
        """Persist *keys* as ingested. A no-op for backends without the store."""
        return None

    async def write_directed_message(
        self,
        *,
        content: str,
        metadata: dict[str, Any] | None = None,
        source: str = "skuld:directed_message",
    ) -> str: ...

    async def write_signal(self, signal: ResidentInboxSignal) -> str: ...

    async def list_signals(
        self,
        *,
        status: str = ResidentInboxStatus.NEW.value,
        limit: int = 10,
    ) -> list[tuple[str, ResidentInboxSignal]]: ...

    async def write_triage(self, triage: ResidentInboxTriage) -> str: ...

    async def append_decision(self, entry: str) -> str: ...

    async def acknowledge(
        self,
        refs: tuple[str, ...],
        *,
        status: str = ResidentInboxStatus.REMEMBERED.value,
        reason: str = "resident turn recorded",
        expected: Mapping[str, str] | None = None,
    ) -> tuple[str, ...]:
        """Acknowledge exactly what the resident judged.

        ``expected`` maps each ref to the archive reference the caller saw when
        it listed the slot.  A slot that advanced while the turn ran is split:
        the judged range is acknowledged and the newer observations stay
        pending, so arrivals mid-turn are never silently swallowed.
        """
        ...

    async def record_failed_attempt(
        self,
        refs: tuple[str, ...],
        *,
        reason: str,
    ) -> tuple[str, ...]:
        """Count one invalid resident outcome; return refs that became blocked.

        A slot no resident turn can validly judge must stop being retried
        forever and become visible to a human instead.
        """
        ...


@dataclass(frozen=True)
class ResidentInboxConfig:
    max_signals_per_wake: int = 5
    create_objectives: bool = True
    attach_to_existing_objectives: bool = True
    min_attach_score: int = 2
