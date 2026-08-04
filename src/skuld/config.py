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

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from niuu.domain.observability import ObservabilityConfig
from niuu.mesh.config import MeshNatsConfig


class SkuldObservabilityConfig(ObservabilityConfig):
    """OpenTelemetry settings with Skuld's stable service identity."""

    service_name: str = Field(default="skuld")


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
    realm_id: str = Field(default="")
    capabilities: list[str] = Field(default_factory=lambda: list(_DEFAULT_MESH_CAPABILITIES))
    tools: list[str] = Field(default_factory=lambda: list(_DEFAULT_MESH_TOOLS))
    persona: str = Field(default="coder")
    transport: str = Field(default="nng")
    nng: NngConfig = Field(default_factory=NngConfig)
    adapters: list[dict[str, Any]] = Field(default_factory=list)
    discovery_adapters: list[dict[str, Any]] = Field(default_factory=list)
    nats: MeshNatsConfig = Field(default_factory=MeshNatsConfig)
    redis_url_env: str = Field(default="REDIS_URL")
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
    ack_timeout_s: float = Field(
        default=20.0,
        description=(
            "Seconds to wait for a workflow.kickoff.acknowledged mesh event "
            "from the flock before republishing the kickoff."
        ),
    )
    ack_max_redeliveries: int = Field(
        default=5,
        description=(
            "How many times an unacknowledged kickoff is republished before "
            "the session fails loudly. Total attempts = 1 + this value."
        ),
    )


class WorkflowRuntimeConfig(BaseModel):
    """Workflow graph metadata injected into Skuld-backed flock sessions."""

    workflow_id: str = Field(default="")
    name: str = Field(default="")
    version: str = Field(default="")
    scope: str = Field(default="")
    initial_context: str = Field(default="")
    graph: dict[str, Any] = Field(default_factory=dict)
    trace_context: dict[str, str] = Field(default_factory=dict)

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
        trace_context = value.get("trace_context")
        if isinstance(trace_context, str) and trace_context.strip():
            with suppress(Exception):
                value = dict(value)
                value["trace_context"] = json.loads(trace_context)
        # The initial context is JSON-encoded on the wire because the sandbox
        # provisioner refuses env values containing newlines, and a workflow
        # brief is prose. Only accept a decode that yields a string: a context
        # whose text happens to be valid JSON ("{...}", a bare number) must
        # stay the literal prose the author wrote, not become an object.
        initial_context = value.get("initial_context")
        if isinstance(initial_context, str) and initial_context.strip():
            with suppress(Exception):
                decoded = json.loads(initial_context)
                if isinstance(decoded, str):
                    value = dict(value)
                    value["initial_context"] = decoded
        return value


class RoomConfig(BaseModel):
    """Multi-agent room chat configuration.

    When enabled, the broker operates in room mode and tracks per-message
    participant identity. Disabled by default so single-agent chat is unaffected.
    """

    enabled: bool = Field(default=False)
    environment_id: str = Field(
        default="local",
        description="Environment whose participants are represented in this room.",
    )
    participant_colors: list[str] = Field(default_factory=lambda: list(_DEFAULT_PARTICIPANT_COLORS))
    activity_detail_max_length: int = Field(default=200)
    delivery_dedupe_max_entries: int = Field(
        default=4096,
        gt=0,
        description=(
            "Maximum number of source collaboration-event identities retained "
            "to suppress exact transport redeliveries."
        ),
    )
    default_target_peer_id: str = Field(
        default="",
        description=(
            "Room participant that untargeted browser messages route to as "
            "directed messages. Set by Volundr for resident sessions (one "
            "long-lived ravn behind the room) so any chat client works "
            "without knowing the peer id. Empty keeps the classic behavior "
            "(CLI transport, or an error in room-only workflow sessions)."
        ),
    )
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
    credential_name: str = Field(
        default="",
        description=(
            "Optional credential-store entry containing bot_token and chat_id. "
            "Direct bot_token/chat_id values win when set."
        ),
    )
    credential_owner_type: str = Field(default="user")
    credential_owner_id: str = Field(default="dev-user")
    credential_store_adapter: str = Field(
        default="volundr.adapters.outbound.file_credential_store.FileCredentialStore"
    )
    credential_store_kwargs: dict[str, Any] = Field(
        default_factory=lambda: {"base_dir": "~/.niuu/credentials"}
    )
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
    owner_id: str = Field(
        default="",
        description=(
            "User ID (IDP sub) that owns this session. Set by Volundr at "
            "spawn time; when non-empty, inbound WebSocket connections must "
            "present a matching identity (see WsAuthConfig)."
        ),
    )
    tenant_id: str = Field(
        default="",
        description=(
            "Tenant the session belongs to. Set by Volundr at spawn time; "
            "cross-tenant WebSocket connections are rejected when both sides "
            "declare a tenant."
        ),
    )
    model: str = Field(default="claude-opus-4-8")
    reasoning_effort: str = Field(
        default="",
        description=(
            "Reasoning effort to launch the CLI at. Empty uses the transport default of 'high'."
        ),
    )
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


