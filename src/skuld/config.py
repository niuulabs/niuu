"""Skuld broker configuration.

Skuld runs in a separate pod from Volundr, so it has its own settings class.
Configuration is loaded from YAML, with environment variables overriding.

Config file locations (first found wins):
- ./config.yaml
- /etc/skuld/config.yaml

Environment variable override format:
- Use SKULD__ prefix with double underscore nesting:
  SKULD__TRANSPORT=subprocess, SKULD__SESSION__MODEL=opus
"""

import json
import os
from contextlib import suppress
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
    presence_sweep_interval_s: float = Field(
        default=30.0,
        description=(
            "How often expired participants (heartbeat TTL exceeded, no live "
            "WebSocket) are evicted from room state. 0 disables the sweep."
        ),
    )


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
    model: str = Field(default="claude-opus-4-8")
    workspace_dir: str | None = Field(default=None)
    system_prompt: str = Field(default="")
    initial_prompt: str = Field(default="")
    saga_id: str | None = Field(default=None)
    run_id: str | None = Field(default=None)
    resume_session_id: str = Field(
        default="",
        description=(
            "Native CLI session/thread id to resume on first start. Set by "
            "Volundr for sessions imported from an external harness."
        ),
    )


class ActivityHeartbeatConfig(BaseModel):
    """Periodic re-report of the current activity state to Volundr.

    Skuld reports activity purely on CLI frame transitions, so a long turn (a
    slow tool, an extended thinking pass) or a session blocked on the user goes
    silent: the UI cannot tell "still progressing" from "frozen", and Volundr's
    liveness reaper (``session_liveness.stale_after_seconds``, default 600) can
    falsely mark a genuinely-busy or input-blocked session as stopped. The
    heartbeat re-reports the current state on an interval while the agent is busy
    (active/tool_executing) or awaiting input, advancing ``last_active``.
    """

    enabled: bool = Field(default=True)
    interval_seconds: float = Field(
        default=30.0,
        gt=0,
        description=(
            "How often to re-report a busy/awaiting state. Keep comfortably "
            "below Volundr's session liveness stale_after_seconds (default 600)."
        ),
    )


class ArchiveStoreConfig(BaseModel):
    """Dynamic archive store adapter configuration."""

    adapter: str = Field(
        default="volundr.adapters.outbound.archive_store.FileSystemArchiveStore",
    )
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)


class ReflexConfig(BaseModel):
    """Retrieval reflex — deterministic Mímir entity pointer injection (NIU-1059).

    When enabled, user messages forwarded to the CLI agent are scanned for
    known Mímir entities and prefixed with compact pointers (never page
    bodies). Enabled by default; the Mímir base URL is derived from
    ``volundr_api_url`` when not set explicitly.
    """

    enabled: bool = Field(
        default=True,
        description="Enable retrieval reflex pointer injection on forwarded user messages.",
    )
    base_url: str = Field(
        default="",
        description="Base URL of the Mímir HTTP service exposing GET /mimir/entities/index.",
    )
    max_pointers: int = Field(
        default=5,
        description="Maximum number of entity pointers injected per message.",
    )
    cache_ttl_seconds: int = Field(
        default=300,
        description="How long (seconds) the entity index is cached before refetching.",
    )
    timeout_seconds: float = Field(
        default=5.0,
        description="HTTP timeout (seconds) for the entity feed request.",
    )


