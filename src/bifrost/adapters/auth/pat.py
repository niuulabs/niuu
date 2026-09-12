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


class PATAuthAdapter(AuthPort):
    """Validate a Bearer JWT signed with a shared HS256 secret.

    Args:
        secret: The HS256 signing secret used to verify tokens.
                Must be at least 32 bytes for adequate security.
    """

    def __init__(self, secret: str) -> None:
        self._secret = secret

    def extract(self, request: Request) -> AgentIdentity:
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

        from niuu.domain.services.token_scope import token_requires_scope_check

        if token_requires_scope_check(payload):
            raise HTTPException(status_code=403, detail="Credential does not grant model access")

        session_id, saga_id = _read_attribution_headers(request)
        return AgentIdentity(
            agent_id=payload.get("sub", "anonymous"),
            tenant_id=payload.get("tenant_id", "default"),
            session_id=session_id,
            saga_id=saga_id,
        )
