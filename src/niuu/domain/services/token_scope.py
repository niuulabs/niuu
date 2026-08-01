"""Least-privilege scope enforcement for Valkyrie build tokens.

A Valkyrie that commissions a build authenticates with a short-lived JWT
minted by the workload-identity exchange. Those build tokens carry two
extra claims:

- ``token_use == "valkyrie_build"`` — marks the token as a scoped build
  credential (ordinary human PATs and ordinary workload tokens never carry
  this value).
- ``scopes`` — the list of build scopes the credential is allowed to use,
  bounded to :data:`KNOWN_BUILD_SCOPES` at issuance time.

Enforcement is **stateless and fail-closed for build tokens only**:

- A token WITHOUT ``token_use == "valkyrie_build"`` (humans, PATs, legacy
  workload tokens) passes through untouched — full backward compatibility.
- A ``valkyrie_build`` token is admitted at a build entry point only when
  its ``scopes`` claim contains the required scope; otherwise it is 403'd.

The JWT is decoded WITHOUT signature verification — the same posture as
``PATValidator``: Envoy validates the signature upstream, so this layer
only reads the already-trusted claims.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import jwt
from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

#: The claim value that marks a token as a scoped Valkyrie build credential.
VALKYRIE_BUILD_TOKEN_USE = "valkyrie_build"
OPENSHELL_SESSION_TOKEN_USE = "openshell_session"
OPENSHELL_RESIDENT_TOKEN_USE = "openshell_resident"

#: The scopes a short-lived workload credential may ever be granted. A caller
#: cannot self-grant anything outside this allowlist — unknown scopes are
#: dropped at issuance time.
#:
#: Named for builds because builds were the first use, but the mechanism is
#: general: any least-privilege workload credential draws its scopes from here.
#: `observatory:topology:push` lets a source publish its own view of the
#: topology and nothing else, so a resident on a bare-metal host does not need
#: a full-authority PAT to appear on the graph.
KNOWN_BUILD_SCOPES: frozenset[str] = frozenset(
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
    """Return True when the token's claims mark it as a build credential."""
    return claims.get("token_use") == VALKYRIE_BUILD_TOKEN_USE


def token_has_scope(token: str, scope: str) -> bool:
    """Return True when ``token`` is permitted to use ``scope``.

    Backward-compatible and fail-closed for build tokens only:

    - A missing or malformed token, or any token that is NOT a
      ``valkyrie_build`` token, returns True (humans / PATs / legacy
      workload tokens pass through unchanged).
    - A ``valkyrie_build`` token returns True only when ``scope`` is present
      in its ``scopes`` claim.
    """
    claims = _decode_claims(token)
    if claims is None:
        return True

    if not token_requires_scope_check(claims):
        return True

    granted = claims.get("scopes", [])
    if not isinstance(granted, list):
        return False
    return scope in granted


def bound_build_scopes(requested: list[str] | None) -> list[str]:
    """Intersect requested build scopes with :data:`KNOWN_BUILD_SCOPES`.

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
        if scope in KNOWN_BUILD_SCOPES:
            allowed.append(scope)
            continue
        dropped.append(scope)

    if dropped:
        logger.warning(
            "Dropping unknown build scopes from token request: %s",
            ", ".join(sorted(dropped)),
        )
    return allowed


def _bearer_from_request(request: Request) -> str:
    """Extract the raw bearer token from the Authorization header, or ""."""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return ""
    return auth[7:]


def require_build_scope(scope: str) -> Callable[..., Awaitable[None]]:
    """FastAPI dependency factory enforcing a build scope, fail-closed.

    The returned dependency reads the bearer token from the request's
    Authorization header, then raises HTTP 403 when the token is a
    ``valkyrie_build`` credential lacking ``scope``. Non-build tokens are
    admitted unchanged.

    Usage::

        @router.post("/sessions", ...)
        async def create_session(
            request: Request,
            data: SessionCreate,
            _: None = Depends(require_build_scope("forge:session:create")),
        ) -> SessionResponse:
            ...
    """
    if scope not in KNOWN_BUILD_SCOPES:
        raise ValueError(f"Unknown build scope: {scope}")

    async def _check(request: Request) -> None:
        token = _bearer_from_request(request)
        if token_has_scope(token, scope):
            return None
        logger.warning("Build token denied: missing scope %s", scope)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Build token is missing the required scope: {scope}",
        )

    return _check


__all__ = [
    "KNOWN_BUILD_SCOPES",
    "OPENSHELL_SESSION_TOKEN_USE",
    "OPENSHELL_RESIDENT_TOKEN_USE",
    "TOPOLOGY_PUSH_SCOPE",
    "VALKYRIE_BUILD_TOKEN_USE",
    "bound_build_scopes",
    "require_build_scope",
    "token_has_scope",
    "token_requires_scope_check",
]
