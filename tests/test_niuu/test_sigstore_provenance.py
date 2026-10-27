"""Sigstore verification of GitHub build-provenance bundles.

The positive and tamper tests run real cryptographic verification against real
bundles from public repositories, signed through the Sigstore public-good
instance: the SLSA provenance GitHub holds for astral-sh/uv's ``source.tar.gz``
of release 0.13.6 (signed by ``.github/workflows/release.yml``), and for
dexidp/dex's container image on ghcr.io (a manifest list, signed from a reusable
workflow). The Sigstore trusted root they verify against is a snapshot of the
production TUF root, passed as ``trusted_root_file``, so the tests need no
network. Nothing in those tests is mocked.

Pieces that cannot be produced from a real signature (statement shape, rarely
absent certificate extensions, a TUF repository that is down) use stand-ins and
say so.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from cryptography.x509 import ExtensionNotFound, ObjectIdentifier
from sigstore.errors import TUFError, VerificationError
from sigstore.models import Bundle, TrustedRoot

from niuu.adapters.outbound import sigstore_provenance
from niuu.adapters.outbound.sigstore_provenance import (
    SigstoreProvenanceVerifier,
    _GitHubWorkflowPolicy,
)
from niuu.domain.models import VerifiedProvenance
from niuu.ports.provenance import (
    ProvenanceVerificationError,
    ProvenanceVerifierUnavailableError,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "provenance"
BUNDLE = (FIXTURES / "uv-source-tarball.bundle.json").read_text(encoding="utf-8")
TRUSTED_ROOT = str(FIXTURES / "sigstore-trusted-root.json")

ISSUER = "https://token.actions.githubusercontent.com"
REPOSITORY = "astral-sh/uv"
WORKFLOW = ".github/workflows/release.yml"
# source.tar.gz, one of 21 subjects the signed statement lists.
DIGEST = "sha256:37af57c31f780488bce798b73fcd382949faef72e7a98a7e73fe0f24874ed924"
COMMIT = "329bf9448afd9f9088d14a4d57fbc61eb454f117"


def _verifier(**overrides) -> SigstoreProvenanceVerifier:
    kwargs = {"issuer": ISSUER, "workflow_paths": [WORKFLOW], "trusted_root_file": TRUSTED_ROOT}
    kwargs.update(overrides)
    return SigstoreProvenanceVerifier(**kwargs)


# A container image's multi-arch manifest list, attested by dexidp/dex with
# actions/attest-build-provenance from a *reusable* workflow: `artifacts.yaml`
# (workflow_call) is called by `ci.yaml`. The certificate's SAN and Build Signer URI
# name the reusable workflow; only its Build Config URI names the caller.
REUSABLE_BUNDLE = (FIXTURES / "dex-image-reusable-workflow.bundle.json").read_text(encoding="utf-8")
REUSABLE_DIGEST = "sha256:3cea00341c073adcf45b6f8ae9d3c26e5e1201316e3a5793c9129d8373f7bee6"
REUSABLE_ISSUER_ARGS = {"issuer": ISSUER, "trusted_root_file": TRUSTED_ROOT}


async def test_an_image_attested_from_a_reusable_workflow_is_signed_as_that_workflow() -> None:
    verifier = SigstoreProvenanceVerifier(
        workflow_paths=[".github/workflows/artifacts.yaml"], **REUSABLE_ISSUER_ARGS
    )

    result = await verifier.verify(
        REUSABLE_BUNDLE, subject_digest=REUSABLE_DIGEST, repository="dexidp/dex"
    )

    assert result.subject_name == "ghcr.io/dexidp/dex"
    assert result.workflow == (
        "https://github.com/dexidp/dex/.github/workflows/artifacts.yaml@refs/heads/master"
    )
    assert result.ref == "refs/heads/master"
    assert result.commit_sha == "b02ead0ba87a320f3328c6860ea45f5b6314e6bd"
    assert result.run_url == "https://github.com/dexidp/dex/actions/runs/40336195631/attempts/1"


async def test_pinning_the_calling_workflow_instead_of_the_reusable_one_rejects_the_image() -> None:
    # The mistake the pin exists to prevent: the caller (`ci.yaml`) is only the Build
    # Config URI; the signing identity is the reusable workflow's file.
    verifier = SigstoreProvenanceVerifier(
        workflow_paths=[".github/workflows/ci.yaml"], **REUSABLE_ISSUER_ARGS
    )

    with pytest.raises(ProvenanceVerificationError, match="not one of the trusted workflows"):
        await verifier.verify(
            REUSABLE_BUNDLE, subject_digest=REUSABLE_DIGEST, repository="dexidp/dex"
        )


# --- Real verification of a real bundle ----------------------------------------


async def test_a_real_github_attestation_verifies_and_yields_its_facts() -> None:
    result = await _verifier().verify(BUNDLE, subject_digest=DIGEST, repository=REPOSITORY)

    assert result == VerifiedProvenance(
        subject_name="source.tar.gz",
        subject_digest=DIGEST,
        source_repository="https://github.com/astral-sh/uv",
        commit_sha=COMMIT,
        ref="refs/heads/main",
        workflow="https://github.com/astral-sh/uv/.github/workflows/release.yml@refs/heads/main",
        run_url="https://github.com/astral-sh/uv/actions/runs/40425141598/attempts/1",
    )


async def test_the_repository_name_is_matched_ignoring_case() -> None:
    result = await _verifier().verify(BUNDLE, subject_digest=DIGEST, repository="Astral-SH/UV")

    assert result.commit_sha == COMMIT


async def test_every_subject_of_a_multi_subject_statement_is_attested() -> None:
    other = "sha256:c7e6d5f081605b347cafe2ead974d60bc737db01f694ea9ab9a644b66c59c40f"

    result = await _verifier().verify(BUNDLE, subject_digest=other, repository=REPOSITORY)

    assert result.subject_name == "dist-manifest.json"
    assert result.subject_digest == other


async def test_a_digest_the_statement_does_not_attest_is_refused() -> None:
    absent = "sha256:" + "0" * 64

    with pytest.raises(ProvenanceVerificationError, match="attests other artifacts"):
        await _verifier().verify(BUNDLE, subject_digest=absent, repository=REPOSITORY)


async def test_another_repository_is_refused() -> None:
    with pytest.raises(ProvenanceVerificationError, match="source repository"):
        await _verifier().verify(BUNDLE, subject_digest=DIGEST, repository="evil/uv")


async def test_a_workflow_that_is_not_trusted_is_refused() -> None:
    verifier = _verifier(workflow_paths=[".github/workflows/reusable-container-build.yaml"])

    with pytest.raises(ProvenanceVerificationError, match="not one of the trusted workflows"):
        await verifier.verify(BUNDLE, subject_digest=DIGEST, repository=REPOSITORY)


async def test_an_unexpected_oidc_issuer_is_refused() -> None:
    verifier = _verifier(issuer="https://accounts.example.test")

    with pytest.raises(ProvenanceVerificationError, match="OIDCIssuerV2"):
        await verifier.verify(BUNDLE, subject_digest=DIGEST, repository=REPOSITORY)


async def test_a_statement_altered_after_signing_is_refused() -> None:
    bundle = json.loads(BUNDLE)
    statement = base64.b64decode(bundle["dsseEnvelope"]["payload"])
    forged = statement.replace(DIGEST.removeprefix("sha256:").encode(), b"f" * 64)
    assert forged != statement
    bundle["dsseEnvelope"]["payload"] = base64.b64encode(forged).decode()

    with pytest.raises(ProvenanceVerificationError, match="invalid signature"):
        await _verifier().verify(json.dumps(bundle), subject_digest=DIGEST, repository=REPOSITORY)


async def test_a_signature_from_another_key_is_refused() -> None:
    bundle = json.loads(BUNDLE)
    signature = bytearray(base64.b64decode(bundle["dsseEnvelope"]["signatures"][0]["sig"]))
    signature[-1] ^= 0xFF
    bundle["dsseEnvelope"]["signatures"][0]["sig"] = base64.b64encode(bytes(signature)).decode()

    with pytest.raises(ProvenanceVerificationError, match="invalid signature"):
        await _verifier().verify(json.dumps(bundle), subject_digest=DIGEST, repository=REPOSITORY)


@pytest.mark.parametrize("garbage", ["not json", "{}", '{"mediaType": "x"}'])
async def test_a_malformed_bundle_is_refused(garbage: str) -> None:
    with pytest.raises(ProvenanceVerificationError, match="malformed"):
        await _verifier().verify(garbage, subject_digest=DIGEST, repository=REPOSITORY)


# --- The trust root --------------------------------------------------------------


async def test_a_missing_trusted_root_file_means_no_verdict(tmp_path: Path) -> None:
    verifier = _verifier(trusted_root_file=str(tmp_path / "absent.json"))

    with pytest.raises(ProvenanceVerifierUnavailableError, match="trusted_root_file"):
        await verifier.verify(BUNDLE, subject_digest=DIGEST, repository=REPOSITORY)


async def test_production_trust_comes_from_tuf_when_no_file_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Stand-in for the network: the TUF repository is replaced by the snapshot of
    # its root; the verification itself is still real.
    production = MagicMock()
    production.return_value.trusted_root = TrustedRoot.from_file(TRUSTED_ROOT)
    monkeypatch.setattr(sigstore_provenance.ClientTrustConfig, "production", production)

    result = await _verifier(trusted_root_file="").verify(
        BUNDLE, subject_digest=DIGEST, repository=REPOSITORY
    )

    production.assert_called_once_with()
    assert result.commit_sha == COMMIT


async def test_an_unreachable_tuf_repository_means_no_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sigstore_provenance.ClientTrustConfig,
        "production",
        MagicMock(side_effect=TUFError("could not refresh")),
    )

    with pytest.raises(ProvenanceVerifierUnavailableError, match="tuf-repo-cdn.sigstore.dev"):
        await _verifier(trusted_root_file="").verify(
            BUNDLE, subject_digest=DIGEST, repository=REPOSITORY
        )


# --- Construction -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "needle"),
    [
        ({"issuer": ""}, "issuer must be set"),
        ({"workflow_paths": []}, "at least one workflow file"),
        ({"workflow_paths": ["/abs.yml"]}, "relative to the repository root"),
        ({"workflow_paths": [".github/workflows/a.yml@refs/heads/dev"]}, "without '@'"),
        ({"workflow_paths": [""]}, "relative to the repository root"),
    ],
)
def test_a_misconfigured_verifier_does_not_start(kwargs: dict, needle: str) -> None:
    with pytest.raises(ValueError, match=needle):
        _verifier(**kwargs)


# --- The signed statement (shapes a real signature cannot be coaxed into) -----------


def _statement(**overrides) -> bytes:
    statement = {
        "_type": "https://in-toto.io/Statement/v1",
        "predicateType": "https://slsa.dev/provenance/v1",
        "subject": [
            {"name": "other", "digest": {"sha512": "ab"}},
            "not-a-subject",
            {"name": "ghcr.io/niuulabs/niuu", "digest": {"sha256": "ab" * 32}},
        ],
    }
    statement.update(overrides)
    return json.dumps(statement).encode()


def _attested(payload_type: str, payload: bytes) -> str:
    return SigstoreProvenanceVerifier._attested_subject(
        payload_type, payload, "sha256:" + "ab" * 32
    )


def test_the_attested_subject_is_the_one_with_the_requested_sha256() -> None:
    assert _attested("application/vnd.in-toto+json", _statement()) == "ghcr.io/niuulabs/niuu"


@pytest.mark.parametrize(
    ("payload_type", "payload", "needle"),
    [
        ("text/plain", _statement(), "not an in-toto statement"),
        ("application/vnd.in-toto+json", b"{nope", "not valid JSON"),
        ("application/vnd.in-toto+json", b"[]", "is not an https://in-toto.io/Statement/v1"),
        (
            "application/vnd.in-toto+json",
            _statement(_type="https://in-toto.io/Statement/v0.1"),
            "is not an https://in-toto.io/Statement/v1",
        ),
        (
            "application/vnd.in-toto+json",
            _statement(predicateType="https://spdx.dev/Document"),
            "not https://slsa.dev/provenance/v1 build provenance",
        ),
        ("application/vnd.in-toto+json", _statement(subject=[]), "attests other artifacts"),
        ("application/vnd.in-toto+json", _statement(subject=None), "attests other artifacts"),
    ],
)
def test_a_statement_that_is_not_provenance_for_the_digest_is_refused(
    payload_type: str, payload: bytes, needle: str
) -> None:
    with pytest.raises(ProvenanceVerificationError, match=needle):
        _attested(payload_type, payload)


# --- The certificate policy against certificates a real signature cannot vary -------


def _der(text: str) -> bytes:
    raw = text.encode()
    assert len(raw) < 128
    return bytes([0x0C, len(raw)]) + raw


def _certificate(*, source: str, signer: str, identities: list[str], issuer: str = ISSUER):
    """A certificate stand-in carrying exactly the extensions the policy reads."""
    by_oid = {
        "1.3.6.1.4.1.57264.1.8": _der(issuer),
        "1.3.6.1.4.1.57264.1.9": _der(signer),
        "1.3.6.1.4.1.57264.1.12": _der(source),
    }

    def get_extension_for_oid(oid: ObjectIdentifier):
        if oid.dotted_string not in by_oid:
            raise ExtensionNotFound("absent", oid)
        extension = MagicMock()
        extension.value.value = by_oid[oid.dotted_string]
        return extension

    cert = MagicMock()
    cert.extensions.get_extension_for_oid.side_effect = get_extension_for_oid
    cert.extensions.get_extension_for_class.return_value.value.get_values_for_type.return_value = (
        identities
    )
    return cert


def _policy() -> _GitHubWorkflowPolicy:
    return _GitHubWorkflowPolicy(
        issuer=ISSUER,
        repository="niuulabs/niuu",
        workflow_paths=frozenset({".github/workflows/reusable-container-build.yaml"}),
    )


REUSABLE = "https://github.com/niuulabs/niuu/.github/workflows/reusable-container-build.yaml"


def test_the_reusable_build_workflow_at_any_ref_is_accepted() -> None:
    for ref in ("refs/heads/dev", "refs/pull/1000/merge", "refs/tags/v1.2.3"):
        identity = f"{REUSABLE}@{ref}"
        cert = _certificate(
            source="https://github.com/niuulabs/niuu", signer=identity, identities=[identity]
        )

        _policy().verify(cert)


@pytest.mark.parametrize(
    ("source", "signer", "identities", "needle"),
    [
        (
            "https://github.com/niuulabs/niuu",
            f"{REUSABLE}@refs/heads/dev",
            [f"{REUSABLE}@refs/heads/dev", f"{REUSABLE}@refs/heads/x"],
            "expected exactly one",
        ),
        (
            "https://github.com/niuulabs/niuu",
            f"{REUSABLE}@refs/heads/dev",
            [f"{REUSABLE}@refs/heads/other"],
            "differs from its identity",
        ),
        (
            "https://github.com/niuulabs/niuu",
            f"{REUSABLE}@",
            [f"{REUSABLE}@"],
            "not a workflow reference",
        ),
        (
            "https://github.com/niuulabs/niuu",
            REUSABLE,
            [REUSABLE],
            "not a workflow reference",
        ),
        (
            "https://github.com/niuulabs/niuu",
            "https://github.com/niuulabs/niuu/.github/workflows/dev.yaml@refs/heads/dev",
            ["https://github.com/niuulabs/niuu/.github/workflows/dev.yaml@refs/heads/dev"],
            "not one of the trusted workflows",
        ),
        (
            "https://github.com/niuulabs/niuu",
            "https://github.com/evil/niuu/.github/workflows/reusable-container-build.yaml@r",
            ["https://github.com/evil/niuu/.github/workflows/reusable-container-build.yaml@r"],
            "does not belong to repository",
        ),
        (
            "https://github.com/evil/niuu",
            f"{REUSABLE}@refs/heads/dev",
            [f"{REUSABLE}@refs/heads/dev"],
            "source repository",
        ),
    ],
)
def test_certificates_outside_the_pinned_identity_are_refused(
    source: str, signer: str, identities: list[str], needle: str
) -> None:
    cert = _certificate(source=source, signer=signer, identities=identities)

    with pytest.raises(VerificationError, match=needle):
        _policy().verify(cert)


def test_a_certificate_without_the_build_signer_extension_is_refused() -> None:
    cert = _certificate(
        source="https://github.com/niuulabs/niuu",
        signer=f"{REUSABLE}@refs/heads/dev",
        identities=[f"{REUSABLE}@refs/heads/dev"],
    )
    original = cert.extensions.get_extension_for_oid.side_effect

    def without_signer(oid: ObjectIdentifier):
        if oid.dotted_string == "1.3.6.1.4.1.57264.1.9":
            raise ExtensionNotFound("absent", oid)
        return original(oid)

    cert.extensions.get_extension_for_oid.side_effect = without_signer

    with pytest.raises(VerificationError, match="no Build Signer URI extension"):
        _policy().verify(cert)


async def test_a_certificate_missing_a_fact_the_caller_needs_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The signature and policy verify for real; only the extraction of one fact is made to fail,
    # standing in for a certificate from an issuer that omits the commit digest extension.
    real = sigstore_provenance._extension_text

    def without_commit(cert, oid, name):
        if name == "Source Repository Digest":
            raise VerificationError("certificate has no Source Repository Digest extension")
        return real(cert, oid, name)

    monkeypatch.setattr(sigstore_provenance, "_extension_text", without_commit)

    with pytest.raises(ProvenanceVerificationError, match="no Source Repository Digest"):
        await _verifier().verify(BUNDLE, subject_digest=DIGEST, repository=REPOSITORY)


def test_the_real_certificate_carries_every_extension_the_facts_need() -> None:
    cert = Bundle.from_json(BUNDLE).signing_certificate

    for oid in ("1.9", "1.12", "1.13", "1.14", "1.21"):
        assert cert.extensions.get_extension_for_oid(ObjectIdentifier(f"1.3.6.1.4.1.57264.{oid}"))
