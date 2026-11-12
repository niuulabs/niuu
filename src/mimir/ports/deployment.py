"""Deployment control independent of Kubernetes and Flux."""

from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, Field


class DeploymentRequest(BaseModel):
    tenant_id: str = Field(default="", exclude=True)
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")
    backend: Literal["gbrain"]
    target: str = ""
    secret: str = Field(default="", pattern=r"^([a-z0-9][a-z0-9.-]*)?$")


class KnowledgeDeploymentPort(ABC):
    @abstractmethod
    async def list_deployments(self, *, tenant_id: str = "") -> dict[str, Any]: ...

    @abstractmethod
    async def deploy(self, request: DeploymentRequest) -> dict[str, Any]: ...

    def mounted_ports(self) -> list[dict]:
        return []

    async def discover_mounts(self, tenant_id: str, authorization: str = "") -> list[dict]:
        return [m for m in self.mounted_ports() if m.get("tenant_id", "") == tenant_id]

    async def inspect_deployment(self, name: str, *, tenant_id: str = "") -> dict:
        raise NotImplementedError("Deployment inspection is not supported by this target")

    async def control(self, name: str, action: str, *, tenant_id: str = "") -> dict:
        raise NotImplementedError("Lifecycle control is not supported by this target")

    async def close(self) -> None:
        pass
