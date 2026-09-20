"""Artifact-neutral contracts for deterministic evidence acceptance."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"
_SHA256 = r"^[a-f0-9]{64}$"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class EvidenceValidationError(ValueError):
    """Evidence is incomplete, stale, foreign, or unauthenticated."""


class EvidenceProvenance(_FrozenModel):
    """Signature made by a configured deployment/workload signing identity."""

    producer_id: str = Field(pattern=_IDENTIFIER)
    key_id: str = Field(pattern=_IDENTIFIER)
    signature: str = Field(min_length=1, repr=False)


class ArtifactIdentity(_FrozenModel):
    """Exact artifact version and attempt to which evidence is bound."""

    artifact_kind: str = Field(pattern=_IDENTIFIER)
    artifact_id: str = Field(min_length=1, max_length=2048)
    artifact_digest: str = Field(pattern=_SHA256)
    attempt_id: str = Field(pattern=_IDENTIFIER)
    execution_id: str = Field(default="", pattern=r"^(?:[A-Za-z0-9][A-Za-z0-9._:/-]*)?$")


class EvidenceResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"


class EvidenceReviewVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"


class EvidenceFindingDisposition(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    ACCEPTED = "accepted"


class EvidenceCheckConclusion(StrEnum):
    PASSING = "passing"
    FAILING = "failing"
    PENDING = "pending"
    CANCELED = "canceled"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"


class EvidenceFinding(_FrozenModel):
    finding_id: str = Field(pattern=_IDENTIFIER)
    blocking: bool
    disposition: EvidenceFindingDisposition
    evidence: str = Field(min_length=1, max_length=4096)


class EvidenceResultReceipt(_FrozenModel):
    receipt_id: str = Field(pattern=_IDENTIFIER)
    identity: ArtifactIdentity
    contract_id: str = Field(pattern=_IDENTIFIER)
    result: EvidenceResult
    result_digest: str = Field(pattern=_SHA256)
    completed_at: datetime
    provenance: EvidenceProvenance | None = None


class EvidenceReviewReceipt(_FrozenModel):
    receipt_id: str = Field(pattern=_IDENTIFIER)
    identity: ArtifactIdentity
    reviewer_id: str = Field(pattern=_IDENTIFIER)
    subject_worker_id: str = Field(pattern=_IDENTIFIER)
    role: str = Field(pattern=_IDENTIFIER)
    verdict: EvidenceReviewVerdict
    summary: str = Field(min_length=1, max_length=4096)
    findings: tuple[EvidenceFinding, ...] = ()
    provenance: EvidenceProvenance | None = None


class EvidenceCheck(_FrozenModel):
    name: str = Field(min_length=1)
    conclusion: EvidenceCheckConclusion


class EvidenceCheckReceipt(_FrozenModel):
    receipt_id: str = Field(pattern=_IDENTIFIER)
    identity: ArtifactIdentity
    checks: tuple[EvidenceCheck, ...]
    observed_at: datetime
    provenance: EvidenceProvenance | None = None


class EvidenceRequirement(_FrozenModel):
    requirement_id: str = Field(pattern=_IDENTIFIER)
    result_contract_ids: tuple[str, ...] = Field(min_length=1)


class EvidenceBundle(_FrozenModel):
    identity: ArtifactIdentity
    worker_id: str = Field(pattern=_IDENTIFIER)
    requirements: tuple[EvidenceRequirement, ...] = ()
    results: tuple[EvidenceResultReceipt, ...] = ()
    reviews: tuple[EvidenceReviewReceipt, ...] = ()
    checks: EvidenceCheckReceipt | None = None


class EvidencePolicy(_FrozenModel):
    required_review_roles: tuple[str, ...] = ()
    required_result_contract_ids: tuple[str, ...] = ()
    review_producers: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    result_producers: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    required_check_names: tuple[str, ...] = ()
    require_checks: bool = False
    check_producers: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_producer_pins(self):
        if not (
            self.required_review_roles
            or self.required_result_contract_ids
            or self.required_check_names
            or self.require_checks
        ):
            raise ValueError("Evidence policy must require at least one result, review, or check")
        missing_reviews = set(self.required_review_roles) - self.review_producers.keys()
        missing_results = set(self.required_result_contract_ids) - self.result_producers.keys()
        if missing_reviews or missing_results:
            raise ValueError("Every required review and result must pin trusted producers")
        if any(not self.review_producers[role] for role in self.required_review_roles):
            raise ValueError("Required review producer sets must not be empty")
        if any(not self.result_producers[item] for item in self.required_result_contract_ids):
            raise ValueError("Required result producer sets must not be empty")
        if (self.require_checks or self.required_check_names) and not self.check_producers:
            raise ValueError("Checks must pin trusted producers")
        return self


class EvidenceValidationReport(_FrozenModel):
    accepted: bool
    blocking_reasons: tuple[str, ...]
    manifest_digest: str = Field(pattern=_SHA256)


def evidence_payload(model: BaseModel) -> bytes:
    """Canonical bytes signed for one evidence object, excluding provenance."""
    data = model.model_dump(mode="json", exclude={"provenance"})
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def evidence_digest(model: BaseModel) -> str:
    return hashlib.sha256(evidence_payload(model)).hexdigest()
