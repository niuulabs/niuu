"""Port for IDP-delegated token issuance.

Abstracts the mechanism for issuing long-lived tokens (PATs) via the
configured identity provider. Each adapter handles a specific IDP
(Keycloak, Entra ID, Okta, etc.) but the interface is uniform.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class IssuedToken:
    """Result of issuing a token via the IDP."""

    raw_token: str
    token_id: str  # jti claim
    subject: str  # sub claim (user ID)
    expires_at: int  # unix timestamp
    scopes: tuple[str, ...] | None = None


class TokenIssuer(ABC):
    """Port for IDP-delegated token issuance."""

    @abstractmethod
    async def issue_token(
        self,
        *,
        subject_token: str,
        name: str,
        ttl_days: int = 365,
        scopes: list[str] | None = None,
    ) -> IssuedToken:
        """Issue a long-lived token for a user via the IDP.

        Args:
            subject_token: The user's current access token (used for
                token exchange to prove identity).
            name: Human-readable label for the token.
            ttl_days: Token lifetime in days.

        Returns:
            IssuedToken with the raw JWT and metadata.
        """

    @abstractmethod
    async def close(self) -> None:
        """Clean up resources (HTTP clients, etc.)."""