class ObservationRelayConfig(BaseModel):
    """External-event relay for a room participant.

    When the broker hosts a resident (``room.default_target_peer_id`` set),
    Sleipnir events matching *event_patterns* are checked against the
    resident's register-frame ``subscribes_to`` declaration. Each match is
    delivered as a directed message (the resident takes a turn and reports
    into the room) plus a ``room_notification`` so the operator sees the
    wake-up even when detached.
    """

    enabled: bool = Field(default=True)
    event_patterns: list[str] = Field(
        default_factory=lambda: ["*"],
        description=(
            "Optional transport-level event patterns used to bound the relay "
            "subscription. The default observes every event; the target Ravn's "
            "declared subscribes_to patterns remain authoritative for delivery."
        ),
    )
    payload_preview_chars: int = Field(
        default=2000,
        gt=0,
        description=(
            "Max characters of the event payload embedded in the resident's "
            "wake-up message; longer payloads are truncated. Raise it for "
            "workflows whose events carry large artifact manifests."
        ),
    )


class WsAuthConfig(BaseModel):
    """Ownership enforcement for inbound WebSocket connections.

    The broker does not validate token signatures — that is Envoy's / the API
    gateway's job (see ``.claude/rules/architecture.md``: delegate to standard
    OIDC flows). What the broker enforces is AUTHORIZATION: the connecting
    identity must own this session. Identity is resolved the same way
    Volundr's ``extract_principal`` does — Envoy ``x-auth-*`` headers first,
    developer query parameters second, decoded bearer claims last — and the
    verdict mirrors ``SimpleRoleAuthorizationAdapter``: tenant scoping, admin
    bypass, then owner match. Sessions with no ``session.owner_id`` (legacy
    and unauthenticated dev sessions) are not restricted.
    """

    enforce_ownership: bool = Field(
        default=True,
        description=(
            "Reject WebSocket connections whose identity does not match the "
            "session owner. Only applies when session.owner_id is set."
        ),
    )
    admin_roles: list[str] = Field(
        default_factory=lambda: ["volundr:admin"],
        description="Roles that may attach to any session within the tenant.",
    )
    allow_loopback: bool = Field(
        default=True,
        description=(
            "Accept unauthenticated connections from loopback addresses. "
            "In-pod peers (the CLI attaching via --sdk-url, flock ravn "
            "daemons) share the pod trust boundary and carry no user token."
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


class DeliveryConfig(BaseModel):
    """Durable inbound-message delivery (SRD FR-5 / INV-7).

    An inbound user message is accepted by the broker as ``pending`` and only
    becomes authoritatively ``active`` once the transport consumes it. A transient
    transport failure (a wedged input channel, a transport still warming up) is
    retried with bounded backoff; on terminal failure the user turn flips to a
    VISIBLE ``failed`` state (never left silently ``pending``). These knobs keep
    every count/delay out of the business logic (no magic numbers).
    """

    max_attempts: int = Field(
        default=4,
        ge=1,
        description=(
            "Total number of transport-delivery attempts for one inbound message "
            "(the first try plus retries). 1 disables retry."
        ),
    )
    attempt_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description=(
            "Per-attempt timeout for a single transport send/redirect. A wedged "
            "send-lock that never returns is bounded by this and retried."
        ),
    )
    initial_backoff_seconds: float = Field(
        default=0.5,
        ge=0,
        description="Delay before the first retry after a transient failure.",
    )
    backoff_multiplier: float = Field(
        default=2.0,
        ge=1,
        description="Multiplier applied to the backoff delay after each failed attempt.",
    )
    max_backoff_seconds: float = Field(
        default=5.0,
        ge=0,
        description="Upper bound on a single inter-attempt backoff delay.",
    )


class WorkloadIdentityConfig(BaseModel):
    """Workload identity token exchange for Volundr API authentication.

    Resident/flock brokers exchange a projected service-account token for a
    short-lived platform JWT. The config file is canonical; the legacy bare
    ``NIUU_WORKLOAD_IDENTITY_*`` environment variables still override for
    already-deployed charts (see ``LegacyWorkloadIdentityEnvSource``).
    """

    token_file: str = Field(
        default="/var/run/secrets/niuu-workload/token",
        description=(
            "Projected workload identity token file. Token exchange is "
            "skipped when the file does not exist (dev/local sessions)."
        ),
    )
    exchange_url: str = Field(
        default="",
        description=(
            "Workload token exchange endpoint. Empty derives "
            "volundr_api_url + /api/v1/tokens/workload/exchange."
        ),
    )
    audiences: list[str] = Field(
        default_factory=lambda: ["volundr-api", "forge", "ting", "mimir", "guild"],
        description="Target service audiences requested from the token exchange.",
    )

    @field_validator("audiences", mode="before")
    @classmethod
    def _coerce_comma_separated(cls, value: Any) -> Any:
        """Accept a comma-separated string (legacy env var format) as a list."""
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value


class CodexAuthConfig(BaseModel):
    """Dynamic Codex authentication adapter configuration."""

    adapter: str = Field(default="skuld.codex_auth.HostCodexAuthProvider")
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)


