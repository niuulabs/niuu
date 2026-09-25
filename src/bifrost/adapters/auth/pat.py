"""PAT (Personal Access Token) authentication adapter.

Validates a long-lived HS256-signed Bearer JWT — the same token format
used by Volundr's PAT system (NIU-222+).  The ``sub`` claim becomes the
agent_id; the ``tenant_id`` claim is used as the tenant identifier.

This adapter is appropriate for deployments where Bifröst is exposed
beyond a trusted network boundary and callers are individual agents or
users who have been issued a PAT by the platform.
"""

from __future__ import annotations

import jwt
from fastapi import HTTPException, Request

from bifrost.auth import AgentIdentity, _read_attribution_headers
from bifrost.ports.auth import AuthPort
from niuu.domain.services.pat_validator import PATValidator


class PATAuthAdapter(AuthPort):
    """Validate a Bearer JWT signed with a shared HS256 secret.

    Signature verification alone does not catch a revoked PAT — HS256
    verification only proves the token was issued by the platform, not that
    the operator hasn't since deleted it. ``revocation_validator`` applies
    the same revocation check every other PAT-accepting service in the
    platform runs (``niuu.domain.services.pat_validator.PATValidator``,
    typically ``niuu.adapters.remote_pats.RemotePATValidator`` here since
    Bifröst is a standalone process with no database pool of its own — see
    ``bifrost.config.PATRevocationConfig``). ``None`` reproduces the
    previous (revocation-blind) behaviour and is only reachable for a real
    ``auth_mode: pat`` deployment through an explicit
    ``pat_revocation.enabled: false`` — ``BifrostConfig.
    _pat_mode_requires_revocation_decision`` refuses to start with ``pat``
    mode and no revocation decision at all, and
    ``bifrost.app._build_pat_revocation_validator`` is what actually
    constructs the value passed here.

    Args:
        secret: The HS256 signing secret used to verify tokens.
                Must be at least 32 bytes for adequate security.
        revocation_validator: Checks the token hasn't been revoked. See above.
    """

    def __init__(self, secret: str, revocation_validator: PATValidator | None = None) -> None:
        self._secret = secret
        self._revocation_validator = revocation_validator

    async def extract(self, request: Request) -> AgentIdentity:
        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Missing Bearer token")

        token = auth_header[7:]
        try:
            payload = jwt.decode(token, self._secret, algorithms=["HS256"])
        except jwt.ExpiredSignatureError as exc:
            raise HTTPException(status_code=401, detail="Token has expired") from exc
        except jwt.InvalidTokenError as exc:
            raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc

        if self._revocation_validator is not None and not await self._revocation_validator.is_valid(
            token
        ):
            raise HTTPException(status_code=401, detail="Token has been revoked")

        from niuu.domain.services.token_scope import token_requires_scope_check

        if token_requires_scope_check(payload):
            raise HTTPException(status_code=403, detail="Credential does not grant model access")

        raw_roles = payload.get("roles")
        roles = tuple(raw_roles) if isinstance(raw_roles, list) else ()

        session_id, saga_id = _read_attribution_headers(request)
        return AgentIdentity(
            agent_id=payload.get("sub", "anonymous"),
            tenant_id=payload.get("tenant_id", "default"),
            session_id=session_id,
            saga_id=saga_id,
            roles=roles,
        )
