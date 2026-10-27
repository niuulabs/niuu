"""Sigstore verification of GitHub Actions build-provenance attestations.

Implements :class:`~niuu.ports.provenance.ProvenanceVerifier` with the
``sigstore`` library: the bundle's certificate chain, signed certificate
timestamp, transparency-log inclusion proof and DSSE signature are verified
against the Sigstore public-good trust root, and the signing certificate must
carry the configured GitHub Actions identity.

The identity policy
-------------------
GitHub Actions workflow identity tokens carry a ``job_workflow_ref`` claim, and
Fulcio puts that claim in the certificate's Subject Alternative Name and in the
"Build Signer URI" extension. For a job in a *reusable* workflow the claim names
the reusable workflow's own file (at the ref the caller ran), not the caller's
workflow, so one configured path covers every workflow that calls it. The
certificate must therefore satisfy all of:

* OIDC issuer is the configured issuer (``https://token.actions.githubusercontent.com``);
* Source Repository URI is ``https://github.com/<owner>/<repo>`` for the
  repository being asked about;
* the SAN is ``https://github.com/<owner>/<repo>/<path>@<ref>`` where ``<path>``
  is one of the configured workflow files, and equals the Build Signer URI.

The ``<ref>`` is deliberately not pinned: feature branches, pull-request merge
refs and ``dev`` all legitimately build images. It is returned as a verified
fact so the caller decides which refs it trusts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Sequence

from cryptography.hazmat import asn1
from cryptography.x509 import (
    Certificate,
    ExtensionNotFound,
    ObjectIdentifier,
    SubjectAlternativeName,
    UniformResourceIdentifier,
)
from sigstore.errors import Error as SigstoreError
from sigstore.errors import VerificationError
from sigstore.models import Bundle, ClientTrustConfig, InvalidBundle, TrustedRoot
from sigstore.verify import Verifier
from sigstore.verify.policy import OIDCIssuerV2

from niuu.domain.models import VerifiedProvenance
from niuu.ports.provenance import (
    ProvenanceVerificationError,
    ProvenanceVerifier,
    ProvenanceVerifierUnavailableError,
)

logger = logging.getLogger(__name__)

# Fulcio certificate extensions: https://github.com/sigstore/fulcio/blob/main/docs/oid-info.md
_BUILD_SIGNER_URI = ObjectIdentifier("1.3.6.1.4.1.57264.1.9")
_SOURCE_REPOSITORY_URI = ObjectIdentifier("1.3.6.1.4.1.57264.1.12")
_SOURCE_REPOSITORY_DIGEST = ObjectIdentifier("1.3.6.1.4.1.57264.1.13")
_SOURCE_REPOSITORY_REF = ObjectIdentifier("1.3.6.1.4.1.57264.1.14")
_RUN_INVOCATION_URI = ObjectIdentifier("1.3.6.1.4.1.57264.1.21")

# `actions/attest-build-provenance` signs an in-toto v1 statement carrying a
# SLSA v1 provenance predicate inside a DSSE envelope.
_DSSE_PAYLOAD_TYPE = "application/vnd.in-toto+json"
_IN_TOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
_SLSA_PROVENANCE_PREDICATE_TYPE = "https://slsa.dev/provenance/v1"

_GITHUB_URL = "https://github.com/"
_SHA256_PREFIX = "sha256:"


def _extension_text(cert: Certificate, oid: ObjectIdentifier, name: str) -> str:
    """Read a Fulcio extension holding a DER UTF8String, or fail verification."""
    try:
        raw = cert.extensions.get_extension_for_oid(oid).value.value  # type: ignore[attr-defined]
    except ExtensionNotFound:
        raise VerificationError(
            f"certificate has no {name} extension ({oid.dotted_string})"
        ) from None
    return asn1.decode_der(str, raw)


class _GitHubWorkflowPolicy:
    """Accept only certificates issued to the configured workflows of one repository."""

    def __init__(self, *, issuer: str, repository: str, workflow_paths: frozenset[str]) -> None:
        self._issuer = OIDCIssuerV2(issuer)
        self._repository = repository
        self._workflow_paths = workflow_paths

    def verify(self, cert: Certificate) -> None:
        self._issuer.verify(cert)

        source = _extension_text(cert, _SOURCE_REPOSITORY_URI, "Source Repository URI")
        expected_source = f"{_GITHUB_URL}{self._repository}"
        # GitHub owner and repository names are unique ignoring case.
        if source.casefold() != expected_source.casefold():
            raise VerificationError(
                f"certificate was issued for source repository {source!r}, not {expected_source!r}"
            )

        san = self._workflow_identity(cert)
        signer = _extension_text(cert, _BUILD_SIGNER_URI, "Build Signer URI")
        if signer != san:
            raise VerificationError(
                f"certificate's Build Signer URI {signer!r} differs from its identity {san!r}"
            )

        repo_prefix = f"{_GITHUB_URL}{self._repository}/"
        if san[: len(repo_prefix)].casefold() != repo_prefix.casefold():
            raise VerificationError(
                f"signing workflow {san!r} does not belong to repository {self._repository!r}"
            )
        workflow, separator, ref = san[len(repo_prefix) :].partition("@")
        if not separator or not ref:
            raise VerificationError(f"signing identity {san!r} is not a workflow reference")
        if workflow not in self._workflow_paths:
            raise VerificationError(
                f"signing workflow {workflow!r} is not one of the trusted workflows "
                f"{sorted(self._workflow_paths)} (git.provenance.kwargs.workflow_paths)"
            )

    @staticmethod
    def _workflow_identity(cert: Certificate) -> str:
        san_extension = cert.extensions.get_extension_for_class(SubjectAlternativeName).value
        identities = san_extension.get_values_for_type(UniformResourceIdentifier)
        if len(identities) != 1:
            raise VerificationError(
                f"certificate has {len(identities)} URI identities, expected exactly one"
            )
        return identities[0]


class SigstoreProvenanceVerifier(ProvenanceVerifier):
    """Verify GitHub Actions SLSA provenance bundles with the ``sigstore`` library.

    Args:
        issuer: OIDC issuer the signing certificate must name.
        workflow_paths: Workflow files, relative to the repository root, whose
            jobs may sign (matched against the certificate's job workflow ref).
        trusted_root_file: Optional Sigstore ``trusted_root.json`` for clusters
            that cannot reach the TUF repository; empty uses the public-good
            production root, refreshed over TUF on every verification so a key
            rotation is never missed. The TUF cache lives under the process
            user's data and cache directories (``XDG_DATA_HOME`` /
            ``XDG_CACHE_HOME``), which must be writable.

    Raises:
        ValueError: ``issuer`` or ``workflow_paths`` is empty or malformed.
    """

    def __init__(
        self,
        *,
        issuer: str,
        workflow_paths: Sequence[str],
        trusted_root_file: str = "",
    ) -> None:
        if not issuer:
            raise ValueError("git.provenance.kwargs.issuer must be set")
        paths = frozenset(workflow_paths)
        if not paths:
            raise ValueError(
                "git.provenance.kwargs.workflow_paths must list at least one workflow file "
                "allowed to sign image attestations"
            )
        for path in paths:
            if not path or path.startswith("/") or "@" in path:
                raise ValueError(
                    f"git.provenance.kwargs.workflow_paths entry {path!r} must be a file path "
                    "relative to the repository root, without '@' (the ref is not part of it)"
                )
        self._issuer = issuer
        self._workflow_paths = paths
        self._trusted_root_file = trusted_root_file
        # The TUF updater writes a shared on-disk cache; one refresh at a time.
        self._trust_lock = threading.Lock()

    async def verify(
        self, bundle: str, *, subject_digest: str, repository: str
    ) -> VerifiedProvenance:
        return await asyncio.to_thread(self._verify, bundle, subject_digest, repository)

    def _verify(self, bundle_json: str, subject_digest: str, repository: str) -> VerifiedProvenance:
        try:
            bundle = Bundle.from_json(bundle_json)
        except InvalidBundle as exc:
            raise ProvenanceVerificationError(f"the bundle is malformed: {exc}") from exc
        verifier = self._build_verifier()

        policy = _GitHubWorkflowPolicy(
            issuer=self._issuer, repository=repository, workflow_paths=self._workflow_paths
        )
        try:
            payload_type, payload = verifier.verify_dsse(bundle, policy)
        except VerificationError as exc:
            raise ProvenanceVerificationError(str(exc)) from exc

        subject_name = self._attested_subject(payload_type, payload, subject_digest)
        cert = bundle.signing_certificate
        try:
            return VerifiedProvenance(
                subject_name=subject_name,
                subject_digest=subject_digest,
                source_repository=_extension_text(
                    cert, _SOURCE_REPOSITORY_URI, "Source Repository URI"
                ),
                commit_sha=_extension_text(
                    cert, _SOURCE_REPOSITORY_DIGEST, "Source Repository Digest"
                ),
                ref=_extension_text(cert, _SOURCE_REPOSITORY_REF, "Source Repository Ref"),
                workflow=_extension_text(cert, _BUILD_SIGNER_URI, "Build Signer URI"),
                run_url=_extension_text(cert, _RUN_INVOCATION_URI, "Run Invocation URI"),
            )
        except VerificationError as exc:
            raise ProvenanceVerificationError(str(exc)) from exc

    def _build_verifier(self) -> Verifier:
        """A verifier on a current trust root, or an error saying why there is none."""
        with self._trust_lock:
            try:
                if self._trusted_root_file:
                    return Verifier(trusted_root=TrustedRoot.from_file(self._trusted_root_file))
                return Verifier(trusted_root=ClientTrustConfig.production().trusted_root)
            except (SigstoreError, OSError, ValueError) as exc:
                source = self._trusted_root_file or "the Sigstore production TUF repository"
                raise ProvenanceVerifierUnavailableError(
                    f"Could not load the Sigstore trust root from {source}: {exc}. Check the "
                    "platform's network access to tuf-repo-cdn.sigstore.dev and that the "
                    "process user's XDG data and cache directories are writable, or point "
                    "git.provenance.kwargs.trusted_root_file at a trusted_root.json."
                ) from exc

    @staticmethod
    def _attested_subject(payload_type: str, payload: bytes, subject_digest: str) -> str:
        """Check the signed statement is SLSA provenance for *subject_digest*; name its subject."""
        if payload_type != _DSSE_PAYLOAD_TYPE:
            raise ProvenanceVerificationError(
                f"the signed payload is {payload_type!r}, not an in-toto statement"
            )
        try:
            statement = json.loads(payload)
        except ValueError as exc:
            raise ProvenanceVerificationError(
                f"the signed in-toto statement is not valid JSON: {exc}"
            ) from exc
        if not isinstance(statement, dict) or statement.get("_type") != _IN_TOTO_STATEMENT_TYPE:
            raise ProvenanceVerificationError(
                f"the signed statement is not an {_IN_TOTO_STATEMENT_TYPE} statement"
            )
        if statement.get("predicateType") != _SLSA_PROVENANCE_PREDICATE_TYPE:
            raise ProvenanceVerificationError(
                f"the signed statement is a {statement.get('predicateType')!r} attestation, "
                f"not {_SLSA_PROVENANCE_PREDICATE_TYPE} build provenance"
            )
        wanted = subject_digest.removeprefix(_SHA256_PREFIX)
        for subject in statement.get("subject") or []:
            if isinstance(subject, dict) and (subject.get("digest") or {}).get("sha256") == wanted:
                return str(subject.get("name") or "")
        raise ProvenanceVerificationError(
            f"the signed statement attests other artifacts, not {subject_digest}"
        )
