"""Which org owns something that does not name one."""

from __future__ import annotations

#: Id of the org that runs a deployment, unless its configuration says otherwise.
DEFAULT_PLATFORM_TENANT_ID = "default"


def belongs_to_tenant(
    owner_tenant_id: str | None,
    tenant_id: str,
    *,
    platform_tenant_id: str,
) -> bool:
    """Whether *tenant_id* owns a thing whose org is *owner_tenant_id*.

    Infrastructure, catalog residents and operator knowledge name no org. They
    belong to the org that runs the deployment, so they never read as public.
    """
    return bool(tenant_id) and (owner_tenant_id or platform_tenant_id) == tenant_id
