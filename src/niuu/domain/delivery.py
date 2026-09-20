"""Immutable contracts for evidence-backed software delivery.

These models carry mechanical facts between Ting, Forge, and runtime workers.
They deliberately contain no model judgment: acceptance is a deterministic
comparison of identities, required receipts, and policy.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from niuu.domain.evidence import EvidenceProvenance
from niuu.domain.evidence import EvidenceValidationError as EvidenceValidationError
from niuu.domain.evidence import EvidenceValidationReport as EvidenceValidationReport
from niuu.domain.evidence import evidence_digest as evidence_digest
from niuu.domain.evidence import evidence_payload as evidence_payload

_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"
_GIT_SHA = r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$"
_SHA256 = r"^[a-f0-9]{64}$"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class WorkstreamSpec(_FrozenModel):
    campaign_id: str = Field(pattern=_IDENTIFIER)
    workstream_key: str = Field(pattern=_IDENTIFIER)
    repository: str = Field(min_length=1)
    repository_path: str | None = Field(default=None, min_length=1)
    base_sha: str = Field(pattern=_GIT_SHA)
    branch_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
    worker_id: str = Field(pattern=_IDENTIFIER)
    allowed_paths: tuple[str, ...] = Field(min_length=1)
    test_contract_ids: tuple[str, ...] = Field(default=(), repr=False)
    dependencies: tuple[str, ...] = ()

    @field_validator("allowed_paths")
    @classmethod
    def validate_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts or value in {"", "."}:
                raise ValueError("Allowed paths must be relative repository paths")
        if len(values) != len(set(values)):
            raise ValueError("Allowed paths must be unique")
        return values


class WorkspaceAllocation(_FrozenModel):
    allocation_id: str = Field(pattern=_IDENTIFIER)
    campaign_id: str = Field(pattern=_IDENTIFIER)
    workstream_key: str = Field(pattern=_IDENTIFIER)
    repository: str = Field(min_length=1)
    workspace_path: str = Field(min_length=1)
    repository_path: str = Field(min_length=1)
    base_sha: str = Field(pattern=_GIT_SHA)
    branch_name: str = Field(min_length=1)
    worker_id: str = Field(pattern=_IDENTIFIER)
    allowed_paths: tuple[str, ...] = Field(min_length=1)
    test_contract_ids: tuple[str, ...] = ()


class CheckConclusion(StrEnum):
    PASSING = "passing"
    FAILING = "failing"
    PENDING = "pending"
    CANCELED = "canceled"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"


class ReviewVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"


class FindingDisposition(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    ACCEPTED = "accepted"


class PublicationState(StrEnum):
    QUEUED = "queued"
    MERGED = "merged"
    FAILED = "failed"


class ReviewFinding(_FrozenModel):
    finding_id: str = Field(pattern=_IDENTIFIER)
    blocking: bool
    disposition: FindingDisposition
    evidence: str = Field(min_length=1, max_length=4096)


class VerificationReceipt(_FrozenModel):
    receipt_id: str = Field(pattern=_IDENTIFIER)
    campaign_id: str = Field(pattern=_IDENTIFIER)
    workstream_key: str = Field(pattern=_IDENTIFIER)
    attempt_id: str = Field(pattern=_IDENTIFIER)
    repository: str = Field(min_length=1)
    candidate_sha: str = Field(pattern=_GIT_SHA)
    candidate_tree: str = Field(pattern=_GIT_SHA)
    base_sha: str = Field(pattern=_GIT_SHA)
    contract_id: str = Field(pattern=_IDENTIFIER)
    command_digest: str = Field(pattern=_SHA256, repr=False)
    exit_code: int
    stdout_digest: str = Field(pattern=_SHA256, repr=False)
    stderr_digest: str = Field(pattern=_SHA256, repr=False)
    started_at: datetime
    completed_at: datetime
    provenance: EvidenceProvenance | None = None

    @model_validator(mode="after")
    def validate_times(self):
        if self.completed_at < self.started_at:
            raise ValueError("Verification completion precedes its start")
        return self


class ReviewReceipt(_FrozenModel):
    receipt_id: str = Field(pattern=_IDENTIFIER)
    campaign_id: str = Field(pattern=_IDENTIFIER)
    workstream_key: str = Field(pattern=_IDENTIFIER)
    attempt_id: str = Field(pattern=_IDENTIFIER)
    repository: str = Field(min_length=1)
    candidate_sha: str = Field(pattern=_GIT_SHA)
    candidate_tree: str = Field(pattern=_GIT_SHA)
    base_sha: str = Field(pattern=_GIT_SHA)
    reviewer_id: str = Field(pattern=_IDENTIFIER)
    worker_id: str = Field(pattern=_IDENTIFIER)
    role: str = Field(pattern=_IDENTIFIER)
    verdict: ReviewVerdict
    summary: str = Field(min_length=1, max_length=4096)
    findings: tuple[ReviewFinding, ...] = ()
    provenance: EvidenceProvenance | None = None


class RequirementEvidence(_FrozenModel):
    requirement_id: str = Field(pattern=_IDENTIFIER)
    implementation_paths: tuple[str, ...] = Field(min_length=1)
    verification_contract_ids: tuple[str, ...] = Field(min_length=1)


class CheckRecord(_FrozenModel):
    name: str = Field(min_length=1)
    conclusion: CheckConclusion
    details_url: str | None = None


class CheckReceipt(_FrozenModel):
    receipt_id: str = Field(pattern=_IDENTIFIER)
    provider: str = Field(pattern=_IDENTIFIER)
    repository: str = Field(min_length=1)
    review_number: int = Field(gt=0)
    candidate_sha: str = Field(pattern=_GIT_SHA)
    tested_base_sha: str = Field(pattern=_GIT_SHA)
    checks: tuple[CheckRecord, ...]
    observed_at: datetime
    provenance: EvidenceProvenance | None = None


class CandidateEvidence(_FrozenModel):
    campaign_id: str = Field(pattern=_IDENTIFIER)
    workstream_key: str = Field(pattern=_IDENTIFIER)
    attempt_id: str = Field(pattern=_IDENTIFIER)
    worker_id: str = Field(pattern=_IDENTIFIER)
    repository: str = Field(min_length=1)
    base_sha: str = Field(pattern=_GIT_SHA)
    candidate_sha: str = Field(pattern=_GIT_SHA)
    candidate_tree: str = Field(pattern=_GIT_SHA)
    requirements: tuple[RequirementEvidence, ...] = Field(min_length=1)
    verifications: tuple[VerificationReceipt, ...] = ()
    reviews: tuple[ReviewReceipt, ...] = ()
    checks: CheckReceipt | None = None


class AcceptancePolicy(_FrozenModel):
    required_review_roles: tuple[str, ...] = Field(min_length=1)
    required_test_contract_ids: tuple[str, ...] = Field(min_length=1)
    review_producers: dict[str, tuple[str, ...]]
    test_producers: dict[str, tuple[str, ...]]
    required_check_names: tuple[str, ...] = ()
    require_forge_checks: bool = False
    forge_producers: tuple[str, ...] = ()
    integration_producers: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_producer_pins(self):
        missing_reviews = set(self.required_review_roles) - self.review_producers.keys()
        missing_tests = set(self.required_test_contract_ids) - self.test_producers.keys()
        if missing_reviews or missing_tests:
            raise ValueError("Every required review and test must pin trusted producers")
        if any(not self.review_producers[role] for role in self.required_review_roles):
            raise ValueError("Required review producer sets must not be empty")
        if any(not self.test_producers[item] for item in self.required_test_contract_ids):
            raise ValueError("Required test producer sets must not be empty")
        if (self.require_forge_checks or self.required_check_names) and not self.forge_producers:
            raise ValueError("Forge checks must pin trusted producers")
        return self


class IntegrationReceipt(_FrozenModel):
    receipt_id: str = Field(pattern=_IDENTIFIER)
    campaign_id: str = Field(pattern=_IDENTIFIER)
    integration_allocation_id: str = Field(pattern=_IDENTIFIER)
    workstream_key: str = Field(pattern=_IDENTIFIER)
    attempt_id: str = Field(pattern=_IDENTIFIER)
    repository: str = Field(min_length=1)
    base_sha: str = Field(pattern=_GIT_SHA)
    candidate_sha: str = Field(pattern=_GIT_SHA)
    candidate_tree: str = Field(pattern=_GIT_SHA)
    previous_integration_sha: str = Field(pattern=_GIT_SHA)
    integrated_commits: tuple[str, ...] = Field(min_length=1)
    resulting_sha: str = Field(pattern=_GIT_SHA)
    resulting_tree: str = Field(pattern=_GIT_SHA)
    completed_at: datetime
    provenance: EvidenceProvenance | None = None


class IntegratedCandidateIdentity(_FrozenModel):
    workstream_key: str = Field(pattern=_IDENTIFIER)
    attempt_id: str = Field(pattern=_IDENTIFIER)
    candidate_sha: str = Field(pattern=_GIT_SHA)
    candidate_tree: str = Field(pattern=_GIT_SHA)


class IntegrationCandidateInspection(_FrozenModel):
    """Bounded, read-only view of a live integration workspace candidate."""

    receipt_id: str = Field(pattern=_IDENTIFIER)
    campaign_id: str = Field(pattern=_IDENTIFIER)
    integration_allocation_id: str = Field(pattern=_IDENTIFIER)
    repository: str = Field(min_length=1)
    base_sha: str = Field(pattern=_GIT_SHA)
    candidate_sha: str = Field(pattern=_GIT_SHA)
    candidate_tree: str = Field(pattern=_GIT_SHA)
    receipt_ids: tuple[str, ...] = Field(min_length=1)
    integrated_candidates: tuple[IntegratedCandidateIdentity, ...] = Field(min_length=1)
    changed_paths: tuple[str, ...]
    patch: str = Field(max_length=1_048_576)
    inspected_at: datetime


class PublicationSource(_FrozenModel):
    """Host-trusted local source returned only after workspace validation."""

    repository: str = Field(min_length=1)
    repository_path: str = Field(min_length=1, repr=False)
    candidate_sha: str = Field(pattern=_GIT_SHA)
    candidate_tree: str = Field(pattern=_GIT_SHA)


class BranchPublicationRequest(_FrozenModel):
    campaign_id: str = Field(pattern=_IDENTIFIER)
    repository: str = Field(min_length=1)
    branch: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
    expected_head_sha: str = Field(pattern=_GIT_SHA)
    expected_remote_sha: str | None = Field(default=None, pattern=_GIT_SHA)


class BranchPublicationReceipt(_FrozenModel):
    receipt_id: str = Field(pattern=_IDENTIFIER)
    campaign_id: str = Field(pattern=_IDENTIFIER)
    provider: str = Field(pattern=_IDENTIFIER)
    repository: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    source_sha: str = Field(pattern=_GIT_SHA)
    previous_remote_sha: str | None = Field(default=None, pattern=_GIT_SHA)
    resulting_remote_sha: str = Field(pattern=_GIT_SHA)
    published_at: datetime
    provenance: EvidenceProvenance | None = None


class ReviewCandidate(_FrozenModel):
    provider: str = Field(pattern=_IDENTIFIER)
    repository: str = Field(min_length=1)
    review_number: int = Field(gt=0)
    source_branch: str = Field(min_length=1)
    target_branch: str = Field(min_length=1)
    candidate_sha: str = Field(pattern=_GIT_SHA)
    tested_base_sha: str = Field(pattern=_GIT_SHA)
    current_target_sha: str = Field(pattern=_GIT_SHA)
    mergeable: bool
    checks: tuple[CheckRecord, ...]
    serialized_publication: bool


class ResolvedRef(_FrozenModel):
    provider: str = Field(pattern=_IDENTIFIER)
    repository: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    sha: str = Field(pattern=_GIT_SHA)
    observed_at: datetime


class ReviewRequest(_FrozenModel):
    campaign_id: str = Field(pattern=_IDENTIFIER)
    repository: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=256)
    description: str = Field(max_length=65536)
    source_branch: str = Field(min_length=1)
    target_branch: str = Field(min_length=1)
    expected_head_sha: str = Field(pattern=_GIT_SHA)
    labels: tuple[str, ...] = ()


class ReviewPublication(_FrozenModel):
    receipt_id: str = Field(pattern=_IDENTIFIER)
    campaign_id: str = Field(pattern=_IDENTIFIER)
    provider: str = Field(pattern=_IDENTIFIER)
    repository: str = Field(min_length=1)
    review_number: int = Field(gt=0)
    url: str = Field(min_length=1)
    candidate_sha: str = Field(pattern=_GIT_SHA)
    source_branch: str = Field(min_length=1)
    target_branch: str = Field(min_length=1)
    created: bool
    provenance: EvidenceProvenance | None = None


class MergeRequest(_FrozenModel):
    campaign_id: str = Field(pattern=_IDENTIFIER)
    repository: str = Field(min_length=1)
    review_number: int = Field(gt=0)
    expected_head_sha: str = Field(pattern=_GIT_SHA)
    expected_base_sha: str = Field(pattern=_GIT_SHA)
    expected_target_branch: str = Field(min_length=1)
    method: str = Field(pattern=r"^(merge|squash|rebase)$")
    provider_operation_id: str | None = Field(default=None, min_length=1)


class MergeReceipt(_FrozenModel):
    receipt_id: str = Field(pattern=_IDENTIFIER)
    campaign_id: str = Field(pattern=_IDENTIFIER)
    provider: str = Field(pattern=_IDENTIFIER)
    repository: str = Field(min_length=1)
    review_number: int = Field(gt=0)
    source_sha: str = Field(pattern=_GIT_SHA)
    base_sha: str = Field(pattern=_GIT_SHA)
    target_branch: str = Field(min_length=1)
    result_sha: str | None = Field(default=None, pattern=_GIT_SHA)
    canonical_target_sha: str | None = Field(default=None, pattern=_GIT_SHA)
    method: str = Field(pattern=r"^(merge|squash|rebase)$")
    state: PublicationState
    provider_operation_id: str | None = None
    verified_at: datetime | None = None
    provenance: EvidenceProvenance | None = None

    @model_validator(mode="after")
    def validate_merged_receipt(self):
        if self.state is PublicationState.MERGED:
            if not self.result_sha or not self.canonical_target_sha or not self.verified_at:
                raise ValueError("Merged receipt requires result SHA and remote verification")
        return self
