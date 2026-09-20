"""Durable, identity-bound observations of remote developer delivery state."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from niuu.domain.delivery import CheckReceipt, MergeReceipt, ReviewCandidate

_GIT_SHA = r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$"


class DeveloperDeliveryWaitMode(StrEnum):
    CHECKS = "checks"
    MERGE = "merge"


class DeveloperDeliveryWaitState(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"
    NOTIFIED = "notified"


class DeveloperDeliveryObservationStatus(StrEnum):
    CHECKS_PENDING = "checks_pending"
    CHECKS_PASSED = "checks_passed"
    CHECKS_FAILED = "checks_failed"
    MERGE_PENDING = "merge_pending"
    MERGED = "merged"
    MERGE_FAILED = "merge_failed"
    STALE_CANDIDATE = "stale_candidate"


_TERMINAL_OBSERVATION_STATUSES = frozenset(
    {
        DeveloperDeliveryObservationStatus.CHECKS_PASSED,
        DeveloperDeliveryObservationStatus.CHECKS_FAILED,
        DeveloperDeliveryObservationStatus.MERGED,
        DeveloperDeliveryObservationStatus.MERGE_FAILED,
        DeveloperDeliveryObservationStatus.STALE_CANDIDATE,
    }
)


class DeveloperDeliveryWaitRequest(BaseModel):
    """Exact remote review identity selected by the parent coordinator."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    mode: DeveloperDeliveryWaitMode
    repository: str = Field(min_length=1)
    review_number: int = Field(
        gt=0,
        validation_alias=AliasChoices("review_number", "reviewNumber"),
    )
    expected_head_sha: str = Field(
        pattern=_GIT_SHA,
        validation_alias=AliasChoices("expected_head_sha", "expectedHeadSha"),
    )
    expected_base_sha: str = Field(
        pattern=_GIT_SHA,
        validation_alias=AliasChoices("expected_base_sha", "expectedBaseSha"),
    )
    expected_target_branch: str = Field(
        min_length=1,
        validation_alias=AliasChoices("expected_target_branch", "expectedTargetBranch"),
    )
    policy_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("policy_id", "policyId"),
    )
    method: str | None = Field(default=None, pattern=r"^(merge|squash|rebase)$")
    provider_operation_id: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("provider_operation_id", "providerOperationId"),
    )

    @model_validator(mode="after")
    def validate_mode_fields(self):
        if self.mode is DeveloperDeliveryWaitMode.MERGE and self.method is None:
            raise ValueError("merge delivery waits require a merge method")
        if self.mode is DeveloperDeliveryWaitMode.CHECKS and self.method is not None:
            raise ValueError("checks delivery waits cannot specify a merge method")
        if self.mode is DeveloperDeliveryWaitMode.CHECKS and self.provider_operation_id is not None:
            raise ValueError("checks delivery waits cannot specify a provider operation ID")
        return self

    def canonical_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", by_alias=False)
        if self.provider_operation_id is None:
            payload.pop("provider_operation_id", None)
        return payload

    @property
    def digest(self) -> str:
        payload = json.dumps(
            self.canonical_payload(), sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        return hashlib.sha256(payload.encode()).hexdigest()


class DeveloperDeliveryObservation(BaseModel):
    """One provider-authenticated, mechanically classified review observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: DeveloperDeliveryObservationStatus
    repository: str = Field(min_length=1)
    review_number: int = Field(gt=0)
    expected_head_sha: str = Field(pattern=_GIT_SHA)
    expected_base_sha: str = Field(pattern=_GIT_SHA)
    expected_target_branch: str = Field(min_length=1)
    observed_at: datetime
    reason: str = ""
    candidate: ReviewCandidate | None = None
    checks: CheckReceipt | None = None
    merge_receipt: MergeReceipt | None = None

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL_OBSERVATION_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "repository": self.repository,
            "reviewNumber": self.review_number,
            "expectedHeadSha": self.expected_head_sha,
            "expectedBaseSha": self.expected_base_sha,
            "expectedTargetBranch": self.expected_target_branch,
            "observedAt": self.observed_at.isoformat(),
            "reason": self.reason,
            "candidate": (
                self.candidate.model_dump(mode="json") if self.candidate is not None else None
            ),
            "checks": self.checks.model_dump(mode="json") if self.checks is not None else None,
            "mergeReceipt": (
                self.merge_receipt.model_dump(mode="json")
                if self.merge_receipt is not None
                else None
            ),
        }


@dataclass(frozen=True)
class DeveloperDeliveryWait:
    id: UUID
    execution_id: UUID
    request: DeveloperDeliveryWaitRequest
    request_digest: str
    execution_generation: int
    execution_revision: int
    candidate_digest: str
    state: DeveloperDeliveryWaitState
    next_poll_at: datetime
    observation: DeveloperDeliveryObservation | None = None
    attempt_count: int = 0
    last_error: str = ""
    lease_owner: str = ""
    lease_token: UUID | None = None
    fencing_generation: int = 0
    lease_expires_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    notified_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "waitId": str(self.id),
            "executionId": str(self.execution_id),
            "mode": self.request.mode.value,
            "state": self.state.value,
            "requestDigest": self.request_digest,
            "generation": self.execution_generation,
            "executionRevision": self.execution_revision,
            "candidateDigest": self.candidate_digest,
            "request": {
                "repository": self.request.repository,
                "reviewNumber": self.request.review_number,
                "expectedHeadSha": self.request.expected_head_sha,
                "expectedBaseSha": self.request.expected_base_sha,
                "expectedTargetBranch": self.request.expected_target_branch,
                "policyId": self.request.policy_id,
                "method": self.request.method,
                **(
                    {"providerOperationId": self.request.provider_operation_id}
                    if self.request.provider_operation_id is not None
                    else {}
                ),
            },
            "nextPollAt": self.next_poll_at.isoformat(),
            "attemptCount": self.attempt_count,
            "lastError": self.last_error,
            "observation": self.observation.to_dict() if self.observation is not None else None,
        }


def developer_delivery_candidate_digest(
    *,
    integration_candidate: dict[str, Any] | None,
    integration_allocation: dict[str, Any] | None,
) -> str:
    """Bind a wait to the exact persisted candidate and its allocation."""
    payload = json.dumps(
        {
            "integrationAllocation": integration_allocation,
            "integrationCandidate": integration_candidate,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def developer_delivery_wait_digest(
    request: DeveloperDeliveryWaitRequest,
    *,
    execution_generation: int,
    candidate_digest: str,
) -> str:
    payload = json.dumps(
        {
            "candidateDigest": candidate_digest,
            "executionGeneration": execution_generation,
            "request": request.canonical_payload(),
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()
