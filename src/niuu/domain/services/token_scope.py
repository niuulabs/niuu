"""Least-privilege scope enforcement for short-lived workload credentials.

A workload that needs to do one specific thing — commission a build, launch a
workflow, publish its own topology — authenticates with a short-lived JWT
minted by the workload-identity exchange. Those tokens carry two extra claims:

- ``token_use == "valkyrie_build"`` — marks the token as a scoped credential
  (ordinary human PATs and ordinary workload tokens never carry this value).
  The value is historical: builds were the first use, but the marker means
  "this credential is scoped", not "this credential builds".
- ``scopes`` — what the credential may do, bounded to
  :data:`KNOWN_WORKLOAD_SCOPES` at issuance time.

Enforcement is **stateless and fail-closed for scoped tokens only**:

- A token WITHOUT ``token_use == "valkyrie_build"`` (humans, PATs, legacy
  workload tokens) passes through untouched — full backward compatibility.
- A scoped token is admitted at an entry point only when its ``scopes`` claim
  contains the required scope; otherwise it is 403'd.

A scope is only real because code enforces it: :func:`require_scope` on a
route is what gives the string meaning. Adding an entry here without a
matching enforcement point produces a credential that reads as restricted but
protects nothing, which is why `test_token_scope.py` asserts the two stay in
step.

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
    return claims.get("token_use") == VALKYRIE_BUILD_TOKEN_USE


def token_has_scope(token: str, scope: str) -> bool:
    """Return True when ``token`` is permitted to use ``scope``.

    Backward-compatible and fail-closed for scoped tokens only:

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
