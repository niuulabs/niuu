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
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from identity.authz_config import AuthorizationAdapterConfig
from niuu.config import DynamicAdapterConfig
from niuu.domain.observability import ObservabilityConfig
from niuu.forge_mcp.models import ForgeMcpGrant
from niuu.mesh.config import DEFAULT_RPC_REPLY_CACHE_SIZE, MeshAttestationConfig, MeshNatsConfig
from personas.archived import reject_archived_persona
from skuld.claude_auth import ClaudeAuthMode
from skuld.claude_permission import DEFAULT_CLAUDE_PERMISSION_MODE


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

    pub_sub_address: str = Field(
        default="tcp://127.0.0.1:0",
        description=(
            "Where this session's PUB socket listens. Port 0 takes a free port, announced as "
            "the port it got. Loopback reaches the sessions on the same machine; a session "
            "other machines must reach listens on every interface (`tcp://0.0.0.0:7480`) and "
            "sets `advertise_host`."
        ),
    )
    req_rep_address: str = Field(default="tcp://127.0.0.1:0")
    advertise_host: str = Field(
        default="",
        description=(
            "The host or IP other machines dial this session at. Needed when "
            "`pub_sub_address` listens on every interface, which has no address of its own to "
            "announce; empty announces the listen address as it is."
        ),
    )
    rediscover_interval_s: float = Field(
        default=1.0,
        gt=0,
        description=(
            "Seconds between re-reads of the peers discovery found, to dial the ones that "
            "joined and hang up on the ones that left. A discovery join or leave refreshes "
            "at once; this is the backstop, and bounds how long a new peer can be unheard."
        ),
    )


class MeshConfig(BaseModel):
    """Mesh peer configuration.

    When enabled, Skuld registers as a mesh peer and subscribes to task
    topics. Other peers can delegate coding work via the standard mesh
    pub/sub protocol. Disabled by default so solo sessions are unaffected.
    """

    enabled: bool = Field(default=False)
    peer_id: str = Field(
        default="",
        description=(
            "This session's one mesh peer id (transport, discovery, signing certificate and "
            "reply routes). Empty uses the session id; a session with neither does not start "
            "its mesh."
        ),
    )
    realm_id: str = Field(default="")
    capabilities: list[str] = Field(default_factory=lambda: list(_DEFAULT_MESH_CAPABILITIES))
    tools: list[str] = Field(default_factory=lambda: list(_DEFAULT_MESH_TOOLS))
    persona: str = Field(default="coder")
    transport: str = Field(default="nng")

    @field_validator("persona")
    @classmethod
    def _persona_is_not_archived(cls, value: str) -> str:
        return reject_archived_persona(value, where="mesh.persona")

    nng: NngConfig = Field(default_factory=NngConfig)
    adapters: list[dict[str, Any]] = Field(default_factory=list)
    discovery_adapters: list[dict[str, Any]] = Field(default_factory=list)
    nats: MeshNatsConfig = Field(default_factory=MeshNatsConfig)
    attestation: MeshAttestationConfig = Field(default_factory=MeshAttestationConfig)
    rpc_timeout_s: float = Field(default=10.0)
    rpc_reply_cache_size: int = Field(default=DEFAULT_RPC_REPLY_CACHE_SIZE, ge=1)
    diff_max_bytes: int = Field(default=8192)
    diff_timeout_s: float = Field(default=10.0)
    consumes_event_types: list[str] = Field(
        default_factory=lambda: ["code.requested"],
        description=(
            "Topics this session subscribes to. A pinned persona's declared "
            "``consumes.event_types`` are subscribed in addition (``skuld.mesh_topics``)."
        ),
    )
    role: str = Field(
        default="",
        description=(
            "What this session is for, announced to its peers so they can choose whom to talk "
            "to (e.g. 'reviewer'). Empty announces the pinned persona's id as the role, or no "
            "role when there is no persona."
        ),
    )
    description: str = Field(
        default="",
        description=(
            "One or two sentences on what this session does, announced to its peers. The "
            "pinned persona's own ``description`` is announced when it has one; this is used "
            "when it has none."
        ),
    )
    publish_event_types: list[str] = Field(
        default_factory=list,
        description=(
            "Topics this session's ``mesh_publish`` tool may publish, in addition to the "
            "ones its pinned persona declares in ``produces``. A topic in neither list is "
            "refused loudly, so a mistyped topic is never published to nobody."
        ),
    )
    share_work: bool = Field(
        default=False,
        description=(
            "Share this session's work with its realm: each reply, tool call and result, and "
            "each workflow step close is published on the session's own activity topic, "
            "which no member subscribes to, so only the realm's room keeps it (a workflow "
            "run's room shows it as this member's turns). Off by default."
        ),
    )
    published_event_urgency: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description=(
            "The attention hint (0.0-1.0) stamped on events the ``mesh_publish`` tool publishes; "
            "the same as the events the browser publishes. It orders attention, it limits nothing."
        ),
    )


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
    routed: bool = Field(
        default=False,
        description=(
            "Declare that this broker hosts a room and owns no CLI agent of "
            "its own. The broker then never starts its own CLI transport, and "
            "untargeted browser messages go to the room's sole ravn when "
            "default_target_peer_id is unset. Set by the room's own configuration. "
            "Without it a room broker lazy-starts a Claude/Codex process that "
            "competes with the room's ravns for the same chat frames."
        ),
    )
    presence_sweep_interval_s: float = Field(
        default=30.0,
        description=(
            "How often participants past their heartbeat TTL with no live WebSocket "
            "are shown as away. They keep their membership until they leave. 0 "
            "disables the check."
        ),
    )
    agent_heartbeat_ttl_s: float = Field(
        default=90.0,
        ge=0,
        description=(
            "Seconds an agent registered in the room may go without a heartbeat (and "
            "without a live WebSocket) before the presence sweep shows it as away. "
            "Bounded by how often agents heartbeat. 0 never marks agents away."
        ),
    )
    peer_heartbeat_ttl_s: float = Field(
        default=90.0,
        ge=0,
        description=(
            "Seconds a participant discovered over the mesh may go without a heartbeat "
            "before it is shown as away. 0 never marks them away."
        ),
    )
    join_heartbeat_ttl_s: float = Field(
        default=300.0,
        ge=0,
        description=(
            "Seconds a person, or an agent of another session, that joined the room may "
            "go without a heartbeat before it is shown as away. Longer than the agent "
            "TTL because a browser tab heartbeats less often. 0 never marks them away."
        ),
    )
    realm_room: bool = Field(
        default=False,
        description=(
            "The room is its mesh realm's room: it shows what every member of the realm "
            "publishes, not only what is tagged with this session. A workflow run's first "
            "session hosts one for the run's members (its realm is the run's own), so "
            "opening it shows every member as a worker of the run. The host keeps its own "
            "agent: its replies stay its own turns and are not copied into the room."
        ),
    )


