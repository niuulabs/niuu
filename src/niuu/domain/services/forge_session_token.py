"""Session-bound ``forge_session`` credentials for the Forge MCP.

Forge mints one token each time it launches a session and hands it to that
session's broker. The token is a workload JWT (the same issuer as the workload
identity exchange) carrying:

* ``sub`` — the session owner, ``tenant_id`` — the owner's tenant;
* ``token_use = "forge_session"`` and ``scopes`` — ``forge:notify`` and
  ``forge:session:read`` always, plus ``forge:session:message`` /
  ``forge:session:lifecycle`` for the ``message`` / ``lifecycle`` grants;
* ``workload_session_id`` — the one session it is bound to (the same claim the
  OpenShell session tokens use) and ``workload_launch_id`` — the launch it
  belongs to.

Its lifetime is tied to the session launch, not to wall-clock refreshes: the
``exp`` backstop is long, and Forge checks every presented token against the live
session row (it must still exist, be on the same launch, and not be stopped or
archived). Restarting a session therefore revokes the previous launch's token.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from niuu.domain.models import Principal
from niuu.domain.services.token_scope import (
    FORGE_SESSION_SCOPES,
    FORGE_SESSION_TOKEN_USE,
    bound_workload_scopes,
)
from niuu.forge_mcp.credentials import grants_from_scopes, normalize_grants, scopes_for_grants
from niuu.forge_mcp.models import ForgeMcpGrant
from niuu.ports.workload_identity import WorkloadTokenIssuer, WorkloadTokenVerificationError

#: Roles a session credential acts with. Never an admin role: a session sees only
#: its owner's sessions and notifications, whatever the owner's own roles are.
FORGE_SESSION_ROLES: tuple[str, ...] = ("volundr:developer",)

SESSION_ID_CLAIM = "workload_session_id"
LAUNCH_ID_CLAIM = "workload_launch_id"
_WORKLOAD_SUBJECT_PREFIX = "forge-session:"
_LAUNCH_ID_BYTES = 16


class ForgeSessionTokenError(ValueError):
    """A presented ``forge_session`` token is invalid, expired or revoked."""


def new_launch_id() -> str:
    """A fresh, unguessable id for one session launch."""
    return secrets.token_hex(_LAUNCH_ID_BYTES)


@dataclass(frozen=True)
class IssuedForgeSessionToken:
    """A minted session credential. ``token`` is a secret: never log it."""

    token: str
    expires_at: int
    session_id: str
    launch_id: str
    scopes: tuple[str, ...]
    grants: tuple[ForgeMcpGrant, ...]

    def __repr__(self) -> str:  # keep the secret out of logs and tracebacks
        return (
            f"IssuedForgeSessionToken(session_id={self.session_id!r}, "
            f"launch_id={self.launch_id!r}, scopes={self.scopes!r}, "
            f"expires_at={self.expires_at})"
        )


@dataclass(frozen=True)
class ForgeSessionClaims:
    """The verified identity a ``forge_session`` token carries."""

    session_id: str
    launch_id: str
    owner_id: str
    tenant_id: str
    email: str
    scopes: tuple[str, ...]
    expires_at: int
    token_id: str

    @property
    def grants(self) -> frozenset[ForgeMcpGrant]:
        return grants_from_scopes(self.scopes)

    def principal(self) -> Principal:
        """The principal requests made with this token act as."""
        return Principal(
            user_id=self.owner_id,
            email=self.email,
            tenant_id=self.tenant_id,
            roles=list(FORGE_SESSION_ROLES),
            token_use=FORGE_SESSION_TOKEN_USE,
            scopes=self.scopes,
            bound_session_id=self.session_id,
        )


class ForgeSessionTokenService:
    """Mints and verifies ``forge_session`` tokens through a workload token issuer."""

    def __init__(
        self,
        issuer: WorkloadTokenIssuer,
        *,
        audiences: Iterable[str],
        ttl_seconds: int,
        key_source: str,
    ) -> None:
        self._issuer = issuer
        self._audiences = [str(audience) for audience in audiences]
        self._ttl_seconds = int(ttl_seconds)
        self._key_source = key_source

    @property
    def key_source(self) -> str:
        """Where the signing key comes from (``workload_identity`` or a key file)."""
        return self._key_source

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    def mint(
        self,
        *,
        session_id: UUID | str,
        session_name: str,
        owner_id: str,
        tenant_id: str | None,
        grants: Iterable[ForgeMcpGrant | str],
        launch_id: str,
    ) -> IssuedForgeSessionToken:
        """Mint the credential for one launch of one session."""
        if not owner_id:
            raise ForgeSessionTokenError("a session credential needs the session owner")
        if not launch_id:
            raise ForgeSessionTokenError("a session credential needs a launch id")
        normalized = normalize_grants(grants)
        scopes = tuple(
            bound_workload_scopes(list(scopes_for_grants(normalized)), allowed=FORGE_SESSION_SCOPES)
        )
        bound_session = str(UUID(str(session_id)))
        issued = self._issuer.issue_token(
            principal=Principal(
                user_id=owner_id,
                email="",
                tenant_id=tenant_id or "default",
                roles=list(FORGE_SESSION_ROLES),
            ),
            workload_subject=f"{_WORKLOAD_SUBJECT_PREFIX}{bound_session}",
            workload_name=session_name or bound_session,
            audiences=list(self._audiences),
            token_use=FORGE_SESSION_TOKEN_USE,
            claims={
                "session_id": bound_session,
                "launch_id": launch_id,
                "scopes": list(scopes),
            },
            ttl_seconds=self._ttl_seconds,
        )
        return IssuedForgeSessionToken(
            token=issued.token,
            expires_at=issued.expires_at,
            session_id=bound_session,
            launch_id=launch_id,
            scopes=scopes,
            grants=normalized,
        )

    def verify(self, token: str) -> ForgeSessionClaims:
        """Verify signature, expiry, issuer, audience and the session-token shape.

        Revocation (the session's current launch and state) is the caller's check:
        it needs the session row.
        """
        try:
            claims = self._issuer.verify_token(token)
        except WorkloadTokenVerificationError as exc:
            raise ForgeSessionTokenError(f"invalid Forge session token: {exc}") from exc
        return _session_claims(claims)


def _session_claims(claims: dict[str, Any]) -> ForgeSessionClaims:
    if claims.get("token_use") != FORGE_SESSION_TOKEN_USE:
        raise ForgeSessionTokenError("not a Forge session token")
    raw_scopes = claims.get("scopes")
    if not isinstance(raw_scopes, list) or not all(isinstance(s, str) for s in raw_scopes):
        raise ForgeSessionTokenError("Forge session token has a malformed scopes claim")
    unknown = sorted(set(raw_scopes) - FORGE_SESSION_SCOPES)
    if unknown:
        raise ForgeSessionTokenError(f"Forge session token carries foreign scopes: {unknown}")
    try:
        session_id = str(UUID(str(claims.get(SESSION_ID_CLAIM) or "")))
    except ValueError as exc:
        raise ForgeSessionTokenError("Forge session token is not bound to a session") from exc
    launch_id = str(claims.get(LAUNCH_ID_CLAIM) or "")
    owner_id = str(claims.get("sub") or "")
    if not launch_id or not owner_id:
        raise ForgeSessionTokenError("Forge session token lacks its launch or owner")
    return ForgeSessionClaims(
        session_id=session_id,
        launch_id=launch_id,
        owner_id=owner_id,
        tenant_id=str(claims.get("tenant_id") or "default"),
        email=str(claims.get("email") or ""),
        scopes=tuple(raw_scopes),
        expires_at=int(claims.get("exp") or 0),
        token_id=str(claims.get("jti") or ""),
    )
