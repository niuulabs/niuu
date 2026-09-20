from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from scripts import verify_delivery_proof as verifier

EXECUTION_ID = UUID("11111111-1111-4111-8111-111111111111")
BASE_SHA = "a" * 40
CHILD_SHA = "b" * 40
CHILD_TREE = "c" * 40
INTEGRATION_SHA = "d" * 40
INTEGRATION_TREE = "e" * 40
MERGE_SHA = "f" * 40
REPOSITORY = "https://git.example/team/repository.git"
TARGET = "main"
NOW = "2026-09-19T12:00:00+00:00"


def _provenance(producer: str) -> dict:
    return {"producer_id": producer, "key_id": "proof-key", "signature": "signed-value"}


def _review(role: str, reviewer: str, *, candidate_sha=CHILD_SHA, candidate_tree=CHILD_TREE):
    return {
        "receipt_id": f"review-{role}",
        "campaign_id": str(EXECUTION_ID),
        "workstream_key": "api",
        "attempt_id": "22222222-2222-4222-8222-222222222222",
        "repository": REPOSITORY,
        "candidate_sha": candidate_sha,
        "candidate_tree": candidate_tree,
        "base_sha": BASE_SHA,
        "reviewer_id": reviewer,
        "worker_id": "worker-api",
        "role": role,
        "verdict": "pass",
        "summary": f"{role} review passed",
        "findings": [],
        "provenance": _provenance(f"{role}-reviewer"),
    }


