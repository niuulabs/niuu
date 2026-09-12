"""FastAPI authentication dependency for Ting.

Reads identity from Envoy-injected trusted headers. In dev/test (no Envoy),
falls back to allow-all with a default identity only when
``auth.allow_anonymous_dev`` is explicitly enabled.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from niuu.adapters.identity_headers import parse_roles_header
from niuu.adapters.inbound.auth_context import (
    current_bearer_token as current_bearer_token,
)
from niuu.adapters.inbound.auth_context import (
    extract_bearer_token as extract_bearer_token,
)
from niuu.domain.models import Principal


async def extract_principal(request: Request) -> Principal:
    """Read identity from Envoy-injected trusted headers.

    When ``auth.allow_anonymous_dev`` is True in settings, missing headers fall
    back to a default developer identity. Otherwise returns 401.
    """
    extract_bearer_token(request)
    user_id = request.headers.get("x-auth-user-id", "")
    if not user_id:
        settings = getattr(request.app.state, "settings", None)
        allow_anon = settings.auth.allow_anonymous_dev if settings else False
        if not allow_anon:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing authentication headers",
            )
        default_uid = settings.auth.default_user_id if settings else "dev-user"
        return Principal(
            user_id=default_uid,
            email="",
            tenant_id="",
            roles=["volundr:developer"],
        )
    return Principal(
        user_id=user_id,
        email=request.headers.get("x-auth-email", ""),
        tenant_id=request.headers.get("x-auth-tenant", ""),
        roles=parse_roles_header(request.headers.get("x-auth-roles", "")),
    )
