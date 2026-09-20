"""Dynamically configured artifact-neutral evidence gate verifier."""

from __future__ import annotations

from typing import Any

from niuu.domain.evidence import EvidenceBundle, EvidencePolicy, EvidenceValidationReport
from niuu.domain.services.evidence import DeterministicEvidenceVerifier
from niuu.ports.evidence import EvidenceAuthenticator, EvidenceGateVerifier
from niuu.utils import import_class


class ConfiguredEvidenceGateVerifier(EvidenceGateVerifier):
    """Compose the generic verifier with an existing authenticator adapter."""

    def __init__(
        self,
        *,
        authenticator_adapter: str,
        authenticator_kwargs: dict[str, Any],
        trusted_producers: tuple[str, ...] | list[str],
    ) -> None:
        authenticator = import_class(authenticator_adapter)(**authenticator_kwargs)
        if not isinstance(authenticator, EvidenceAuthenticator):
            raise TypeError("Evidence authenticator adapter must implement EvidenceAuthenticator")
        self._verifier = DeterministicEvidenceVerifier(
            authenticator,
            trusted_producers=tuple(trusted_producers),
        )

    def validate(
        self,
        evidence: dict[str, Any],
        policy: dict[str, Any],
    ) -> EvidenceValidationReport:
        return self._verifier.validate(
            EvidenceBundle.model_validate(evidence),
            EvidencePolicy.model_validate(policy),
        )
