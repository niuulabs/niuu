"""Settings for the tracker bridge, read from the Ravn configuration file.

The bridge Skuld launches for harness sessions finds the platform API the same
way a Ravn resident does: the ``gateway.platform`` section of the file named by
``RAVN_CONFIG`` (or the default locations), overridable with ``RAVN_`` environment
variables. Every other section of that file is ignored, so it may carry a full
Ravn configuration.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from tracker.platform_client import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    DEFAULT_WORKLOAD_AUDIENCES,
    DEFAULT_WORKLOAD_TOKEN_FILE,
)

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


class PlatformClientConfig(BaseModel):
    """How to reach and authenticate to the platform API (``gateway.platform``)."""

    base_url: str = Field(default=DEFAULT_BASE_URL)
    timeout: float = Field(default=DEFAULT_TIMEOUT)
    access_token: str = Field(
        default="",
        description=(
            "Explicit bearer token for platform API authentication: a PAT for an "
            "off-cluster resident, or the workload-bound token a sandbox runtime "
            "mediates. Leave empty in-cluster to use projected workload identity."
        ),
    )
    workload_token_file: str = Field(
        default=DEFAULT_WORKLOAD_TOKEN_FILE,
        description="Projected workload identity token file used when access_token is not set.",
    )
    workload_exchange_url: str = Field(
        default="",
        description=(
            "Optional workload token exchange URL. Defaults to "
            "base_url /api/v1/tokens/workload/exchange."
        ),
    )
    workload_audiences: list[str] = Field(
        default_factory=lambda: list(DEFAULT_WORKLOAD_AUDIENCES),
        description="Target service audiences requested from workload token exchange.",
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_renamed_pat_token(cls, data: Any) -> Any:
        """``pat_token`` was renamed; dropping it silently would drop the credential."""
        if isinstance(data, dict) and "pat_token" in data:
            raise ValueError(
                "gateway.platform.pat_token was renamed to gateway.platform.access_token "
                "(env RAVN_GATEWAY__PLATFORM__ACCESS_TOKEN): it carries any platform "
                "bearer, not only a PAT. Rename the key in this config."
            )
        return data


class GatewayClientConfig(BaseModel):
    platform: PlatformClientConfig = Field(default_factory=PlatformClientConfig)


class PlatformClientSettings(BaseSettings):
    """The slice of a Ravn configuration file the tracker bridge reads."""

    model_config = SettingsConfigDict(
        yaml_file_encoding="utf-8",
        env_prefix="RAVN_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    gateway: GatewayClientConfig = Field(default_factory=GatewayClientConfig)

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
