"""Configuration for the Yggdrasil service."""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AdapterConfig(BaseModel):
    """A dynamically configured realm summary adapter."""

    adapter: str = Field(description="Fully-qualified RealmAdapter class path.")
    kwargs: dict[str, object] = Field(default_factory=dict)


class YggdrasilSettings(BaseSettings):
    """Runtime settings for Yggdrasil."""

    model_config = SettingsConfigDict(env_prefix="YGGDRASIL_", extra="ignore")

    poll_interval: float = Field(
        default=15.0,
        gt=0,
        description="Seconds between configured realm health polls.",
    )
    stream_disconnect_check_interval: float = Field(
        default=1.0,
        gt=0,
        description="Seconds between client disconnect checks for idle SSE streams.",
    )
    max_sse_clients: int = Field(
        default=100,
        gt=0,
        description="Maximum concurrent realm summary SSE clients.",
    )
    adapters: list[AdapterConfig] = Field(
        default_factory=list,
        description="Configured RealmAdapter implementations.",
    )
