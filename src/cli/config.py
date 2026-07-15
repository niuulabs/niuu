"""CLI configuration — pydantic-settings with ~/.niuu/config.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from bifrost.config import BifrostConfig

DEFAULT_CONFIG_DIR = Path.home() / ".niuu"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.yaml"


def config_paths() -> list[Path]:
    """Resolve config file paths. NIUU_CONFIG env var takes precedence."""
    import os

    env = os.environ.get("NIUU_CONFIG")
    if env:
        return [Path(env)]
    return [
        DEFAULT_CONFIG_FILE,
        Path("/etc/niuu/config.yaml"),
    ]


class PerServiceConfig(BaseModel):
    """Per-service enabled/port overrides."""

    enabled: bool | None = Field(
        default=None,
        description="Override whether this service is enabled. None = use plugin default.",
    )
    port: int | None = Field(
        default=None,
        description="Override the listen port. None = use plugin default.",
    )


class PluginConfig(BaseModel):
    """Per-plugin enable/disable configuration."""

    enabled: dict[str, bool] = Field(
        default_factory=dict,
        description="Map of plugin name to enabled status.",
    )
    extra: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Extra plugins loaded via dynamic adapter pattern.",
    )


class DatabaseConfig(BaseModel):
    """Database configuration for mini mode."""

    mode: str = Field(
        default="embedded",
        description="Database mode: 'embedded' (bundled PostgreSQL) or 'external'.",
    )
    dsn: str = Field(
        default="",
        description="Database DSN for external mode.",
    )


class PodManagerConfig(BaseModel):
    """Pod manager configuration — dynamic adapter pattern.

    The ``adapter`` key specifies the fully-qualified class path.
    All remaining keys are forwarded as ``**kwargs`` to the adapter constructor.
    Mini mode defaults are kept for backwards compatibility; cluster mode
    overrides them via the YAML config file.
    """

    model_config = {"extra": "allow"}

    adapter: str = Field(
        default="volundr.adapters.outbound.local_process.LocalProcessPodManager",
        description="Fully-qualified class path for the pod manager adapter.",
    )
    # Mini-mode defaults (ignored by DirectK8sPodManager via **_extra)
    workspaces_dir: str = Field(
        default="~/.niuu/workspaces",
        description="Directory for session workspaces (mini mode).",
    )
    claude_binary: str = Field(
        default="claude",
        description="Path or name of the claude binary (mini mode).",
    )
    max_concurrent: int = Field(
        default=4,
        description="Maximum concurrent sessions.",
    )
    sdk_port_start: int = Field(
        default=9100,
        description="Starting port for Skuld/SDK WebSocket allocation.",
    )

    def adapter_kwargs(self) -> dict[str, Any]:
        """Return kwargs to pass to the adapter constructor.

        Excludes ``adapter`` (the class path) and returns everything else,
        including extra fields from the YAML config.
        """
        data = self.model_dump()
        data.pop("adapter", None)
        return data


class ServerConfig(BaseModel):
    """Server configuration — single port for all services."""

    host: str = Field(
        default="127.0.0.1",
        description="Host to bind the server to.",
    )
    external_host: str = Field(
        default="",
        description="Externally reachable host/IP for browser-facing URLs. Empty = reuse host.",
    )
    port: int = Field(
        default=8080,
        description="Single port for all services (Volundr, Ting, Web UI).",
    )


class ServiceConfig(BaseModel):
    """Service management configuration."""

    health_check_interval_seconds: float = Field(
        default=2.0,
        description="Interval between health check polls.",
    )
    health_check_timeout_seconds: float = Field(
        default=30.0,
        description="Max time to wait for a service to become healthy.",
    )
    health_check_max_retries: int = Field(
        default=15,
        description="Max retries for health checks before declaring failure.",
    )


class TUIConfig(BaseModel):
    """TUI appearance configuration."""

    theme: str = Field(
        default="textual-dark",
        description="Textual theme name.",
    )


class CLISettings(BaseSettings):
    """Root configuration for the niuu CLI."""

    model_config = SettingsConfigDict(
        env_prefix="NIUU_",
        env_nested_delimiter="__",
        yaml_file_encoding="utf-8",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        **kwargs: Any,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Resolve config paths at instantiation time (after --config callback)
        return (
            kwargs["init_settings"],
            kwargs["env_settings"],
            YamlConfigSettingsSource(
                settings_cls,
                yaml_file=[str(p) for p in config_paths()],
            ),
        )

    mode: str = Field(
        default="mini",
        description="Operating mode: 'mini', 'openshell', or 'cluster'.",
    )
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    pod_manager: PodManagerConfig = Field(default_factory=PodManagerConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    plugins: PluginConfig = Field(default_factory=PluginConfig)
    services: ServiceConfig = Field(default_factory=ServiceConfig)
    bifrost: BifrostConfig = Field(default_factory=BifrostConfig)
    service_overrides: dict[str, PerServiceConfig] = Field(
        default_factory=dict,
        description="Per-service enabled/port overrides keyed by service name.",
    )
    tui: TUIConfig = Field(default_factory=TUIConfig)
    context: str = Field(
        default="local",
        description="Active context (local, remote, etc.).",
    )
    version: str = Field(default="0.1.0")
