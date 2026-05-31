"""Skuld broker configuration.

Skuld runs in a separate pod from Volundr, so it has its own settings class.
Configuration is loaded from YAML, with environment variables overriding.

Config file locations (first found wins):
- ./config.yaml
- /etc/skuld/config.yaml

Environment variable override format:
- Use SKULD__ prefix with double underscore nesting:
  SKULD__TRANSPORT=subprocess, SKULD__SESSION__MODEL=opus
- Flat legacy env vars are also supported for backward compatibility:
  SESSION_ID, MODEL, HOST, PORT, VOLUNDR_API_URL, SERVICE_USER_ID, WORKSPACE_DIR
"""

from contextlib import suppress
import json
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


# Config file search paths (in order of priority).
# NIUU_CONFIG env var (set by the CLI --config flag) takes precedence.
def _config_paths() -> list[Path]:
    env = os.environ.get("NIUU_CONFIG")
    if env:
        return [Path(env)]
    return [
        Path("./config.yaml"),
        Path("/etc/skuld/config.yaml"),
    ]


CONFIG_PATHS = _config_paths()

_DEFAULT_TRANSPORT_ADAPTER = "skuld.transports.sdk.SDKTransport"


_DEFAULT_PARTICIPANT_COLORS = [
    "p1",
    "p2",
    "p3",
    "p4",
    "p5",
    "p6",
    "p7",
]


_DEFAULT_MESH_CAPABILITIES = [
    "coding",
    "git",
    "terminal",
    "file_edit",
]

_DEFAULT_MESH_TOOLS = [
    "claude-code",
    "codex",
]


class NngConfig(BaseModel):
    """NNG transport addresses for mesh communication."""

    pub_sub_address: str = Field(default="tcp://127.0.0.1:0")
    req_rep_address: str = Field(default="tcp://127.0.0.1:0")


class MeshConfig(BaseModel):
    """Mesh peer configuration for flock participation.

    When enabled, Skuld registers as a mesh peer and subscribes to task
    topics. Other ravens can delegate coding work via the standard mesh
    pub/sub protocol. Disabled by default so solo sessions are unaffected.
    """

    enabled: bool = Field(default=False)
    peer_id: str = Field(default="")
    capabilities: list[str] = Field(default_factory=lambda: list(_DEFAULT_MESH_CAPABILITIES))
    tools: list[str] = Field(default_factory=lambda: list(_DEFAULT_MESH_TOOLS))
    persona: str = Field(default="coder")
    transport: str = Field(default="nng")
    nng: NngConfig = Field(default_factory=NngConfig)
    adapters: list[dict[str, Any]] = Field(default_factory=list)
    rpc_timeout_s: float = Field(default=10.0)
    default_work_timeout_s: float = Field(default=120.0)
    default_response_urgency: float = Field(default=0.3)
    diff_max_bytes: int = Field(default=8192)
    diff_timeout_s: float = Field(default=10.0)
    consumes_event_types: list[str] = Field(
        default_factory=lambda: ["code.requested"],
    )


class WorkflowTriggerConfig(BaseModel):
    """Startup workflow trigger published by Skuld onto the mesh."""

    enabled: bool = Field(default=False)
    node_id: str = Field(default="")
    label: str = Field(default="")
    source: str = Field(default="manual dispatch")
    event_type: str = Field(default="")
    startup_delay_s: float = Field(default=3.0)