TelegramTopicMode = Literal["shared_chat", "fixed_topic", "topic_per_session"]


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
    topic_mode: TelegramTopicMode = Field(
        default="topic_per_session",
        description=(
            "Where messages go: shared_chat (the whole chat), fixed_topic (the topic named by "
            "message_thread_id, which is then required) or topic_per_session (a forum topic "
            "created for the session; the bot must be able to manage topics). The mode is "
            "never replaced by another when it cannot be honoured: the session fails to start."
        ),
    )
    message_thread_id: int | None = Field(default=None)

    @model_validator(mode="after")
    def _fixed_topic_needs_thread(self) -> Self:
        if self.enabled and self.topic_mode == "fixed_topic" and self.message_thread_id is None:
            raise ValueError(
                "telegram.topic_mode is 'fixed_topic' but telegram.message_thread_id is not "
                "set; set telegram.message_thread_id, or set telegram.topic_mode: shared_chat"
            )
        return self


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
    model: str = Field(default="claude-opus-5-5")
    reasoning_effort: str = Field(
        default="",
        description=(
            "Reasoning effort to launch the CLI at (e.g. 'ultra' for GPT-6 Sol). "
            "Empty lets the transport pick a model-appropriate default."
        ),
    )
    workspace_dir: str | None = Field(default=None)
    system_prompt: str = Field(
        default="",
        description=(
            "Ad-hoc instructions from the launch request. With a ``persona`` they are appended "
            "after the persona's own instructions, never in place of them."
        ),
    )
    persona: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The pinned portable persona document (``schema_version``, ``id``, ``revision``, "
            "``definition``) this session plays, as Forge resolved it at launch. Skuld projects "
            "it onto the harness on every start, resume included (``skuld.personas``): its "
            "instructions are appended to the harness's own prompt and its tool and permission "
            "limits are enforced by the harness, or the session refuses to start. Null means the "
            "session plays no persona and nothing is projected."
        ),
    )
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
    """Optional ownership enforcement behind a trusted authentication proxy.

    Disabled mode supports local mini-mode without authentication. Enabled mode
    requires verified identity headers and explicit session owner/tenant metadata.
    The application listener must only be reachable through the trusted proxy.
    """

    enforce_ownership: bool = Field(
        default=False,
        description="Require authenticated session ownership for WebSocket connections.",
    )
    authorization: AuthorizationAdapterConfig = Field(default_factory=AuthorizationAdapterConfig)
    identity: AuthorizationAdapterConfig | None = None
    websocket_check_interval: float = Field(
        default=5.0,
        gt=0,
        allow_inf_nan=False,
        description="Maximum interval between active WebSocket credential lifetime checks.",
    )
    user_id_header: str = "x-auth-user-id"
    tenant_header: str = "x-auth-tenant"
    roles_header: str = "x-auth-roles"
    display_name_header: str = Field(
        default="x-auth-display-name",
        description=(
            "Header the session proxy (niuu.session_proxy) stamps with the "
            "caller's display name (percent-encoded UTF-8), recorded with the "
            "user id as the author of every human message sent over the "
            "connection. Only the proxy-stamped value is read, like "
            "room_role_header: a browser-supplied copy never reaches the "
            "broker. Must match the proxy's own header name."
        ),
    )
    room_role_header: str = Field(
        default="x-niuu-room-role",
        description=(
            "Header the session proxy (niuu.session_proxy) stamps with the "
            "caller's verified room role (owner/approver/viewer), used to gate "
            "per-message-type WebSocket authorization. Must match the proxy's "
            "own header name."
        ),
    )
    role_mapping: dict[str, str] = Field(default_factory=dict)
    admin_roles: list[str] = Field(
        default_factory=lambda: ["volundr:admin"],
        description="Verified roles mapped to the Cedar administrator role.",
    )
    allow_loopback: bool = Field(
        default=False,
        description=(
            "Trust unauthenticated loopback CLI/Ravn peers. Enable only when "
            "those endpoints cannot be reached through a reverse proxy."
        ),
    )
    room_role_source: Literal["proxy", "deployment", "remote"] = Field(
        default="deployment",
        description=(
            "How to resolve the room role when room_role_header is absent. "
            "'deployment' (the default, and what every non-process backend "
            "gets — Kubernetes, OpenShell, VM, and docker unless routed "
            "through the session proxy): this pod's own auth boundary "
            "(ext_authz / enforce_ownership / the deployment's Gateway or "
            "ingress) already gates every caller who reaches this pod, "
            "participants are not supported on these backends (invites are "
            "refused with 409 before this ever matters), so a missing "
            "header simply means owner — identical to this pod's behavior "
            "before session_participants existed. A VM broker keeps this "
            "default although VM invites are allowed: every browser request "
            "reaches it through Forge's session proxy, which always stamps "
            "the header, and only Forge's own tunnel and the guest itself "
            "can reach it without one. 'proxy' (rendered only "
            "for the process backend, by the local process launcher): the "
            "session proxy (niuu.session_proxy) resolves and stamps the "
            "header itself from session_participants grants, so trust it — "
            "a missing header means viewer, except a loopback caller "
            "carrying no x-forwarded-for (same-pod tooling a reverse proxy "
            "could never present as). 'remote' (Kubernetes pods deliberately "
            "opted into session_participants support — "
            "see PodManagerConfig.room_role_source in volundr/config.py): "
            "room_role_remote's dynamic adapter asks Forge for the caller's "
            "grant on every request, so a missing header genuinely means "
            "'ask Forge', not an implicit default — see "
            "skuld.room_role_port.RoomRoleResolverPort. Requires "
            "room_role_remote to be set; unreachable Forge or a resolution "
            "error is a hard deny, never owner or viewer."
        ),
    )
    room_role_remote: DynamicAdapterConfig | None = Field(
        default=None,
        description=(
            "Dynamic adapter (skuld.room_role_port.RoomRoleResolverPort) used "
            "when room_role_source is 'remote'. Required in that case — "
            "config that asks for remote resolution and supplies no adapter "
            "to do it is a configuration error, not an implicit fallback to "
            "'deployment'."
        ),
    )
    room_role_revalidate_interval_seconds: float = Field(
        default=5.0,
        gt=0,
        allow_inf_nan=False,
        description=(
            "How often an open 'remote'-mode browser WebSocket re-checks its "
            "room role against Forge and closes on revoke/demotion (mirrors "
            "niuu.session_proxy._revalidate_loop). Only used when "
            "room_role_source is 'remote'."
        ),
    )
    room_role_revalidate_max_consecutive_failures: int = Field(
        default=3,
        ge=1,
        description=(
            "A transient RoomRoleResolutionError during revalidation (a "
            "momentary Forge blip) keeps the connection open rather than "
            "closing it — but that grace is bounded: after this many "
            "consecutive failures, or room_role_revalidate_max_staleness_"
            "seconds since the first one (whichever comes first), the "
            "socket closes (1011) rather than staying open indefinitely on "
            "an authority that never recovers."
        ),
    )
    room_role_revalidate_max_staleness_seconds: float = Field(
        default=60.0,
        gt=0,
        allow_inf_nan=False,
        description=(
            "See room_role_revalidate_max_consecutive_failures — the time"
            "-based half of the same bound."
        ),
    )

    @model_validator(mode="after")
    def _remote_room_role_requires_adapter(self) -> "WsAuthConfig":
        if self.room_role_source != "remote":
            return self
        if self.room_role_remote is None:
            raise ValueError(
                "ws_auth.room_role_source is 'remote' but ws_auth.room_role_remote is not "
                "set — configure its adapter (e.g. "
                "skuld.room_role_remote.RemoteAuthorizationAdapter) and kwargs, or set "
                "room_role_source back to 'deployment'."
            )
        if self.enforce_ownership:
            raise ValueError(
                "ws_auth.room_role_source is 'remote' but ws_auth.enforce_ownership is "
                "true — this pod's own ext_authz sidecar (identity.adapters.envoy_authz) "
                "gates every connection to Cedar's 'start' action (owner/admin only) "
                "before a remote room-role lookup ever runs, so a participant could "
                "never attach regardless of their grant. Disable enforce_ownership for "
                "this backend, or set room_role_source back to 'deployment' and keep "
                "session_participants invites refused (see "
                "rest_session_participants.REMOTE_CAPABLE_RUNTIME_BACKENDS)."
            )
        return self


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
    stale_output_seconds: float = Field(
        default=600.0,
        gt=0,
        description=(
            "A busy state is re-reported only while the transport produced a row "
            "(terminal frames included) within this window. Past it the broker "
            "reconciles with the transport's turn flag: idle when no turn is in "
            "flight, otherwise no heartbeat (G10, F12c)."
        ),
    )


