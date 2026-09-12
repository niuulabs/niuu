"""Domain service for personal access token lifecycle management."""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING
from uuid import UUID

from identity.models import Resource
from identity.ports import AuthorizationDeniedError, AuthorizationPort
from niuu.domain.models import PersonalAccessToken, Principal
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
        *,
        authorization: AuthorizationPort,
    ) -> None:
        self._repo = repo
        self._issuer = token_issuer
        self._ttl_days = ttl_days
        self._validator = validator
        self._authorization = authorization

    def _resource(
        self, principal: Principal, identifier: str, *, owner_id: str | None = None
    ) -> Resource:
        if not principal.user_id or not principal.tenant_id:
            raise AuthorizationDeniedError("An authenticated user and tenant are required")
        return Resource(
            "personal_access_token",
            identifier,
            {
                "owner_id": principal.user_id if owner_id is None else owner_id,
                "tenant_id": principal.tenant_id,
            },
        )

    async def _check(self, principal: Principal, action: str, identifier: str) -> None:
        if not await self._authorization.is_allowed(
            principal, action, self._resource(principal, identifier)
        ):
            raise AuthorizationDeniedError("Personal access token operation denied")

    async def create(
        self,
        principal: Principal,
        name: str,
        *,
        subject_token: str = "",
    ) -> tuple[PersonalAccessToken, str]:
        """Create a new PAT via the IDP. Returns (metadata, raw_jwt).

        Args:
            principal: Authenticated owner of the token.
            name: Human-readable label.
            subject_token: The user's current access token, used by the
                IDP token exchange to prove identity.

        Returns:
            Tuple of (PAT metadata, raw JWT shown once only).
        """
        owner_id = principal.user_id
        await self._check(principal, "create", owner_id)
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

    async def list(self, principal: Principal) -> list[PersonalAccessToken]:
        """List only owner tokens admitted by the resource policy."""
        self._resource(principal, principal.user_id)
        pats = await self._repo.list(principal.user_id)
        allowed = await self._authorization.filter_allowed(
            principal,
            "list",
            [self._resource(principal, str(p.id), owner_id=p.owner_id) for p in pats],
        )
        allowed_ids = {r.id for r in allowed}
        return [p for p in pats if str(p.id) in allowed_ids]

    async def revoke(self, pat_id: UUID, principal: Principal) -> bool:
        """Authorize and revoke an owner-bound PAT."""
        owner_id = principal.user_id
        await self._check(principal, "delete", str(pat_id))
        token_hash = await self._repo.delete(pat_id, owner_id)
        if token_hash is not None:
            logger.info("PAT revoked: id=%s owner=%s", pat_id, owner_id)
            if self._validator is not None:
                self._validator.invalidate_by_hash(token_hash)
            return True
        logger.warning("PAT revoke failed (not found): id=%s owner=%s", pat_id, owner_id)
        return False