def _documents():
    child_id = "22222222-2222-4222-8222-222222222222"
    allocation = {
        "allocation_id": "allocation-api",
        "campaign_id": str(EXECUTION_ID),
        "workstream_key": "api",
        "repository": REPOSITORY,
        "workspace_path": "/work/api",
        "repository_path": "/repos/api",
        "base_sha": BASE_SHA,
        "branch_name": "campaign/api",
        "worker_id": "worker-api",
        "allowed_paths": ["src/api.py"],
        "test_contract_ids": ["unit"],
    }
    verification = {
        "receipt_id": "verification-unit",
        "campaign_id": str(EXECUTION_ID),
        "workstream_key": "api",
        "attempt_id": child_id,
        "repository": REPOSITORY,
        "candidate_sha": CHILD_SHA,
        "candidate_tree": CHILD_TREE,
        "base_sha": BASE_SHA,
        "contract_id": "unit",
        "command_digest": "1" * 64,
        "exit_code": 0,
        "stdout_digest": "2" * 64,
        "stderr_digest": "3" * 64,
        "started_at": NOW,
        "completed_at": NOW,
        "provenance": _provenance("workstream-runner"),
    }
    child = {
        "childId": child_id,
        "childKey": "api",
        "attempt": 1,
        "generation": 1,
        "state": "completed",
        "dependencies": [],
        "requirementIds": ["REQ-api"],
        "taskHandle": {"agentId": "agent", "taskId": "task", "contextId": "context"},
        "workspace": allocation,
        "candidate": {
            "attemptId": child_id,
            "candidateSha": CHILD_SHA,
            "candidateTree": CHILD_TREE,
            "verificationReceipts": [verification],
            "reviewReceipts": [
                _review("code", "reviewer-code"),
                _review("security", "reviewer-security"),
                _review("adversarial", "reviewer-adversarial"),
            ],
            "requirementEvidence": [
                {
                    "requirement_id": "REQ-api",
                    "implementation_paths": ["src/api.py"],
                    "verification_contract_ids": ["unit"],
                }
            ],
        },
        "evidence": [],
        "evidenceValidation": {
            "accepted": True,
            "blocking_reasons": [],
            "manifest_digest": "4" * 64,
        },
        "evidenceValidatedAt": NOW,
        "pendingQuestions": [],
        "pendingGates": [],
        "error": None,
        "deadline": NOW,
        "createdAt": NOW,
        "updatedAt": NOW,
    }
    child_evidence = child["candidate"]
    child["evidenceValidation"]["manifest_digest"] = verifier.evidence_digest(
        verifier.CandidateEvidence(
            campaign_id=str(EXECUTION_ID),
            workstream_key="api",
            attempt_id=child_id,
            worker_id="worker-api",
            repository=REPOSITORY,
            base_sha=BASE_SHA,
            candidate_sha=CHILD_SHA,
            candidate_tree=CHILD_TREE,
            requirements=child_evidence["requirementEvidence"],
            verifications=child_evidence["verificationReceipts"],
            reviews=child_evidence["reviewReceipts"],
        )
    )
    integration_allocation = {
        **allocation,
        "allocation_id": "allocation-integration",
        "workstream_key": "integration",
        "workspace_path": "/work/integration",
        "branch_name": "campaign/integration",
        "worker_id": "worker-integration",
    }
    integration_receipt = {
        "receipt_id": "integration-api",
        "campaign_id": str(EXECUTION_ID),
        "integration_allocation_id": "allocation-integration",
        "workstream_key": "api",
        "attempt_id": child_id,
        "repository": REPOSITORY,
        "base_sha": BASE_SHA,
        "candidate_sha": CHILD_SHA,
        "candidate_tree": CHILD_TREE,
        "previous_integration_sha": BASE_SHA,
        "integrated_commits": [CHILD_SHA],
        "resulting_sha": INTEGRATION_SHA,
        "resulting_tree": INTEGRATION_TREE,
        "completed_at": NOW,
        "provenance": _provenance("forge-service"),
    }
    inspection = {
        "receipt_id": "inspection-integration",
        "campaign_id": str(EXECUTION_ID),
        "integration_allocation_id": "allocation-integration",
        "repository": REPOSITORY,
        "base_sha": BASE_SHA,
        "candidate_sha": INTEGRATION_SHA,
        "candidate_tree": INTEGRATION_TREE,
        "receipt_ids": ["integration-api"],
        "integrated_candidates": [
            {
                "workstream_key": "api",
                "attempt_id": child_id,
                "candidate_sha": CHILD_SHA,
                "candidate_tree": CHILD_TREE,
            }
        ],
        "changed_paths": ["src/api.py"],
        "patch": "diff --git a/src/api.py b/src/api.py",
        "inspected_at": NOW,
    }
    integration_review = {
        **_review(
            "integration",
            "reviewer-integration",
            candidate_sha=INTEGRATION_SHA,
            candidate_tree=INTEGRATION_TREE,
        ),
        "receipt_id": "review-integration",
        "workstream_key": "integration",
        "attempt_id": "allocation-integration",
        "worker_id": "worker-integration",
        "provenance": _provenance("integration-reviewer"),
    }
    merge = {
        "receipt_id": "merge-final",
        "campaign_id": str(EXECUTION_ID),
        "provider": "gitlab",
        "repository": REPOSITORY,
        "review_number": 42,
        "source_sha": INTEGRATION_SHA,
        "base_sha": BASE_SHA,
        "target_branch": TARGET,
        "result_sha": MERGE_SHA,
        "canonical_target_sha": MERGE_SHA,
        "method": "merge",
        "state": "merged",
        "provider_operation_id": "merge-op-42",
        "verified_at": NOW,
        "provenance": _provenance("forge-service"),
        "evidence_manifest_digest": "5" * 64,
    }
    forge_checks = {
        "receipt_id": "checks-proof-ci",
        "provider": "gitlab",
        "repository": REPOSITORY,
        "review_number": 42,
        "candidate_sha": INTEGRATION_SHA,
        "tested_base_sha": BASE_SHA,
        "checks": [{"name": "proof-ci", "conclusion": "passing"}],
        "observed_at": NOW,
        "provenance": _provenance("forge-service"),
    }
    detail = {
        "executionId": str(EXECUTION_ID),
        "repo": REPOSITORY,
        "baseBranch": TARGET,
        "baseSha": BASE_SHA,
        "state": "completed",
        "currentGeneration": 1,
        "planRevision": "plan-v1",
        "completedAt": NOW,
        "join": {
            "generation": 1,
            "sealed": True,
            "ready": True,
            "pending": [],
            "blocked": [],
            "failed": [],
        },
        "children": [child],
        "integrationReceipts": [integration_receipt],
        "integrationAllocation": integration_allocation,
        "integrationCandidate": inspection,
        "integrationReviewReceipt": integration_review,
        "mergeReceipt": merge,
    }
    evidence = {
        "schemaVersion": 1,
        "executionId": str(EXECUTION_ID),
        "workflowDigest": "sha256:" + "6" * 64,
        "repository": REPOSITORY,
        "baseRef": TARGET,
        "baseSha": BASE_SHA,
        "state": "completed",
        "mergeReceipt": merge,
        "completedAt": NOW,
        "verification": {"status": "accepted", "blockingReasons": []},
        "children": [child],
    }
    waits = [
        {
            "waitId": "33333333-3333-4333-8333-333333333332",
            "executionId": str(EXECUTION_ID),
            "mode": "checks",
            "state": "notified",
            "requestDigest": "6" * 64,
            "generation": 1,
            "executionRevision": 4,
            "candidateDigest": "8" * 64,
            "request": {
                "repository": REPOSITORY,
                "reviewNumber": 42,
                "expectedHeadSha": INTEGRATION_SHA,
                "expectedBaseSha": BASE_SHA,
                "expectedTargetBranch": TARGET,
                "policyId": "developer-integration",
                "method": None,
            },
            "nextPollAt": NOW,
            "attemptCount": 2,
            "lastError": "",
            "observation": {"status": "checks_passed", "checks": forge_checks},
        },
        {
            "waitId": "33333333-3333-4333-8333-333333333333",
            "executionId": str(EXECUTION_ID),
            "mode": "merge",
            "state": "notified",
            "requestDigest": "7" * 64,
            "generation": 1,
            "executionRevision": 5,
            "candidateDigest": "8" * 64,
            "request": {
                "repository": REPOSITORY,
                "reviewNumber": 42,
                "expectedHeadSha": INTEGRATION_SHA,
                "expectedBaseSha": BASE_SHA,
                "expectedTargetBranch": TARGET,
                "policyId": "developer-integration",
                "method": "merge",
                "providerOperationId": "merge-op-42",
            },
            "nextPollAt": NOW,
            "attemptCount": 2,
            "lastError": "",
            "observation": {
                "status": "merged",
                "mergeReceipt": {
                    key: value
                    for key, value in deepcopy(merge).items()
                    if key != "evidence_manifest_digest"
                },
            },
        },
    ]
    return detail, evidence, waits


def _verify(documents=None):
    detail, evidence, waits = documents or _documents()
    return verifier.verify_documents(
        detail,
        evidence,
        waits,
        execution_id=EXECUTION_ID,
        source_urls=verifier._source_urls("http://127.0.0.1:8180", EXECUTION_ID),
    )


def _signed_documents():
    documents = _documents()
    detail, evidence, waits = documents
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    producers = {
        "workstream-runner",
        "code-reviewer",
        "security-reviewer",
        "adversarial-reviewer",
        "forge-service",
        "integration-reviewer",
    }
    producer_keys = {producer: ["proof-public-key"] for producer in producers}
    signer = verifier.RsaEvidenceAuthenticator(
        key_id="proof-public-key",
        private_key_pem=private_pem,
        producer_keys=producer_keys,
    )

    def sign(raw: dict, model_type) -> None:
        model_raw = dict(raw)
        if model_type is verifier.MergeReceipt:
            model_raw.pop("evidence_manifest_digest", None)
        model = model_type.model_validate(model_raw)
        producer = raw["provenance"]["producer_id"]
        raw["provenance"] = signer.sign(verifier.evidence_payload(model), producer).model_dump(
            mode="json"
        )

    child = detail["children"][0]
    for raw in child["candidate"]["verificationReceipts"]:
        sign(raw, verifier.VerificationReceipt)
    for raw in child["candidate"]["reviewReceipts"]:
        sign(raw, verifier.ReviewReceipt)
    _refresh_child_manifest(child)
    for raw in detail["integrationReceipts"]:
        sign(raw, verifier.IntegrationReceipt)
    sign(detail["integrationReviewReceipt"], verifier.ReviewReceipt)
    sign(detail["mergeReceipt"], verifier.MergeReceipt)
    sign(waits[0]["observation"]["checks"], verifier.CheckReceipt)
    sign(waits[1]["observation"]["mergeReceipt"], verifier.MergeReceipt)
    evidence["mergeReceipt"] = detail["mergeReceipt"]
    trust = {
        "trusted_public_keys": {"proof-public-key": public_pem},
        "producer_keys": producer_keys,
    }
    evidence_authenticator = verifier.RsaEvidenceAuthenticator(
        key_id="proof-public-key",
        trusted_public_keys=trust["trusted_public_keys"],
        producer_keys=producer_keys,
    )
    return documents, evidence_authenticator, trust


def _refresh_child_manifest(child: dict) -> None:
    candidate = child["candidate"]
    child["evidenceValidation"]["manifest_digest"] = verifier.evidence_digest(
        verifier.CandidateEvidence(
            campaign_id=str(EXECUTION_ID),
            workstream_key=child["childKey"],
            attempt_id=child["childId"],
            worker_id=child["workspace"]["worker_id"],
            repository=REPOSITORY,
            base_sha=BASE_SHA,
            candidate_sha=candidate["candidateSha"],
            candidate_tree=candidate["candidateTree"],
            requirements=candidate["requirementEvidence"],
            verifications=candidate["verificationReceipts"],
            reviews=candidate["reviewReceipts"],
        )
    )


def _verify_signed(documents, evidence_authenticator, review_producers=None):
    trust = (
        evidence_authenticator
        if isinstance(evidence_authenticator, verifier.EvidenceTrust)
        else verifier.EvidenceTrust(
            authenticator=evidence_authenticator, review_producers=review_producers or {}
        )
    )
    detail, evidence, waits = documents
    return verifier.verify_documents(
        detail,
        evidence,
        waits,
        execution_id=EXECUTION_ID,
        source_urls=verifier._source_urls("http://127.0.0.1:8180", EXECUTION_ID),
        evidence_trust=trust,
    )


def test_completed_exact_evidence_produces_bounded_success_report() -> None:
    report = _verify()

    assert report["status"] == "verified", report["errors"]
    assert report["trust"]["serverValidatedEvidence"] is True
    assert report["trust"]["independentCryptographicVerification"] is False
    assert report["trust"]["cryptographicVerificationRequested"] is False
    assert report["children"][0]["attemptId"] == "22222222-2222-4222-8222-222222222222"
    assert report["integration"]["canonicalTargetSha"] == MERGE_SHA
    assert report["sourceUrls"]["execution"].endswith(str(EXECUTION_ID))


def test_public_trust_file_independently_verifies_every_receipt(tmp_path: Path) -> None:
    documents, _evidence_authenticator, trust = _signed_documents()
    trust_file = tmp_path / "evidence-trust.json"
    trust_file.write_text(json.dumps(trust), encoding="utf-8")

    report = _verify_signed(
        documents,
        verifier._load_evidence_trust_file(trust_file),
    )

    assert report["status"] == "verified", report["errors"]
    assert report["trust"]["serverValidatedEvidence"] is True
    assert report["trust"]["independentCryptographicVerification"] is True
    assert report["trust"]["cryptographicVerificationRequested"] is True
    assert report["trust"]["receiptSignaturesChecked"] == 9


def test_review_producers_accepts_correctly_pinned_reviewers() -> None:
    documents, evidence_authenticator, _trust = _signed_documents()
    review_producers = {
        "code": ["code-reviewer"],
        "security": ["security-reviewer"],
        "adversarial": ["adversarial-reviewer"],
        "integration": ["integration-reviewer"],
    }

    report = _verify_signed(documents, evidence_authenticator, review_producers=review_producers)

    assert report["status"] == "verified", report["errors"]


def test_review_producers_rejects_a_producer_signing_outside_its_pinned_role() -> None:
    """A producer that is a globally trusted signer (its key validates) must still be
    rejected for a review role it is not pinned to — a workstream-runner key must not
    be able to sign a security review, the same guarantee EvidencePolicy.review_producers
    enforces server-side."""
    documents, evidence_authenticator, _trust = _signed_documents()
    child = documents[0]["children"][0]
    security_review = next(
        item for item in child["candidate"]["reviewReceipts"] if item["role"] == "security"
    )
    security_review["provenance"]["producer_id"] = "workstream-runner"
    _refresh_child_manifest(child)
    review_producers = {
        "code": ["code-reviewer"],
        "security": ["security-reviewer"],
        "adversarial": ["adversarial-reviewer"],
        "integration": ["integration-reviewer"],
    }

    report = _verify_signed(documents, evidence_authenticator, review_producers=review_producers)

    assert report["status"] == "failed"
    assert any("authorized for role 'security'" in item for item in report["errors"])


def test_review_producers_trust_file_must_pin_every_required_role(tmp_path: Path) -> None:
    _documents_unused, _evidence_authenticator, trust = _signed_documents()
    # missing security/adversarial/integration
    trust["review_producers"] = {"code": ["code-reviewer"]}
    trust_file = tmp_path / "evidence-trust.json"
    trust_file.write_text(json.dumps(trust), encoding="utf-8")

    with pytest.raises(verifier.ProofVerificationError, match="pin every required review role"):
        verifier._load_evidence_trust_file(trust_file)


def test_completed_immediate_remote_delivery_without_waits_remains_valid() -> None:
    detail, evidence, _waits = _documents()

    report = _verify((detail, evidence, []))

    assert report["status"] == "verified", report["errors"]
    assert report["deliveryWaits"] == []


def test_independent_verification_rejects_tampered_wait_receipts() -> None:
    documents, evidence_authenticator, _trust = _signed_documents()
    waits = documents[2]
    waits[0]["observation"]["checks"]["checks"][0]["conclusion"] = "failing"
    waits[1]["observation"]["mergeReceipt"]["verified_at"] = "2026-09-19T12:00:01+00:00"

    report = _verify_signed(documents, evidence_authenticator)

    assert report["status"] == "failed"
    assert report["trust"]["independentCryptographicVerification"] is False
    assert any("wait" in item and "check receipt" in item for item in report["errors"])
    assert any("wait" in item and "merge receipt" in item for item in report["errors"])


def test_wait_receipts_must_match_exact_remote_request_identity() -> None:
    detail, evidence, waits = _documents()
    waits[0]["observation"]["checks"]["candidate_sha"] = "9" * 40
    waits[1]["observation"]["mergeReceipt"]["target_branch"] = "other-target"

    report = _verify((detail, evidence, waits))

    assert report["status"] == "failed"
    assert any(
        "check receipt matches its exact request identity" in item for item in report["errors"]
    )
    assert any(
        "merge receipt matches its exact request identity" in item for item in report["errors"]
    )


def test_evidence_trust_file_rejects_private_key_material(tmp_path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    trust_file = tmp_path / "not-public.json"
    trust_file.write_text(
        json.dumps(
            {
                "trusted_public_keys": {"proof-key": private_pem},
                "producer_keys": {"runner": ["proof-key"]},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(verifier.ProofVerificationError, match="invalid public key"):
        verifier._load_evidence_trust_file(trust_file)


def test_cli_uses_public_evidence_trust_file(monkeypatch, tmp_path: Path) -> None:
    documents, _evidence_authenticator, trust = _signed_documents()
    responses = iter(documents)
    monkeypatch.setattr(verifier, "_fetch_json", lambda *args, **kwargs: next(responses))
    trust_file = tmp_path / "evidence-trust.json"
    trust_file.write_text(json.dumps(trust), encoding="utf-8")
    output = tmp_path / "proof.json"

    result = verifier.main(
        [
            "--base-url",
            "http://127.0.0.1:8180",
            "--execution-id",
            str(EXECUTION_ID),
            "--evidence-trust-file",
            str(trust_file),
            "--output",
            str(output),
        ]
    )

    assert result == 0
    assert json.loads(output.read_text())["trust"]["independentCryptographicVerification"] is True


def test_independent_verification_rejects_tampered_payload() -> None:
    documents, evidence_authenticator, _trust = _signed_documents()
    child = documents[0]["children"][0]
    child["candidate"]["verificationReceipts"][0]["stdout_digest"] = "9" * 64
    _refresh_child_manifest(child)

    report = _verify_signed(documents, evidence_authenticator)

    assert report["status"] == "failed"
    assert report["trust"]["serverValidatedEvidence"] is True
    assert report["trust"]["independentCryptographicVerification"] is False
    assert any("valid authorized cryptographic signature" in item for item in report["errors"])


def test_independent_verification_rejects_wrong_producer() -> None:
    documents, evidence_authenticator, _trust = _signed_documents()
    child = documents[0]["children"][0]
    child["candidate"]["reviewReceipts"][0]["provenance"]["producer_id"] = "attacker"
    _refresh_child_manifest(child)

    report = _verify_signed(documents, evidence_authenticator)

    assert report["status"] == "failed"
    assert report["trust"]["serverValidatedEvidence"] is True
    assert report["trust"]["independentCryptographicVerification"] is False
    assert any("valid authorized cryptographic signature" in item for item in report["errors"])


def test_independent_verification_rejects_wrong_key() -> None:
    documents, evidence_authenticator, _trust = _signed_documents()
    merge = documents[0]["mergeReceipt"]
    merge["provenance"]["key_id"] = "untrusted-key"

    report = _verify_signed(documents, evidence_authenticator)

    assert report["status"] == "failed"
    assert report["trust"]["serverValidatedEvidence"] is True
    assert report["trust"]["independentCryptographicVerification"] is False
    assert any("merge receipt has a valid authorized" in item for item in report["errors"])


def test_incomplete_execution_fails_closed() -> None:
    detail, evidence, waits = _documents()
    detail["state"] = "canceled"
    detail["completedAt"] = None
    evidence["state"] = "canceled"

    report = _verify((detail, evidence, waits))

    assert report["status"] == "failed"
    assert any("state is completed" in error for error in report["errors"])


def test_stale_integration_and_unsigned_child_receipt_fail_closed() -> None:
    detail, evidence, waits = _documents()
    evidence["children"][0]["candidate"]["verificationReceipts"][0]["provenance"] = None
    detail["integrationReceipts"][0]["candidate_sha"] = "9" * 40

    report = _verify((detail, evidence, waits))

    assert report["status"] == "failed"
    assert any("signed provenance" in error for error in report["errors"])
    assert any("selects an accepted child" in error for error in report["errors"])


def test_duplicate_or_unassigned_verification_contract_fails_closed() -> None:
    detail, evidence, waits = _documents()
    child = evidence["children"][0]
    child["workspace"]["test_contract_ids"] = ["unit", "integration"]
    duplicate = deepcopy(child["candidate"]["verificationReceipts"][0])
    duplicate["receipt_id"] = "verification-unit-duplicate"
    child["candidate"]["verificationReceipts"].append(duplicate)

    report = _verify((detail, evidence, waits))

    assert report["status"] == "failed"
    assert any("contract receipts are unique" in error for error in report["errors"])
    assert any("exactly match its allocation" in error for error in report["errors"])


def test_review_worker_must_match_allocated_worker() -> None:
    detail, evidence, waits = _documents()
    evidence["children"][0]["candidate"]["reviewReceipts"][0]["worker_id"] = "other-worker"

    report = _verify((detail, evidence, waits))

    assert report["status"] == "failed"
    assert any("worker matches child allocation" in error for error in report["errors"])


def test_duplicate_integration_receipt_cannot_hide_an_unintegrated_child() -> None:
    detail, evidence, waits = _documents()
    second_child = deepcopy(detail["children"][0])
    second_child.update(
        childId="44444444-4444-4444-8444-444444444444",
        childKey="ui",
        attempt=1,
    )
    second_child["workspace"].update(
        allocation_id="allocation-ui",
        workstream_key="ui",
        branch_name="campaign/ui",
    )
    second_child["candidate"].update(
        attemptId=second_child["childId"],
        requirementEvidence=[
            {
                "requirement_id": "REQ-ui",
                "implementation_paths": ["src/ui.py"],
                "verification_contract_ids": ["unit"],
            }
        ],
    )
    second_child["requirementIds"] = ["REQ-ui"]
    for receipt in second_child["candidate"]["verificationReceipts"]:
        receipt.update(
            receipt_id="verification-ui",
            workstream_key="ui",
            attempt_id=second_child["childId"],
        )
    for receipt in second_child["candidate"]["reviewReceipts"]:
        receipt.update(
            receipt_id=f"review-ui-{receipt['role']}",
            workstream_key="ui",
            attempt_id=second_child["childId"],
        )
    second_evidence = second_child["candidate"]
    second_child["evidenceValidation"]["manifest_digest"] = verifier.evidence_digest(
        verifier.CandidateEvidence(
            campaign_id=str(EXECUTION_ID),
            workstream_key="ui",
            attempt_id=second_child["childId"],
            worker_id="worker-api",
            repository=REPOSITORY,
            base_sha=BASE_SHA,
            candidate_sha=CHILD_SHA,
            candidate_tree=CHILD_TREE,
            requirements=second_evidence["requirementEvidence"],
            verifications=second_evidence["verificationReceipts"],
            reviews=second_evidence["reviewReceipts"],
        )
    )
    detail["children"].append(second_child)
    evidence["children"].append(deepcopy(second_child))
    duplicate = deepcopy(detail["integrationReceipts"][0])
    duplicate["receipt_id"] = "integration-api-duplicate"
    detail["integrationReceipts"].append(duplicate)

    report = _verify((detail, evidence, waits))

    assert report["status"] == "failed"
    assert any("unique child identities" in error for error in report["errors"])
    assert any("exact accepted child identity set" in error for error in report["errors"])


def test_latest_current_attempt_is_verified_without_rejecting_failed_history() -> None:
    detail, evidence, waits = _documents()
    prior = deepcopy(detail["children"][0])
    prior.update(
        childId="00000000-0000-4000-8000-000000000001",
        attempt=0,
        state="failed",
        candidate=None,
        evidenceValidation=None,
    )
    detail["children"].insert(0, prior)
    evidence["children"].insert(0, deepcopy(prior))

    report = _verify((detail, evidence, waits))

    assert report["status"] == "verified", report["errors"]
    assert report["children"][0]["attemptId"] == "22222222-2222-4222-8222-222222222222"


def test_missing_latest_current_child_from_evidence_fails_closed() -> None:
    detail, evidence, waits = _documents()
    missing = deepcopy(detail["children"][0])
    missing.update(
        childId="44444444-4444-4444-8444-444444444444",
        childKey="ui",
        attempt=1,
    )
    detail["children"].append(missing)

    report = _verify((detail, evidence, waits))

    assert report["status"] == "failed"
    assert any("every exact latest" in error for error in report["errors"])


def test_missing_independent_review_and_merge_identity_mismatch_fail_closed() -> None:
    detail, evidence, waits = _documents()
    reviews = evidence["children"][0]["candidate"]["reviewReceipts"]
    evidence["children"][0]["candidate"]["reviewReceipts"] = [
        item for item in reviews if item["role"] != "security"
    ]
    detail["mergeReceipt"]["canonical_target_sha"] = "9" * 40
    evidence["mergeReceipt"] = detail["mergeReceipt"]

    report = _verify((detail, evidence, waits))

    assert report["status"] == "failed"
    assert any("code, security, and adversarial" in error for error in report["errors"])
    assert any("verified canonical target SHA" in error for error in report["errors"])


def test_cli_reads_only_three_public_endpoints_and_writes_report(
    monkeypatch, tmp_path: Path
) -> None:
    detail, evidence, waits = _documents()
    fetched = []

    def fake_fetch(url, **kwargs):
        fetched.append((url, kwargs))
        if url.endswith("/evidence"):
            return evidence
        if url.endswith("/delivery-waits"):
            return waits
        return detail

    monkeypatch.setattr(verifier, "_fetch_json", fake_fetch)
    output = tmp_path / "proof.json"
    token_file = tmp_path / "token"
    token_file.write_text("secret-proof-token", encoding="utf-8")

    result = verifier.main(
        [
            "--base-url",
            "http://127.0.0.1:8180",
            "--execution-id",
            str(EXECUTION_ID),
            "--output",
            str(output),
            "--token-file",
            str(token_file),
            "--trust-server-attestation",
        ]
    )

    assert result == 0
    assert len(fetched) == 3
    assert all(item[1]["token"] == "secret-proof-token" for item in fetched)
    rendered = output.read_text()
    assert "secret-proof-token" not in rendered
    assert json.loads(rendered)["status"] == "server-attested"


def test_cli_requires_a_trust_choice(monkeypatch) -> None:
    """Without a trust file or an explicit opt-in, the script must refuse rather than
    silently report `status: "verified"` with zero signatures checked."""
    detail, evidence, waits = _documents()
    documents = iter((detail, evidence, waits))
    monkeypatch.setattr(verifier, "_fetch_json", lambda *args, **kwargs: next(documents))

    with pytest.raises(SystemExit) as exc_info:
        verifier.main(["--base-url", "http://127.0.0.1:8180", "--execution-id", str(EXECUTION_ID)])

    assert exc_info.value.code == 2


def test_cli_returns_nonzero_for_canceled_public_fixture(monkeypatch) -> None:
    detail, evidence, waits = _documents()
    detail["state"] = "canceled"
    documents = iter((detail, evidence, waits))
    monkeypatch.setattr(verifier, "_fetch_json", lambda *args, **kwargs: next(documents))

    assert (
        verifier.main(
            [
                "--base-url",
                "http://127.0.0.1:8180",
                "--execution-id",
                str(EXECUTION_ID),
                "--trust-server-attestation",
            ]
        )
        == 1
    )
