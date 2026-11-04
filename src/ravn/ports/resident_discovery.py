"""Port for discovering standalone residents.

Standalone residents are long-lived resident agents deployed outside the
Forge session lifecycle (e.g. via the agent Helm chart with the
``niuu.world/kind: resident`` label). Discovery adapters surface them so the
Ravn UI lists them alongside Forge-provisioned residents.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from niuu.domain.models import InstanceVisibility

#: An RFC 1123 label, the shape of Kubernetes cluster, namespace and workload names.
_KUBERNETES_NAME = r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"


class ResidentPlacement(BaseModel):
    """Where a resident runs: the Kubernetes workload whose pods are the resident."""

    cluster: str = Field(pattern=_KUBERNETES_NAME)
    namespace: str = Field(pattern=_KUBERNETES_NAME)
    # The Deployment name; its pods are named ``<workload>-<replicaset>-<pod>``.
    workload: str = Field(pattern=_KUBERNETES_NAME)


class StandaloneResident(BaseModel):
    """A resident running outside Forge, described by its deployment."""

    id: str
    resident_name: str
    persona_name: str
    # One of the raven fleet states: idle | active | suspended | failed | completed.
    status: str
    model: str = ""
    chat_endpoint: str | None = None
    location: str = ""
    created_at: str | None = None
    updated_at: str | None = None
    visibility: InstanceVisibility = InstanceVisibility.SYSTEM
    owner_id: str = ""
    tenant_id: str = ""
    # How the resident is deployed, as the fleet UI reports it. Residents read
    # from a cluster deployment are "standalone"; ones that come from another
    # authoritative catalog (e.g. the Valkyrie roster) name that source.
    deployment: str = "standalone"
    # What the resident can do. Chat is the long-standing default for a
    # discovered deployment; a source that knows better (no chat endpoint)
    # states its own list.
    capabilities: list[str] = Field(default_factory=lambda: ["chat"])
    # Reconciled lifecycle conditions, in the shape managed runtimes report.
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    # Where it runs, when its source knows. A resident's logs are read by
    # placement, and the fleet shows its cluster rather than the instance
    # that happens to list it.
    placement: ResidentPlacement | None = None


class ResidentDiscoveryPort(Protocol):
    """Read-only discovery surface for standalone residents."""

    async def list_residents(self) -> list[StandaloneResident]:
        """Return known standalone residents."""
        raise NotImplementedError