class PresentedDurableConfig(BaseModel):
    """Uploading present-file copies to Forge's durable store (control-plane F2)."""

    inline_upload_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        description=(
            "Time present-file waits for the first upload before answering; a slower "
            "upload continues as a background retry."
        ),
    )
    upload_timeout_seconds: float = Field(
        default=60.0, gt=0, description="Time allowed for one background durable upload."
    )
    retry_attempts: int = Field(
        default=6, ge=0, description="Background retries after a failed upload."
    )
    retry_backoff_seconds: float = Field(
        default=2.0, gt=0, description="First retry delay; doubles per attempt."
    )
    retry_backoff_max_seconds: float = Field(
        default=120.0, gt=0, description="Upper bound on the retry delay."
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
            "send-lock that never returns is bounded by this and retried. Does not apply to a "
            "transport whose send returns only when the turn is over "
            "(capabilities.send_runs_turn): the turn takes as long as it takes, and the "
            "transport's own turn timeout bounds it."
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
            "Projected workload identity token file. Where it exists, the broker "
            "exchanges it for its own Forge credential and a failed exchange is an "
            "error, never a different credential; where it does not, no workload "
            "identity is configured for this session."
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


class PreviewConfig(BaseModel):
    """Live preview: serve the session's own listening ports to its owner.

    Skuld watches which TCP ports the session listens on, reports changes to the
    browser, and forwards Forge's preview router to them on loopback (see
    ``skuld.preview``). Both adapters are required when enabled.
    """

    enabled: bool = Field(
        default=False,
        description="Discover the session's listening ports and serve them as live previews.",
    )
    port_scan_interval_seconds: float = Field(
        default=2.0,
        gt=0,
        allow_inf_nan=False,
        description=(
            "Seconds between scans of the session's listening sockets: how soon a "
            "dev server that starts or stops shows up in the browser."
        ),
    )
    port_scanner: DynamicAdapterConfig | None = Field(
        default=None,
        description=(
            "skuld.preview_port.ListeningPortScanner adapter, e.g. "
            "skuld.preview_adapters.ProcNetListeningPortScanner (a pod or VM: the whole "
            "network namespace, minus excluded_ports) or "
            "skuld.preview_adapters.LsofSessionPortScanner (a shared host: only "
            "the session's own processes)."
        ),
    )
    router_auth: DynamicAdapterConfig | None = Field(
        default=None,
        description=(
            "skuld.preview_port.PreviewRouterAuth adapter that admits Forge's preview "
            "router: skuld.preview_adapters.WorkloadJwtRouterAuth, or "
            "skuld.preview_adapters.NoRouterAuth only on a host with no identity provider."
        ),
    )
    connect_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        allow_inf_nan=False,
        description=(
            "Seconds to wait for a session port to accept a connection. Responses and "
            "WebSockets stream unbounded, for as long as the browser holds them."
        ),
    )

    @model_validator(mode="after")
    def _adapters_when_enabled(self) -> "PreviewConfig":
        if not self.enabled:
            return self
        missing = [
            name
            for name, adapter in (
                ("port_scanner", self.port_scanner),
                ("router_auth", self.router_auth),
            )
            if adapter is None or not adapter.adapter
        ]
        if missing:
            raise ValueError(
                f"preview.enabled is true but preview.{' and preview.'.join(missing)} "
                "is not set: configure the adapter, or set preview.enabled to false"
            )
        return self


