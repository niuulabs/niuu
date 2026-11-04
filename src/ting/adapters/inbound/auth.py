"""FastAPI authentication dependency for Ting.

Reads identity from Envoy-injected trusted headers. In dev/test (no Envoy),
falls back to allow-all with a default identity only when
``auth.allow_anonymous_dev`` is explicitly enabled.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from niuu.adapters.identity_headers import parse_roles_header
from niuu.adapters.inbound.auth_context import (
    current_bearer_token as current_bearer_token,
)
from niuu.adapters.inbound.auth_context import (
    extract_bearer_token as extract_bearer_token,
)
from niuu.domain.models import Principal
from niuu.domain.tenancy import DEFAULT_PLATFORM_TENANT_ID

#: Roles that may change settings shared by every org on this Ting.
SHARED_SETTINGS_ROLES = frozenset({"admin", "ting:admin", "volundr:admin"})


def is_platform_admin(principal: Principal, *, platform_tenant_id: str) -> bool:
    """Whether *principal* administers the org that runs this deployment."""
    return principal.tenant_id == platform_tenant_id and not SHARED_SETTINGS_ROLES.isdisjoint(
        role.strip() for role in principal.roles
    )


async def extract_principal(request: Request) -> Principal:
    """Read identity from Envoy-injected trusted headers.

    When ``auth.allow_anonymous_dev`` is True in settings, missing headers fall
    back to a default developer identity. Otherwise returns 401.
    """
    extract_bearer_token(request)
    if getattr(request.app.state, "identity", None) is not None:
        from identity.adapters.http_auth import extract_principal as configured_identity

        settings = getattr(request.app.state, "settings", None)
        if settings is None or not settings.auth.allow_anonymous_dev:
            principal = await configured_identity(request)
            from identity.adapters.http_auth import authorization_http_errors
            from identity.models import Resource

            # Admission before route code can call external trackers or runtimes.
            authorization = request.app.state.authorization
            with authorization_http_errors():
                allowed = await authorization.is_allowed(
                    principal,
                    "enter",
                    Resource(
                        "gateway",
                        request.url.path,
                        {
                            "owner_id": "",
                            "tenant_id": principal.tenant_id,
                            "method": request.method,
                            "required_scope": "",
                            "scoped": False,
                            "scopes": [],
                        },
                    ),
                )
            if not allowed:
                raise HTTPException(status_code=403, detail="Operation denied")
            return principal
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
            tenant_id=settings.auth.default_tenant_id if settings else "",
            roles=["volundr:developer"],
        )
    from identity.adapters.http_auth import presented_actor

    return Principal(
        user_id=user_id,
        email=request.headers.get("x-auth-email", ""),
        tenant_id=request.headers.get("x-auth-tenant", ""),
        roles=parse_roles_header(request.headers.get("x-auth-roles", "")),
        actor=presented_actor(request),
    )


def platform_tenant_id(request: Request) -> str:
    """The org that runs this deployment, from the app's settings."""
    settings = getattr(request.app.state, "settings", None)
    return settings.auth.platform_tenant_id if settings else DEFAULT_PLATFORM_TENANT_ID


async def require_shared_settings_admin(
    request: Request,
    principal: Principal = Depends(extract_principal),
) -> Principal:
    """Refuse a caller who does not administer the org running this deployment."""
    if not is_platform_admin(principal, platform_tenant_id=platform_tenant_id(request)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Shared Ting settings are limited to the admins of the platform org",
        )
    return principal