class SkuldSettings(BaseSettings):
    """Skuld broker settings.

    Loads configuration from YAML file with environment variable overrides.

    YAML file locations (first found wins):
    - ./config.yaml
    - /etc/skuld/config.yaml

    Environment variable overrides use SKULD__ prefix with double underscore nesting:
    - SKULD__TRANSPORT=subprocess -> settings.transport
    - SKULD__SESSION__MODEL=opus -> settings.session.model

    """

    model_config = SettingsConfigDict(
        yaml_file=CONFIG_PATHS,
        yaml_file_encoding="utf-8",
        env_prefix="SKULD__",
        env_nested_delimiter="__",
        extra="ignore",
    )

    session: SkuldSessionConfig = Field(default_factory=SkuldSessionConfig)
    cli_type: str = Field(default="claude")  # "claude" | "codex" | "grok"
    transport: str = Field(default="sdk")  # claude only: "sdk" | "subprocess"
    transport_adapter: str = Field(default=_DEFAULT_TRANSPORT_ADAPTER)
    skip_permissions: bool = Field(default=False)
    approval_policy: str = Field(default="")
    sandbox: str = Field(default="")
    # Default ON: Claude tmux sessions launch with agent teams (--teammate-mode
    # tmux) so a session can spin up a team of agents. Only the tmux transport
    # consumes this; other transports ignore it. Override with SKULD__AGENT_TEAMS=0.
    agent_teams: bool = Field(default=True)
    ask_user_question_enabled: bool = Field(
        default=False,
        description=(
            "Route Claude tool permissions over the control protocol so "
            "AskUserQuestion reaches a human (requires a client that answers "
            "ask_user_question events). Off by default: sessions keep the "
            "classic bypassPermissions behavior."
        ),
    )
    activity_heartbeat: ActivityHeartbeatConfig = Field(default_factory=ActivityHeartbeatConfig)
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8081)
    volundr_api_url: str = Field(default="")
    service_user_id: str = Field(default="skuld-broker")
    service_tenant_id: str = Field(default="default")
    persistence_mount_path: str = Field(default="/volundr/sessions")
    archive_store: ArchiveStoreConfig = Field(default_factory=ArchiveStoreConfig)
    # OFF by default in our pipeline: the watcher tails session JSONL and POSTs
    # chronicle timeline events we don't use (and which 405 through the guild
    # aggregate). Opt in with SKULD__CHRONICLE_WATCHER_ENABLED=true.
    chronicle_watcher_enabled: bool = Field(default=False)
    chronicle_watcher_debounce_ms: int = Field(default=500)
    # Generate + report a Chronicle SUMMARY (an LLM pass) when a session stops.
    # OFF by default in our pipeline: stopping a session must NOT trigger an
    # extra summarization (cost / latency / unwanted behavior). Opt in with
    # SKULD__CHRONICLE_ON_STOP_ENABLED=true.
    chronicle_on_stop_enabled: bool = Field(default=False)
    # Durable full-fidelity event log: every CLI frame is appended to the
    # Volundr session_event_log so any client can replay the full transcript
    # (including the in-flight turn) regardless of whether a socket is attached.
    event_log_enabled: bool = Field(default=True)
    event_log_batch_size: int = Field(default=100)
    event_log_flush_interval_ms: int = Field(default=500)
    event_log_max_buffer: int = Field(default=50_000)
    max_upload_size_bytes: int = Field(default=104_857_600)  # 100 MB
    acp_prompt_timeout_s: float = Field(default=300.0)  # ACP (Grok Build) prompt turn timeout
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)
    reflex: ReflexConfig = Field(default_factory=ReflexConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    peer_watchdog: PeerWatchdogConfig = Field(default_factory=PeerWatchdogConfig)
    room: RoomConfig = Field(default_factory=RoomConfig)
    mesh: MeshConfig = Field(default_factory=MeshConfig)
    workflow_trigger: WorkflowTriggerConfig = Field(default_factory=WorkflowTriggerConfig)
    workflow: WorkflowRuntimeConfig = Field(default_factory=WorkflowRuntimeConfig)

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

        if self.cli_type == "grok":
            self.transport_adapter = "skuld.transports.grok.GrokACPTransport"
            return self

        if self.transport == "subprocess":
            self.transport_adapter = "skuld.transports.subprocess.SubprocessTransport"
        elif self.transport == "tmux-interactive":
            self.transport_adapter = "skuld.transports.tmux_interactive.TmuxInteractiveTransport"

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