class ForgeMcpConfig(BaseModel):
    """The built-in ``forge`` MCP server Skuld injects into every MCP-capable engine.

    The engine spawns ``python -m skuld.forge_mcp`` over stdio; it forwards each
    tool call to this broker's loopback ``/api/forge-mcp/*`` routes, guarded by
    the per-start broker secret. The broker performs the Forge calls with its
    own credential (or ``token`` when set), so no Forge credential reaches the
    model.
    """

    enabled: bool = Field(
        default=True,
        description="Inject the built-in forge MCP server (SKULD__FORGE_MCP__ENABLED=false: off).",
    )
    server_name: str = Field(
        default="forge",
        min_length=1,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="MCP server name; tools appear to Claude as mcp__<name>__<tool>.",
    )
    grants: Annotated[list[ForgeMcpGrant] | None, NoDecode] = Field(
        default=None,
        description=(
            "Narrow the tool grants of the session token (SKULD__FORGE_MCP__GRANTS): "
            "'message' (send_message, message_status) and 'lifecycle' "
            "(create/start/stop_session). The grants themselves come from the token's "
            "scopes; this can only remove some. Unset keeps the token's grants; without "
            "a token no grant is offered. Read tools and notify need no grant."
        ),
    )
    token: str = Field(
        default="",
        description=(
            "Scoped session-bound Forge token (SKULD__FORGE_MCP__TOKEN), minted by Forge "
            "at launch. The bearer for MCP-proxied Forge calls, durable presented-file "
            "uploads and the session's own record (log, activity, usage, message claims); "
            "empty uses the broker's own credential (and offers no grants)."
        ),
    )
    token_file: str = Field(
        default="",
        description=(
            "File holding the session token, for session pods where Forge mounts it "
            "from a Kubernetes Secret instead of the environment. Read once at start; "
            "a missing or empty file is fatal. Ignored when token is set."
        ),
    )
    runtime_dir: str = Field(
        default="",
        description=(
            "Broker-owned 0700 directory for the loopback secret and generated engine "
            "configs. Empty uses <tempdir>/skuld-<uid>/<session id>."
        ),
    )
    notify_confirm_timeout_s: float = Field(
        default=5.0,
        ge=0,
        description=(
            "How long notify, and workflow_step_close, wait for Forge to acknowledge the log flush."
        ),
    )
    forge_request_timeout_s: float = Field(
        default=20.0, gt=0, description="Timeout for one Forge REST call made by a tool."
    )
    broker_request_timeout_s: float = Field(
        default=60.0,
        gt=0,
        description="Timeout for one stdio-server → broker tool call.",
    )
    stdio_max_concurrency: int = Field(
        default=4, ge=1, description="Tool calls the stdio server runs in parallel."
    )
    startup_timeout_s: float = Field(
        default=20.0, gt=0, description="Engine-side MCP server startup timeout (Codex)."
    )
    tool_timeout_s: float = Field(
        default=120.0, gt=0, description="Engine-side MCP tool call timeout (Codex)."
    )
    list_default_limit: int = Field(
        default=20, ge=1, description="Items returned by list tools when none is asked."
    )
    list_max_limit: int = Field(default=50, ge=1, description="Upper bound for list tools.")
    transcript_default_turns: int = Field(
        default=10, ge=1, description="Turns session_transcript returns when none is asked."
    )
    transcript_max_turns: int = Field(
        default=30, ge=1, description="Upper bound on turns session_transcript returns."
    )
    transcript_turn_max_chars: int = Field(
        default=2000, ge=1, description="Characters kept per transcript turn."
    )
    output_max_chars: int = Field(
        default=24000, ge=1024, description="Upper bound on one tool result's text."
    )
    skill_enabled: bool = Field(
        default=True,
        description="Materialize the forge-notify skill for engines with per-session skills.",
    )

    @field_validator("grants", mode="before")
    @classmethod
    def _coerce_grants(cls, value: Any) -> Any:
        """Accept ``message,lifecycle`` or a JSON list (env forms) as well as a list.

        An empty string is an explicit empty list (narrow to no grants).
        """
        if not isinstance(value, str):
            return value
        if value.strip().startswith("["):
            return json.loads(value)
        return [part.strip() for part in value.split(",") if part.strip()]

    @model_validator(mode="after")
    def _token_from_file(self) -> "ForgeMcpConfig":
        if self.token or not self.token_file:
            return self
        path = Path(self.token_file).expanduser()
        try:
            token = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(
                f"forge_mcp.token_file {str(path)!r} cannot be read ({exc}); Forge mounts "
                "the session credential there, so the session pod is missing its Secret"
            ) from exc
        if not token:
            raise ValueError(f"forge_mcp.token_file {str(path)!r} is empty")
        self.token = token
        return self