class WorkflowRuntimeConfig(BaseModel):
    """Workflow graph metadata injected into Skuld-backed flock sessions."""

    workflow_id: str = Field(default="")
    name: str = Field(default="")
    version: str = Field(default="")
    scope: str = Field(default="")
    initial_context: str = Field(default="")
    graph: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce_graph_json(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        graph = value.get("graph")
        if isinstance(graph, str) and graph.strip():
            with suppress(Exception):
                value = dict(value)
                value["graph"] = json.loads(graph)
        return value


class RoomConfig(BaseModel):
    """Multi-agent room chat configuration.

    When enabled, the broker operates in room mode and tracks per-message
    participant identity. Disabled by default so single-agent chat is unaffected.
    """

    enabled: bool = Field(default=False)
    max_participants: int = Field(default=8)
    participant_colors: list[str] = Field(default_factory=lambda: list(_DEFAULT_PARTICIPANT_COLORS))
    activity_detail_max_length: int = Field(default=200)


class TelegramConfig(BaseModel):
    """Telegram messaging channel configuration.

    When enabled, the Skuld broker will send CLI events to a Telegram
    chat in addition to the browser WebSocket. Requires the
    python-telegram-bot package to be installed.
    """

    enabled: bool = Field(default=False)
    bot_token: str = Field(default="")
    chat_id: str = Field(default="")
    notify_only: bool = Field(default=False)
    topic_mode: str = Field(default="topic_per_session")
    message_thread_id: int | None = Field(default=None)


class PeerWatchdogConfig(BaseModel):
    """Silence watchdog settings for workflow flock peers."""

    enabled: bool = Field(default=True)
    poll_seconds: float = Field(
        default=5.0,
        description="Seconds between silence watchdog checks.",
    )
    silence_seconds: float = Field(
        default=300.0,
        description="Seconds of no visible peer progress before warning in normal execution.",
    )
    tool_silence_seconds: float = Field(
        default=300.0,
        description="Seconds of no visible peer progress before warning while a tool is running.",
    )


class SkuldSessionConfig(BaseModel):
    """Per-session configuration (set by Volundr at pod creation)."""

    id: str = Field(default="unknown")
    name: str = Field(default="unknown")
    model: str = Field(default="claude-sonnet-4-6")
    workspace_dir: str | None = Field(default=None)
    system_prompt: str = Field(default="")
    initial_prompt: str = Field(default="")
    saga_id: str | None = Field(default=None)
    run_id: str | None = Field(default=None)


class ArchiveStoreConfig(BaseModel):
    """Dynamic archive store adapter configuration."""

    adapter: str = Field(
        default="volundr.adapters.outbound.archive_store.FileSystemArchiveStore",
    )
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)


