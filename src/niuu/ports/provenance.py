"""Port for verifying build-provenance attestations."""

from __future__ import annotations

from abc import ABC, abstractmethod

from niuu.domain.models import VerifiedProvenance


class ProvenanceVerificationError(Exception):
    """Raised when an attestation does not prove what it claims.

    The signature, the certificate chain, the transparency log entry, the
    signer identity, the statement or the subject digest failed a check.
    ``reason`` names which; there is no "unverified but returned" outcome.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ProvenanceVerifierUnavailableError(RuntimeError):
    """Raised when verification could not run at all, so no verdict exists.

    For example the trust root could not be fetched or loaded. This is a
    platform fault, not a statement about the attestation.
    """


class ProvenanceNotConfiguredError(RuntimeError):
    """Raised when provenance verification is asked for but turned off.

    ``git.provenance.enabled`` is false, so the platform has no verifier. The
    operator decided to go without it; the request is refused, not answered
    with an unverified result.
    """


class ProvenanceVerifier(ABC):
    """Port for cryptographic verification of a build attestation bundle.

    An implementation accepts a bundle only when it can prove, against a trust
    root it controls, that the attestation was signed by the expected build
    workflow of the expected repository and that the named digest is one of
    the attested subjects.
    """

    @abstractmethod
    async def verify(
        self, bundle: str, *, subject_digest: str, repository: str
    ) -> VerifiedProvenance:
        """Verify one attestation bundle and return the facts it proves.

        Args:
            bundle: The bundle's JSON text.
            subject_digest: The digest the caller is asking about, as
                ``sha256:<64 hex>``; it must be one of the signed subjects.
            repository: ``owner/name`` of the repository the build must have
                come from.

        Raises:
            ProvenanceVerificationError: The bundle failed verification.
            ProvenanceVerifierUnavailableError: Verification could not run.
        """
