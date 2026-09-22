"""Artifact-neutral deterministic evidence verification tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError

from niuu.adapters.artifact_digest import FilesystemArtifactDigestResolver
from niuu.adapters.evidence_gate import ConfiguredEvidenceGateVerifier
from niuu.adapters.evidence_signing import RsaEvidenceAuthenticator
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
    evidence_payload,
)
from niuu.domain.services.evidence import DeterministicEvidenceVerifier
from niuu.domain.workflow_evidence import evaluate_workflow_evidence

NOW = datetime.now(UTC)
DOCUMENT_DIGEST = "a" * 64
RESULT_DIGEST = "b" * 64


@pytest.fixture
def key_pem() -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


@pytest.fixture
def authenticator(key_pem: bytes) -> RsaEvidenceAuthenticator:
    return RsaEvidenceAuthenticator(
        key_id="document-key",
        private_key_pem=key_pem,
        producer_keys={
            "document-validator": ("document-key",),
            "independent-reviewer": ("document-key",),
        },
    )


def _sign(model, authenticator: RsaEvidenceAuthenticator, producer: str):
    return model.model_copy(
        update={"provenance": authenticator.sign(evidence_payload(model), producer)}
    )


def _document_bundle(
    authenticator: RsaEvidenceAuthenticator,
    *,
    execution_id: str = "",
) -> EvidenceBundle:
    identity = ArtifactIdentity(
        artifact_kind="document",
        artifact_id="policy/retention-v3",
        artifact_digest=DOCUMENT_DIGEST,
        attempt_id="attempt-2",
        execution_id=execution_id,
    )
    result = _sign(
        EvidenceResultReceipt(
            receipt_id="result-schema",
            identity=identity,
            contract_id="document-schema",
            result=EvidenceResult.PASS,
            result_digest=RESULT_DIGEST,
            completed_at=NOW,
        ),
        authenticator,
        "document-validator",
    )
    review = _sign(
        EvidenceReviewReceipt(
            receipt_id="review-legal",
            identity=identity,
            reviewer_id="reviewer-42",
            subject_worker_id="author-7",
            role="legal",
            verdict=EvidenceReviewVerdict.PASS,
            summary="The exact document digest satisfies the retention policy.",
        ),
        authenticator,
        "independent-reviewer",
    )
    checks = _sign(
        EvidenceCheckReceipt(
            receipt_id="checks-publication",
            identity=identity,
            checks=(
                EvidenceCheck(
                    name="links-resolve",
                    conclusion=EvidenceCheckConclusion.PASSING,
                ),
            ),
            observed_at=NOW,
        ),
        authenticator,
        "document-validator",
    )
    return EvidenceBundle(
        identity=identity,
        worker_id="author-7",
        requirements=(
            EvidenceRequirement(
                requirement_id="RETENTION-1",
                result_contract_ids=("document-schema",),
            ),
        ),
        results=(result,),
        reviews=(review,),
        checks=checks,
    )


def _policy() -> EvidencePolicy:
    return EvidencePolicy(
        required_review_roles=("legal",),
        required_result_contract_ids=("document-schema",),
        review_producers={"legal": ("independent-reviewer",)},
        result_producers={"document-schema": ("document-validator",)},
        required_check_names=("links-resolve",),
        require_checks=True,
        check_producers=("document-validator",),
    )


def test_accepts_signed_non_coding_artifact_evidence(
    authenticator: RsaEvidenceAuthenticator,
) -> None:
    bundle = _document_bundle(authenticator)
    verifier = DeterministicEvidenceVerifier(
        authenticator,
        trusted_producers=("document-validator", "independent-reviewer"),
    )

    report = verifier.validate(bundle, _policy())

    assert report.accepted is True
    assert report.blocking_reasons == ()
    assert verifier.require(bundle, _policy()) == report.manifest_digest
    assert EvidenceBundle.model_validate_json(bundle.model_dump_json()) == bundle


def test_accepts_checks_only_policy_without_a_model_reviewer(
    authenticator: RsaEvidenceAuthenticator,
) -> None:
    bundle = _document_bundle(authenticator).model_copy(
        update={"requirements": (), "results": (), "reviews": ()}
    )
    policy = EvidencePolicy(
        required_check_names=("links-resolve",),
        check_producers=("document-validator",),
    )

    report = DeterministicEvidenceVerifier(
        authenticator,
        trusted_producers=("document-validator",),
    ).validate(bundle, policy)

    assert report.accepted is True


def test_rejects_empty_policy_and_malformed_native_bundle(
    authenticator: RsaEvidenceAuthenticator,
) -> None:
    with pytest.raises(ValidationError, match="at least one result, review, or check"):
        EvidencePolicy()
    malformed = _document_bundle(authenticator).model_dump(mode="json")
    malformed["identity"]["artifact_digest"] = "not-a-digest"
    with pytest.raises(ValidationError, match="artifact_digest"):
        EvidenceBundle.model_validate(malformed)


def test_native_semantic_tampering_invalidates_the_receipt_signature(
    authenticator: RsaEvidenceAuthenticator,
) -> None:
    bundle = _document_bundle(authenticator)
    tampered = bundle.results[0].model_copy(update={"result": EvidenceResult.FAIL})
    bundle = bundle.model_copy(update={"results": (tampered,)})

    report = DeterministicEvidenceVerifier(
        authenticator,
        trusted_producers=("document-validator", "independent-reviewer"),
    ).validate(bundle, _policy())

    assert report.accepted is False
    assert "result result-schema has untrusted provenance" in report.blocking_reasons


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("attempt", "different attempt"),
        ("self-review", "self-approved"),
        ("finding", "blocking finding DOC-1 is unresolved"),
        ("failed-result", "result contract document-schema failed"),
    ],
)
def test_rejects_invalid_non_coding_evidence(
    mutation: str,
    reason: str,
    authenticator: RsaEvidenceAuthenticator,
) -> None:
    bundle = _document_bundle(authenticator)
    if mutation == "attempt":
        result = bundle.results[0].model_copy(
            update={
                "identity": bundle.identity.model_copy(update={"attempt_id": "attempt-old"}),
                "provenance": None,
            }
        )
        result = _sign(result, authenticator, "document-validator")
        bundle = bundle.model_copy(update={"results": (result,)})
    elif mutation == "self-review":
        review = bundle.reviews[0].model_copy(
            update={"reviewer_id": "author-7", "provenance": None}
        )
        review = _sign(review, authenticator, "independent-reviewer")
        bundle = bundle.model_copy(update={"reviews": (review,)})
    elif mutation == "finding":
        review = bundle.reviews[0].model_copy(
            update={
                "findings": (
                    EvidenceFinding(
                        finding_id="DOC-1",
                        blocking=True,
                        disposition=EvidenceFindingDisposition.OPEN,
                        evidence="The retention exception has no cited authority.",
                    ),
                ),
                "provenance": None,
            }
        )
        review = _sign(review, authenticator, "independent-reviewer")
        bundle = bundle.model_copy(update={"reviews": (review,)})
    else:
        result = bundle.results[0].model_copy(
            update={"result": EvidenceResult.FAIL, "provenance": None}
        )
        result = _sign(result, authenticator, "document-validator")
        bundle = bundle.model_copy(update={"results": (result,)})

    report = DeterministicEvidenceVerifier(
        authenticator,
        trusted_producers=("document-validator", "independent-reviewer"),
    ).validate(bundle, _policy())

    assert report.accepted is False
    assert reason in "; ".join(report.blocking_reasons)


def test_dynamic_gate_adapter_validates_serialized_bundle_and_policy(
    key_pem: bytes,
    authenticator: RsaEvidenceAuthenticator,
) -> None:
    gate = ConfiguredEvidenceGateVerifier(
        authenticator_adapter="niuu.adapters.evidence_signing.RsaEvidenceAuthenticator",
        authenticator_kwargs={
            "key_id": "document-key",
            "private_key_pem": key_pem,
            "producer_keys": {
                "document-validator": ("document-key",),
                "independent-reviewer": ("document-key",),
            },
        },
        trusted_producers=("document-validator", "independent-reviewer"),
    )

    report = evaluate_workflow_evidence(
        gate,
        evidence=_document_bundle(authenticator).model_dump(mode="json"),
        policy=_policy().model_dump(mode="json"),
    )

    assert report.accepted is True


def test_workflow_helper_rejects_non_object_payload(
    authenticator: RsaEvidenceAuthenticator,
) -> None:
    class Gate:
        def validate(self, evidence, policy):  # pragma: no cover
            raise AssertionError("invalid payload must be rejected before adapter invocation")

    with pytest.raises(ValueError, match="evidence object"):
        evaluate_workflow_evidence(Gate(), evidence=[], policy={})


def test_filesystem_artifact_digest_tracks_actual_document_bytes(tmp_path) -> None:
    artifact = tmp_path / "policies" / "retention.md"
    artifact.parent.mkdir()
    artifact.write_text("Retention period: 30 days.\n")
    resolver = FilesystemArtifactDigestResolver(root=str(tmp_path), chunk_size_bytes=3)

    first = resolver.digest(artifact_kind="document", artifact_id="policies/retention.md")
    artifact.write_text("Retention period: 90 days.\n")
    second = resolver.digest(artifact_kind="document", artifact_id="policies/retention.md")

    assert first != second
    assert second == hashlib.sha256(artifact.read_bytes()).hexdigest()


def test_filesystem_artifact_digest_rejects_escape_and_non_files(tmp_path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("outside")
    (root / "escape").symlink_to(outside)
    (root / "directory").mkdir()
    resolver = FilesystemArtifactDigestResolver(root=str(root))

    with pytest.raises(ValueError, match="relative path"):
        resolver.digest(artifact_kind="document", artifact_id="../secret.txt")
    with pytest.raises(ValueError, match="symlink escapes"):
        resolver.digest(artifact_kind="document", artifact_id="escape")
    with pytest.raises(ValueError, match="regular file"):
        resolver.digest(artifact_kind="document", artifact_id="directory")


def test_filesystem_artifact_digest_rejects_deleted_artifact_and_invalid_chunk_size(
    tmp_path,
) -> None:
    artifact = tmp_path / "document.txt"
    artifact.write_text("ephemeral")
    resolver = FilesystemArtifactDigestResolver(root=str(tmp_path))
    artifact.unlink()

    with pytest.raises(ValueError, match="does not exist"):
        resolver.digest(artifact_kind="document", artifact_id="document.txt")
    with pytest.raises(ValueError, match="positive integer"):
        FilesystemArtifactDigestResolver(root=str(tmp_path), chunk_size_bytes=0)
