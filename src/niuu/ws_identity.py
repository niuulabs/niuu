"""Shared WebSocket identity parsing (signature-free).

The single home for the JWT-claim logic that both the Skuld broker's WS
ownership check and the niuu gateway's WS proxy guard depend on. Keeping it
in one place is not cosmetic: when the two copies drifted (tenant claim
shape, roles fallback) a legitimate owner got locked out. skuld may import
niuu (module-boundaries.md), so both callers share this.

Signatures are NOT verified here — that is Envoy's / the API gateway's job.
These functions only DECODE claims to resolve identity for authorization.
"""

from __future__ import annotations

import base64
import json


def decode_jwt_claims(token: str) -> dict:
    """Decode a JWT payload without verifying the signature."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return {}


def claims_to_identity(claims: dict) -> tuple[str | None, str, tuple[str, ...]]:
    """Resolve ``(user_id, tenant, roles)`` from decoded JWT claims.

    ``user_id`` is None when there is no ``sub``. PATs carry only ``sub``;
    OIDC/Keycloak tokens may carry tenant and roles in a few shapes (a
    ``roles`` list or comma string, or ``realm_access.roles``). Absent claims
    stay empty — an empty tenant just skips tenant scoping downstream.
    """
    user_id = str(claims.get("sub") or "").strip()
    if not user_id:
        return (None, "", ())

    tenant = str(claims.get("tenant") or claims.get("tenant_id") or "").strip()

    roles_claim = claims.get("roles")
    if isinstance(roles_claim, list):
        roles: tuple[str, ...] = tuple(str(role) for role in roles_claim)
    elif isinstance(roles_claim, str):
        roles = tuple(r.strip() for r in roles_claim.split(",") if r.strip())
    else:
        realm_access = claims.get("realm_access")
        if isinstance(realm_access, dict) and isinstance(realm_access.get("roles"), list):
            roles = tuple(str(role) for role in realm_access["roles"])
        else:
            roles = ()

    return (user_id, tenant, roles)
