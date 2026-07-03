"""Ports for resident artifact review and verification."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ravn.domain.resident_review import (
    ResidentVerificationCheck,
    ResidentVerificationEvidence,
)


class ResidentVerificationPort(ABC):
    """Run concrete verification checks for resident-created artifacts."""

    @abstractmethod
    async def verify(self, check: ResidentVerificationCheck) -> ResidentVerificationEvidence:
        """Run one check and return concrete evidence."""
        raise NotImplementedError
