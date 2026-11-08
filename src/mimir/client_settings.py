"""Standalone settings for a process that talks to Mímir without a host runtime.

The Mímir bridge Skuld launches for harness sessions, ``python -m mimir mcp`` and
``python -m mimir ingest`` read the same YAML a Ravn resident is configured with
(``RAVN_CONFIG``), but only the sections a Mímir client needs: ``mimir``,
``environment.id``, ``gateway.platform``, ``logging`` and ``observability``.
Every other section is ignored, so the file may carry a full Ravn configuration.

Precedence matches the host runtime: ``RAVN_`` environment variables, then the
YAML file, then defaults.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from mimir.client_config import MimirConfig
from niuu.domain.observability import ObservabilityConfig

_DEFAULT_CONFIG_PATHS: tuple[Path, ...] = (
    Path.home() / ".ravn" / "config.yaml",
    Path("./ravn.yaml"),
    Path("/etc/ravn/config.yaml"),
)


def _config_paths() -> tuple[Path, ...]:
    env = os.environ.get("RAVN_CONFIG")
    if env:
        return (Path(env),)
    return _DEFAULT_CONFIG_PATHS


class ClientLoggingConfig(BaseModel):
    """Logging configuration."""

    level: str = Field(default="warning")
    format: str = Field(default="text")


class ClientEnvironmentConfig(BaseModel):
    """The environment identity a Mímir connection is made for."""

    id: str = Field(
        default="local",
        description="Stable environment id, for example a k8s cluster or host id.",
    )


class ClientPlatformConfig(BaseModel):
    """Platform workload-identity settings workload-authenticated Mímir mounts fall back to."""

    enabled: bool = Field(
        default=False,
        description="Whether the platform integration is configured for this runtime.",
    )
    base_url: str = Field(default="http://localhost:8080")
    workload_token_file: str = Field(
        default="/var/run/secrets/kubernetes.io/serviceaccount/token",
        description="Projected workload identity token file.",
    )
    workload_exchange_url: str = Field(
        default="",
        description=(
            "Optional workload token exchange URL. Defaults to "
            "base_url /api/v1/tokens/workload/exchange."
        ),
    )


class ClientGatewayConfig(BaseModel):
    platform: ClientPlatformConfig = Field(default_factory=ClientPlatformConfig)


class MimirClientSettings(BaseSettings):
    """The slice of a Ravn configuration file a Mímir client reads."""

    model_config = SettingsConfigDict(
        yaml_file_encoding="utf-8",
        env_prefix="RAVN_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    mimir: MimirConfig = Field(default_factory=MimirConfig)
    environment: ClientEnvironmentConfig = Field(default_factory=ClientEnvironmentConfig)
    gateway: ClientGatewayConfig = Field(default_factory=ClientGatewayConfig)
    logging: ClientLoggingConfig = Field(default_factory=ClientLoggingConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=_config_paths()),
            file_secret_settings,
        )
