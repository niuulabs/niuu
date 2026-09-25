"""Authentication port — abstract interface for agent identity extraction."""

from __future__ import annotations

from abc import ABC, abstractmethod

from fastapi import Request

from bifrost.auth import AgentIdentity


class AuthPort(ABC):
    """Extract a verified ``AgentIdentity`` from an incoming HTTP request."""

    @abstractmethod
    async def extract(self, request: Request) -> AgentIdentity:
        """Return the caller's verified identity.

        Async because OIDC/PAT verification does I/O (JWKS fetch, PAT
        revocation lookup) — every adapter implements this as a coroutine,
        even the ones that do no I/O, so callers always ``await`` it.

        Raises:
            HTTPException(401): When the request cannot be authenticated.
        """
