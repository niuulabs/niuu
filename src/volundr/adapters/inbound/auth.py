"""FastAPI authentication dependencies."""

from __future__ import annotations

import inspect
import logging

from fastapi import Depends, HTTPException, Request, status

from volundr.domain.models import Principal, User
from volundr.domain.ports import (
    AuthorizationPort,
    IdentityPort,
    InvalidTokenError,
    Resource,
    UserProvisioningError,
)

logger = logging.getLogger(__name__)


def _split_roles(raw: str) -> list[str]:
    return [role.strip() for role in raw.split(",") if role.strip()]


async def extract_principal(request: Request) -> Principal:
    """FastAPI dependency: validate identity and extract Principal.

    Supports two modes:
    - Envoy header mode: reads trusted headers injected by the Envoy sidecar
    - Token mode (allow-all / dev): validates the Authorization header
    """
    identity: IdentityPort = request.app.state.identity
    from volundr.adapters.outbound.identity import (
        AllowAllIdentityAdapter,
        EnvoyHeaderIdentityAdapter,
    )

    # Shared local shells forward explicit developer identity through
    # x-auth-* headers. Honor those first so proxied browser flows preserve
    # tenant/user ownership even when the worker is running in allow-all mode.
    #
    # When the configured identity adapter understands trusted headers, route
    # the forwarded payload through that adapter so claim role mappings stay
    # consistent with the normal Envoy-backed path.
    forwarded_user_id = request.headers.get("x-auth-user-id", "").strip()
    if forwarded_user_id:
        if isinstance(identity, EnvoyHeaderIdentityAdapter):
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
        return Principal(
            user_id=forwarded_user_id,
            email=request.headers.get("x-auth-email", "").strip(),
            tenant_id=request.headers.get("x-auth-tenant", "").strip() or "default",
            roles=_split_roles(request.headers.get("x-auth-roles", "volundr:developer")),
        )

    dev_user_id = request.query_params.get("devUserId", "").strip()
    if dev_user_id:
        return Principal(
            user_id=dev_user_id,
            email=request.query_params.get("devEmail", "").strip(),
            tenant_id=request.query_params.get("devTenantId", "").strip(),
            roles=_split_roles(request.query_params.get("devRoles", "volundr:developer")),
        )

    # If the adapter supports header-based auth (Envoy mode), use it
    if isinstance(identity, EnvoyHeaderIdentityAdapter):
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

    # Allow-all mode: skip token validation entirely
    if isinstance(identity, AllowAllIdentityAdapter):
        return await identity.validate_token("allow-all")

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


async def get_current_user(
    request: Request,
    principal: Principal = Depends(extract_principal),
) -> User:
    """FastAPI dependency: get or provision the current user.

    Usage:
        @router.get("/me")
        async def get_me(user: User = Depends(get_current_user)):
            ...
    """
    identity: IdentityPort = request.app.state.identity

    try:
        return await identity.get_or_provision_user(principal)
    except UserProvisioningError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User provisioning in progress, retry later",
            headers={"Retry-After": "5"},
        )


def require_role(*roles: str):
    """FastAPI dependency factory: require one of the given roles.

    Usage:
        @router.post("/tenants", dependencies=[Depends(require_role("volundr:admin"))])
        async def create_tenant(...):
            ...
    """

    async def check_roles(principal: Principal = Depends(extract_principal)):
        if not any(r in principal.roles for r in roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of: {', '.join(roles)}",
            )
        return principal

    return check_roles


async def check_authorization(
    request: Request,
    principal: Principal,
    action: str,
    resource: Resource,
) -> None:
    """Check authorization for a principal to perform an action on a resource.

    Raises HTTPException 403 if not allowed.
    """
    authz: AuthorizationPort = request.app.state.authorization

    if not await authz.is_allowed(principal, action, resource):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Not authorized to {action} {resource.kind}/{resource.id}",
        )
