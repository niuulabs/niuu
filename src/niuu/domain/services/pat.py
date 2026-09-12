"""Domain service for personal access token lifecycle management."""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING
from uuid import UUID

from niuu.domain.models import PersonalAccessToken
from niuu.ports.pat_repository import PATRepository
from niuu.ports.token_issuer import TokenIssuer

if TYPE_CHECKING:
    from niuu.domain.services.pat_validator import PATValidator

logger = logging.getLogger(__name__)


class PATService:
    """Service for creating, listing, and revoking personal access tokens.

    Delegates token signing to the configured ``TokenIssuer`` (IDP adapter)
    so the resulting JWT is signed by the IDP and recognised by Envoy.
    """

    def __init__(
        self,
        repo: PATRepository,
        token_issuer: TokenIssuer,
        ttl_days: int = 365,
        validator: PATValidator | None = None,
    ) -> None:
        self._repo = repo
        self._issuer = token_issuer
        self._ttl_days = ttl_days
        self._validator = validator

    async def create(
        self,
        owner_id: str,
        name: str,
        *,
        subject_token: str = "",
    ) -> tuple[PersonalAccessToken, str]:
        """Create a new PAT via the IDP. Returns (metadata, raw_jwt).

        Args:
            owner_id: User ID (IDP sub claim).
            name: Human-readable label.
            subject_token: The user's current access token, used by the
                IDP token exchange to prove identity.

        Returns:
            Tuple of (PAT metadata, raw JWT shown once only).
        """
        issued = await self._issuer.issue_token(
            subject_token=subject_token,
            name=name,
            ttl_days=self._ttl_days,
        )

        if issued.subject != owner_id:
            raise ValueError(
                "Token issuer returned a different subject than the authenticated owner"
            )

        token_hash = hashlib.sha256(issued.raw_token.encode()).hexdigest()
        pat = await self._repo.create(owner_id, name, token_hash)
        logger.info("PAT created: id=%s owner=%s name=%s", pat.id, owner_id, name)
        return pat, issued.raw_token

    async def list(self, owner_id: str) -> list[PersonalAccessToken]:
        """List all PATs for an owner."""
        return await self._repo.list(owner_id)

    async def revoke(self, pat_id: UUID, owner_id: str) -> bool:
        """Revoke (delete) a PAT. Returns True if found and deleted."""
        token_hash = await self._repo.delete(pat_id, owner_id)
        if token_hash is not None:
            logger.info("PAT revoked: id=%s owner=%s", pat_id, owner_id)
            if self._validator is not None:
                self._validator.invalidate_by_hash(token_hash)
            return True
        logger.warning("PAT revoke failed (not found): id=%s owner=%s", pat_id, owner_id)
        return False
