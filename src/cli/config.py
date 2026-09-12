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


class DockerVllmConfig(BaseModel):
    """Optional local model served by vLLM inside the compose bundle."""

    enabled: bool = Field(
        default=False,
        description="Start a vLLM container serving `model` on the host GPU.",
    )
    model: str = Field(
        default="",
        description="Hugging Face model id to serve (e.g. nvidia/Nemotron-3-Nano-30B-A3B).",
    )
    image: str = Field(
        default="nvcr.io/nvidia/vllm:25.09-py3",
        description="vLLM container image (must match the host architecture).",
    )
    port: int = Field(default=8000, description="Port vLLM listens on inside the compose network.")
    max_model_len: int = Field(default=65536, description="Context length passed to vLLM.")
    gpu_memory_utilization: float = Field(
        default=0.6,
        description="Fraction of GPU memory vLLM may reserve; leave room for sandboxes.",
    )
    hf_token: str = Field(
        default="",
        description="Hugging Face token for gated repositories (empty = anonymous).",
    )


class DockerConfig(BaseModel):
    """Docker mode: the whole platform as containers on one Docker host."""

    data_dir: str = Field(
        default="/var/lib/niuu",
        description="Host directory for postgres data, workspaces, credentials and models.",
    )
    compose_dir: str = Field(
        default="~/.niuu/docker",
        description="Where the rendered compose bundle and env file are written.",
    )
    project_name: str = Field(default="niuu", description="Docker Compose project name.")
    image: str = Field(
        default="ghcr.io/niuulabs/niuu:dev",
        description="All-in-one platform image (CI publishes multi-arch `dev` and version tags).",
    )
    postgres_image: str = Field(
        default="pgvector/pgvector:pg17",
        description="PostgreSQL image with pgvector.",
    )
    skuld_image: str = Field(
        default="ghcr.io/niuulabs/skuld:dev",
        description="Session broker image started once per Forge session.",
    )
    bind_host: str = Field(
        default="0.0.0.0",
        description="Host interface the platform port is published on "
        "(0.0.0.0 = whole LAN, 127.0.0.1 = this machine only).",
    )
    postgres_password: str = Field(
        default="",
        description="Password for the postgres superuser; generated on first `niuu up` when empty.",
    )
    require_gpu: bool = Field(
        default=False,
        description="Fail preflight when no NVIDIA GPU or container runtime is present "
        "(off by default: a GPU is detected and used when present, never required).",
    )
    min_disk_space_gib: int = Field(
        default=50,
        description="Warn when the data directory has less free space than this.",
    )
    startup_timeout_seconds: float = Field(
        default=180.0,
        description="How long `niuu up` waits for the platform health endpoint.",
    )
    vllm: DockerVllmConfig = Field(default_factory=DockerVllmConfig)


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
        description="Operating mode: 'mini', 'openshell', 'cluster', or 'docker'.",
    )
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    pod_manager: PodManagerConfig = Field(default_factory=PodManagerConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    docker: DockerConfig = Field(default_factory=DockerConfig)
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
