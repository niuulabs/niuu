"""REST endpoint for cluster resource discovery."""

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

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