# Legacy bare env vars (pre config-first rule) that deployed charts still set.
# Mapped into the workload_identity section by LegacyWorkloadIdentityEnvSource;
# the config file is canonical, these are a compatibility override.
_LEGACY_WORKLOAD_IDENTITY_ENV = {
    "NIUU_WORKLOAD_IDENTITY_TOKEN_FILE": "token_file",
    "NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL": "exchange_url",
    "NIUU_WORKLOAD_IDENTITY_AUDIENCES": "audiences",
}


class LegacyWorkloadIdentityEnvSource(PydanticBaseSettingsSource):
    """Settings source mapping bare NIUU_WORKLOAD_IDENTITY_* env vars.

    The SKULD__ env prefix means pydantic-settings cannot alias un-prefixed
    env names on nested fields, so this source feeds the legacy names into
    ``workload_identity.*`` explicitly. It ranks below SKULD__* env vars and
    above the YAML file (an env override of the canonical config).
    """

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, "", False

    def __call__(self) -> dict[str, Any]:
        section: dict[str, Any] = {}
        for env_name, key in _LEGACY_WORKLOAD_IDENTITY_ENV.items():
            raw = os.environ.get(env_name, "").strip()
            if raw:
                section[key] = raw
        if not section:
            return {}
        return {"workload_identity": section}


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
    skip_permissions: bool = Field(
        default=True,
        description="Run agent transports without interactive tool approval prompts.",
    )
    approval_policy: str = Field(default="")
    sandbox: str = Field(default="")
    cli_binary: str = Field(
        default="claude",
        description="CLI executable for subprocess transports.",
    )
    remote_control_permission_mode: str = Field(
        default="",
        description=(
            "Claude Remote Control permission-mode override; empty follows skip_permissions."
        ),
    )
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
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)
    ws_auth: WsAuthConfig = Field(default_factory=WsAuthConfig)
    observation_relay: ObservationRelayConfig = Field(default_factory=ObservationRelayConfig)
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8081)
    volundr_api_url: str = Field(default="")
    usage_report_path: str = Field(
        default="",
        description=(
            "Absolute Volundr API path for token usage reports. Empty uses the "
            "Forge session usage endpoint."
        ),
    )
    external_api_token: str = Field(
        default="",
        validation_alias=AliasChoices(
            "external_api_token",
            "SKULD__EXTERNAL_API_TOKEN",
            "VOLUNDR_EXTERNAL_API_TOKEN",
        ),
        description=(
            "Explicit service token used for outbound Volundr API calls. "
            "VOLUNDR_EXTERNAL_API_TOKEN remains a supported legacy alias."
        ),
    )
    workload_identity: WorkloadIdentityConfig = Field(default_factory=WorkloadIdentityConfig)
    codex_auth: CodexAuthConfig = Field(default_factory=CodexAuthConfig)
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
    # Unified internal-visibility default for a freshly-connected live channel
    # (SRD FR-7 / INV-10). The read paths thread the SAME configured default
    # (``ReplayConfig.default_show_internal`` in volundr); a live ``WebSocketChannel``
    # must read its default from ONE configured source too, not a hardcoded literal,
    # so all three paths (live channel, replay tail, cold-read) move together.
    # Default ``False`` (internal tool_use/tool_result HIDDEN), matching ReplayConfig.
    default_show_internal: bool = Field(default=False)
    max_upload_size_bytes: int = Field(default=104_857_600)  # 100 MB
    max_presented_file_bytes: int = Field(
        default=52_428_800,
        gt=0,
        validation_alias=AliasChoices(
            "max_presented_file_bytes",
            "SKULD__MAX_PRESENTED_FILE_BYTES",
            "MAX_PRESENTED_FILE_BYTES",
        ),
        description="Maximum size of a file staged by the present-file endpoint.",
    )
    acp_prompt_timeout_s: float = Field(default=300.0)  # ACP (Grok Build) prompt turn timeout
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)
    reflex: ReflexConfig = Field(default_factory=ReflexConfig)
    observability: SkuldObservabilityConfig = Field(default_factory=SkuldObservabilityConfig)
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
        3. legacy workload-identity env vars (NIUU_WORKLOAD_IDENTITY_*)
        4. yaml - YAML config file
        5. file_secret_settings - /run/secrets files
        """
        return (
            init_settings,
            env_settings,
            LegacyWorkloadIdentityEnvSource(settings_cls),
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )
