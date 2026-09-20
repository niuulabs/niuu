"""Contract tests for deterministic delivery evidence acceptance."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from niuu.adapters.evidence_signing import RsaEvidenceAuthenticator
from niuu.domain.delivery import (
    AcceptancePolicy,
    CandidateEvidence,
    CheckConclusion,
    CheckReceipt,
    CheckRecord,
    EvidenceValidationError,
    FindingDisposition,
    RequirementEvidence,
    ReviewFinding,
    ReviewReceipt,
    ReviewVerdict,
    VerificationReceipt,
    evidence_payload,
)
from niuu.domain.services.delivery import EvidenceVerifier

BASE = "a" * 40
CANDIDATE = "b" * 40
TREE = "c" * 40
DIGEST = "d" * 64
NOW = datetime.now(UTC)
REPOSITORY = "https://gitlab.example/org/repo"


@pytest.fixture
def authenticator() -> RsaEvidenceAuthenticator:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return RsaEvidenceAuthenticator(
        key_id="deployment-key",
        private_key_pem=pem,
        producer_keys={"trusted-runner": ("deployment-key",)},
    )


@pytest.fixture
def verifier(authenticator: RsaEvidenceAuthenticator) -> EvidenceVerifier:
    return EvidenceVerifier(authenticator, trusted_producers=("trusted-runner",))


def _sign(model, authenticator: RsaEvidenceAuthenticator):
    provenance = authenticator.sign(evidence_payload(model), "trusted-runner")
    return model.model_copy(update={"provenance": provenance})


def _verification(authenticator: RsaEvidenceAuthenticator) -> VerificationReceipt:
    receipt = VerificationReceipt(
        receipt_id="verification-1",
        campaign_id="campaign-1",
        workstream_key="api",
        attempt_id="attempt-1",
        repository=REPOSITORY,
        candidate_sha=CANDIDATE,
        candidate_tree=TREE,
        base_sha=BASE,
        contract_id="unit",
        command_digest=DIGEST,
        exit_code=0,
        stdout_digest=DIGEST,
        stderr_digest=DIGEST,
        started_at=NOW,
        completed_at=NOW,
    )
    return _sign(receipt, authenticator)


def _review(role: str, authenticator: RsaEvidenceAuthenticator) -> ReviewReceipt:
    receipt = ReviewReceipt(
        receipt_id=f"review-{role}",
        campaign_id="campaign-1",
        workstream_key="api",
        attempt_id="attempt-1",
        repository=REPOSITORY,
        candidate_sha=CANDIDATE,
        candidate_tree=TREE,
        base_sha=BASE,
        reviewer_id=f"reviewer-{role}",
        worker_id="worker-1",
        role=role,
        verdict=ReviewVerdict.PASS,
        summary="Reviewed the immutable candidate and found no blockers.",
    )
    return _sign(receipt, authenticator)


def _checks(authenticator: RsaEvidenceAuthenticator) -> CheckReceipt:
    receipt = CheckReceipt(
        receipt_id="checks-1",
        provider="gitlab",
        repository="https://gitlab.example/org/repo",
        review_number=1,
        candidate_sha=CANDIDATE,
        tested_base_sha=BASE,
        checks=(CheckRecord(name="ci", conclusion=CheckConclusion.PASSING),),
        observed_at=NOW,
    )
    return _sign(receipt, authenticator)


def _evidence(authenticator: RsaEvidenceAuthenticator) -> CandidateEvidence:
    return CandidateEvidence(
        campaign_id="campaign-1",
        workstream_key="api",
        attempt_id="attempt-1",
        worker_id="worker-1",
        repository=REPOSITORY,
        base_sha=BASE,
        candidate_sha=CANDIDATE,
        candidate_tree=TREE,
        requirements=(
            RequirementEvidence(
                requirement_id="REQ-1",
                implementation_paths=("src/api.py",),
                verification_contract_ids=("unit",),
            ),
        ),
        verifications=(_verification(authenticator),),
        reviews=tuple(_review(role, authenticator) for role in ("code", "security", "adversarial")),
        checks=_checks(authenticator),
    )


def _policy() -> AcceptancePolicy:
    return AcceptancePolicy(
        required_review_roles=("code", "security", "adversarial"),
        required_test_contract_ids=("unit",),
        review_producers={
            "code": ("trusted-runner",),
            "security": ("trusted-runner",),
            "adversarial": ("trusted-runner",),
        },
        test_producers={"unit": ("trusted-runner",)},
        required_check_names=("ci",),
        require_forge_checks=True,
        forge_producers=("trusted-runner",),
    )


def test_accepts_complete_attempt_bound_evidence(
    authenticator: RsaEvidenceAuthenticator,
    verifier: EvidenceVerifier,
) -> None:
    evidence = _evidence(authenticator)
    report = verifier.validate(evidence, _policy())
    assert report.accepted is True
    assert report.blocking_reasons == ()
    assert verifier.require(evidence, _policy()) == report.manifest_digest
    assert CandidateEvidence.model_validate_json(evidence.model_dump_json()) == evidence


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("stale-verification", "different attempt"),
        ("self-approval", "self-approved"),
        ("missing-review", "required review role security is missing"),
        ("missing-checks", "forge check receipt is missing"),
        ("skipped-check", "required forge check ci is skipped"),
        ("stale-check", "forge checks are for a stale candidate"),
        ("forged", "untrusted provenance"),
    ],
)
def test_rejects_invalid_evidence(
    mutation: str,
    reason: str,
    authenticator: RsaEvidenceAuthenticator,
    verifier: EvidenceVerifier,
) -> None:
    evidence = _evidence(authenticator)
    if mutation == "stale-verification":
        stale = evidence.verifications[0].model_copy(update={"attempt_id": "attempt-old"})
        evidence = evidence.model_copy(update={"verifications": (stale,)})
    elif mutation == "self-approval":
        review = evidence.reviews[0].model_copy(update={"reviewer_id": "worker-1"})
        review = _sign(review.model_copy(update={"provenance": None}), authenticator)
        evidence = evidence.model_copy(update={"reviews": (review, *evidence.reviews[1:])})
    elif mutation == "missing-review":
        evidence = evidence.model_copy(
            update={"reviews": tuple(item for item in evidence.reviews if item.role != "security")}
        )
    elif mutation == "missing-checks":
        evidence = evidence.model_copy(update={"checks": None})
    elif mutation == "skipped-check":
        checks = evidence.checks.model_copy(
            update={"checks": (CheckRecord(name="ci", conclusion=CheckConclusion.SKIPPED),)}
        )
        checks = _sign(checks.model_copy(update={"provenance": None}), authenticator)
        evidence = evidence.model_copy(update={"checks": checks})
    elif mutation == "stale-check":
        checks = evidence.checks.model_copy(update={"candidate_sha": "e" * 40})
        checks = _sign(checks.model_copy(update={"provenance": None}), authenticator)
        evidence = evidence.model_copy(update={"checks": checks})
    else:
        forged = evidence.verifications[0].model_copy(update={"exit_code": 9})
        evidence = evidence.model_copy(update={"verifications": (forged,)})

    report = verifier.validate(evidence, _policy())
    assert report.accepted is False
    assert reason in "; ".join(report.blocking_reasons)
    with pytest.raises(EvidenceValidationError):
        verifier.require(evidence, _policy())


def test_rejects_unresolved_blocking_finding(
    authenticator: RsaEvidenceAuthenticator,
    verifier: EvidenceVerifier,
) -> None:
    review = _review("code", authenticator).model_copy(
        update={
            "findings": (
                ReviewFinding(
                    finding_id="F-1",
                    blocking=True,
                    disposition=FindingDisposition.OPEN,
                    evidence="A failing boundary case remains.",
                ),
            ),
            "provenance": None,
        }
    )
    review = _sign(review, authenticator)
    evidence = _evidence(authenticator).model_copy(
        update={"reviews": (review, *_evidence(authenticator).reviews[1:])}
    )
    report = verifier.validate(evidence, _policy())
    assert report.accepted is False
    assert "blocking finding F-1 is unresolved" in report.blocking_reasons


def test_supplied_checks_are_rejected_when_policy_pins_no_forge_producer(
    authenticator: RsaEvidenceAuthenticator,
    verifier: EvidenceVerifier,
) -> None:
    policy = _policy().model_copy(
        update={
            "required_check_names": (),
            "require_forge_checks": False,
            "forge_producers": (),
        }
    )
    report = verifier.validate(_evidence(authenticator), policy)
    assert report.accepted is False
    assert "forge check receipt has an unauthorized producer" in report.blocking_reasons


def test_public_key_verifier_cannot_sign_and_rejects_unknown_key() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    signer = RsaEvidenceAuthenticator(
        key_id="key-1",
        private_key_pem=private_pem,
        producer_keys={"runner": ("key-1",)},
    )
    verifier = RsaEvidenceAuthenticator(
        key_id="verifier",
        trusted_public_keys={"key-1": public_pem},
        producer_keys={"runner": ("key-1",)},
    )
    provenance = signer.sign(b"payload", "runner")
    assert verifier.verify(b"payload", provenance) is True
    assert verifier.verify(b"changed", provenance) is False
    assert (
        verifier.verify(b"payload", provenance.model_copy(update={"key_id": "unknown-key"}))
        is False
    )
    with pytest.raises(RuntimeError, match="no private signing key"):
        verifier.sign(b"payload", "runner")


def test_file_backed_signer_preserves_key_and_producer_boundaries(tmp_path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    key_file = tmp_path / "evidence.pem"
    key_file.write_bytes(private_pem)
    signer = RsaEvidenceAuthenticator(
        key_id="file-key",
        private_key_pem_file=str(key_file),
        producer_keys={"runner": ("file-key",)},
    )
    provenance = signer.sign(b"payload", "runner")
    assert signer.verify(b"payload", provenance) is True
    with pytest.raises(RuntimeError, match="not authorized"):
        signer.sign(b"payload", "reviewer")
    with pytest.raises(ValueError, match="one evidence private key source"):
        RsaEvidenceAuthenticator(
            key_id="file-key",
            private_key_pem=private_pem,
            private_key_pem_file=str(key_file),
            producer_keys={"runner": ("file-key",)},
        )
    with pytest.raises(ValueError, match="key ID"):
        RsaEvidenceAuthenticator(key_id="", producer_keys={"runner": ("file-key",)})
    with pytest.raises(ValueError, match="bound"):
        RsaEvidenceAuthenticator(key_id="file-key", producer_keys={})


def test_signer_rejects_non_rsa_keys() -> None:
    from cryptography.hazmat.primitives.asymmetric import ed25519

    private = ed25519.Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = (
        ed25519.Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    with pytest.raises(ValueError, match="RSA private"):
        RsaEvidenceAuthenticator(
            key_id="key-1",
            private_key_pem=private,
            producer_keys={"runner": ("key-1",)},
        )
    with pytest.raises(ValueError, match="RSA public"):
        RsaEvidenceAuthenticator(
            key_id="key-1",
            trusted_public_keys={"key-1": public},
            producer_keys={"runner": ("key-1",)},
        )


def test_signature_producer_label_cannot_be_spoofed(
    authenticator: RsaEvidenceAuthenticator,
) -> None:
    receipt = _verification(authenticator)
    forged_provenance = receipt.provenance.model_copy(update={"producer_id": "attacker"})
    assert authenticator.verify(evidence_payload(receipt), forged_provenance) is False


def test_policy_rejects_authentic_but_unauthorized_review_producer() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    authenticator = RsaEvidenceAuthenticator(
        key_id="shared-test-key",
        private_key_pem=pem,
        producer_keys={
            "trusted-runner": ("shared-test-key",),
            "wrong-reviewer-service": ("shared-test-key",),
        },
    )
    evidence = _evidence(authenticator)
    review = evidence.reviews[0].model_copy(update={"provenance": None})
    provenance = authenticator.sign(
        evidence_payload(review),
        "wrong-reviewer-service",
    )
    evidence = evidence.model_copy(
        update={
            "reviews": (review.model_copy(update={"provenance": provenance}), *evidence.reviews[1:])
        }
    )
    verifier = EvidenceVerifier(
        authenticator,
        trusted_producers=("trusted-runner", "wrong-reviewer-service"),
    )
    report = verifier.validate(evidence, _policy())
    assert report.accepted is False
    assert "review role code has an unauthorized producer" in report.blocking_reasons
