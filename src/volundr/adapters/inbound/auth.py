"""FastAPI authentication dependencies."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status

from identity.adapters.http_auth import extract_principal as extract_principal
from volundr.domain.models import Principal, User
from volundr.domain.ports import (
    AuthorizationPort,
    IdentityPort,
    Resource,
    UserProvisioningError,
)

logger = logging.getLogger(__name__)


async def check_session_or_resident_access(
    request: Request,
    subject_id: UUID,
    *,
    session_service: Any | None,
    resident_runtime_service: Any | None,
    action: str,
    resource_name: str,
) -> None:
    """Authorize an existing Forge session or resident runtime subject."""
    if session_service is None and resident_runtime_service is None:
        raise HTTPException(status_code=503, detail="Resource authorization unavailable")

    from volundr.domain.services.resident_runtime import ResidentRuntimeNotFoundError
    from volundr.domain.services.session import SessionAccessDeniedError

    principal = await extract_principal(request)
    session = await session_service.get_session(subject_id) if session_service else None
    if session is not None and session_service is not None:
        try:
            await session_service._check_access(session, principal, action)
        except SessionAccessDeniedError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Not authorized to access {resource_name} for session {subject_id}",
            )
        return

    if resident_runtime_service is not None:
        try:
            await resident_runtime_service.get(principal, subject_id)
        except ResidentRuntimeNotFoundError:
            pass
        else:
            return

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Session or resident runtime not found: {subject_id}",
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
    from niuu.adapters.inbound.auth_context import extract_bearer_token

    extract_bearer_token(request)
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
