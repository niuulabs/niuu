#!/usr/bin/env python3
"""Read-only verification of a completed Ting developer-delivery execution."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from pydantic import ValidationError

from niuu.adapters.evidence_signing import RsaEvidenceAuthenticator
from niuu.domain.delivery import (
    CandidateEvidence,
    CheckReceipt,
    IntegrationCandidateInspection,
    IntegrationReceipt,
    MergeReceipt,
    PublicationState,
    ReviewReceipt,
    ReviewVerdict,
    VerificationReceipt,
    WorkspaceAllocation,
    evidence_digest,
    evidence_payload,
)

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_CHILD_REVIEW_ROLES = frozenset({"code", "security", "adversarial"})
# Every review role this script checks. When a trust file opts into
# review-role producer pinning (`review_producers`), it must pin all of
# these — the same all-or-nothing requirement `EvidencePolicy.
# validate_producer_pins` applies server-side, so a role can't be left
# unpinned by omission.
_REVIEW_ROLES_REQUIRING_PINS = _REQUIRED_CHILD_REVIEW_ROLES | frozenset({"integration"})


class ProofVerificationError(RuntimeError):
    """The proof could not be fetched or did not satisfy the public contract."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        del req, fp, code, msg, headers, newurl
        return None


@dataclass
class _Checks:
    errors: list[str] = field(default_factory=list)
    cryptographic_errors: list[str] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)
    cryptographic_checks: int = 0

    def require(self, condition: bool, message: str) -> None:
        (self.passed if condition else self.errors).append(message)

    def model(self, model_type, value: object, label: str):
        try:
            return model_type.model_validate(value)
        except ValidationError as exc:
            self.errors.append(f"{label} does not satisfy {model_type.__name__}: {exc}")
            return None

    def require_cryptographic(self, condition: bool, message: str) -> None:
        self.cryptographic_checks += 1
        if condition:
            self.passed.append(message)
        else:
            self.cryptographic_errors.append(message)

    @property
    def all_errors(self) -> list[str]:
        return [*self.errors, *self.cryptographic_errors]


def _source_urls(base_url: str, execution_id: UUID) -> dict[str, str]:
    root = base_url.rstrip("/")
    execution = quote(str(execution_id), safe="")
    prefix = f"{root}/api/v1/ting/workflow-executions/{execution}"
    return {
        "execution": prefix,
        "evidence": f"{prefix}/evidence",
        "deliveryWaits": f"{prefix}/delivery-waits",
    }