class SkuldSettings(BaseSettings):
    """Skuld broker settings.

    Loads configuration from YAML file with environment variable overrides.

    YAML file locations (first found wins):
    - ./config.yaml
    - /etc/skuld/config.yaml

    Environment variable overrides use SKULD__ prefix with double underscore nesting:
    - SKULD__TRANSPORT=subprocess -> settings.transport
    - SKULD__SESSION__MODEL=opus -> settings.session.model

    Legacy flat env vars are also supported (lowest priority, backward compat):
    - SESSION_ID, SESSION_NAME, MODEL, HOST, PORT, VOLUNDR_API_URL,
      SERVICE_USER_ID, SERVICE_TENANT_ID, WORKSPACE_DIR
    """

    model_config = SettingsConfigDict(
        yaml_file=CONFIG_PATHS,
        yaml_file_encoding="utf-8",
        env_prefix="SKULD__",
        env_nested_delimiter="__",
        extra="ignore",
    )

    session: SkuldSessionConfig = Field(default_factory=SkuldSessionConfig)
    cli_type: str = Field(default="claude")  # "claude" | "codex"
    transport: str = Field(default="sdk")  # claude only: "sdk" | "subprocess"
    transport_adapter: str = Field(default=_DEFAULT_TRANSPORT_ADAPTER)
    skip_permissions: bool = Field(default=False)
    approval_policy: str = Field(default="")
    sandbox: str = Field(default="")
    agent_teams: bool = Field(default=False)
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8081)
    volundr_api_url: str = Field(default="")
    service_user_id: str = Field(default="skuld-broker")
    service_tenant_id: str = Field(default="default")
    persistence_mount_path: str = Field(default="/volundr/sessions")
    archive_store: ArchiveStoreConfig = Field(default_factory=ArchiveStoreConfig)
    chronicle_watcher_enabled: bool = Field(default=True)
    chronicle_watcher_debounce_ms: int = Field(default=500)
    max_upload_size_bytes: int = Field(default=104_857_600)  # 100 MB
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    peer_watchdog: PeerWatchdogConfig = Field(default_factory=PeerWatchdogConfig)
    room: RoomConfig = Field(default_factory=RoomConfig)
    mesh: MeshConfig = Field(default_factory=MeshConfig)
    workflow_trigger: WorkflowTriggerConfig = Field(default_factory=WorkflowTriggerConfig)
    workflow: WorkflowRuntimeConfig = Field(default_factory=WorkflowRuntimeConfig)

    @model_validator(mode="after")
    def _apply_legacy_env_vars(self) -> "SkuldSettings":
        """Apply flat legacy env vars as fallbacks.

        Only overrides fields that still hold their default values, so
        SKULD__* prefixed vars and YAML always take precedence.
        """
        if self.cli_type == "claude":
            val = os.environ.get("CLI_TYPE")
            if val:
                self.cli_type = val

        if self.session.id == "unknown":
            val = os.environ.get("SESSION_ID")
            if val:
                self.session.id = val

        if self.session.name == "unknown":
            val = os.environ.get("SESSION_NAME")
            if val:
                self.session.name = val

        if self.session.model == "claude-sonnet-4-6":
            val = os.environ.get("MODEL")
            if val:
                self.session.model = val

        if self.session.workspace_dir is None:
            val = os.environ.get("WORKSPACE_DIR")
            if val:
                self.session.workspace_dir = val

        if not self.session.system_prompt:
            val = os.environ.get("SESSION_SYSTEM_PROMPT")
            if val:
                self.session.system_prompt = val

        if not self.session.initial_prompt:
            val = os.environ.get("SESSION_INITIAL_PROMPT")
            if val:
                self.session.initial_prompt = val

        if self.host == "0.0.0.0":
            val = os.environ.get("HOST")
            if val:
                self.host = val

        if self.port == 8081:
            val = os.environ.get("PORT")
            if val:
                self.port = int(val)

        if self.volundr_api_url == "":
            val = os.environ.get("VOLUNDR_API_URL")
            if val:
                self.volundr_api_url = val

        if self.service_user_id == "skuld-broker":
            val = os.environ.get("SERVICE_USER_ID")
            if val:
                self.service_user_id = val

        if self.service_tenant_id == "default":
            val = os.environ.get("SERVICE_TENANT_ID")
            if val:
                self.service_tenant_id = val

        return self

    @model_validator(mode="after")
    def _resolve_transport_adapter(self) -> "SkuldSettings":
        """Map legacy cli_type/transport fields to transport_adapter.

        Only overrides transport_adapter when it still holds the default value,
        so an explicit transport_adapter always takes precedence.
        """
        if self.transport_adapter != _DEFAULT_TRANSPORT_ADAPTER:
            return self

        if self.cli_type == "codex":
            self.transport_adapter = "skuld.transports.codex.CodexSubprocessTransport"
            return self

        if self.cli_type == "codex-ws":
            self.transport_adapter = "skuld.transports.codex_ws.CodexWebSocketTransport"
            return self

        if self.cli_type == "opencode":
            self.transport_adapter = "skuld.transports.opencode.OpenCodeHttpTransport"
            return self

        if self.transport == "subprocess":
            self.transport_adapter = "skuld.transports.subprocess.SubprocessTransport"

        return self

    @property
    def workspace_path(self) -> str:
        """Resolved workspace directory path."""
        if self.session.workspace_dir:
            return self.session.workspace_dir
        return f"{self.persistence_mount_path}/{self.session.id}/workspace"

    @property
    def home_path(self) -> str:
        """Resolved home directory path for the session."""
        return f"{self.persistence_mount_path}/{self.session.id}/home"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Customize settings sources.

        Order (first wins):
        1. init_settings - explicit constructor arguments
        2. env_settings - SKULD__* environment variables
        3. yaml - YAML config file
        4. file_secret_settings - /run/secrets files
        """
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )
