"""Explicit configuration for generic VM allocation administration."""

from pydantic import BaseModel, ConfigDict, Field

from niuu.config import DynamicAdapterConfig, HttpAuthAdapterConfig
from niuu.config_models import DatabaseConfig
from volundr.domain.compute import MachineBootstrap


class ComputeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    pool_id: str = Field(min_length=1)
    max_machines: int = Field(gt=0)
    provider: DynamicAdapterConfig
    auth: HttpAuthAdapterConfig | None = None
    runtime: DynamicAdapterConfig | None = None
    database: DatabaseConfig | None = None
    bootstrap: MachineBootstrap = Field(default_factory=MachineBootstrap)
    provisioning_timeout_seconds: float = Field(default=600, gt=0)
    cleanup_timeout_seconds: float = Field(default=300, gt=0)
    warm_min: int = Field(default=0, ge=0)
    max_provisioning: int = Field(default=1, gt=0)
    idle_timeout_seconds: float = Field(default=3600, gt=0)
    retry_interval_seconds: float = Field(default=10, gt=0)
    retry_max_seconds: float = Field(default=300, gt=0)
    maintenance_interval_seconds: float = Field(default=10, gt=0)
    poll_interval_seconds: float = Field(default=2, gt=0)
