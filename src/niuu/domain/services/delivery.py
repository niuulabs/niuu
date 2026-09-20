"""Deterministic acceptance for attempt-bound delivery evidence."""

from __future__ import annotations

import hashlib
import json

from niuu.domain.delivery import (
    AcceptancePolicy,
    CandidateEvidence,
    EvidenceValidationError,
    EvidenceValidationReport,
)
from niuu.domain.evidence import (
    ArtifactIdentity,
    EvidenceBundle,
    EvidenceCheck,
    EvidenceCheckConclusion,
    EvidenceCheckReceipt,
    EvidenceFinding,
    EvidenceFindingDisposition,
    EvidencePolicy,
    EvidenceRequirement,
    EvidenceResult,
    EvidenceResultReceipt,
    EvidenceReviewReceipt,
    EvidenceReviewVerdict,
)
from niuu.domain.services.evidence import DeterministicEvidenceVerifier
from niuu.ports.evidence import EvidenceAuthenticator


class EvidenceVerifier:
    """Delivery projection over the artifact-neutral deterministic verifier."""

    def __init__(
        self,
        authenticator: EvidenceAuthenticator,
        *,
        trusted_producers: tuple[str, ...],
    ) -> None:
        self._verifier = DeterministicEvidenceVerifier(
            authenticator,
            trusted_producers=trusted_producers,
        )

    def validate(
        self,
        evidence: CandidateEvidence,
        policy: AcceptancePolicy,
    ) -> EvidenceValidationReport:
        identity = _delivery_identity(
            evidence.campaign_id,
            evidence.workstream_key,
            evidence.attempt_id,
            evidence.repository,
            evidence.candidate_sha,
            evidence.candidate_tree,
            evidence.base_sha,
        )
        results = tuple(
            EvidenceResultReceipt(
                receipt_id=receipt.receipt_id,
                identity=_delivery_identity(
                    receipt.campaign_id,
                    receipt.workstream_key,
                    receipt.attempt_id,
                    receipt.repository,
                    receipt.candidate_sha,
                    receipt.candidate_tree,
                    receipt.base_sha,
                ),
                contract_id=receipt.contract_id,
                result=(EvidenceResult.PASS if receipt.exit_code == 0 else EvidenceResult.FAIL),
                result_digest=receipt.stdout_digest,
                completed_at=receipt.completed_at,
                provenance=receipt.provenance,
            )
            for receipt in evidence.verifications
        )
        reviews = tuple(
            EvidenceReviewReceipt(
                receipt_id=receipt.receipt_id,
                identity=_delivery_identity(
                    receipt.campaign_id,
                    receipt.workstream_key,
                    receipt.attempt_id,
                    receipt.repository,
                    receipt.candidate_sha,
                    receipt.candidate_tree,
                    receipt.base_sha,
                ),
                reviewer_id=receipt.reviewer_id,
                subject_worker_id=receipt.worker_id,
                role=receipt.role,
                verdict=EvidenceReviewVerdict(receipt.verdict.value),
                summary=receipt.summary,
                findings=tuple(
                    EvidenceFinding(
                        finding_id=finding.finding_id,
                        blocking=finding.blocking,
                        disposition=EvidenceFindingDisposition(finding.disposition.value),
                        evidence=finding.evidence,
                    )
                    for finding in receipt.findings
                ),
                provenance=receipt.provenance,
            )
            for receipt in evidence.reviews
        )
        initial_reasons: list[str] = []
        checks = None
        if evidence.checks is not None:
            if evidence.checks.candidate_sha != evidence.candidate_sha:
                initial_reasons.append("forge checks are for a stale candidate")
            if evidence.checks.repository != evidence.repository:
                initial_reasons.append("forge checks are for a different repository")
            if evidence.checks.tested_base_sha != evidence.base_sha:
                initial_reasons.append("forge checks are for a different base")
            checks = EvidenceCheckReceipt(
                receipt_id=evidence.checks.receipt_id,
                identity=identity,
                checks=tuple(
                    EvidenceCheck(
                        name=item.name,
                        conclusion=EvidenceCheckConclusion(item.conclusion.value),
                    )
                    for item in evidence.checks.checks
                ),
                observed_at=evidence.checks.observed_at,
                provenance=evidence.checks.provenance,
            )

        bundle = EvidenceBundle(
            identity=identity,
            worker_id=evidence.worker_id,
            requirements=tuple(
                EvidenceRequirement(
                    requirement_id=requirement.requirement_id,
                    result_contract_ids=requirement.verification_contract_ids,
                )
                for requirement in evidence.requirements
            ),
            results=results,
            reviews=reviews,
            checks=checks,
        )
        generic_policy = EvidencePolicy(
            required_review_roles=policy.required_review_roles,
            required_result_contract_ids=policy.required_test_contract_ids,
            review_producers=policy.review_producers,
            result_producers=policy.test_producers,
            required_check_names=policy.required_check_names,
            require_checks=policy.require_forge_checks,
            check_producers=policy.forge_producers,
        )
        signed_sources = {
            item.receipt_id: item for item in (*evidence.verifications, *evidence.reviews)
        }
        if evidence.checks is not None:
            signed_sources[evidence.checks.receipt_id] = evidence.checks
        return self._verifier.validate_projection(
            bundle,
            generic_policy,
            signed_sources=signed_sources,
            manifest=evidence,
            initial_reasons=tuple(initial_reasons),
            result_noun="verification",
            check_noun="forge check",
        )

    def require(self, evidence: CandidateEvidence, policy: AcceptancePolicy) -> str:
        report = self.validate(evidence, policy)
        if not report.accepted:
            raise EvidenceValidationError("; ".join(report.blocking_reasons))
        return report.manifest_digest


def _delivery_identity(
    campaign_id: str,
    workstream_key: str,
    attempt_id: str,
    repository: str,
    candidate_sha: str,
    candidate_tree: str,
    base_sha: str,
) -> ArtifactIdentity:
    artifact = {
        "campaign_id": campaign_id,
        "workstream_key": workstream_key,
        "repository": repository,
        "candidate_sha": candidate_sha,
        "candidate_tree": candidate_tree,
        "base_sha": base_sha,
    }
    serialized = json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode()
    return ArtifactIdentity(
        artifact_kind="software-delivery",
        artifact_id=f"{campaign_id}/{workstream_key}",
        artifact_digest=hashlib.sha256(serialized).hexdigest(),
        attempt_id=attempt_id,
        execution_id=campaign_id,
    )
