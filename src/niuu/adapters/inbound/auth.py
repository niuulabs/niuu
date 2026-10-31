"""Shared authentication dependency; honors the application's configured identity adapter."""

from __future__ import annotations

from fastapi import HTTPException, Request, status
from starlette.requests import HTTPConnection

from identity.adapters.http_auth import extract_principal as extract_identity_principal
from niuu.domain.models import Principal
from niuu.domain.services.forge_session_token import (
    FORGE_SESSION_ROLES,
    SESSION_ID_CLAIM,
)
from niuu.domain.services.token_scope import FORGE_SESSION_TOKEN_USE
from niuu.forge_mcp.credentials import is_forge_session_claims, token_scopes, unverified_claims

#: The only API a ``forge_session`` credential may call through a Niuu host.
FORGE_API_PREFIX = "/api/v1/forge/"
_BEARER_PREFIX = "bearer "


def presented_session_claims(connection: HTTPConnection) -> dict | None:
    """Claims (unverified) of a ``forge_session`` token in the Authorization header
    or ``?token=``, or ``None`` when the caller presents none."""
    candidates: list[str] = []
    authorization = connection.headers.get("authorization", "").strip()
    if authorization.lower().startswith(_BEARER_PREFIX):
        candidates.append(authorization[len(_BEARER_PREFIX) :].strip())
    query_token = connection.query_params.get("token")
    if query_token:
        candidates.append(query_token)
    for token in candidates:
        claims = unverified_claims(token)
        if is_forge_session_claims(claims):
            return claims
    return None


def _session_principal(request: Request) -> Principal | None:
    """The principal of a Forge session credential, or ``None`` for other callers.

    This host does not verify the token: it only routes. Every Forge route of the
    Guild facade forwards the credential to the owning Forge node, which verifies
    it, and the facade refuses to forward it to any node but the local one. Outside
    the Forge API a session credential is refused outright.
    """
    claims = presented_session_claims(request)
    if claims is None:
        return None
    if not request.url.path.startswith(FORGE_API_PREFIX):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forge session tokens may only call the Forge API (/api/v1/forge)",
        )
    return Principal(
        user_id=str(claims.get("sub") or ""),
        email=str(claims.get("email") or ""),
        tenant_id=str(claims.get("tenant_id") or "default"),
        roles=list(FORGE_SESSION_ROLES),
        token_use=FORGE_SESSION_TOKEN_USE,
        scopes=token_scopes(claims),
        bound_session_id=str(claims.get(SESSION_ID_CLAIM) or "") or None,
    )


async def extract_principal(request: Request) -> Principal:
    """Resolve the caller through the application's configured identity adapter.

    A Forge session credential comes first: it names its own owner and scopes,
    is confined to the Forge API (see :func:`_session_principal`) and is verified
    by the Forge node the Guild facade forwards it to. Every other caller goes
    through :func:`identity.adapters.http_auth.extract_principal`.
    """
    session_principal = _session_principal(request)
    if session_principal is not None:
        return session_principal
    return await extract_identity_principal(request)
