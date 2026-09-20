"""Durable, identity-bound observations of an external wait condition.

A ``wait`` node in a workflow graph suspends its parent execution until an
opaque condition external to the engine is observed: a CI check, a
publication event, a timer, a tracker issue, anything. The condition's
meaning belongs entirely to
whichever ``WaitConditionObserver`` is registered for its ``condition_type``
(see ``ting.ports.workflow_wait``); this module owns only the identity, state
machine, and lease mechanics of the wait itself, never what the condition
means.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from ting.domain.workflow_execution import WorkflowExecutionError

WAIT_SUSPENSION_PREFIX = "awaiting:"
"""Suspension reason prefix while a wait is pending: ``awaiting:<condition_type>``."""

WAIT_OBSERVED_SUSPENSION_REASON = "wait_observed"
"""Suspension reason recorded once a wait's terminal observation is satisfied."""

WAIT_FAILURE_PREFIX = "wait_failed: "
"""Prefix for the suspension reason recorded when a wait terminally fails."""


class WorkflowWaitState(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"
    NOTIFIED = "notified"


class WaitObservationStatus(StrEnum):
    PENDING = "pending"
    SATISFIED = "satisfied"
    FAILED = "failed"


_TERMINAL_OBSERVATION_STATUSES = frozenset(
    {WaitObservationStatus.SATISFIED, WaitObservationStatus.FAILED}
)


@dataclass(frozen=True)
class WaitObservation:
    """One condition-agnostic read of an external wait condition.

    ``detail`` carries whatever the observer's own condition type needs to
    explain the observation (remote check names, a publication receipt, a
    resolved timer deadline, ...); the generic wait machinery never reads it.
    """

    status: WaitObservationStatus
    observed_at: datetime
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    retry_after_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.retry_after_seconds is not None and self.retry_after_seconds < 0:
            raise WorkflowExecutionError("wait observation retry_after_seconds cannot be negative")
        try:
            json.dumps(self.detail, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise WorkflowExecutionError(
                "wait observation detail must be finite JSON-compatible values"
            ) from exc

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL_OBSERVATION_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "observedAt": self.observed_at.isoformat(),
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class WorkflowWait:
    """One durable, leaseable registration of a wait against a pinned execution."""

    id: UUID
    execution_id: UUID
    node_id: str
    condition_type: str
    request: dict[str, Any]
    request_digest: str
    execution_generation: int
    execution_revision: int
    state: WorkflowWaitState
    next_poll_at: datetime
    observation: WaitObservation | None = None
    attempt_count: int = 0
    last_error: str = ""
    lease_owner: str = ""
    lease_token: UUID | None = None
    fencing_generation: int = 0
    lease_expires_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    notified_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.node_id.strip():
            raise WorkflowExecutionError("wait node_id is required")
        if not self.condition_type.strip():
            raise WorkflowExecutionError("wait condition_type is required")
        if not self.request_digest.strip():
            raise WorkflowExecutionError("wait request_digest is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "waitId": str(self.id),
            "executionId": str(self.execution_id),
            "nodeId": self.node_id,
            "conditionType": self.condition_type,
            "state": self.state.value,
            "requestDigest": self.request_digest,
            "generation": self.execution_generation,
            "executionRevision": self.execution_revision,
            "request": self.request,
            "nextPollAt": self.next_poll_at.isoformat(),
            "attemptCount": self.attempt_count,
            "lastError": self.last_error,
            "observation": self.observation.to_dict() if self.observation is not None else None,
        }


def wait_suspension_reason(condition_type: str) -> str:
    """Return the execution suspension reason recorded while a wait is pending."""
    return f"{WAIT_SUSPENSION_PREFIX}{condition_type}"


def is_wait_suspension_reason(suspension_reason: str) -> bool:
    """True once a suspension reason means the execution moved past its join into a wait.

    Covers a pending wait (``awaiting:<condition_type>``), a satisfied wait
    (``wait_observed``), and a terminally failed wait (``wait_failed: ...``).
    """
    return (
        suspension_reason.startswith(WAIT_SUSPENSION_PREFIX)
        or suspension_reason == WAIT_OBSERVED_SUSPENSION_REASON
        or suspension_reason.startswith(WAIT_FAILURE_PREFIX)
    )


def workflow_wait_digest(
    *,
    node_id: str,
    condition_type: str,
    request: dict[str, Any],
    execution_generation: int,
) -> str:
    """Bind a wait to its exact node, condition, request, and execution generation."""
    payload = json.dumps(
        {
            "nodeId": node_id,
            "conditionType": condition_type,
            "executionGeneration": execution_generation,
            "request": request,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()
