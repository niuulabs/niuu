"""Port for discovering standalone residents.

Standalone residents are long-lived resident agents deployed outside the
Forge session lifecycle (e.g. via the agent Helm chart with the
``niuu.world/kind: resident`` label). Discovery adapters surface them so the
Ravn UI lists them alongside Forge-provisioned residents.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from niuu.domain.models import InstanceVisibility


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


class ResidentDiscoveryPort(Protocol):
    """Read-only discovery surface for standalone residents."""

    async def list_residents(self) -> list[StandaloneResident]:
        """Return known standalone residents."""
        raise NotImplementedError
