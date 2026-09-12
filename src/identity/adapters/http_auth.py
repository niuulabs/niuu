"""Shared HTTP identity extraction; only explicit dev adapters allow impersonation."""

from __future__ import annotations

import inspect
from contextlib import contextmanager

from fastapi import HTTPException, Request, status

from identity.models import Principal
from identity.ports import AuthorizationDeniedError, AuthorizationEvaluationError
from niuu.ports.identity import HeaderAuthenticationPort, IdentityPort, InvalidTokenError


def _split_roles(raw: str) -> list[str]:
    return [role.strip() for role in raw.split(",") if role.strip()]


async def extract_principal(request: Request) -> Principal:
    """FastAPI dependency: validate identity and extract Principal.

    Supports two modes:
    - Envoy header mode: reads trusted headers injected by the Envoy sidecar
    - Token mode (allow-all / dev): validates the Authorization header
    """
    from niuu.adapters.inbound.auth_context import extract_bearer_token

    extract_bearer_token(request)
    identity: IdentityPort | HeaderAuthenticationPort | None = getattr(
        request.app.state, "identity", None
    )
    if identity is None:
        raise HTTPException(status_code=401, detail="Authentication is not configured")
    from identity.adapters.identity import AllowAllIdentityAdapter

    # Impersonation is available only in explicitly configured local-dev mode.
    # Never let query parameters or forwarded headers bypass a token adapter.
    if isinstance(identity, AllowAllIdentityAdapter):
        forwarded_user_id = request.headers.get("x-auth-user-id", "").strip()
        dev_user_id = request.query_params.get("devUserId", "").strip()
        if forwarded_user_id:
            return Principal(
                user_id=forwarded_user_id,
                email=request.headers.get("x-auth-email", "").strip(),
                tenant_id=request.headers.get("x-auth-tenant", "").strip() or "default",
                roles=_split_roles(request.headers.get("x-auth-roles", "volundr:developer")),
            )
        if dev_user_id:
            return Principal(
                user_id=dev_user_id,
                email=request.query_params.get("devEmail", "").strip(),
                tenant_id=request.query_params.get("devTenantId", "").strip(),
                roles=_split_roles(request.query_params.get("devRoles", "volundr:developer")),
            )
        return await identity.validate_token("allow-all")

    # If the adapter supports header-based auth (Envoy mode), use it
    if isinstance(identity, HeaderAuthenticationPort):
        header_items = request.headers.items()
        if inspect.iscoroutine(header_items):
            header_items.close()
            header_items = ()
        elif inspect.isawaitable(header_items):
            header_items = ()
        headers = {k.lower(): v for k, v in header_items}
        try:
            return await identity.validate_headers(headers)
        except InvalidTokenError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(e),
                headers={"WWW-Authenticate": "Bearer"},
            )

    # Token-based mode
    auth_header = request.headers.get("Authorization", "")
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        return await identity.validate_token(auth_header)
    except InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )


@contextmanager
def authorization_http_errors():
    """Translate policy denial and evaluation failure at HTTP boundaries."""
    try:
        yield
    except AuthorizationDeniedError as exc:
        raise HTTPException(status_code=403, detail="Not authorized") from exc
    except AuthorizationEvaluationError as exc:
        raise HTTPException(status_code=503, detail="Authorization unavailable") from exc
