"""REST endpoints for cluster resource discovery."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from niuu.domain.guild_membership import GuildMembership
from volundr.adapters.inbound.rest_guild import INSTANCE_UNKNOWN_DETAIL, NOT_JOINED_DETAIL
from volundr.domain.ports import ResourceProvider

logger = logging.getLogger(__name__)


class ResourceTypeResponse(BaseModel):
    """A discoverable resource type."""

    name: str = Field(description="Resource type name (e.g. gpu)")
    resource_key: str = Field(
        description="K8s resource key (e.g. nvidia.com/gpu)",
    )
    display_name: str = Field(description="Human-readable display name")
    unit: str = Field(description="Unit of measurement (e.g. cores, GiB)")
    category: str = Field(
        description="Category: compute, accelerator, or custom",
    )


class NodeResourceSummaryResponse(BaseModel):
    """Resource availability for a node."""

    name: str = Field(description="Kubernetes node name")
    labels: dict[str, str] = Field(
        default_factory=dict,
        description="Node labels",
    )
    allocatable: dict[str, str] = Field(
        default_factory=dict,
        description="Total allocatable resources on the node",
    )
    allocated: dict[str, str] = Field(
        default_factory=dict,
        description="Currently allocated resources",
    )
    available: dict[str, str] = Field(
        default_factory=dict,
        description="Available (unallocated) resources",
    )


class ClusterResourceInfoResponse(BaseModel):
    """Cluster resource discovery response."""

    resource_types: list[ResourceTypeResponse] = Field(
        default_factory=list,
        description="Discovered resource types in the cluster",
    )
    nodes: list[NodeResourceSummaryResponse] = Field(
        default_factory=list,
        description="Per-node resource availability",
    )


def create_resources_router(
    resource_provider: ResourceProvider,
    *,
    prefix: str = "/api/v1/volundr",
) -> APIRouter:
    """Create the resources REST router."""

    router = APIRouter(prefix=prefix, tags=["Resources"])

    @router.get(
        "/resources",
        response_model=ClusterResourceInfoResponse,
    )
    async def get_cluster_resources(request: Request) -> ClusterResourceInfoResponse:
        """Discover available cluster resource types and capacity."""
        info = await resource_provider.discover()
        return ClusterResourceInfoResponse(
            resource_types=[
                ResourceTypeResponse(
                    name=rt.name,
                    resource_key=rt.resource_key,
                    display_name=rt.display_name,
                    unit=rt.unit,
                    category=rt.category.value,
                )
                for rt in info.resource_types
            ],
            nodes=[
                NodeResourceSummaryResponse(
                    name=n.name,
                    labels=n.labels,
                    allocatable=n.allocatable,
                    allocated=n.allocated,
                    available=n.available,
                )
                for n in info.nodes
            ],
        )

    return router


def create_forge_cluster_resources_router(
    resource_provider: ResourceProvider,
    membership: GuildMembership | None,
    *,
    prefix: str = "/api/v1/forge",
) -> APIRouter:
    """Serve this Forge node's own ``GET /cluster/resources``.

    The Guild facade answers the same path for every member, keyed by the
    registry's instance id. A UI scoped to one node reads it from the node
    itself, so the node answers in the facade's shape for its own instance
    only: an ``instance_id`` naming another Forge is a 404, not this node's
    data under someone else's name.
    """
    router = APIRouter(prefix=prefix, tags=["Resources"])

    @router.get(
        "/cluster/resources",
        responses={
            404: {"description": "Not joined to a Guild, or instance_id names another Forge"},
            503: {"description": "The node's instance in its Guild is not recorded yet"},
        },
    )
    async def get_own_cluster_resources(
        instance_id: str | None = Query(
            default=None,
            description="Guild instance id; must be this node's own when given",
        ),
    ) -> dict[str, Any]:
        if membership is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_JOINED_DETAIL)
        instance = membership.instance
        if instance is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                INSTANCE_UNKNOWN_DETAIL.format(guild_url=membership.guild_url),
            )
        if instance_id is not None and instance_id != instance.id:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"Instance {instance_id} is not this Forge node ({instance.name}, "
                f"{instance.id}); read other Forges through the Guild at "
                f"{membership.guild_url}",
            )
        info = await resource_provider.discover()
        resource_types = [
            {
                "name": rt.name,
                "resource_key": rt.resource_key,
                "resourceKey": rt.resource_key,
                "display_name": rt.display_name,
                "displayName": rt.display_name,
                "unit": rt.unit,
                "category": rt.category.value,
            }
            for rt in info.resource_types
        ]
        return {
            "instances": [
                {
                    "id": instance.id,
                    "name": instance.name,
                    "slug": instance.slug,
                }
            ],
            "resource_types": resource_types,
            "resourceTypes": resource_types,
            "nodes": [
                {
                    "name": f"{instance.slug}/{node.name}",
                    "labels": node.labels,
                    "allocatable": node.allocatable,
                    "allocated": node.allocated,
                    "available": node.available,
                    **instance.fields(),
                }
                for node in info.nodes
            ],
        }

    return router
