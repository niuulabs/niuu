"""How Forge MCP grants map onto the scopes of a ``forge_session`` credential.

Every session holds ``forge:notify`` and ``forge:session:read``. An operator
grant adds one scope each: ``message`` → ``forge:session:message`` and
``lifecycle`` → ``forge:session:lifecycle``. Forge enforces the scopes; Skuld and
the MCP hosts only read them (unverified) to decide which tools to offer.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import jwt

from niuu.domain.services.token_scope import (
    FORGE_NOTIFY_SCOPE,
    FORGE_SESSION_LIFECYCLE_SCOPE,
    FORGE_SESSION_MESSAGE_SCOPE,
    FORGE_SESSION_READ_SCOPE,
    FORGE_SESSION_TOKEN_USE,
)
from niuu.forge_mcp.models import ForgeMcpGrant

#: Scopes every session credential carries.
DEFAULT_SESSION_SCOPES: tuple[str, ...] = (FORGE_NOTIFY_SCOPE, FORGE_SESSION_READ_SCOPE)

#: The scope each operator grant adds.
GRANT_SCOPES: dict[ForgeMcpGrant, str] = {
    ForgeMcpGrant.MESSAGE: FORGE_SESSION_MESSAGE_SCOPE,
    ForgeMcpGrant.LIFECYCLE: FORGE_SESSION_LIFECYCLE_SCOPE,
}


def normalize_grants(grants: Iterable[ForgeMcpGrant | str]) -> tuple[ForgeMcpGrant, ...]:
    """Validate and sort grants; raises ``ValueError`` for an unknown grant."""
    return tuple(sorted({ForgeMcpGrant(grant) for grant in grants}, key=lambda g: g.value))


def scopes_for_grants(grants: Iterable[ForgeMcpGrant | str]) -> tuple[str, ...]:
    """The scopes of a session credential holding ``grants`` (defaults included)."""
    granted = [GRANT_SCOPES[grant] for grant in normalize_grants(grants)]
    return (*DEFAULT_SESSION_SCOPES, *granted)


def grants_from_scopes(scopes: Iterable[str]) -> frozenset[ForgeMcpGrant]:
    """The operator grants a credential's scopes amount to."""
    held = set(scopes)
    return frozenset(grant for grant, scope in GRANT_SCOPES.items() if scope in held)


def unverified_claims(token: str) -> dict[str, Any] | None:
    """Read a JWT's claims WITHOUT verifying it (display and routing only).

    Returns ``None`` for an empty or malformed token. Never use the result to
    authorize anything: Forge verifies ``forge_session`` tokens itself.
    """
    if not token:
        return None
    try:
        claims = jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
    except jwt.InvalidTokenError:
        return None
    return claims if isinstance(claims, dict) else None


def is_forge_session_claims(claims: dict[str, Any] | None) -> bool:
    return bool(claims) and claims.get("token_use") == FORGE_SESSION_TOKEN_USE


def is_forge_session_token(token: str) -> bool:
    """True when ``token`` claims to be a ``forge_session`` credential (unverified)."""
    return is_forge_session_claims(unverified_claims(token))


def token_scopes(claims: dict[str, Any] | None) -> tuple[str, ...]:
    """The ``scopes`` claim as a tuple of strings; empty when absent or malformed."""
    if not claims:
        return ()
    raw = claims.get("scopes")
    if not isinstance(raw, list):
        return ()
    return tuple(str(scope) for scope in raw)


def effective_grants(
    token: str,
    explicit: Iterable[ForgeMcpGrant | str] | None,
) -> frozenset[ForgeMcpGrant]:
    """The grants a broker may offer: the token's, narrowed by ``explicit`` if set.

    Explicit grants can only narrow. Without a session token there is nothing to
    narrow, so no grant is offered (the broker's own credential is not scoped by
    Forge, and a local setting must not be able to widen what Forge granted).
    """
    claims = unverified_claims(token)
    if not is_forge_session_claims(claims):
        return frozenset()
    granted = grants_from_scopes(token_scopes(claims))
    if explicit is None:
        return granted
    return granted & frozenset(normalize_grants(explicit))