class SecretRequestsConfig(BaseModel):
    """How the ``request_secret`` tool waits for the user to type a secret into the chat card."""

    poll_interval_s: float = Field(
        default=2.0,
        gt=0,
        description=(
            "How often request_secret asks Forge whether the user answered the card. It "
            "trades answer latency against Forge load; there is no event to wait on."
        ),
    )
    wait_max_s: float = Field(
        default=50.0,
        gt=0,
        description=(
            "Longest one request_secret call waits for the user. Bounded by the engine's MCP "
            "call timeouts: it must be shorter than forge_mcp.broker_request_timeout_s and "
            "forge_mcp.tool_timeout_s, or the engine kills the call first (startup fails "
            "otherwise). When it passes the call fails loudly and the request stays open in "
            "the chat; the agent calls request_secret again with the same name to keep waiting."
        ),
    )


class AskConfig(BaseModel):
    """How the ``ask`` tool waits for another session's answer."""

    poll_interval_s: float = Field(
        default=2.0,
        gt=0,
        description=(
            "How often ask checks Forge for the recipient's state (blocked on a person, stopped, "
            "failed or deleted). The answer itself is not polled: it wakes the wait the moment it "
            "reaches this session's inbox. The state has no event to wait on, so this trades "
            "how fast a blocked or gone recipient is noticed against Forge load."
        ),
    )
    wait_max_s: float = Field(
        default=50.0,
        gt=0,
        description=(
            "Longest one ask call waits, and the ceiling for the `deadline_s` a caller passes "
            "(a larger one is refused with this knob named). Bounded by the engine's MCP call "
            "timeouts: it must be shorter than forge_mcp.broker_request_timeout_s (the stdio "
            "server's wait on this broker, default 60) and forge_mcp.tool_timeout_s (the "
            "tool_timeout_sec Skuld sets for the engine, default 120), or the engine ends the "
            "call before it can answer (startup fails otherwise). Codex's own default for "
            "tool_timeout_sec is 60; Claude Code's MCP_TOOL_TIMEOUT defaults to about 28 hours "
            "(30 minutes without progress for a stdio server). When the wait passes without an "
            "answer the call returns `deadline` (or `not_started` / `delivery_unknown`), the "
            "message stays sent, and a later answer is delivered at the turn boundary as usual; "
            "ask again with the same request_id to keep waiting."
        ),
    )


class CodexAuthConfig(BaseModel):
    """Dynamic Codex authentication adapter configuration."""

    adapter: str = Field(default="skuld.codex_auth.HostCodexAuthProvider")
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)


class ModelGatewayConfig(BaseModel):
    """Route the Claude Code and Codex CLIs through the platform's model gateway.

    Set by the platform (``SKULD__MODEL_GATEWAY__URL``) when a session is
    launched with the Model server provider: the CLIs then talk to Bifrost,
    which speaks both the Anthropic and the OpenAI dialect and forwards to the
    self-hosted server. Empty means the CLIs use their vendors' own APIs.
    """

    url: str = Field(
        default="",
        description="Bifrost base URL as reachable from the session, e.g. http://niuu:8080/api/v1/bifrost.",
    )
    token: str = Field(
        default="",
        description=(
            "Bearer token the CLIs present to the gateway. Empty (the default) sends "
            "no meaningful credential — fine for a gateway running auth_mode: open "
            "(it ignores whatever is sent), a hard 401 from any other gateway auth "
            "mode. A 'pat'/'mesh'/'oidc' gateway needs a real PAT configured here; "
            "there is no platform-computed default yet (see "
            "cli.config.CLISettings._OIDC_UNCOVERED_PLUGINS['bifrost'] for the "
            "current state of automatically minting one)."
        ),
    )


class PiRuntimeConfig(BaseModel):
    """Native PI RPC process settings; credentials remain owned by PI on the host."""

    binary: str = "pi"
    agent_dir: str = ""
    session_dir: str = ""
    command_timeout_s: float = Field(default=30, gt=0)
    turn_timeout_s: float = Field(default=1800, gt=0)
    shutdown_timeout_s: float = Field(default=5, gt=0)


class DshRuntimeConfig(BaseModel):
    """DeepSeek Harness (dsh) SDK runtime settings for DshJsonRpcTransport."""

    runtime_bin: str = Field(
        default="",
        description=(
            "Path to a dsh-jsonrpc-agent executable. Empty uses the bundled "
            "runtime from the deepseek-harness-runtime-bin package."
        ),
    )
    cordis_config: str = Field(
        default="",
        description=(
            "Path to a Cordis composition file for the runtime. Empty uses the "
            "SDK profile bundled with deepseek-harness-runtime-bin."
        ),
    )
    base_url: str = Field(
        default="",
        description=(
            "OpenAI-compatible chat-completions endpoint base "
            "(the runtime requests {base}/chat/completions). Empty uses the "
            "public DeepSeek API."
        ),
    )
    api_key: str = Field(
        default="",
        description=(
            "API key handed to the runtime as DEEPSEEK_API_KEY. Set via config "
            "file or SKULD__DSH__API_KEY."
        ),
    )
    provider: str = Field(
        default="deepseek-official",
        description="Provider route registered by the runtime's composition.",
    )
    prompt_timeout_s: float = Field(
        default=600.0,
        description="Maximum seconds to wait for a turn to reach idle.",
    )


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


