"""Restrict IDP-signed OAuth PAT scopes and workload scope arrays.

Envoy verifies the JWT signature; this module only narrows verified authority.
Scoped credentials are admitted solely at their named entry points and may
inspect their own current identity. Legacy unscoped credentials retain their
resource-policy permissions; hardened PAT issuance requires explicit scopes.

Two kinds of scoped workload credential exist. Each is marked by its
``token_use`` claim and carries a ``scopes`` claim bounded, at issuance, to its
own *family* of scopes (:data:`SCOPES_BY_TOKEN_USE`):

- ``token_use == "valkyrie_build"`` — minted by the workload-identity exchange
  (and for Guild pairing codes) for a workload that needs to do one specific
  thing (commission a build, launch or coordinate a workflow, publish its own
  topology, join a Guild). The value is historical: builds were the first use,
  but the marker means "this credential is scoped", not "this credential
  builds". Scoped OAuth PATs (``type == "pat"`` with a known ``scope``) share
  this family.
- ``token_use == "forge_session"`` — minted by Forge when it launches a
  session, bound to that one session, and held by its broker for the Forge MCP
  (notify, read sessions and, with an operator grant, message or run peers).

Enforcement is **stateless and fail-closed for scoped tokens only**:

- A token without a scoped ``token_use`` or PAT scope (humans, unscoped PATs,
  legacy workload tokens) passes through untouched — full backward
  compatibility.
- At the edge (:func:`credential_allows_route`), a scoped credential is
  admitted only on its named entry points. A ``forge_session`` credential is
  admitted only on the Forge session allow-list
  (``niuu.domain.services.forge_session_policy``).
- At a route (:func:`require_scope`), a scoped token is admitted only when it
  holds one of the required scopes *of its own family*; a check naming no
  scope of its family does not apply to a ``valkyrie_build`` token or scoped
  PAT but denies a ``forge_session`` token: session credentials are
  deny-by-default everywhere. Forge additionally verifies ``forge_session``
  tokens itself (``volundr.adapters.inbound.forge_session_auth``).

A scope is only real because code enforces it: :func:`require_scope` on a
route is what gives the string meaning. Adding an entry here without a
matching enforcement point produces a credential that reads as restricted but
protects nothing, which is why `test_token_scope.py` asserts the two stay in
step.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Iterable

import jwt
from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

#: The claim value that marks a token as a scoped workload credential.
VALKYRIE_BUILD_TOKEN_USE = "valkyrie_build"
#: The claim value of the session-bound credential Forge mints at launch.
FORGE_SESSION_TOKEN_USE = "forge_session"
OPENSHELL_SESSION_TOKEN_USE = "openshell_session"
OPENSHELL_RESIDENT_TOKEN_USE = "openshell_resident"

#: Scope required to publish a topology fragment to the push inbox.
TOPOLOGY_PUSH_SCOPE = "observatory:topology:push"

#: Scope carried by a minted Guild pairing code — see
#: ``niuu.domain.services.guild_join`` and ``.claude/rules/architecture.md``.
NODE_JOIN_SCOPE = "node_join"

#: Forge session credential scopes. ``notify`` and ``read`` are granted to every
#: session; ``message`` and ``lifecycle`` only through an operator grant.
FORGE_NOTIFY_SCOPE = "forge:notify"
FORGE_SESSION_READ_SCOPE = "forge:session:read"
FORGE_SESSION_MESSAGE_SCOPE = "forge:session:message"
FORGE_SESSION_LIFECYCLE_SCOPE = "forge:session:lifecycle"

#: Scopes a ``valkyrie_build`` credential (exchange or pairing code) or a scoped
#: OAuth PAT may carry.
VALKYRIE_BUILD_SCOPES: frozenset[str] = frozenset(
    {
        "forge:session:create",
        "forge:session:room-role",
        "ting:workflow:launch",
        "ting:workflow:coordinate",
        TOPOLOGY_PUSH_SCOPE,
        NODE_JOIN_SCOPE,
    }
)

#: Scopes a ``forge_session`` credential may carry.
FORGE_SESSION_SCOPES: frozenset[str] = frozenset(
    {
        FORGE_NOTIFY_SCOPE,
        FORGE_SESSION_READ_SCOPE,
        FORGE_SESSION_MESSAGE_SCOPE,
        FORGE_SESSION_LIFECYCLE_SCOPE,
    }
)

#: Each scoped ``token_use`` and the family of scopes it may carry.
SCOPES_BY_TOKEN_USE: dict[str, frozenset[str]] = {
    VALKYRIE_BUILD_TOKEN_USE: VALKYRIE_BUILD_SCOPES,
    FORGE_SESSION_TOKEN_USE: FORGE_SESSION_SCOPES,
}

#: Scoped token uses that are denied wherever no scope of their family applies.
_DENY_BY_DEFAULT_TOKEN_USES: frozenset[str] = frozenset({FORGE_SESSION_TOKEN_USE})

#: The scopes a short-lived workload credential may ever be granted. A caller
#: cannot self-grant anything outside this allowlist — unknown scopes are
#: dropped at issuance time, which is why this is deliberately a constant and
#: not configuration: whoever could edit the config could mint privilege.
#:
#: Every entry must have an enforcement point (see the module docstring).
KNOWN_WORKLOAD_SCOPES: frozenset[str] = VALKYRIE_BUILD_SCOPES | FORGE_SESSION_SCOPES

#: Scoped credentials may always inspect their own current identity.
_IDENTITY_ME = ("GET", "/api/v1/identity/me")
#: A WebSocket upgrade has no HTTP method; the Forge session allow-list names it GET.
_WEBSOCKET_METHOD = "WEBSOCKET"


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
    return claims.get("token_use") in SCOPES_BY_TOKEN_USE or (
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


def _scope_family(claims: dict) -> frozenset[str] | None:
    """The scope family a scoped credential draws from, or ``None`` when unscoped."""
    token_use = claims.get("token_use")
    if isinstance(token_use, str) and token_use in SCOPES_BY_TOKEN_USE:
        return SCOPES_BY_TOKEN_USE[token_use]
    if token_requires_scope_check(claims):
        return VALKYRIE_BUILD_SCOPES
    return None


def claims_have_scope(claims: dict, scopes: Iterable[str]) -> bool:
    """Return True when a token with ``claims`` may use one of ``scopes``.

    - An unscoped token (no scoped ``token_use`` or PAT scope) is always admitted.
    - A scoped token is admitted when its granted scopes hold one of the
      requested scopes that belong to its own family.
    - When none of the requested scopes belongs to its family, a
      ``valkyrie_build`` token or scoped PAT is admitted (it is only restricted
      at its own entry points) and a ``forge_session`` token is denied.
    - A malformed scope claim grants nothing.
    """
    family = _scope_family(claims)
    if family is None:
        return True

    applicable = [scope for scope in scopes if scope in family]
    if not applicable:
        return claims.get("token_use") not in _DENY_BY_DEFAULT_TOKEN_USES

    granted = credential_scopes(claims)
    return any(scope in granted for scope in applicable)


def validate_pat_scopes(scopes: list[str] | None) -> tuple[str, ...] | None:
    """Reject unknown or empty grants rather than mint an unrestricted token.

    PATs draw from the ``valkyrie_build`` family; Forge session scopes are only
    ever minted by Forge, bound to one session.
    """
    if scopes is None:
        return None
    if not scopes or any(s not in VALKYRIE_BUILD_SCOPES for s in scopes):
        raise ValueError("PAT scopes must be a non-empty list of supported scopes")
    return tuple(sorted(set(scopes)))


def token_has_scope(token: str, scope: str) -> bool:
    """Return True when ``token`` is permitted to use ``scope``.

    Backward-compatible and fail-closed for scoped tokens only; see
    :func:`claims_have_scope`. A missing or malformed token passes through
    (humans / PATs / legacy workload tokens are unaffected).
    """
    claims = _decode_claims(token)
    if claims is None:
        return True
    return claims_have_scope(claims, (scope,))


def scoped_credential_claims(token: str) -> dict | None:
    """Return claims only for credentials governed by workload scope checks.

    Callers use this after authentication when a scope grants entry to a
    multiplexed endpoint and the endpoint must further bind an operation to
    signed workload lineage claims.
    """
    claims = _decode_claims(token)
    if claims is None or not token_requires_scope_check(claims):
        return None
    return claims


def workload_owner_scoped(token: str) -> bool:
    """Whether this caller's workload-identity mapping derived a per-caller
    ``owner_id`` (``owner_id_claim``) rather than a fixed one every caller
    matching that mapping's subject/subject_prefix shares.

    Set by ``niuu.domain.services.workload_identity.WorkloadIdentityService``
    at exchange time as the ``workload_owner_scoped`` claim. Read directly off
    the trusted claims (Envoy already verified the signature upstream — same
    posture as the rest of this module) rather than threading a new field
    through ``Principal``, so the one caller that needs this narrow signal
    (resident budget reporting, which must refuse to run under a possibly
    shared identity) does not force it onto every consumer of ``Principal``.
    """
    claims = _decode_claims(token)
    if claims is None:
        return False
    return bool(claims.get("workload_owner_scoped", False))


def bound_workload_scopes(
    requested: list[str] | None,
    *,
    allowed: frozenset[str] = KNOWN_WORKLOAD_SCOPES,
) -> list[str]:
    """Intersect requested scopes with ``allowed`` (default: every known scope).

    Unknown scopes are dropped (and logged) so a caller can never
    self-grant a scope the platform does not recognise. Order and
    duplicates from the request are collapsed to a stable, de-duplicated
    list of known scopes. Issuers pass their own family
    (:data:`VALKYRIE_BUILD_SCOPES`, :data:`FORGE_SESSION_SCOPES`) so one kind of
    credential can never carry another kind's scopes.
    """
    if not requested:
        return []

    seen: set[str] = set()
    kept: list[str] = []
    dropped: list[str] = []
    for raw in requested:
        scope = str(raw).strip()
        if not scope or scope in seen:
            continue
        seen.add(scope)
        if scope in allowed:
            kept.append(scope)
            continue
        dropped.append(scope)

    if dropped:
        logger.warning(
            "Dropping unknown scopes from token request: %s",
            ", ".join(sorted(dropped)),
        )
    return kept


def _forge_session_allows_route(claims: dict, method: str, path: str) -> bool:
    """Route and scope half of the Forge session allow-list (Forge checks bindings)."""
    # Imported lazily: the policy module builds on this module's scope constants.
    from niuu.domain.services.forge_session_policy import policy_refusal

    verb = "GET" if method == _WEBSOCKET_METHOD else method
    return policy_refusal(verb, path, tuple(credential_scopes(claims))) is None


def credential_allows_route(token: str, method: str, path: str) -> bool:
    """Deny scoped credentials everywhere except their explicit entry points.

    A ``forge_session`` credential is admitted only on the Forge session
    allow-list; every other scoped credential on the entry points below.
    """
    claims = _decode_claims(token)
    if claims is None or not token_requires_scope_check(claims):
        return True
    if claims.get("token_use") == FORGE_SESSION_TOKEN_USE:
        return _forge_session_allows_route(claims, method, path)
    if (method, path) == _IDENTITY_ME:
        return True
    routes = [
        ("POST", r"/api/v1/forge/sessions", "forge:session:create"),
        (
            "GET",
            r"/api/v1/forge/sessions/[^/?%]+/participants/role",
            "forge:session:room-role",
        ),
        ("POST", r"/api/v1/ting/a2a", "ting:workflow:launch"),
        ("POST", r"/api/v1/ting/workflows/[^/?%]+/launch", "ting:workflow:launch"),
        (
            "POST",
            r"/api/v1/ting/workflow-executions/[^/?%]+/(expansions|messages|reconcile|cancel|waits)",
            "ting:workflow:coordinate",
        ),
        (
            "POST",
            r"/api/v1/ting/workflow-executions/[^/?%]+/children/[^/?%]+/retry",
            "ting:workflow:coordinate",
        ),
        (
            "POST",
            r"/api/v1/ting/delivery-executions/[^/?%]+/"
            r"(expansions|integration-candidate|complete|delivery-authorizations)",
            "ting:workflow:coordinate",
        ),
        (
            "POST",
            r"/api/v1/forge/delivery/[^?#]+",
            "ting:workflow:coordinate",
        ),
        ("PUT", r"/api/v1/niuu/observatory/fragments/[^/?%]+", "observatory:topology:push"),
        ("DELETE", r"/api/v1/niuu/observatory/fragments/[^/?%]+", "observatory:topology:push"),
        ("POST", r"/api/v1/niuu/guild/join", NODE_JOIN_SCOPE),
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


def require_scope(scope: str, *alternatives: str) -> Callable[..., Awaitable[None]]:
    """FastAPI dependency factory enforcing a scope (any of several), fail-closed.

    The returned dependency reads the bearer token from the request's
    Authorization header, then raises HTTP 403 when the token is a scoped
    credential lacking every applicable scope (see :func:`claims_have_scope`).
    Unscoped tokens are admitted unchanged. Name one scope per credential
    family when a route serves both, e.g.
    ``require_scope("forge:session:create", FORGE_SESSION_LIFECYCLE_SCOPE)``.

    Usage::

        @router.post("/sessions", ...)
        async def create_session(
            request: Request,
            data: SessionCreate,
            _: None = Depends(require_scope("forge:session:create")),
        ) -> SessionResponse:
            ...
    """
    scopes = (scope, *alternatives)
    for name in scopes:
        if name not in KNOWN_WORKLOAD_SCOPES:
            raise ValueError(f"Unknown workload scope: {name}")
    wanted = " or ".join(scopes)

    async def _check(request: Request) -> None:
        claims = _decode_claims(_bearer_from_request(request))
        if claims is None or claims_have_scope(claims, scopes):
            return None
        logger.warning("Scoped token denied: missing scope %s", wanted)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Token is missing the required scope: {wanted}",
        )

    return _check


__all__ = [
    "FORGE_NOTIFY_SCOPE",
    "FORGE_SESSION_LIFECYCLE_SCOPE",
    "FORGE_SESSION_MESSAGE_SCOPE",
    "FORGE_SESSION_READ_SCOPE",
    "FORGE_SESSION_SCOPES",
    "FORGE_SESSION_TOKEN_USE",
    "KNOWN_WORKLOAD_SCOPES",
    "NODE_JOIN_SCOPE",
    "OPENSHELL_SESSION_TOKEN_USE",
    "OPENSHELL_RESIDENT_TOKEN_USE",
    "SCOPES_BY_TOKEN_USE",
    "TOPOLOGY_PUSH_SCOPE",
    "VALKYRIE_BUILD_SCOPES",
    "VALKYRIE_BUILD_TOKEN_USE",
    "bound_workload_scopes",
    "claims_have_scope",
    "credential_allows_route",
    "credential_scopes",
    "require_scope",
    "scoped_credential_claims",
    "token_has_scope",
    "token_requires_scope_check",
    "validate_pat_scopes",
    "workload_owner_scoped",
]
