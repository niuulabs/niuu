"""Domain models for resident self-review and verification loops."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class ResidentReviewDecision(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED_DUPLICATE = "skipped_duplicate"


@dataclass(frozen=True)
class ResidentVerificationCheck:
    """One concrete verification command/check to run for a resident artifact."""

    id: str
    description: str
    command: tuple[str, ...]
    working_dir: str = ""
    expected_exit_code: int = 0


@dataclass(frozen=True)
class ResidentReviewTarget:
    """Artifact or output that must be reviewed before becoming trusted."""

    id: str
    title: str
    artifact_ref: str
    artifact_kind: str
    checks: tuple[ResidentVerificationCheck, ...]
    source_objective_id: str = ""
    review_key: str = ""
    complete_objective_on_pass: bool = False
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResidentVerificationEvidence:
    """Evidence captured from one concrete verification check."""

    check_id: str
    description: str
    command: tuple[str, ...]
    status: str
    exit_code: int
    summary: str
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class ResidentArtifactReview:
    """Persisted resident review decision for an artifact."""

    id: str
    review_key: str
    target: ResidentReviewTarget
    decision: str
    reason: str
    evidence: tuple[ResidentVerificationEvidence, ...] = ()
    duplicate_of: str = ""
    follow_up_objective_id: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class ResidentReviewReport:
    """Result of one resident artifact review pass."""

    mandate: str
    target: ResidentReviewTarget
    review: ResidentArtifactReview
    persisted_refs: tuple[str, ...]
    created_follow_up_objective_id: str = ""
    updated_objective_id: str = ""
    duplicate_skipped: bool = False
    final_suggested_next_action: str = ""