class TraceContextConfig(BaseSettings):
    """The W3C trace context this broker process inherited at spawn time.

    Not operator configuration — a per-process runtime carrier. Völundr's
    ``CoreSessionContributor`` sets bare ``TRACEPARENT``/``TRACESTATE`` (no
    ``SKULD__`` prefix, since these are the standard W3C env var names other
    tools — including Claude Code itself — read the same way) in the env of
    the pod/process it spawns for a session; this is Skuld reading that back
    as typed settings instead of a bare ``os.environ.get`` (config-first.md).
    A separate ``BaseSettings`` subclass, not a nested field on
    ``SkuldSettings``, because pydantic-settings only resolves a field's own
    ``validation_alias`` against nested env vars for a ``BaseSettings``
    instance — a plain nested ``BaseModel`` field ignores it and only ever
    sees ``SKULD__TRACE_CONTEXT__TRACEPARENT``, not the real env var.
    """

    model_config = SettingsConfigDict(extra="ignore")

    traceparent: str = Field(
        default="",
        validation_alias=AliasChoices("traceparent", "TRACEPARENT"),
    )
    tracestate: str = Field(
        default="",
        validation_alias=AliasChoices("tracestate", "TRACESTATE"),
    )


class WorkflowStepsConfig(BaseModel):
    """A session's part in a workflow run's mesh: a member that takes offers, or the run's root.

    A run is one unit: its root (a Skuld with no agent) and every member start together and
    share a local mesh. Forge sets this when it launches each of them.

    * A **member** (``enabled``) listens for the offers of the topics its pinned persona
      consumes, takes the ones that are its own, holds the step durably in the session's
      event log, injects its instructions at a turn boundary, lists the
      ``workflow_step_close`` tool for the whole session, and publishes ``step.picked_up``,
      ``step.passed`` and ``step.outcome`` on the run's mesh.
    * The **root** (``root``) publishes the offers Ting posts to its ``room/offer`` operation.

    An ordinary session leaves both off and pays for none of it.
    """

    enabled: bool = Field(
        default=False,
        description=(
            "This session is a member of a workflow run: it takes the run's offers and serves "
            "the workflow_step_close tool. Needs the durable event log and history hydration "
            "(event_log_enabled and history_hydration_enabled, with volundr_api_url): a held "
            "step is restored from the log after a restart. Also needs the mesh (mesh.enabled), "
            "a pinned persona (session.persona), run_id, member, persona_alias and "
            "orchestrator_peer_id. Enabling without any of them is fatal at startup."
        ),
    )
    root: bool = Field(
        default=False,
        description=(
            "This session is the root of a workflow run: Ting hands it offers through "
            "POST /api/room/offer and it publishes them on the run's mesh. Needs the mesh "
            "(mesh.enabled) and run_id. Exclusive with enabled: a coordinator that also did "
            "work would take its own offers."
        ),
    )
    run_id: str = Field(
        default="",
        description=(
            "The workflow run this session belongs to. Offers of any other run are refused."
        ),
    )
    orchestration: Literal["engine", "session"] = Field(
        default="engine",
        description=(
            "Who decides the run's next step. engine: Ting's engine, whose offers carry its "
            "signature (offer_signer) on top of the root's. session: the root's coordinator "
            "agent, whose offers carry the root's signature only."
        ),
    )
    orchestrator_peer_id: str = Field(
        default="",
        description=(
            "The mesh peer id of the run's root. A member takes offers published by this peer "
            "and no other."
        ),
    )
    offer_signer: str = Field(
        default="service:ting",
        description=(
            "Engine mode: the platform service peer id whose signature an offer must carry "
            "(Ting's, service:ting). Ignored in session mode."
        ),
    )
    member: str = Field(
        default="",
        description="This member's name in the workflow stage (shown in the room and the ledger).",
    )
    persona_alias: str = Field(
        default="",
        description=(
            "The alias the workflow gives this member's pinned persona. The offer carries one "
            "contract per alias; the member finds its own by this and checks it against its "
            "own persona document."
        ),
    )
    event_filters: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "The member's eventFilters from the workflow: each key must equal the value found "
            "in the offered inputs' payload (a list: contain it), else the member passes the "
            "offer and says which filter did not match. Empty takes every offer of its topics."
        ),
    )
    topics: list[str] = Field(
        default_factory=list,
        description=(
            "Every topic the workflow routes to this member (its stages' consumesEventTypes). "
            "The member subscribes to them on top of its persona's own topics: the engine "
            "offers a stage to the members its stage names on that topic, which need not be "
            "one the persona declares (the merge stage gives the coder merge.approved)."
        ),
    )

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if self.enabled and self.root:
            raise ValueError(
                "workflow_steps.enabled and workflow_steps.root are exclusive: a session is a "
                "member of the run or its root"
            )
        if not (self.enabled or self.root):
            return self
        needed = {"run_id": self.run_id}
        if self.enabled:
            needed |= {
                "member": self.member,
                "persona_alias": self.persona_alias,
                "orchestrator_peer_id": self.orchestrator_peer_id,
            }
        missing = [f"workflow_steps.{name}" for name, value in needed.items() if not value.strip()]
        if missing:
            role = "enabled" if self.enabled else "root"
            raise ValueError(f"workflow_steps.{role} needs {', '.join(missing)}")
        return self


