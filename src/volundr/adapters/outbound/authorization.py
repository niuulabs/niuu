"""Authorization adapters.

All adapters accept **kwargs (dynamic adapter pattern).
"""

from __future__ import annotations

from identity.adapters.authorization import (
    AllowAllAuthorizationAdapter as AllowAllAuthorizationAdapter,
)
from volundr.domain.models import Principal
from volundr.domain.ports import AuthorizationPort, Resource


class SimpleRoleAuthorizationAdapter(AuthorizationPort):
    """Simple role-based authorization adapter.

    Enforces ownership for non-admin users:
    - Admins can do anything
    - Developers can read/write their own resources
    - Viewers can only read resources in their tenant
    """

    def __init__(self, **_extra: object) -> None:
        pass

    async def is_allowed(
        self,
        principal: Principal,
        action: str,
        resource: Resource,
    ) -> bool:
        resource_tenant = resource.attr.get("tenant_id")
        owner_id = resource.attr.get("owner_id")

        # Tenant scoping first: cross-tenant access is always denied
        if resource_tenant and resource_tenant != principal.tenant_id:
            return False

        # Admins can do anything within their tenant
        if "volundr:admin" in principal.roles:
            return True

        # Viewers can only read
        if "volundr:viewer" in principal.roles and "volundr:developer" not in principal.roles:
            if action not in ("read", "list"):
                return False

        # Ownership check for write operations
        if action in ("update", "delete", "stop", "start"):
            if owner_id and owner_id != principal.user_id:
                return False

        return True

    async def filter_allowed(
        self,
        principal: Principal,
        action: str,
        resources: list[Resource],
    ) -> list[Resource]:
        result = []
        for resource in resources:
            if await self.is_allowed(principal, action, resource):
                result.append(resource)
        return result
