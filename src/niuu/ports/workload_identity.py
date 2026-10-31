"""Ports for workload identity proof verification."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from niuu.domain.models import Principal


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


@dataclass(frozen=True)
class IssuedWorkloadToken:
    """Short-lived bearer token minted for an already verified workload."""

    token: str
    expires_at: int


class WorkloadTokenIssuer(ABC):
    """Mints workload JWTs after an adapter has authenticated the workload."""

    @property
    @abstractmethod
    def enabled(self) -> bool:
        """Return whether workload token issuance is configured."""

    @abstractmethod
    def issue_token(
        self,
        *,
        principal: Principal,
        workload_subject: str,
        workload_name: str,
        audiences: list[str],
        token_use: str = "",
        claims: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> IssuedWorkloadToken:
        """Issue a token bound to *principal* and workload claims.

        ``ttl_seconds`` overrides the issuer's configured lifetime for callers
        whose credential is bound to something shorter-lived than wall-clock
        time (a Forge session launch) and is revoked server-side.
        """

    @abstractmethod
    def verify_token(self, token: str) -> dict[str, Any]:
        """Verify a token this issuer minted (signature, expiry, issuer, audience).

        Returns the claims. Raises :class:`WorkloadTokenVerificationError` when the
        token is not valid.
        """


class WorkloadTokenVerificationError(ValueError):
    """A token was not minted by this issuer, or is expired or malformed."""