class PeerInboxConfig(BaseModel):
    """The inbox through which other agents' messages reach this session's harness.

    Every message from another agent (a session, a room, a mesh peer) is held durably in the
    session's event log and handed to the harness at a turn boundary. The inbox has no
    capacity: loops between agents are made visible through the per-pair counts in the
    session status and the ``skuld.peer.messages`` counter, never capped.
    """

    enabled: bool = Field(
        default=True,
        description=(
            "Accept peer messages. A session that turns this off refuses every one with that "
            "reason. Accepting needs the durable event log (event_log_enabled and "
            "volundr_api_url): without it a message is refused loudly, never held in memory "
            "only."
        ),
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
    cli_type: str = Field(default="claude")  # "claude" | "codex" | "grok" | "muse"
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
    # A Forge node decides how unattended Claude sessions run: YOLO where the host allows it,
    # Claude's classifier-gated ``auto`` mode elsewhere. Set it once in the node's config file
    # (``claude_permission_mode: bypassPermissions`` on YOLO hosts). Claude transports validate it.
    claude_permission_mode: str = Field(
        default=DEFAULT_CLAUDE_PERMISSION_MODE,
        description=(
            "Claude Code --permission-mode used when skip_permissions is on "
            "(auto, bypassPermissions or acceptEdits)."
        ),
    )
    claude_auth: ClaudeAuthMode = Field(
        default="subscription",
        description=(
            "How Claude sessions sign in: 'subscription' uses the host's claude.ai login and "
            "strips ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN from the child env; 'api_key' "
            "keeps the platform API key. Any other value is refused at startup. Deploys "
            "override it with SKULD__CLAUDE_AUTH."
        ),
    )
    claude_oauth_token_file: str | None = Field(
        default=None,
        description=(
            "Absolute path of an owner-only 0400 file holding a Claude OAuth token for direct "
            "subscription inference. Requires claude_auth 'subscription', no model gateway "
            "and no ambient Anthropic credentials; a conflict or an invalid file refuses the "
            "launch. Deploys override it with SKULD__CLAUDE_OAUTH_TOKEN_FILE."
        ),
    )
    tmux_remote_control: bool = Field(
        default=True,
        description=(
            "Claude tmux sessions also register with the host's claude.ai login (Remote "
            "Control) so the Anthropic apps can drive them in parallel. Always off under "
            "claude_auth 'api_key', which cannot register a session. Deploys override it with "
            "SKULD__TMUX_REMOTE_CONTROL."
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
    presented_durable: PresentedDurableConfig = Field(default_factory=PresentedDurableConfig)
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)
    ws_auth: WsAuthConfig = Field(default_factory=WsAuthConfig)
    observation_relay: ObservationRelayConfig = Field(default_factory=ObservationRelayConfig)
    trace_context: TraceContextConfig = Field(default_factory=TraceContextConfig)
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
    dsh: DshRuntimeConfig = Field(default_factory=DshRuntimeConfig)
    model_gateway: ModelGatewayConfig = Field(default_factory=ModelGatewayConfig)

    pi: PiRuntimeConfig = Field(default_factory=PiRuntimeConfig)
    service_user_id: str = Field(default="skuld-broker")
    service_tenant_id: str = Field(default="default")
    persistence_mount_path: str = Field(default="/volundr/sessions")
    home_dir: str = Field(
        default="",
        description=(
            "The session's home directory (what the agent sees as ~). Empty derives the "
            "cluster layout {persistence_mount_path}/{session.id}/home. Local mode sets the "
            "HOME the broker actually runs with."
        ),
    )
    presented_files_dir: str = Field(
        default="",
        description=(
            "Broker-owned staging directory for present-file copies (outside the "
            "workspace, survives broker restarts). Empty uses {home}/.forge-presented."
        ),
    )
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
    activity_reporting_enabled: bool = Field(
        default=True,
        description=(
            "Report activity state to the Forge session "
            "(/api/v1/forge/sessions/<id>/activity). Off for a runtime that has "
            "no Forge session, such as a resident."
        ),
    )
    event_log_batch_size: int = Field(default=100)
    event_log_flush_interval_ms: int = Field(default=500)
    event_log_max_buffer: int = Field(
        default=50_000,
        gt=0,
        description=(
            "Most unflushed event-log frames the broker holds in memory while Forge "
            "does not accept a flush. Bounded by the broker's memory. Past it the oldest "
            "frames are dropped, replaced by one log_gap row, and the hit is counted "
            "(cap_hits.event_log_max_buffer on /health)."
        ),
    )
    # Native-session recovery restores the durable transcript before the CLI
    # starts, so reconnect snapshots include the imported conversation prefix.
    history_hydration_enabled: bool = Field(default=True)
    history_hydration_timeout_seconds: float = Field(default=30.0, gt=0)
    history_hydration_page_size: int = Field(default=1000, ge=1, le=5000)
    history_hydration_max_frames: int = Field(default=100_000, ge=1)
    history_hydration_max_bytes: int = Field(default=64 * 1024 * 1024, gt=0)
    # Leave room below clients with a 1 MiB WebSocket receive limit. Large
    # reconnect histories use the existing lazy tool-result preview contract.
    conversation_snapshot_max_bytes: int = Field(default=900 * 1024, gt=0)
    conversation_recent_max_turns: int = Field(
        default=15, gt=0, description="Recent reconnect window requested by history=recent clients."
    )
    conversation_recent_max_bytes: int = Field(
        default=256 * 1024, ge=1024, description="Byte budget for recent reconnect activity."
    )
    live_frame_max_bytes: int = Field(default=900 * 1024, ge=1024)
    history_read_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        description="Bounded wait for a coherent protocol2 snapshot; never blocks writers.",
    )
    history_bootstrap_max_frames: int = Field(
        default=256,
        gt=0,
        description="Maximum queued live frames while sending a protocol2 snapshot.",
    )
    # Native results are retained whole before the browser projection elides them.
    muse_bin: str = Field(
        default="",
        validation_alias=AliasChoices("muse_bin", "SKULD__MUSE_BIN", "MUSE_BIN"),
        description="Muse executable override; empty resolves muse on PATH.",
    )
    tmux_question_transcript_max_bytes: int = Field(
        default=1048576,
        ge=1,
        description="Maximum native transcript bytes inspected to confirm a Claude answer.",
    )
    tmux_question_result_history_limit: int = Field(
        default=128,
        ge=1,
        description="Maximum retained native question receipt identities for deduplication.",
    )
    tmux_workspace_trust_stable_s: float = Field(
        default=0.5,
        ge=0,
        description=(
            "How long Claude's workspace-trust menu must render unchanged before a key is "
            "sent (early keys are dropped while the CLI negotiates the terminal)."
        ),
    )
    tmux_workspace_trust_settle_s: float = Field(
        default=1.0,
        ge=0,
        description=(
            "Minimum time after a workspace-trust key before the next one; a menu still "
            "shown after it means the key was not consumed and is retried."
        ),
    )
    tmux_workspace_trust_max_attempts: int = Field(
        default=3,
        ge=1,
        description="Maximum navigation keys and, separately, confirmations for workspace trust.",
    )
    tmux_native_text_wait_s: float = Field(
        default=0.1,
        gt=0,
        description=(
            "Longest a PreToolUse hook waits for Claude's native transcript to show the "
            "text preceding that tool before falling back to captured hook order."
        ),
    )
    tmux_native_text_poll_s: float = Field(
        default=0.005,
        gt=0,
        description="Interval between native transcript reads while that wait is open.",
    )
    codex_receive_max_bytes: int = Field(default=16 * 1024 * 1024, ge=1024)
    # Unified internal-visibility default for a freshly-connected live channel
    # (SRD FR-7 / INV-10). The read paths thread the SAME configured default
    # (``ReplayConfig.default_show_internal`` in volundr); a live ``WebSocketChannel``
    # must read its default from ONE configured source too, not a hardcoded literal,
    # so all three paths (live channel, replay tail, cold-read) move together.
    # Default ``False`` (internal tool_use/tool_result HIDDEN), matching ReplayConfig.
    default_show_internal: bool = Field(default=False)
    max_upload_size_bytes: int = Field(default=104_857_600)  # 100 MB
    exit_when_transport_stops: bool = Field(
        default=True,
        description=(
            "When the agent's transport dies (Claude's pane exited, its tmux session is "
            "gone), report activity 'stopped' and exit, so the pod manager reconciles the "
            "session as stopped: Forge then refuses messages instead of queueing them for "
            "a corpse, and start launches a fresh agent (LEX-1699). false keeps the broker "
            "up, serving history only."
        ),
    )
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
    effort_control_timeout_s: float = Field(
        default=15.0,
        gt=0,
        description="Time allowed for a native effort or model change to be acknowledged.",
    )
    acp_prompt_timeout_s: float = Field(default=300.0)  # ACP/MSP (Grok Build, Muse) turn timeout
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)
    forge_mcp: ForgeMcpConfig = Field(default_factory=ForgeMcpConfig)
    preview: PreviewConfig = Field(default_factory=PreviewConfig)
    secret_requests: SecretRequestsConfig = Field(default_factory=SecretRequestsConfig)
    ask: AskConfig = Field(default_factory=AskConfig)
    reflex: ReflexConfig = Field(default_factory=ReflexConfig)
    observability: SkuldObservabilityConfig = Field(default_factory=SkuldObservabilityConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    room: RoomConfig = Field(default_factory=RoomConfig)
    mesh: MeshConfig = Field(default_factory=MeshConfig)
    workflow_steps: WorkflowStepsConfig = Field(default_factory=WorkflowStepsConfig)
    peer_inbox: PeerInboxConfig = Field(default_factory=PeerInboxConfig)

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

        if self.cli_type == "dsh":
            self.transport_adapter = "skuld.transports.dsh.DshJsonRpcTransport"
            return self

        if self.cli_type == "grok":
            self.transport_adapter = "skuld.transports.grok.GrokACPTransport"
            return self
        if self.cli_type == "muse":
            self.transport_adapter = "skuld.transports.muse.MuseMSPTransport"
            return self

        if self.transport == "subprocess":
            self.transport_adapter = "skuld.transports.subprocess.SubprocessTransport"
        elif self.transport == "tmux-interactive":
            self.transport_adapter = "skuld.transports.tmux_interactive.TmuxInteractiveTransport"

        return self

    @model_validator(mode="after")
    def _waits_fit_engine_call(self) -> "SkuldSettings":
        """A wait the engine cuts off mid-call would read as a failure, not as "still open"."""
        if not self.forge_mcp.enabled:
            return self
        for name, wait in (
            ("secret_requests.wait_max_s", self.secret_requests.wait_max_s),
            ("ask.wait_max_s", self.ask.wait_max_s),
        ):
            for knob, bound in (
                ("forge_mcp.broker_request_timeout_s", self.forge_mcp.broker_request_timeout_s),
                ("forge_mcp.tool_timeout_s", self.forge_mcp.tool_timeout_s),
            ):
                if wait >= bound:
                    raise ValueError(
                        f"{name} ({wait}) must be shorter than {knob} ({bound}), "
                        f"or the engine ends every long call before it can answer. "
                        f"Lower {name} or raise {knob}"
                    )
        return self

    @property
    def workspace_path(self) -> str:
        """Resolved workspace directory path."""
        if self.session.workspace_dir:
            return self.session.workspace_dir
        return f"{self.persistence_mount_path}/{self.session.id}/workspace"

    persistent_home_path: str = Field(
        default="",
        description="Mounted user home for file management; empty uses the session-local home.",
    )

    @property
    def home_path(self) -> str:
        """Resolved home directory path for the session."""
        if self.home_dir:
            return self.home_dir
        if self.persistent_home_path:
            return self.persistent_home_path
        return f"{self.persistence_mount_path}/{self.session.id}/home"

    @property
    def creates_session_home(self) -> bool:
        """Whether home is the cluster layout's directory beside the workspace.

        That is ``{persistence_mount_path}/{session.id}/home`` on the session volume, in
        the same session directory as the workspace, when neither a home volume nor a
        home directory is configured (a flock session mounts no home volume). The broker
        creates it as it creates the workspace; a configured home is never created, so a
        missing mount stays an error.
        """
        if self.home_dir or self.persistent_home_path:
            return False
        return Path(self.home_path).parent == Path(self.workspace_path).parent

    @property
    def presented_files_path(self) -> str:
        """Where present-file stages its copies."""
        if self.presented_files_dir:
            return self.presented_files_dir
        return f"{self.home_path}/.forge-presented"

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
