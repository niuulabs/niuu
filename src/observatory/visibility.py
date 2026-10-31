"""What one org may see of the estate an Observatory discovered.

Discovery runs once, with this service's own credentials, and every caller is
served from that one snapshot. So this is the only thing between one org and
another: nothing leaves an Observatory without passing through here.
"""

from __future__ import annotations

from typing import Any

from niuu.domain.models import Principal
from niuu.domain.tenancy import belongs_to_tenant


def _is_visible(node: dict[str, Any], principal: Principal, platform_tenant_id: str) -> bool:
    if not belongs_to_tenant(
        node.get("tenantId"), principal.tenant_id, platform_tenant_id=platform_tenant_id
    ):
        return False
    owner = node.get("ownerId")
    if node.get("visibility") in {"user", "private"} and owner:
        return owner == principal.user_id
    return True


def topology_for(
    snapshot: dict[str, Any],
    principal: Principal,
    *,
    platform_tenant_id: str,
) -> dict[str, Any]:
    """Keep the nodes *principal*'s org owns, and the edges between them.

    Cluster, namespace and host nodes name no org, so they belong to the org
    running the deployment. Another org's work runs on them, but they are not
    its to see: a kept node whose parent is not visible loses that parent, and
    is drawn at the top level instead.
    """
    nodes = [
        node
        for node in snapshot.get("nodes", [])
        if _is_visible(node, principal, platform_tenant_id)
    ]
    visible = {node["id"] for node in nodes}
    return {
        **snapshot,
        "nodes": [
            {**node, "parentId": None} if node.get("parentId") not in visible else node
            for node in nodes
        ],
        "edges": [
            edge
            for edge in snapshot.get("edges", [])
            if edge.get("sourceId") in visible and edge.get("targetId") in visible
        ],
        "pendingEdges": [
            edge for edge in snapshot.get("pendingEdges", []) if edge.get("sourceId") in visible
        ],
    }


def events_for(
    events: list[dict[str, Any]],
    principal: Principal,
    *,
    platform_tenant_id: str,
) -> list[dict[str, Any]]:
    """Return the signal log for the org running the deployment, and nothing to others.

    An event names a service or a caller, never an org, so it cannot be divided.
    """
    return events if principal.tenant_id == platform_tenant_id else []
