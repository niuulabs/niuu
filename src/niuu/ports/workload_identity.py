"""Ports for workload identity proof verification."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VerifiedWorkloadProof:
    """Normalized result of a validated workload identity proof."""

    verifier: str
    claims: dict[str, Any]


class WorkloadIdentityVerifier(ABC):
    """Validates a workload proof and returns trusted claims."""

    @abstractmethod
    async def verify(self, token: str) -> dict[str, Any]:
        """Verify *token* and return trusted claims."""
