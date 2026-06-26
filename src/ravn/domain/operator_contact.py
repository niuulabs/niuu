"""Shared operator contact primitives.

These models describe the need for human judgment. They are deliberately
transport-neutral: interactive sessions can answer them directly, while daemon
sessions can emit existing ``help_needed`` events through the normal channel
stack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class OperatorContactKind(StrEnum):
    ASK_USER = "ask_user"
    HELP_NEEDED = "help_needed"


class OperatorContactPurpose(StrEnum):
    CLARIFICATION = "clarification"
    APPROVAL = "approval"
    SUMMARY = "summary"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    INTERESTING_FINDING = "interesting_finding"
    RECOMMENDATION = "recommendation"


class OperatorContactStatus(StrEnum):
    PENDING = "pending"
    ANSWERED = "answered"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


@dataclass(frozen=True)
class OperatorContactRequest:
    question: str
    reason: str
    impact: str
    kind: OperatorContactKind = OperatorContactKind.HELP_NEEDED
    purpose: OperatorContactPurpose = OperatorContactPurpose.CLARIFICATION
    id: str = ""
    source_objective_id: str = ""
    risk_boundaries: tuple[str, ...] = ()
    tool_name: str = ""
    tool_input: dict[str, str] = field(default_factory=dict)
    help_needed_outcome: dict[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class OperatorContactResult:
    request: OperatorContactRequest
    status: str
    answer: str = ""
    approved: bool | None = None
    emitted_ref: str = ""
    responded_at: datetime | None = None