def _read_token(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        token = path.expanduser().read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ProofVerificationError(f"cannot read authentication token file: {exc}") from exc
    if not token:
        raise ProofVerificationError("authentication token file is empty")
    return token


@dataclass(frozen=True)
class EvidenceTrust:
    """A loaded trust file: who may sign evidence, and who may sign which review role.

    ``review_producers`` mirrors ``niuu.domain.evidence.EvidencePolicy.
    review_producers`` (role -> authorized producer IDs), so a producer that
    is a trusted signer in general (present in ``producer_keys``) still
    cannot pass off a review under a role it is not pinned to — the same
    workstream-runner-cannot-sign-a-security-review guarantee the server-side
    policy enforces.
    """

    authenticator: RsaEvidenceAuthenticator
    review_producers: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _load_evidence_trust_file(path: Path | None) -> EvidenceTrust | None:
    if path is None:
        return None
    try:
        raw = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProofVerificationError(f"cannot read evidence trust file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ProofVerificationError("evidence trust file is not valid JSON") from exc
    allowed_top_level_keys = {"trusted_public_keys", "producer_keys", "review_producers"}
    if (
        not isinstance(raw, dict)
        or not set(raw) <= allowed_top_level_keys
        or not {"trusted_public_keys", "producer_keys"} <= set(raw)
    ):
        raise ProofVerificationError(
            "evidence trust file must contain trusted_public_keys and producer_keys, "
            "plus an optional review_producers"
        )
    trusted = raw["trusted_public_keys"]
    producers = raw["producer_keys"]
    if (
        not isinstance(trusted, dict)
        or not trusted
        or any(
            not isinstance(key, str) or not key or not isinstance(pem, str) or not pem
            for key, pem in trusted.items()
        )
    ):
        raise ProofVerificationError(
            "trusted_public_keys must map nonempty key IDs to inline public PEM strings"
        )
    if not isinstance(producers, dict) or not producers:
        raise ProofVerificationError("producer_keys must be a nonempty object")
    normalized_producers: dict[str, list[str]] = {}
    for producer, key_ids in producers.items():
        if (
            not isinstance(producer, str)
            or not producer
            or not isinstance(key_ids, list)
            or not key_ids
            or any(not isinstance(item, str) or not item for item in key_ids)
            or len(key_ids) != len(set(key_ids))
        ):
            raise ProofVerificationError(
                "producer_keys must map nonempty producer IDs to unique key ID arrays"
            )
        unknown = set(key_ids) - set(trusted)
        if unknown:
            raise ProofVerificationError(
                f"producer {producer!r} references unknown evidence key IDs"
            )
        normalized_producers[producer] = key_ids
    review_producers = _load_review_producer_pins(raw.get("review_producers"), producers)
    try:
        authenticator = RsaEvidenceAuthenticator(
            key_id=next(iter(sorted(trusted))),
            trusted_public_keys=trusted,
            producer_keys=normalized_producers,
        )
    except (TypeError, ValueError) as exc:
        raise ProofVerificationError(
            f"evidence trust file contains an invalid public key: {exc}"
        ) from exc
    return EvidenceTrust(authenticator=authenticator, review_producers=review_producers)


def _load_review_producer_pins(
    raw: object, producers: dict[str, list[str]]
) -> dict[str, tuple[str, ...]]:
    """Parse the optional ``review_producers`` role -> producer-ID pinning.

    Required (fail loudly, not a silent no-op) once present: a partially
    pinned set would let an attacker sign the one review role the operator
    forgot to list.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not raw:
        raise ProofVerificationError("review_producers must be a nonempty object")
    normalized: dict[str, tuple[str, ...]] = {}
    for role, producer_ids in raw.items():
        if (
            not isinstance(role, str)
            or not role
            or not isinstance(producer_ids, list)
            or not producer_ids
            or any(not isinstance(item, str) or not item for item in producer_ids)
            or len(producer_ids) != len(set(producer_ids))
        ):
            raise ProofVerificationError(
                "review_producers must map nonempty role names to unique producer ID arrays"
            )
        unknown = set(producer_ids) - set(producers)
        if unknown:
            raise ProofVerificationError(
                f"review_producers role {role!r} references unknown producer IDs: "
                + ", ".join(sorted(unknown))
            )
        normalized[role] = tuple(producer_ids)
    missing_roles = _REVIEW_ROLES_REQUIRING_PINS - set(normalized)
    if missing_roles:
        raise ProofVerificationError(
            "review_producers must pin every required review role: "
            + ", ".join(sorted(missing_roles))
        )
    return normalized


def _fetch_json(
    url: str,
    *,
    token: str,
    timeout_seconds: float,
    max_response_bytes: int,
) -> object:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers, method="GET")
    try:
        with build_opener(_NoRedirect).open(request, timeout=timeout_seconds) as response:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > max_response_bytes:
                raise ProofVerificationError(f"response from {url} exceeds configured byte limit")
            payload = response.read(max_response_bytes + 1)
            if len(payload) > max_response_bytes:
                raise ProofVerificationError(f"response from {url} exceeds configured byte limit")
    except HTTPError as exc:
        raise ProofVerificationError(f"GET {url} returned HTTP {exc.code}") from exc
    except (URLError, OSError, ValueError) as exc:
        raise ProofVerificationError(f"GET {url} failed: {exc}") from exc
    try:
        return json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProofVerificationError(f"GET {url} returned invalid JSON") from exc


def _provenance_present(value: object) -> bool:
    return isinstance(value, dict) and all(
        isinstance(value.get(key), str) and bool(value[key])
        for key in ("producer_id", "key_id", "signature")
    )


def _verify_receipt_signature(
    checks: _Checks,
    authenticator: RsaEvidenceAuthenticator | None,
    receipt,
    label: str,
) -> None:
    if authenticator is None:
        return
    provenance = receipt.provenance
    checks.require_cryptographic(
        provenance is not None and authenticator.verify(evidence_payload(receipt), provenance),
        f"{label} has a valid authorized cryptographic signature",
    )


def _verify_review_producer_pin(
    checks: _Checks,
    review_producers: dict[str, tuple[str, ...]] | None,
    receipt,
    label: str,
) -> None:
    """A producer trusted in general still must be pinned to *this* review role.

    Mirrors the server-side `EvidencePolicy.review_producers` gate: a
    `workstream-runner` key that legitimately signs code reviews must not
    also be accepted for a `security` review just because its signature is
    cryptographically valid and it is a globally trusted producer.
    """
    if not review_producers:
        return
    allowed = review_producers.get(receipt.role, ())
    provenance = receipt.provenance
    checks.require_cryptographic(
        provenance is not None and provenance.producer_id in allowed,
        f"{label} was produced by a producer authorized for role {receipt.role!r}",
    )


def _current_children(evidence: dict[str, Any], generation: int) -> dict[str, dict[str, Any]]:
    current: dict[str, dict[str, Any]] = {}
    for raw in evidence.get("children") or []:
        if not isinstance(raw, dict) or raw.get("generation") != generation:
            continue
        key = str(raw.get("childKey") or "")
        attempt = raw.get("attempt")
        if not key or type(attempt) is not int:
            continue
        previous = current.get(key)
        if previous is None or attempt > previous["attempt"]:
            current[key] = raw
    return current


def verify_documents(
    detail: object,
    evidence: object,
    waits: object,
    *,
    execution_id: UUID,
    source_urls: dict[str, str],
    evidence_trust: EvidenceTrust | None = None,
    success_status: str = "verified",
) -> dict[str, Any]:
    """Verify public Ting documents without performing any external mutation."""
    evidence_authenticator = evidence_trust.authenticator if evidence_trust else None
    review_producers = evidence_trust.review_producers if evidence_trust else None
    checks = _Checks()
    checks.require(isinstance(detail, dict), "execution detail is an object")
    checks.require(isinstance(evidence, dict), "execution evidence is an object")
    checks.require(isinstance(waits, list), "delivery wait history is an array")
    if checks.errors:
        return _report(
            checks,
            execution_id,
            source_urls,
            cryptographic_verification_requested=evidence_authenticator is not None,
            success_status=success_status,
        )
    detail = dict(detail)
    evidence = dict(evidence)
    waits = list(waits)

    expected_id = str(execution_id)
    generation = detail.get("currentGeneration")
    repository = detail.get("repo")
    base_ref = detail.get("baseBranch")
    base_sha = detail.get("baseSha")
    checks.require(detail.get("executionId") == expected_id, "detail execution ID matches request")
    checks.require(detail.get("state") == "completed", "execution state is completed")
    checks.require(bool(detail.get("completedAt")), "execution has a completion timestamp")
    checks.require(type(generation) is int and generation > 0, "current generation is positive")
    checks.require(
        isinstance(repository, str) and bool(repository), "repository identity is present"
    )
    checks.require(
        isinstance(base_ref, str) and bool(base_ref), "target branch identity is present"
    )
    checks.require(isinstance(base_sha, str) and bool(base_sha), "base SHA identity is present")

    join = detail.get("join")
    checks.require(isinstance(join, dict), "current generation join is present")
    if isinstance(join, dict):
        checks.require(join.get("generation") == generation, "join generation is current")
        checks.require(join.get("sealed") is True, "current generation is sealed")
        checks.require(join.get("ready") is True, "current generation join is ready")
        checks.require(
            all(not join.get(name) for name in ("pending", "blocked", "failed")),
            "current generation has no unresolved children",
        )

    checks.require(evidence.get("schemaVersion") == 1, "evidence schema version is supported")
    checks.require(evidence.get("executionId") == expected_id, "evidence execution ID matches")
    checks.require(evidence.get("repository") == repository, "evidence repository matches")
    checks.require(evidence.get("baseRef") == base_ref, "evidence target branch matches")
    checks.require(evidence.get("baseSha") == base_sha, "evidence base SHA matches")
    checks.require(evidence.get("state") == "completed", "evidence records completion")
    checks.require(
        evidence.get("mergeReceipt") == detail.get("mergeReceipt"),
        "detail and evidence expose the same merge receipt",
    )
    verification = evidence.get("verification")
    checks.require(
        isinstance(verification, dict)
        and verification.get("status") == "accepted"
        and not verification.get("blockingReasons"),
        "server evidence projection is accepted without blockers",
    )

    selected_generation = generation if type(generation) is int else -1
    detail_children = _current_children(detail, selected_generation)
    children = _current_children(evidence, selected_generation)
    detail_attempts = {
        key: (item.get("childId"), item.get("attempt")) for key, item in detail_children.items()
    }
    evidence_attempts = {
        key: (item.get("childId"), item.get("attempt")) for key, item in children.items()
    }
    checks.require(bool(detail_children), "detail lists current generation children")
    checks.require(
        evidence_attempts == detail_attempts,
        "evidence contains every exact latest current-generation child attempt",
    )
    checks.require(bool(children), "current generation contains child evidence")
    child_summaries: list[dict[str, Any]] = []
    child_identities: set[tuple[str, str, str, str]] = set()
    all_requirements: set[str] = set()
    for key in sorted(children):
        child = children[key]
        child_id = str(child.get("childId") or "")
        candidate = child.get("candidate")
        validation = child.get("evidenceValidation")
        workspace = child.get("workspace")
        checks.require(child.get("state") == "completed", f"child {key} is completed")
        checks.require(
            isinstance(validation, dict)
            and validation.get("accepted") is True
            and not validation.get("blocking_reasons"),
            f"child {key} has accepted server evidence validation",
        )
        manifest_digest = validation.get("manifest_digest") if isinstance(validation, dict) else ""
        checks.require(
            isinstance(manifest_digest, str) and _DIGEST.fullmatch(manifest_digest) is not None,
            f"child {key} has a canonical evidence manifest digest",
        )
        allocation = checks.model(WorkspaceAllocation, workspace, f"child {key} workspace")
        checks.require(isinstance(candidate, dict), f"child {key} candidate is present")
        if not isinstance(candidate, dict) or allocation is None:
            continue
        candidate_sha = str(candidate.get("candidateSha") or "")
        candidate_tree = str(candidate.get("candidateTree") or "")
        checks.require(candidate.get("attemptId") == child_id, f"child {key} attempt is exact")
        checks.require(allocation.campaign_id == expected_id, f"child {key} campaign is exact")
        checks.require(allocation.workstream_key == key, f"child {key} workspace key is exact")
        checks.require(allocation.repository == repository, f"child {key} repository is exact")
        checks.require(allocation.base_sha == base_sha, f"child {key} base SHA is exact")

        requirements = candidate.get("requirementEvidence")
        assigned = set(child.get("requirementIds") or [])
        reported = {
            str(item.get("requirement_id") or "")
            for item in requirements or []
            if isinstance(item, dict)
        }
        checks.require(bool(assigned) and reported == assigned, f"child {key} requirements match")
        all_requirements.update(assigned)

        verifications = candidate.get("verificationReceipts")
        parsed_verifications = [
            checks.model(VerificationReceipt, item, f"child {key} verification")
            for item in (verifications if isinstance(verifications, list) else [])
        ]
        valid_verifications = [item for item in parsed_verifications if item is not None]
        reported_contracts = [receipt.contract_id for receipt in valid_verifications]
        checks.require(bool(valid_verifications), f"child {key} has verification receipts")
        checks.require(
            len(reported_contracts) == len(set(reported_contracts)),
            f"child {key} verification contract receipts are unique",
        )
        checks.require(
            set(reported_contracts) == set(allocation.test_contract_ids),
            f"child {key} verification contracts exactly match its allocation",
        )
        for receipt in valid_verifications:
            checks.require(receipt.exit_code == 0, f"verification {receipt.receipt_id} passed")
            checks.require(
                (
                    receipt.campaign_id,
                    receipt.workstream_key,
                    receipt.attempt_id,
                    receipt.repository,
                    receipt.base_sha,
                    receipt.candidate_sha,
                    receipt.candidate_tree,
                )
                == (
                    expected_id,
                    key,
                    child_id,
                    repository,
                    base_sha,
                    candidate_sha,
                    candidate_tree,
                ),
                f"verification {receipt.receipt_id} is bound to child {key}",
            )
            checks.require(
                _provenance_present(receipt.model_dump(mode="json").get("provenance")),
                f"verification {receipt.receipt_id} carries signed provenance",
            )
            _verify_receipt_signature(
                checks,
                evidence_authenticator,
                receipt,
                f"verification {receipt.receipt_id}",
            )

        reviews = candidate.get("reviewReceipts")
        parsed_reviews = [
            checks.model(ReviewReceipt, item, f"child {key} review")
            for item in (reviews if isinstance(reviews, list) else [])
        ]
        valid_reviews = [item for item in parsed_reviews if item is not None]
        roles = {receipt.role for receipt in valid_reviews}
        checks.require(
            _REQUIRED_CHILD_REVIEW_ROLES <= roles,
            f"child {key} has code, security, and adversarial reviews",
        )
        for receipt in valid_reviews:
            checks.require(
                receipt.verdict is ReviewVerdict.PASS, f"review {receipt.receipt_id} passed"
            )
            checks.require(
                receipt.worker_id != receipt.reviewer_id,
                f"review {receipt.receipt_id} is independent",
            )
            checks.require(
                receipt.worker_id == allocation.worker_id,
                f"review {receipt.receipt_id} worker matches child allocation",
            )
            checks.require(
                (
                    receipt.campaign_id,
                    receipt.workstream_key,
                    receipt.attempt_id,
                    receipt.repository,
                    receipt.base_sha,
                    receipt.candidate_sha,
                    receipt.candidate_tree,
                )
                == (
                    expected_id,
                    key,
                    child_id,
                    repository,
                    base_sha,
                    candidate_sha,
                    candidate_tree,
                ),
                f"review {receipt.receipt_id} is bound to child {key}",
            )
            checks.require(
                _provenance_present(receipt.model_dump(mode="json").get("provenance")),
                f"review {receipt.receipt_id} carries signed provenance",
            )
            _verify_receipt_signature(
                checks,
                evidence_authenticator,
                receipt,
                f"review {receipt.receipt_id}",
            )
            _verify_review_producer_pin(
                checks,
                review_producers,
                receipt,
                f"review {receipt.receipt_id}",
            )
            checks.require(
                not any(
                    item.blocking and item.disposition.value == "open" for item in receipt.findings
                ),
                f"review {receipt.receipt_id} has no open blocking finding",
            )
        try:
            candidate_evidence = CandidateEvidence(
                campaign_id=expected_id,
                workstream_key=key,
                attempt_id=child_id,
                worker_id=allocation.worker_id,
                repository=repository,
                base_sha=base_sha,
                candidate_sha=candidate_sha,
                candidate_tree=candidate_tree,
                requirements=requirements or [],
                verifications=valid_verifications,
                reviews=valid_reviews,
            )
        except ValidationError as exc:
            checks.errors.append(f"child {key} candidate evidence is invalid: {exc}")
        else:
            checks.require(
                evidence_digest(candidate_evidence) == manifest_digest,
                f"child {key} manifest digest matches the typed evidence payload",
            )
        child_identities.add((key, child_id, candidate_sha, candidate_tree))
        child_summaries.append(
            {
                "childKey": key,
                "attemptId": child_id,
                "candidateSha": candidate_sha,
                "candidateTree": candidate_tree,
                "requirements": sorted(assigned),
                "verificationReceiptIds": sorted(item.receipt_id for item in valid_verifications),
                "reviewReceiptIds": sorted(item.receipt_id for item in valid_reviews),
                "evidenceManifestDigest": manifest_digest,
                "requirementEvidence": requirements,
                "verificationReceipts": [
                    item.model_dump(mode="json") for item in valid_verifications
                ],
                "reviewReceipts": [item.model_dump(mode="json") for item in valid_reviews],
            }
        )

    allocation = checks.model(
        WorkspaceAllocation, detail.get("integrationAllocation"), "integration allocation"
    )
    integration_receipts = [
        checks.model(IntegrationReceipt, item, "integration receipt")
        for item in (detail.get("integrationReceipts") or [])
    ]
    integration_receipts = [item for item in integration_receipts if item is not None]
    inspection = checks.model(
        IntegrationCandidateInspection,
        detail.get("integrationCandidate"),
        "integration candidate inspection",
    )
    integration_review = checks.model(
        ReviewReceipt,
        detail.get("integrationReviewReceipt"),
        "integration review receipt",
    )
    raw_merge = detail.get("mergeReceipt") or {}
    merge = checks.model(
        MergeReceipt,
        {key: value for key, value in raw_merge.items() if key != "evidence_manifest_digest"},
        "merge receipt",
    )
    checks.require(bool(integration_receipts), "integration receipts are present")
    if allocation is not None:
        checks.require(
            allocation.campaign_id == expected_id, "integration allocation campaign matches"
        )
        checks.require(
            allocation.repository == repository, "integration allocation repository matches"
        )
        checks.require(allocation.base_sha == base_sha, "integration allocation base SHA matches")
    integrated_identities = [
        (
            receipt.workstream_key,
            receipt.attempt_id,
            receipt.candidate_sha,
            receipt.candidate_tree,
        )
        for receipt in integration_receipts
    ]
    for receipt in integration_receipts:
        checks.require(
            allocation is not None
            and receipt.integration_allocation_id == allocation.allocation_id,
            f"integration receipt {receipt.receipt_id} uses the persisted allocation",
        )
        checks.require(
            (receipt.campaign_id, receipt.repository, receipt.base_sha)
            == (expected_id, repository, base_sha),
            f"integration receipt {receipt.receipt_id} matches execution identity",
        )
        checks.require(
            (
                receipt.workstream_key,
                receipt.attempt_id,
                receipt.candidate_sha,
                receipt.candidate_tree,
            )
            in child_identities,
            f"integration receipt {receipt.receipt_id} selects an accepted child",
        )
        checks.require(
            _provenance_present(receipt.model_dump(mode="json").get("provenance")),
            f"integration receipt {receipt.receipt_id} carries signed provenance",
        )
        _verify_receipt_signature(
            checks,
            evidence_authenticator,
            receipt,
            f"integration receipt {receipt.receipt_id}",
        )
    checks.require(
        len(integrated_identities) == len(set(integrated_identities)),
        "integration receipts select unique child identities",
    )
    checks.require(
        set(integrated_identities) == child_identities,
        "integration receipts cover the exact accepted child identity set",
    )

    if inspection is not None:
        checks.require(
            (inspection.campaign_id, inspection.repository, inspection.base_sha)
            == (expected_id, repository, base_sha),
            "integration candidate identity matches execution",
        )
        checks.require(
            allocation is not None
            and inspection.integration_allocation_id == allocation.allocation_id,
            "integration candidate uses persisted allocation",
        )
        checks.require(
            set(inspection.receipt_ids) == {item.receipt_id for item in integration_receipts},
            "integration candidate names exact integration receipts",
        )
        inspected_children = {
            (item.workstream_key, item.attempt_id, item.candidate_sha, item.candidate_tree)
            for item in inspection.integrated_candidates
        }
        checks.require(
            inspected_children == child_identities, "integration candidate covers exact children"
        )
    if integration_review is not None and inspection is not None:
        checks.require(integration_review.role == "integration", "integration review role is exact")
        checks.require(
            integration_review.verdict is ReviewVerdict.PASS, "integration review passed"
        )
        checks.require(
            integration_review.worker_id != integration_review.reviewer_id,
            "integration review is independent",
        )
        checks.require(
            (
                integration_review.campaign_id,
                integration_review.repository,
                integration_review.base_sha,
                integration_review.attempt_id,
                integration_review.candidate_sha,
                integration_review.candidate_tree,
            )
            == (
                expected_id,
                repository,
                base_sha,
                inspection.integration_allocation_id,
                inspection.candidate_sha,
                inspection.candidate_tree,
            ),
            "integration review is bound to inspected candidate",
        )
        checks.require(
            _provenance_present(integration_review.model_dump(mode="json").get("provenance")),
            "integration review carries signed provenance",
        )
        _verify_receipt_signature(
            checks,
            evidence_authenticator,
            integration_review,
            "integration review",
        )
        _verify_review_producer_pin(
            checks,
            review_producers,
            integration_review,
            "integration review",
        )
    if merge is not None and inspection is not None:
        checks.require(merge.state is PublicationState.MERGED, "forge confirms merged state")
        checks.require(
            (merge.campaign_id, merge.repository, merge.base_sha, merge.target_branch)
            == (expected_id, repository, base_sha, base_ref),
            "merge receipt matches execution identity",
        )
        checks.require(
            merge.source_sha == inspection.candidate_sha, "merge source is inspected candidate"
        )
        checks.require(
            merge.result_sha == merge.canonical_target_sha,
            "merge result is the verified canonical target SHA",
        )
        checks.require(
            _provenance_present(merge.model_dump(mode="json").get("provenance")),
            "merge receipt carries signed provenance",
        )
        _verify_receipt_signature(
            checks,
            evidence_authenticator,
            merge,
            "merge receipt",
        )
        checks.require(
            isinstance(raw_merge.get("evidence_manifest_digest"), str)
            and _DIGEST.fullmatch(raw_merge["evidence_manifest_digest"]) is not None,
            "merge receipt records the accepted integration evidence digest",
        )

    current_waits = [
        item for item in waits if isinstance(item, dict) and item.get("generation") == generation
    ]
    for item in current_waits:
        wait_id = str(item.get("waitId") or "")
        checks.require(item.get("executionId") == expected_id, f"wait {wait_id} execution matches")
        checks.require(item.get("state") == "notified", f"wait {wait_id} reached notified state")
        checks.require(not item.get("lastError"), f"wait {wait_id} has no terminal delivery error")
        request = item.get("request") or {}
        checks.require(
            request.get("repository") == repository, f"wait {wait_id} repository matches"
        )
        checks.require(
            request.get("expectedBaseSha") == base_sha, f"wait {wait_id} base SHA matches"
        )
        checks.require(
            request.get("expectedTargetBranch") == base_ref, f"wait {wait_id} target matches"
        )
        if inspection is not None:
            checks.require(
                request.get("expectedHeadSha") == inspection.candidate_sha,
                f"wait {wait_id} candidate matches inspected integration",
            )
        observation = item.get("observation")
        checks.require(isinstance(observation, dict), f"wait {wait_id} has an observation")
        if not isinstance(observation, dict):
            continue
        raw_wait_checks = observation.get("checks")
        if raw_wait_checks is not None:
            wait_checks = checks.model(
                CheckReceipt,
                raw_wait_checks,
                f"wait {wait_id} check receipt",
            )
            if wait_checks is not None:
                checks.require(
                    item.get("mode") == "checks" and observation.get("status") == "checks_passed",
                    f"wait {wait_id} check receipt belongs to a passed checks observation",
                )
                checks.require(
                    (
                        wait_checks.repository,
                        wait_checks.review_number,
                        wait_checks.candidate_sha,
                        wait_checks.tested_base_sha,
                    )
                    == (
                        repository,
                        request.get("reviewNumber"),
                        request.get("expectedHeadSha"),
                        request.get("expectedBaseSha"),
                    ),
                    f"wait {wait_id} check receipt matches its exact request identity",
                )
                checks.require(
                    _provenance_present(wait_checks.model_dump(mode="json").get("provenance")),
                    f"wait {wait_id} check receipt carries signed provenance",
                )
                _verify_receipt_signature(
                    checks,
                    evidence_authenticator,
                    wait_checks,
                    f"wait {wait_id} check receipt",
                )
        raw_wait_merge = observation.get("mergeReceipt")
        if raw_wait_merge is not None:
            wait_merge = checks.model(
                MergeReceipt,
                raw_wait_merge,
                f"wait {wait_id} merge receipt",
            )
            if wait_merge is not None:
                checks.require(
                    item.get("mode") == "merge"
                    and observation.get("status") == "merged"
                    and wait_merge.state is PublicationState.MERGED,
                    f"wait {wait_id} merge receipt belongs to a merged observation",
                )
                checks.require(
                    (
                        wait_merge.campaign_id,
                        wait_merge.repository,
                        wait_merge.review_number,
                        wait_merge.source_sha,
                        wait_merge.base_sha,
                        wait_merge.target_branch,
                        wait_merge.method,
                        wait_merge.provider_operation_id,
                    )
                    == (
                        expected_id,
                        repository,
                        request.get("reviewNumber"),
                        request.get("expectedHeadSha"),
                        request.get("expectedBaseSha"),
                        request.get("expectedTargetBranch"),
                        request.get("method"),
                        request.get("providerOperationId"),
                    ),
                    f"wait {wait_id} merge receipt matches its exact request identity",
                )
                checks.require(
                    _provenance_present(wait_merge.model_dump(mode="json").get("provenance")),
                    f"wait {wait_id} merge receipt carries signed provenance",
                )
                _verify_receipt_signature(
                    checks,
                    evidence_authenticator,
                    wait_merge,
                    f"wait {wait_id} merge receipt",
                )

    report = _report(
        checks,
        execution_id,
        source_urls,
        cryptographic_verification_requested=evidence_authenticator is not None,
        success_status=success_status,
    )
    report["execution"] = {
        "repository": repository,
        "baseRef": base_ref,
        "baseSha": base_sha,
        "generation": generation,
        "planRevision": detail.get("planRevision"),
        "workflowDigest": evidence.get("workflowDigest"),
        "completedAt": detail.get("completedAt"),
    }
    report["children"] = child_summaries
    report["integration"] = {
        "allocationId": allocation.allocation_id if allocation is not None else None,
        "candidateSha": inspection.candidate_sha if inspection is not None else None,
        "candidateTree": inspection.candidate_tree if inspection is not None else None,
        "integrationReceiptIds": sorted(item.receipt_id for item in integration_receipts),
        "integrationReviewReceiptId": (
            integration_review.receipt_id if integration_review is not None else None
        ),
        "mergeReceiptId": merge.receipt_id if merge is not None else None,
        "reviewNumber": merge.review_number if merge is not None else None,
        "canonicalTargetSha": merge.canonical_target_sha if merge is not None else None,
        "evidenceManifestDigest": (
            (detail.get("mergeReceipt") or {}).get("evidence_manifest_digest")
        ),
        "requirements": sorted(all_requirements),
        "integrationReceipts": [item.model_dump(mode="json") for item in integration_receipts],
        "integrationReviewReceipt": (
            integration_review.model_dump(mode="json") if integration_review is not None else None
        ),
        "mergeReceipt": detail.get("mergeReceipt"),
    }
    report["deliveryWaits"] = [
        {
            "waitId": item.get("waitId"),
            "mode": item.get("mode"),
            "state": item.get("state"),
            "requestDigest": item.get("requestDigest"),
            "candidateDigest": item.get("candidateDigest"),
            "checkReceiptId": (
                ((item.get("observation") or {}).get("checks") or {}).get("receipt_id")
            ),
            "mergeReceiptId": (
                ((item.get("observation") or {}).get("mergeReceipt") or {}).get("receipt_id")
            ),
        }
        for item in current_waits
    ]
    return report


def _report(
    checks: _Checks,
    execution_id: UUID,
    source_urls: dict[str, str],
    *,
    cryptographic_verification_requested: bool,
    success_status: str = "verified",
) -> dict[str, Any]:
    independent_verified = (
        cryptographic_verification_requested
        and checks.cryptographic_checks > 0
        and not checks.all_errors
    )
    return {
        "schemaVersion": 1,
        "verifiedAt": datetime.now(UTC).isoformat(),
        "executionId": str(execution_id),
        "status": success_status if not checks.all_errors else "failed",
        "sourceUrls": source_urls,
        "trust": {
            "serverValidatedEvidence": not checks.errors,
            "independentCryptographicVerification": independent_verified,
            "cryptographicVerificationRequested": cryptographic_verification_requested,
            "receiptSignaturesChecked": checks.cryptographic_checks,
            "statement": (
                "This verifier checks typed identities, accepted Ting validation records, and "
                "typed receipts present in current-generation delivery-wait observations. "
                + (
                    "It independently verified every such receipt signature present in the "
                    "completed execution, accepted child evidence, and current delivery waits "
                    "against the supplied public-key and producer authorization mappings."
                    if independent_verified
                    else (
                        "Independent cryptographic verification was requested but did not verify "
                        "every required receipt."
                        if cryptographic_verification_requested
                        else (
                            "It checks provenance presence on those receipts, but no public-key "
                            "trust file was supplied; the operator explicitly accepted the "
                            "server's own attestation instead of independent verification "
                            "(--trust-server-attestation), so this report's status is "
                            "'server-attested', never 'verified'."
                        )
                    )
                )
            ),
        },
        "checksPassed": checks.passed,
        "errors": checks.all_errors,
    }


def _write_report(report: dict[str, Any], output: Path | None) -> None:
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if output is None:
        sys.stdout.write(rendered)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Ting platform base URL")
    parser.add_argument("--execution-id", required=True, type=UUID)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--token-file",
        type=Path,
        help="Optional file containing a bearer token; its value is never printed",
    )
    parser.add_argument(
        "--evidence-trust-file",
        type=Path,
        help=(
            "Public-only JSON trust file containing trusted_public_keys inline PEMs, "
            "producer_keys authorization mappings, and an optional review_producers "
            "role pinning. Required for a 'verified' report status; without it, pass "
            "--trust-server-attestation instead."
        ),
    )
    parser.add_argument(
        "--trust-server-attestation",
        action="store_true",
        help=(
            "Proceed without independent cryptographic verification, trusting the "
            "server's own report of what happened. The report status is then "
            "'server-attested', never 'verified'."
        ),
    )
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--max-response-bytes", type=int, default=10 * 1024 * 1024)
    args = parser.parse_args(argv)
    if args.timeout_seconds <= 0 or args.max_response_bytes <= 0:
        parser.error("timeout and response byte limit must be positive")
    if args.evidence_trust_file is None and not args.trust_server_attestation:
        parser.error(
            "either --evidence-trust-file (independent cryptographic verification, "
            "status 'verified') or --trust-server-attestation (status 'server-attested', "
            "never 'verified') is required"
        )
    success_status = "verified" if args.evidence_trust_file is not None else "server-attested"
    urls = _source_urls(args.base_url, args.execution_id)
    try:
        token = _read_token(args.token_file)
        evidence_trust = _load_evidence_trust_file(args.evidence_trust_file)
        documents = {
            name: _fetch_json(
                url,
                token=token,
                timeout_seconds=args.timeout_seconds,
                max_response_bytes=args.max_response_bytes,
            )
            for name, url in urls.items()
        }
        report = verify_documents(
            documents["execution"],
            documents["evidence"],
            documents["deliveryWaits"],
            execution_id=args.execution_id,
            source_urls=urls,
            evidence_trust=evidence_trust,
            success_status=success_status,
        )
    except ProofVerificationError as exc:
        report = {
            "schemaVersion": 1,
            "verifiedAt": datetime.now(UTC).isoformat(),
            "executionId": str(args.execution_id),
            "status": "failed",
            "sourceUrls": urls,
            "trust": {
                "serverValidatedEvidence": False,
                "independentCryptographicVerification": False,
                "cryptographicVerificationRequested": args.evidence_trust_file is not None,
            },
            "checksPassed": [],
            "errors": [str(exc)],
        }
    _write_report(report, args.output)
    return 0 if report["status"] in {"verified", "server-attested"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
