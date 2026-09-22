"""Deterministic, artifact-neutral evidence verification."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping

from pydantic import BaseModel

from niuu.domain.evidence import (
    EvidenceBundle,
    EvidenceCheckConclusion,
    EvidenceFindingDisposition,
    EvidencePolicy,
    EvidenceResult,
    EvidenceReviewVerdict,
    EvidenceValidationError,
    EvidenceValidationReport,
    evidence_payload,
)
from niuu.ports.evidence import EvidenceAuthenticator


class DeterministicEvidenceVerifier:
    """Verify signed results and independent reviews for any immutable artifact."""

    def __init__(
        self,
        authenticator: EvidenceAuthenticator,
        *,
        trusted_producers: tuple[str, ...],
    ) -> None:
        if not trusted_producers:
            raise ValueError("At least one trusted evidence producer is required")
        self._authenticator = authenticator
        self._trusted_producers = frozenset(trusted_producers)

    def validate(
        self,
        evidence: EvidenceBundle,
        policy: EvidencePolicy,
    ) -> EvidenceValidationReport:
        """Validate a native serializable bundle using its own canonical signed bytes."""
        return self._validate(evidence, policy, payload_for=evidence_payload)

    def require(self, evidence: EvidenceBundle, policy: EvidencePolicy) -> str:
        report = self.validate(evidence, policy)
        if not report.accepted:
            raise EvidenceValidationError("; ".join(report.blocking_reasons))
        return report.manifest_digest

    def validate_projection(
        self,
        evidence: EvidenceBundle,
        policy: EvidencePolicy,
        *,
        signed_sources: Mapping[str, BaseModel],
        manifest: BaseModel,
        initial_reasons: tuple[str, ...] = (),
        result_noun: str = "result",
        check_noun: str = "check",
    ) -> EvidenceValidationReport:
        """Validate a trusted adapter projection while authenticating original models.

        This exists for historical typed contracts whose signed serialization cannot
        change. Callers must derive every projected semantic field from ``signed_sources``.
        User-supplied payload bytes are deliberately not accepted.
        """

        expected_ids = {
            *(item.receipt_id for item in evidence.results),
            *(item.receipt_id for item in evidence.reviews),
        }
        if evidence.checks is not None:
            expected_ids.add(evidence.checks.receipt_id)
        if set(signed_sources) != expected_ids:
            raise ValueError("Signed source models must exactly match projected receipts")

        def payload_for(receipt: BaseModel) -> bytes:
            return evidence_payload(signed_sources[receipt.receipt_id])

        return self._validate(
            evidence,
            policy,
            payload_for=payload_for,
            manifest=manifest,
            initial_reasons=initial_reasons,
            result_noun=result_noun,
            check_noun=check_noun,
        )

    def _validate(
        self,
        evidence: EvidenceBundle,
        policy: EvidencePolicy,
        *,
        payload_for: Callable[[BaseModel], bytes],
        manifest: BaseModel | None = None,
        initial_reasons: tuple[str, ...] = (),
        result_noun: str = "result",
        check_noun: str = "check",
    ) -> EvidenceValidationReport:
        reasons = list(initial_reasons)
        successful_contracts: set[str] = set()
        seen_contracts: set[str] = set()

        for receipt in evidence.results:
            if receipt.identity != evidence.identity:
                reasons.append(f"{result_noun} {receipt.receipt_id} is for a different attempt")
            if receipt.contract_id in seen_contracts:
                reasons.append(
                    f"{result_noun} contract {receipt.contract_id} has duplicate receipts"
                )
            seen_contracts.add(receipt.contract_id)
            if not self._authentic(receipt, payload_for):
                reasons.append(f"{result_noun} {receipt.receipt_id} has untrusted provenance")
            elif receipt.contract_id in policy.result_producers and (
                receipt.provenance.producer_id not in policy.result_producers[receipt.contract_id]
            ):
                reasons.append(
                    f"{result_noun} contract {receipt.contract_id} has an unauthorized producer"
                )
            if receipt.result is not EvidenceResult.PASS:
                reasons.append(f"{result_noun} contract {receipt.contract_id} failed")
                continue
            successful_contracts.add(receipt.contract_id)

        for contract_id in policy.required_result_contract_ids:
            if contract_id not in successful_contracts:
                reasons.append(f"required {result_noun} contract {contract_id} is missing")

        seen_roles: set[str] = set()
        for receipt in evidence.reviews:
            if receipt.identity != evidence.identity:
                reasons.append(f"review {receipt.receipt_id} is for a different attempt")
            if (
                receipt.reviewer_id == evidence.worker_id
                or receipt.reviewer_id == receipt.subject_worker_id
            ):
                reasons.append(f"review role {receipt.role} is self-approved")
            if receipt.subject_worker_id != evidence.worker_id:
                reasons.append(f"review role {receipt.role} names a different worker")
            if receipt.role in seen_roles:
                reasons.append(f"review role {receipt.role} has duplicate receipts")
            seen_roles.add(receipt.role)
            if not self._authentic(receipt, payload_for):
                reasons.append(f"review {receipt.receipt_id} has untrusted provenance")
            elif receipt.role in policy.review_producers and (
                receipt.provenance.producer_id not in policy.review_producers[receipt.role]
            ):
                reasons.append(f"review role {receipt.role} has an unauthorized producer")
            if receipt.verdict is not EvidenceReviewVerdict.PASS:
                reasons.append(f"review role {receipt.role} did not pass")
            for finding in receipt.findings:
                if finding.blocking and finding.disposition is EvidenceFindingDisposition.OPEN:
                    reasons.append(f"blocking finding {finding.finding_id} is unresolved")

        for role in policy.required_review_roles:
            if role not in seen_roles:
                reasons.append(f"required review role {role} is missing")

        requirement_ids: set[str] = set()
        for requirement in evidence.requirements:
            if requirement.requirement_id in requirement_ids:
                reasons.append(f"requirement {requirement.requirement_id} is duplicated")
            requirement_ids.add(requirement.requirement_id)
            for contract_id in requirement.result_contract_ids:
                if contract_id not in successful_contracts:
                    reasons.append(
                        f"requirement {requirement.requirement_id} lacks passing "
                        f"{result_noun} {contract_id}"
                    )

        if evidence.checks is not None or policy.require_checks or policy.required_check_names:
            self._validate_checks(evidence, policy, reasons, payload_for, check_noun)

        manifest_model = evidence if manifest is None else manifest
        serialized = json.dumps(
            manifest_model.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        unique_reasons = tuple(dict.fromkeys(reasons))
        return EvidenceValidationReport(
            accepted=not unique_reasons,
            blocking_reasons=unique_reasons,
            manifest_digest=hashlib.sha256(serialized).hexdigest(),
        )

    def _authentic(
        self,
        receipt: BaseModel,
        payload_for: Callable[[BaseModel], bytes],
    ) -> bool:
        provenance = receipt.provenance
        if provenance is None or provenance.producer_id not in self._trusted_producers:
            return False
        return self._authenticator.verify(payload_for(receipt), provenance)

    def _validate_checks(
        self,
        evidence: EvidenceBundle,
        policy: EvidencePolicy,
        reasons: list[str],
        payload_for: Callable[[BaseModel], bytes],
        check_noun: str,
    ) -> None:
        receipt = evidence.checks
        if receipt is None:
            reasons.append(f"{check_noun} receipt is missing")
            return
        if receipt.identity != evidence.identity:
            reasons.append(f"{check_noun}s are for a different attempt")
        if not self._authentic(receipt, payload_for):
            reasons.append(f"{check_noun} receipt {receipt.receipt_id} has untrusted provenance")
        elif receipt.provenance.producer_id not in policy.check_producers:
            reasons.append(f"{check_noun} receipt has an unauthorized producer")

        records: dict[str, EvidenceCheckConclusion] = {}
        for check in receipt.checks:
            if check.name in records:
                reasons.append(f"{check_noun} {check.name} is duplicated")
            records[check.name] = check.conclusion
        for name in policy.required_check_names:
            conclusion = records.get(name)
            if conclusion is None:
                reasons.append(f"required {check_noun} {name} is missing")
            elif conclusion is not EvidenceCheckConclusion.PASSING:
                reasons.append(f"required {check_noun} {name} is {conclusion.value}")
