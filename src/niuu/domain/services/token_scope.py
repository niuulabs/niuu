"""Restrict IDP-signed OAuth PAT scopes and workload scope arrays.

Envoy verifies the JWT signature; this module only narrows verified authority.
Scoped credentials are admitted solely at their named entry points and may
inspect their own current identity. Legacy unscoped credentials retain their
resource-policy permissions; hardened PAT issuance requires explicit scopes.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable

import jwt
from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

#: The claim value that marks a token as a scoped workload credential.
VALKYRIE_BUILD_TOKEN_USE = "valkyrie_build"
OPENSHELL_SESSION_TOKEN_USE = "openshell_session"
OPENSHELL_RESIDENT_TOKEN_USE = "openshell_resident"

#: The scopes a short-lived workload credential may ever be granted. A caller
#: cannot self-grant anything outside this allowlist — unknown scopes are
#: dropped at issuance time, which is why this is deliberately a constant and
#: not configuration: whoever could edit the config could mint privilege.
#:
#: Every entry must have an enforcement point (see the module docstring).
KNOWN_WORKLOAD_SCOPES: frozenset[str] = frozenset(
    {
        "forge:session:create",
        "ting:workflow:launch",
        "observatory:topology:push",
    }
)

#: Scope required to publish a topology fragment to the push inbox.
TOPOLOGY_PUSH_SCOPE = "observatory:topology:push"


def _decode_claims(token: str) -> dict | None:
    """Decode a JWT's claims without verifying its signature.

    Returns ``None`` when the token is missing or malformed. Signature
    verification is delegated to Envoy upstream, so this layer only reads
    the already-trusted claims (same posture as ``PATValidator``).
    """
    if not token:
        return None
    try:
        return jwt.decode(
            token,
            options={"verify_signature": False, "verify_exp": False},
        )
    except jwt.InvalidTokenError:
        return None


def token_requires_scope_check(claims: dict) -> bool:
    """Return True when the token's claims mark it as a scoped credential."""
    return claims.get("token_use") == VALKYRIE_BUILD_TOKEN_USE or (
        claims.get("type") == "pat"
        and bool(set(str(claims.get("scope", "")).split()) & KNOWN_WORKLOAD_SCOPES)
    )


def credential_scopes(claims: dict) -> list[str]:
    """Read the signed OAuth scope claim for PATs, or workload scope array."""
    if claims.get("type") == "pat":
        value = claims.get("scope", "")
        return value.split() if isinstance(value, str) else []
    value = claims.get("scopes", [])
    return value if isinstance(value, list) and all(isinstance(s, str) for s in value) else []


def validate_pat_scopes(scopes: list[str] | None) -> tuple[str, ...] | None:
    """Reject unknown or empty grants rather than mint an unrestricted token."""
    if scopes is None:
        return None
    if not scopes or any(s not in KNOWN_WORKLOAD_SCOPES for s in scopes):
        raise ValueError("PAT scopes must be a non-empty list of supported scopes")
    return tuple(sorted(set(scopes)))


def token_has_scope(token: str, scope: str) -> bool:
    """Check a named grant on scoped PATs and workload credentials."""
    claims = _decode_claims(token)
    if claims is None:
        return True

    if not token_requires_scope_check(claims):
        return True

    granted = credential_scopes(claims)
    if not isinstance(granted, list):
        return False
    return scope in granted


def bound_workload_scopes(requested: list[str] | None) -> list[str]:
    """Intersect requested scopes with :data:`KNOWN_WORKLOAD_SCOPES`.

    Unknown scopes are dropped (and logged) so a caller can never
    self-grant a scope the platform does not recognise. Order and
    duplicates from the request are collapsed to a stable, de-duplicated
    list of known scopes.
    """
    if not requested:
        return []

    seen: set[str] = set()
    allowed: list[str] = []
    dropped: list[str] = []
    for raw in requested:
        scope = str(raw).strip()
        if not scope or scope in seen:
            continue
        seen.add(scope)
        if scope in KNOWN_WORKLOAD_SCOPES:
            allowed.append(scope)
            continue
        dropped.append(scope)

    if dropped:
        logger.warning(
            "Dropping unknown scopes from token request: %s",
            ", ".join(sorted(dropped)),
        )
    return allowed


def credential_allows_route(token: str, method: str, path: str) -> bool:
    """Deny scoped credentials everywhere except their explicit entry points."""
    claims = _decode_claims(token)
    if claims is None or not token_requires_scope_check(claims):
        return True
    if method == "GET" and path == "/api/v1/identity/me":
        return True
    routes = [
        ("POST", r"/api/v1/forge/sessions", "forge:session:create"),
        ("POST", r"/api/v1/ting/workflows/[^/?%]+/launch", "ting:workflow:launch"),
        ("PUT", r"/api/v1/niuu/observatory/fragments/[^/?%]+", "observatory:topology:push"),
        ("DELETE", r"/api/v1/niuu/observatory/fragments/[^/?%]+", "observatory:topology:push"),
    ]
    granted = credential_scopes(claims)
    return any(
        method == verb and re.fullmatch(pattern, path) and scope in granted
        for verb, pattern, scope in routes
    )


def _bearer_from_request(request: Request) -> str:
    """Extract the raw bearer token from the Authorization header, or ""."""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return ""
    return auth[7:]


def require_scope(scope: str) -> Callable[..., Awaitable[None]]:
    """FastAPI dependency factory enforcing one scope, fail-closed.

    The returned dependency reads the bearer token from the request's
    Authorization header, then raises HTTP 403 when the token is a scoped
    credential lacking ``scope``. Unscoped tokens are admitted unchanged.

    Usage::

        @router.post("/sessions", ...)
        async def create_session(
            request: Request,
            data: SessionCreate,
            _: None = Depends(require_scope("forge:session:create")),
        ) -> SessionResponse:
            ...
    """
    if scope not in KNOWN_WORKLOAD_SCOPES:
        raise ValueError(f"Unknown workload scope: {scope}")

    async def _check(request: Request) -> None:
        token = _bearer_from_request(request)
        if token_has_scope(token, scope):
            return None
        logger.warning("Scoped token denied: missing scope %s", scope)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Token is missing the required scope: {scope}",
        )

    return _check


__all__ = [
    "KNOWN_WORKLOAD_SCOPES",
    "OPENSHELL_SESSION_TOKEN_USE",
    "OPENSHELL_RESIDENT_TOKEN_USE",
    "TOPOLOGY_PUSH_SCOPE",
    "VALKYRIE_BUILD_TOKEN_USE",
    "bound_workload_scopes",
    "require_scope",
    "token_has_scope",
    "token_requires_scope_check",
]
