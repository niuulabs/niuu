"""Deployment control independent of Kubernetes and Flux."""

from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DreamSchedule(BaseModel):
    enabled: bool = False
    schedule: str = Field(default="0 2 * * *", min_length=9, max_length=128)
    phases: list[
        Literal[
            "lint", "backlinks", "sync", "synthesize", "extract", "patterns", "embed", "orphans"
        ]
    ] = Field(default_factory=lambda: ["lint", "backlinks", "orphans"], min_length=1)


class WardenOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str | None = Field(default=None, min_length=1, max_length=200)
    persona: str | None = Field(default=None, min_length=1, max_length=200)
    dream_cycle_cron_expression: str | None = Field(default=None, pattern=r"^\S+(?:\s+\S+){4}$")
    source_trigger_poll_interval_seconds: int | None = Field(default=None, ge=10)
    staleness_trigger_schedule_hours: int | None = Field(default=None, ge=1)

    def apply(self, defaults: dict) -> dict:
        result = dict(defaults)
        schedules = dict(result.get("schedules", {}))
        for key, value in self.model_dump(exclude_none=True).items():
            if key in {"model", "persona"}:
                result[key] = value
            else:
                schedules[key] = value
        if schedules:
            result["schedules"] = schedules
        return result


class DeploymentRequest(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")
    backend: Literal["mimir", "gbrain"]
    dream: DreamSchedule = Field(default_factory=DreamSchedule)
    target: str = ""
    warden: bool = False
    warden_overrides: WardenOverrides = Field(default_factory=WardenOverrides)
    secret: str = Field(default="", pattern=r"^([a-z0-9][a-z0-9.-]*)?$")

    @model_validator(mode="after")
    def maintenance_matches_backend(self):
        if self.warden_overrides.model_dump(exclude_none=True) and not self.warden:
            raise ValueError("Warden overrides require an attached warden")
        if self.backend == "gbrain" and self.warden:
            raise ValueError(
                "gbrain uses native dream cycles; wardens are only supported for Mimir"
            )
        if self.backend == "mimir" and self.dream.enabled:
            raise ValueError(
                "Mimir uses a warden; native dream scheduling is only supported for gbrain"
            )
        return self


class KnowledgeDeploymentPort(ABC):
    @abstractmethod
    async def list_deployments(self) -> dict[str, Any]: ...

    @abstractmethod
    async def deploy(self, request: DeploymentRequest) -> dict[str, Any]: ...

    def mounted_ports(self) -> list[dict]:
        return []

    async def inspect_deployment(self, name: str) -> dict:
        raise NotImplementedError("Deployment inspection is not supported by this target")

    async def control(self, name: str, action: str) -> dict:
        raise NotImplementedError("Lifecycle control is not supported by this target")

    async def close(self) -> None:
        pass
