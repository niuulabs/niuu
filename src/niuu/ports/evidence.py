"""Ports for authenticating artifact-neutral evidence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from niuu.domain.evidence import EvidenceProvenance, EvidenceValidationReport


class EvidenceAuthenticator(ABC):
    @abstractmethod
    def sign(self, payload: bytes, producer_id: str) -> EvidenceProvenance: ...

    @abstractmethod
    def verify(self, payload: bytes, provenance: EvidenceProvenance) -> bool: ...


class EvidenceGateVerifier(ABC):
    """Validate serialized evidence at a deterministic workflow gate."""

    @abstractmethod
    def validate(
        self,
        evidence: dict[str, Any],
        policy: dict[str, Any],
    ) -> EvidenceValidationReport: ...


class ArtifactDigestResolver(ABC):
    """Resolve an artifact identity to its deployment-observed content digest."""

    @abstractmethod
    def digest(self, *, artifact_kind: str, artifact_id: str) -> str: ...
