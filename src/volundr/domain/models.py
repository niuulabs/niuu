"""Domain models for Völundr session management."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from credentials.models import MCPServerConfig, MountType, SecretInfo, SecretMountSpec  # noqa: F401
from identity.models import (  # noqa: F401
    ProvisioningResult,
    QuotaCheck,
    StorageQuota,
    Tenant,
    TenantMembership,
    TenantRole,
    TenantTier,
    User,
    UserStatus,
)
from niuu.domain import models as shared_models
from tracker.models import ProjectMapping, TrackerConnectionStatus, TrackerIssue  # noqa: F401

CIStatus = shared_models.CIStatus
GitProviderType = shared_models.GitProviderType
IntegrationConnection = shared_models.IntegrationConnection
IntegrationType = shared_models.IntegrationType
Principal = shared_models.Principal
PullRequest = shared_models.PullRequest
PullRequestStatus = shared_models.PullRequestStatus
RepoInfo = shared_models.RepoInfo
ReviewStatus = shared_models.ReviewStatus
SecretType = shared_models.SecretType
StoredCredential = shared_models.StoredCredential


def _utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


def flock_peer_id(persona: str) -> str:
    """Mesh peer id for a flock ravn running *persona*.

    The single source of truth for this naming convention: the flock pod
    builder and the Skuld relay's routing target must agree, or operator
    chat and event relay silently target a participant that never
    registered. Keep every derivation routed through here.
    """
    return f"flock-{persona}"


class CleanupTarget(StrEnum):
    """Resources that can optionally be cleaned up when deleting a session."""

    WORKSPACE_STORAGE = "workspace_storage"
    CHRONICLES = "chronicles"


class SessionStatus(StrEnum):
    """Status of a coding session."""

    CREATED = "created"
    STARTING = "starting"
    PROVISIONING = "provisioning"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    ARCHIVED = "archived"


class SessionActivityState(StrEnum):
    """Activity state of a running session (orthogonal to lifecycle status).

    This is the "what is the agent doing right now" axis, distinct from the
    lifecycle ``status`` (which only tracks whether the container is up):

    - ``provisioning`` — the session is booting; the CLI REPL is not ready yet.
      The first real frame (system/init) flips it to ``idle``.
    - ``active`` / ``tool_executing`` — the agent is progressing (thinking,
      streaming, or running a tool). Both mean "busy".
    - ``idle`` — the turn finished; the session is waiting for the user's next
      message but is NOT blocked on anything.
    - ``stopped`` — the transport died (e.g. its tmux session vanished); the
      session is no longer running and the last live state is final.
    - ``error`` — an unattended workflow reached a terminal agent/transport
      error. The workload may remain up long enough to report diagnostics, but
      downstream workflow state must treat this as failed.
    - ``awaiting_input`` — the session is BLOCKED on a human: an
      ``AskUserQuestion``, a confirmation, or a tool-permission prompt. This is
      the "needs attention" state — the agent cannot make progress until the
      user responds. ``activity_metadata`` carries the specifics via
      ``kind`` (``question`` | ``confirmation`` | ``permission``), ``prompt``,
      ``options``, and ``request_id``.
    """

    PROVISIONING = "provisioning"
    ACTIVE = "active"
    IDLE = "idle"
    TOOL_EXECUTING = "tool_executing"
    AWAITING_INPUT = "awaiting_input"
    STOPPED = "stopped"
    ERROR = "error"

    @property
    def is_busy(self) -> bool:
        """True when the agent is actively progressing (not idle, not blocked)."""
        return self in (SessionActivityState.ACTIVE, SessionActivityState.TOOL_EXECUTING)

    @property
    def needs_attention(self) -> bool:
        """True when the session is blocked waiting on the user."""
        return self is SessionActivityState.AWAITING_INPUT


class EventType(StrEnum):
    """Type of real-time event."""

    SESSION_CREATED = "session_created"
    SESSION_UPDATED = "session_updated"
    SESSION_DELETED = "session_deleted"
    STATS_UPDATED = "stats_updated"
    HEARTBEAT = "heartbeat"
    CHRONICLE_CREATED = "chronicle_created"
    CHRONICLE_UPDATED = "chronicle_updated"
    CHRONICLE_DELETED = "chronicle_deleted"
    CHRONICLE_EVENT = "chronicle_event"
    PR_CREATED = "pr_created"
    PR_MERGED = "pr_merged"
    SESSION_ACTIVITY = "session_activity"
    SESSION_NEEDS_INPUT = "session_needs_input"


class CommunicationPlatform(StrEnum):
    """External communication platform."""

    TELEGRAM = "telegram"
    SLACK = "slack"
    DISCORD = "discord"
    WHATSAPP = "whatsapp"


class CommunicationRouteMode(StrEnum):
    """How inbound communication should enter the target session."""

    ROOM = "room"


class ModelProvider(StrEnum):
    """Provider type for LLM models."""

    CLOUD = "cloud"
    LOCAL = "local"


class ModelTier(StrEnum):
    """Tier classification for LLM models."""

    FRONTIER = "frontier"
    BALANCED = "balanced"
    EXECUTION = "execution"
    REASONING = "reasoning"


@dataclass(frozen=True)
class Model:
    """An available LLM model with pricing and metadata."""

    id: str
    name: str
    description: str
    provider: ModelProvider
    vendor: str
    tier: ModelTier
    color: str
    cost_per_million_tokens: float | None = None
    vram_required: str | None = None
    session_definition: str | None = None


@dataclass(frozen=True)
class TokenUsageRecord:
    """A record of token usage for a session."""

    id: UUID
    session_id: UUID
    recorded_at: datetime
    tokens: int
    provider: ModelProvider
    model: str
    cost: Decimal | None


@dataclass(frozen=True)
class Stats:
    """Aggregate statistics for the dashboard."""

    active_sessions: int
    total_sessions: int
    tokens_today: int
    local_tokens: int
    cloud_tokens: int
    cost_today: Decimal
    sessions_today: int = 0
    sparklines: dict[str, list[float]] | None = None


@dataclass(frozen=True)
class RealtimeEvent:
    """A real-time event for SSE streaming."""

    type: EventType
    data: dict
    timestamp: datetime


class DevicePlatform(StrEnum):
    """Platform a registered push device belongs to."""

    IOS = "ios"
    ANDROID = "android"
    WEB = "web"


class DeviceToken(BaseModel):
    """A user's registered device for push notifications.

    Stored per-owner so a session that needs attention can fan a push out to
    every device the owner has registered (the iOS app/widget, etc.).
    """

    id: UUID = Field(default_factory=uuid4)
    owner_id: str = Field(description="User ID (IDP sub) the device belongs to")
    platform: DevicePlatform = Field(description="Device platform")
    token: str = Field(
        min_length=1,
        max_length=512,
        description="APNs device token, FCM token, or web-push endpoint",
    )
    app_bundle_id: str | None = Field(
        default=None,
        description="APNs topic (bundle id) to target; falls back to the channel default",
    )
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    model_config = {"frozen": False}


@dataclass(frozen=True)
class PushMessage:
    """A user-facing push built from a session attention signal."""

    owner_id: str
    title: str
    body: str
    session_id: str
    #: question | confirmation | permission
    kind: str
    urgency: float
    request_id: str = ""


class GitSource(BaseModel):
    """Git repository workspace source."""

    type: Literal["git"] = "git"
    repo: str = Field(
        default="",
        max_length=500,
        description="Git repository URL or shorthand (e.g. github.com/org/repo)",
    )
    branch: str = Field(
        default="main",
        max_length=255,
        description="Git branch to checkout",
    )
    base_branch: str = Field(
        default="",
        max_length=255,
        description="Branch to create feature branch from if it doesn't exist",
    )

    model_config = {"frozen": False}


class MountMapping(BaseModel):
    """A single host-to-container path mapping."""

    host_path: str = Field(
        ...,
        min_length=1,
        description="Absolute path on the host node filesystem",
    )
    mount_path: str = Field(
        ...,
        min_length=1,
        description="Absolute path inside the session container",
    )
    read_only: bool = Field(
        default=True,
        description="Whether the mount is read-only",
    )

    model_config = {"frozen": False}


class LocalMountSource(BaseModel):
    """Local filesystem mount workspace source.

    In mini/local mode, only ``local_path`` is needed — the CLI runs
    Claude directly in that directory.  In cluster mode, ``paths``
    provides host-to-container volume mappings.
    """

    type: Literal["local_mount"] = "local_mount"
    local_path: str = Field(
        default="",
        description="Absolute path to use as workspace directly (mini/local mode)",
    )
    paths: list[MountMapping] = Field(
        default_factory=list,
        description="Host-to-container path mappings for the workspace (cluster mode)",
    )
    node_selector: dict[str, str] = Field(
        default_factory=dict,
        description="Kubernetes node selector labels to schedule on a specific node",
    )

    @model_validator(mode="after")
    def _require_path_or_paths(self) -> LocalMountSource:
        if not self.local_path and not self.paths:
            raise ValueError("Either local_path or paths must be provided")
        return self

    model_config = {"frozen": False}


SessionSource = Annotated[
    GitSource | LocalMountSource,
    Field(discriminator="type"),
]


class Session(BaseModel):
    """A Claude Code coding session."""

    id: UUID = Field(
        default_factory=uuid4,
        description="Unique session identifier",
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Human-readable session name",
    )
    model: str = Field(
        default="",
        max_length=100,
        description="LLM model identifier used by the session",
    )
    source: SessionSource = Field(
        default_factory=GitSource,
        description="Workspace source configuration (git or local mount)",
    )
    status: SessionStatus = Field(
        default=SessionStatus.CREATED,
        description="Current lifecycle status of the session",
    )
    chat_endpoint: str | None = Field(
        default=None,
        description="URL for the Skuld chat proxy when running",
    )
    code_endpoint: str | None = Field(
        default=None,
        description="URL for the editor IDE when running",
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        description="Timestamp when the session was created",
    )
    updated_at: datetime = Field(
        default_factory=_utc_now,
        description="Timestamp of the last session update",
    )
    last_active: datetime | None = Field(
        default=None,
        description="Timestamp of the last activity",
    )
    message_count: int = Field(
        default=0,
        ge=0,
        description="Total number of chat messages exchanged",
    )
    tokens_used: int = Field(
        default=0,
        ge=0,
        description="Total token count consumed by the session",
    )
    pod_name: str | None = Field(
        default=None,
        description="Kubernetes pod name when the session is running",
    )
    error: str | None = Field(
        default=None,
        description="Error message if the session is in a failed state",
    )
    tracker_issue_id: str | None = Field(
        default=None,
        description="Linked issue tracker issue identifier",
    )
    issue_tracker_url: str | None = Field(
        default=None,
        description="Web URL for the linked issue in the tracker",
    )
    launch_spec_id: UUID | None = Field(
        default=None,
        description="User launch spec used to configure this session",
    )
    archived_at: datetime | None = Field(
        default=None,
        description="Timestamp when the session was archived",
    )
    owner_id: str | None = Field(
        default=None,
        description="User ID of the session owner (IDP sub claim)",
    )
    tenant_id: str | None = Field(
        default=None,
        description="Tenant ID for multi-tenant isolation",
    )
    workspace_id: UUID | None = Field(
        default=None,
        description="Workspace PVC identifier for storage isolation",
    )
    activity_state: SessionActivityState | None = Field(
        default=None,
        description=(
            "Current activity state "
            "(provisioning/active/idle/tool_executing/awaiting_input/stopped)"
        ),
    )
    activity_state_since: datetime | None = Field(
        default=None,
        description=(
            "Timestamp (UTC) when the session ENTERED its current activity_state. "
            "Stamped only on a real state change (not on re-asserting the same "
            "state), so clients can render an accurate 'active for Ns' elapsed."
        ),
    )
    activity_metadata: dict = Field(
        default_factory=dict,
        description="Metadata from the latest activity report",
    )
    workload_type: str = Field(
        default="session",
        description="Workload type used to launch the session",
    )
    workload_config: dict = Field(
        default_factory=dict,
        description=(
            "Workload config used to launch the session (persisted so "
            "workload identity — e.g. a flock's personas — survives "
            "restarts). Internal: may carry auth refs; never expose raw "
            "through the API."
        ),
    )
    origin: str = Field(
        default="volundr",
        max_length=50,
        description="Where the session originated (volundr, claude, codex)",
    )
    external_session_id: str | None = Field(
        default=None,
        max_length=255,
        description="Native CLI session/thread id for imported sessions",
    )
    cli_session_id: str | None = Field(
        default=None,
        max_length=255,
        description=(
            "CLI/agent conversation id (Claude session UUID or Codex thread id) "
            "captured at runtime, used to resume the prior conversation when the "
            "session is restarted."
        ),
    )
    session_definition: str | None = Field(
        default=None,
        max_length=255,
        description=(
            "Session definition (runtime type, e.g. skuldClaude / skuldGrok) the "
            "session was launched with, persisted so restarts re-apply the same "
            "transport instead of falling back to the platform default."
        ),
    )

    model_config = {"frozen": False}

    @property
    def needs_attention(self) -> bool:
        """True when the session is blocked waiting on the user (awaiting_input).

        Surfaced directly so clients (web badges, the iOS widget) and the REST
        list endpoint can filter "this session needs you" without re-deriving
        the rule from ``activity_state``.
        """
        return self.activity_state is SessionActivityState.AWAITING_INPUT

    @property
    def repo(self) -> str:
        """Repository URL from git source, or empty string for non-git sources."""
        if isinstance(self.source, GitSource):
            return self.source.repo
        return ""

    @property
    def branch(self) -> str:
        """Branch from git source, or empty string for non-git sources."""
        if isinstance(self.source, GitSource):
            return self.source.branch
        return ""

    def model_post_init(self, __context) -> None:
        """Initialize last_active to created_at if not set."""
        if self.last_active is None:
            object.__setattr__(self, "last_active", self.created_at)

    def can_start(self) -> bool:
        """Check if session can be started."""
        return self.status in (SessionStatus.CREATED, SessionStatus.STOPPED, SessionStatus.FAILED)

    def can_stop(self) -> bool:
        """Check if session can be stopped."""
        return self.status in (
            SessionStatus.STARTING,
            SessionStatus.RUNNING,
            SessionStatus.PROVISIONING,
        )

    def with_status(self, status: SessionStatus) -> Session:
        """Return a copy with updated status and timestamp."""
        return self.model_copy(update={"status": status, "updated_at": datetime.now(UTC)})

    def with_endpoints(self, chat_endpoint: str, code_endpoint: str) -> Session:
        """Return a copy with updated endpoints and timestamp."""
        return self.model_copy(
            update={
                "chat_endpoint": chat_endpoint,
                "code_endpoint": code_endpoint,
                "updated_at": datetime.now(UTC),
            }
        )

    def with_cleared_endpoints(self) -> Session:
        """Return a copy with cleared endpoints and timestamp."""
        return self.model_copy(
            update={
                "chat_endpoint": None,
                "code_endpoint": None,
                "updated_at": datetime.now(UTC),
            }
        )

    def with_pod_name(self, pod_name: str) -> Session:
        """Return a copy with updated pod_name and timestamp."""
        return self.model_copy(
            update={
                "pod_name": pod_name,
                "updated_at": datetime.now(UTC),
            }
        )

    def with_error(self, error: str) -> Session:
        """Return a copy with error message and timestamp."""
        return self.model_copy(
            update={
                "error": error,
                "updated_at": datetime.now(UTC),
            }
        )

    def with_activity(self, message_count: int, tokens: int) -> Session:
        """Return a copy with updated activity metrics and timestamp."""
        now = datetime.now(UTC)
        return self.model_copy(
            update={
                "message_count": message_count,
                "tokens_used": tokens,
                "last_active": now,
                "updated_at": now,
            }
        )


class ExternalSessionRecord(BaseModel):
    """A CLI session discovered outside Volundr (Claude Code, Codex, ...).

    External sessions live in the harness's own on-disk store (e.g.
    ``~/.claude/projects`` or ``~/.codex/sessions``). They can be imported
    into Volundr, which creates a stopped Session that resumes the native
    session when started.
    """

    provider: str = Field(description="Provider key that discovered the session")
    harness: str = Field(description="CLI harness that owns the session (claude, codex)")
    external_id: str = Field(description="Native session/thread identifier")
    workspace_path: str = Field(
        default="",
        description="Working directory the session ran in",
    )
    title: str = Field(
        default="",
        description="Short human-readable summary (first prompt or similar)",
    )
    model: str = Field(
        default="",
        description="Model the session used, when known",
    )
    created_at: datetime | None = Field(
        default=None,
        description="When the session started, when known",
    )
    updated_at: datetime | None = Field(
        default=None,
        description="Last activity in the session store",
    )
    live: bool = Field(
        default=False,
        description="Whether the session appears to be actively running",
    )
    workspace_exists: bool = Field(
        default=False,
        description="Whether the workspace directory still exists on disk",
    )
    workspace_allowed: bool = Field(
        default=True,
        description="Whether the workspace passes the allowed mount prefix policy",
    )
    imported_session_id: UUID | None = Field(
        default=None,
        description="Volundr session id when this session was already imported",
    )


@dataclass(frozen=True)
class CommunicationRoute:
    """Route an external conversation/thread back to a live session."""

    id: UUID
    platform: CommunicationPlatform
    conversation_id: str
    thread_id: str | None
    session_id: UUID
    owner_id: str
    mode: CommunicationRouteMode = CommunicationRouteMode.ROOM
    default_target: str | None = None
    active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True)
class InboundCommunicationMessage:
    """Normalized inbound human message from an external communication channel."""

    platform: CommunicationPlatform
    conversation_id: str
    thread_id: str | None
    sender_external_id: str
    sender_display_name: str
    text: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RoomParticipantInfo:
    """Minimal participant information for dynamic targeting."""

    peer_id: str
    persona: str
    display_name: str = ""
    participant_type: str = "ravn"
    status: str = "idle"


@dataclass(frozen=True)
class SessionCommunicationTarget:
    """Resolved external communication target exposed by a live session."""

    platform: CommunicationPlatform
    conversation_id: str
    thread_id: str | None = None
    mode: CommunicationRouteMode = CommunicationRouteMode.ROOM
    default_target: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class TimelineEventType(StrEnum):
    """Type of timeline event within a chronicle."""

    SESSION = "session"
    MESSAGE = "message"
    FILE = "file"
    GIT = "git"
    TERMINAL = "terminal"
    ERROR = "error"


@dataclass(frozen=True)
class TimelineEvent:
    """A single event in a chronicle's timeline."""

    id: UUID
    chronicle_id: UUID
    session_id: UUID
    t: int  # seconds elapsed since session start
    type: TimelineEventType
    label: str
    tokens: int | None = None
    action: str | None = None  # created, modified, deleted (file events)
    ins: int | None = None
    del_: int | None = None
    hash: str | None = None  # short commit hash (git events)
    exit_code: int | None = None  # terminal events
    created_at: datetime | None = None


@dataclass(frozen=True)
class FileSummary:
    """Aggregated file change summary for a timeline."""

    path: str
    status: str  # new, mod, del
    ins: int
    del_: int


@dataclass(frozen=True)
class CommitSummary:
    """Commit summary for a timeline."""

    hash: str
    msg: str
    time: str  # wall clock time, e.g. "14:35"


@dataclass(frozen=True)
class TimelineResponse:
    """Full timeline response for a session chronicle."""

    events: list[TimelineEvent]
    files: list[FileSummary]
    commits: list[CommitSummary]
    token_burn: list[int]


class ChronicleStatus(StrEnum):
    """Status of a chronicle entry."""

    DRAFT = "draft"
    COMPLETE = "complete"


class Chronicle(BaseModel):
    """A chronicle entry capturing session metadata for continuity."""

    id: UUID = Field(
        default_factory=uuid4,
        description="Unique chronicle identifier",
    )
    session_id: UUID | None = Field(
        default=None,
        description="Session that produced this chronicle entry",
    )
    status: ChronicleStatus = Field(
        default=ChronicleStatus.DRAFT,
        description="Whether the chronicle is draft or complete",
    )
    project: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Project name derived from the repository",
    )
    repo: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Git repository URL",
    )
    branch: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Git branch used during the session",
    )
    model: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="LLM model used during the session",
    )
    config_snapshot: dict = Field(
        default_factory=dict,
        description="Snapshot of the session configuration at creation time",
    )
    summary: str | None = Field(
        default=None,
        description="AI-generated summary of what was accomplished",
    )
    key_changes: list[str] = Field(
        default_factory=list,
        description="List of significant changes made during the session",
    )
    unfinished_work: str | None = Field(
        default=None,
        description="Description of work left incomplete for the next session",
    )
    token_usage: int = Field(
        default=0,
        ge=0,
        description="Total tokens consumed during the session",
    )
    cost: Decimal | None = Field(
        default=None,
        description="Estimated cost in USD for cloud model usage",
    )
    duration_seconds: int | None = Field(
        default=None,
        ge=0,
        description="Wall-clock duration of the session in seconds",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="User-defined tags for categorization and filtering",
    )
    parent_chronicle_id: UUID | None = Field(
        default=None,
        description="Parent chronicle ID for reforge chains",
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        description="Timestamp when the chronicle was created",
    )
    updated_at: datetime = Field(
        default_factory=_utc_now,
        description="Timestamp of the last chronicle update",
    )

    model_config = {"frozen": False}


@dataclass(frozen=True)
class MergeConfidence:
    """Confidence assessment for merging a pull request."""

    score: float  # 0.0 - 1.0
    factors: dict[str, float]
    action: str  # "auto_merge", "notify_then_merge", "require_approval"
    reason: str


class SessionEventType(StrEnum):
    """Type of raw session event for the event pipeline."""

    MESSAGE_USER = "message_user"
    MESSAGE_ASSISTANT = "message_assistant"
    OUTCOME = "outcome"
    FILE_CREATED = "file_created"
    FILE_MODIFIED = "file_modified"
    FILE_DELETED = "file_deleted"
    GIT_COMMIT = "git_commit"
    GIT_PUSH = "git_push"
    GIT_BRANCH = "git_branch"
    GIT_CHECKOUT = "git_checkout"
    TERMINAL_COMMAND = "terminal_command"
    TOOL_USE = "tool_use"
    ERROR = "error"
    TOKEN_USAGE = "token_usage"
    SESSION_START = "session_start"
    SESSION_STOP = "session_stop"


class SessionSpanStatus(StrEnum):
    """Lifecycle status for a trace span."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class SessionEvent:
    """A raw event from a session, dispatched through the event pipeline.

    Data payloads by event_type (not enforced by schema):

    message_user:      {"content_length": int, "content_preview": str}
    message_assistant:  {"content_length": int, "content_preview": str, "finish_reason": str}
    outcome:           {"persona": str, "event_type": str, "fields": dict, "valid": bool}
    file_created:       {"path": str, "size_bytes": int}
    file_modified:      {"path": str, "insertions": int, "deletions": int}
    file_deleted:       {"path": str}
    git_commit:         {"hash": str, "message": str, "files_changed": int}
    git_push:           {"branch": str, "commits_count": int, "remote": str}
    git_branch:         {"name": str, "from_branch": str}
    git_checkout:       {"branch": str}
    terminal_command:   {"command": str, "exit_code": int, "duration_ms": int}
    tool_use:           {"tool": str, "arguments_preview": str, "duration_ms": int}
    error:              {"source": str, "message": str}
    token_usage:        {"provider": str, "model": str, "tokens_in": int, "tokens_out": int}
    session_start:      {"model": str, "repo": str, "branch": str}
    session_stop:       {"reason": str, "total_tokens": int}
    """

    id: UUID
    session_id: UUID
    event_type: SessionEventType
    timestamp: datetime
    data: dict
    sequence: int
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost: Decimal | None = None
    duration_ms: int | None = None
    model: str | None = None


@dataclass(frozen=True)
class SessionLogEntry:
    """One full-fidelity, append-only frame in a session's durable event log.

    Unlike :class:`SessionEvent` (analytics previews), this preserves the
    COMPLETE agent output verbatim — assistant text, thinking, tool_use,
    tool_result, deltas — ordered by a monotonic per-session ``seq``. It is the
    source of truth for transcript replay: any client can resume from a cursor
    (``seq``) and reconstruct the full conversation regardless of whether a
    socket was attached when the frames were produced.

    ``kind`` is the open-vocabulary wire frame type (e.g. ``assistant``,
    ``content_block_delta``, ``tool_use``, ``tool_result``, ``result``,
    ``user``). ``payload`` is the raw frame, preserved without truncation.
    ``request_id`` correlates frames to the turn that produced them.
    """

    session_id: UUID
    seq: int
    kind: str
    payload: dict
    ts: datetime
    role: str | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class SessionSpan:
    """A hierarchical timing span recorded for a Volundr session."""

    id: UUID
    session_id: UUID
    trace_id: UUID
    kind: str
    name: str
    started_at: datetime
    source_service: str
    parent_span_id: UUID | None = None
    status: SessionSpanStatus = SessionSpanStatus.RUNNING
    ended_at: datetime | None = None
    duration_ms: int | None = None
    actor_type: str | None = None
    actor_id: str | None = None
    actor_label: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


class PromptScope(StrEnum):
    """Scope of a saved prompt."""

    GLOBAL = "global"
    PROJECT = "project"


class SavedPrompt(BaseModel):
    """A reusable saved prompt."""

    id: UUID = Field(
        default_factory=uuid4,
        description="Unique prompt identifier",
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Human-readable prompt name",
    )
    content: str = Field(
        ...,
        min_length=1,
        description="The prompt text content",
    )
    scope: PromptScope = Field(
        default=PromptScope.GLOBAL,
        description="Visibility scope: global or project-specific",
    )
    project_repo: str | None = Field(
        default=None,
        max_length=500,
        description="Repository URL when scope is project",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Tags for categorization and search",
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        description="Timestamp when the prompt was created",
    )
    updated_at: datetime = Field(
        default_factory=_utc_now,
        description="Timestamp of the last prompt update",
    )

    model_config = {"frozen": False}


@dataclass(frozen=True)
class MCPServerSpec:
    """MCP server specification for an integration.

    Describes how to launch an MCP server when a session uses this
    integration. Credential fields are mapped to environment variables
    via ``env_from_credentials``.
    """

    name: str
    command: str
    args: tuple[str, ...] = ()
    env_from_credentials: dict[str, str] = ()  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if not isinstance(self.args, tuple):
            object.__setattr__(self, "args", tuple(self.args))
        if not isinstance(self.env_from_credentials, dict):
            object.__setattr__(
                self,
                "env_from_credentials",
                dict(self.env_from_credentials),
            )


@dataclass(frozen=True)
class OAuthSpec:
    """OAuth2 provider specification — all URLs and params needed for a flow."""

    authorize_url: str
    token_url: str
    revoke_url: str = ""
    scopes: tuple[str, ...] = ()
    token_field_mapping: dict[str, str] = ()  # type: ignore[assignment]
    extra_authorize_params: dict[str, str] = ()  # type: ignore[assignment]
    extra_token_params: dict[str, str] = ()  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if not isinstance(self.scopes, tuple):
            object.__setattr__(self, "scopes", tuple(self.scopes))
        if not isinstance(self.token_field_mapping, dict):
            object.__setattr__(self, "token_field_mapping", dict(self.token_field_mapping))
        if not isinstance(self.extra_authorize_params, dict):
            object.__setattr__(self, "extra_authorize_params", dict(self.extra_authorize_params))
        if not isinstance(self.extra_token_params, dict):
            object.__setattr__(self, "extra_token_params", dict(self.extra_token_params))


@dataclass(frozen=True)
class IntegrationDefinition:
    """A known integration type in the catalog.

    Describes what an integration is, how to instantiate its adapter,
    what credentials it needs, and optionally an MCP server spec.

    ``env_from_credentials`` maps environment variable names to credential
    field names for non-MCP integrations (e.g. ``{"ANTHROPIC_API_KEY": "api_key"}``).
    For MCP integrations use ``MCPServerSpec.env_from_credentials`` instead.
    """

    slug: str
    name: str
    description: str
    integration_type: IntegrationType
    adapter: str  # fully-qualified class path
    icon: str = ""
    credential_schema: dict = ()  # type: ignore[assignment]
    config_schema: dict = ()  # type: ignore[assignment]
    mcp_server: MCPServerSpec | None = None
    env_from_credentials: dict[str, str] = ()  # type: ignore[assignment]
    auth_type: str = "api_key"
    oauth: OAuthSpec | None = None
    file_mounts: dict[str, str] = ()  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if not isinstance(self.credential_schema, dict):
            object.__setattr__(self, "credential_schema", dict(self.credential_schema))
        if not isinstance(self.config_schema, dict):
            object.__setattr__(self, "config_schema", dict(self.config_schema))
        if not isinstance(self.env_from_credentials, dict):
            object.__setattr__(self, "env_from_credentials", dict(self.env_from_credentials))
        if not isinstance(self.file_mounts, dict):
            object.__setattr__(self, "file_mounts", dict(self.file_mounts))


@dataclass(frozen=True)
class CredentialMapping:
    """Maps a stored credential to its injection targets.

    Used by SecretInjectionPort to build agent templates that render
    credential fields directly to env vars and/or file paths.

    ``env_mappings``: ``{ENV_VAR_NAME: credential_field_name}``
    ``file_mappings``: ``{target_file_path: credential_field_name}``
    """

    credential_name: str
    env_mappings: dict[str, str] = ()  # type: ignore[assignment]
    file_mappings: dict[str, str] = ()  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if not isinstance(self.env_mappings, dict):
            object.__setattr__(self, "env_mappings", dict(self.env_mappings))
        if not isinstance(self.file_mappings, dict):
            object.__setattr__(self, "file_mappings", dict(self.file_mappings))


@dataclass(frozen=True)
class PodSpecAdditions:
    """Pod spec contributions for secret injection.

    Returned by SecretInjectionPort adapters to tell the orchestrator
    how to configure volumes, mounts, labels, and annotations.
    Volundr never sees secret values.
    """

    volumes: tuple[dict, ...] = ()
    volume_mounts: tuple[dict, ...] = ()
    labels: dict[str, str] = ()  # type: ignore[assignment]
    annotations: dict[str, str] = ()  # type: ignore[assignment]
    env: tuple[dict, ...] = ()
    service_account: str | None = None
    extra_containers: tuple[dict, ...] = ()
    init_containers: tuple[dict, ...] = ()

    def __post_init__(self) -> None:
        # Ensure dict fields default to empty dicts, not empty tuples
        if not isinstance(self.labels, dict):
            object.__setattr__(self, "labels", {})
        if not isinstance(self.annotations, dict):
            object.__setattr__(self, "annotations", {})


@dataclass(frozen=True)
class WorkloadPersonaOverride:
    """Typed helper for per-persona overrides in workload_config.

    Callers can build these instead of raw dicts.  The ``llm`` dict is
    merged with global/default LLM config via ``niuu.domain.llm_merge.merge_llm``.
    """

    name: str
    llm: dict[str, Any] = field(default_factory=dict)
    system_prompt_extra: str | None = None
    iteration_budget: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the wire dict format consumed by workload_config."""
        d: dict[str, Any] = {"name": self.name}
        if self.llm:
            d["llm"] = dict(self.llm)
        if self.system_prompt_extra:
            d["system_prompt_extra"] = self.system_prompt_extra
        if self.iteration_budget is not None:
            d["iteration_budget"] = self.iteration_budget
        return d


@dataclass(frozen=True)
class SecretProfile:
    """Named collection of secret mount specs for a tenant or user."""

    owner_id: str
    owner_type: str  # "tenant" or "user"
    mounts: tuple[SecretMountSpec, ...] = ()


class WorkspaceStatus(StrEnum):
    """Lifecycle status of a workspace."""

    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


@dataclass(frozen=True)
class Workspace:
    """A per-session workspace with storage isolation."""

    id: UUID
    session_id: UUID
    user_id: str
    tenant_id: str
    pvc_name: str
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE
    size_gb: int = 1
    created_at: datetime | None = None
    archived_at: datetime | None = None
    deleted_at: datetime | None = None
    name: str | None = None
    source_url: str | None = None
    source_ref: str | None = None


# Kubernetes label keys used for PVC isolation (Kyverno policy enforcement).
# Keep in sync with charts/volundr/templates/kyverno-pvc-isolation.yaml.
LABEL_OWNER = "volundr/owner"
LABEL_SESSION_ID = "volundr/session-id"
LABEL_PVC_TYPE = "volundr/pvc-type"
LABEL_TENANT_ID = "volundr/tenant-id"
LABEL_MANAGED_BY = "app.kubernetes.io/managed-by"
LABEL_WORKSPACE_STATUS = "volundr/workspace-status"

# Annotation keys for workspace metadata that may exceed label limits.
ANNOT_WORKSPACE_NAME = "volundr/workspace-name"
ANNOT_WORKSPACE_SOURCE_URL = "volundr/workspace-source-url"
ANNOT_WORKSPACE_SOURCE_REF = "volundr/workspace-source-ref"


@dataclass(frozen=True)
class PVCRef:
    """Reference to a Kubernetes PersistentVolumeClaim."""

    name: str
    namespace: str = "volundr-sessions"


class LaunchScope(StrEnum):
    """Ownership/mutability scope for a launch spec."""

    SYSTEM = "system"  # config-seeded, read-only (replaces profiles + templates)
    USER = "user"  # DB-stored, CRUD-managed (replaces presets)


class LaunchSpec(BaseModel):
    """Unified blueprint for launching a session.

    The single concept that replaces the former ForgeProfile / WorkspaceTemplate
    / Preset trio. A ``system`` spec is config-seeded and read-only; a ``user``
    spec is DB-stored and fully CRUD-managed. ``session-definition`` (the runtime
    type) remains a separate concept that a launch spec references.
    """

    name: str = Field(..., min_length=1, max_length=255, description="Reference key")
    scope: LaunchScope = Field(default=LaunchScope.SYSTEM, description="Ownership scope")
    id: UUID | None = Field(default=None, description="Stable id (user scope; DB)")
    description: str = Field(default="", description="Human-readable description")
    is_default: bool = Field(default=False, description="Default within its scope")
    session_definition: str | None = Field(
        default=None, description="Session definition (runtime type) to launch on"
    )
    workload_type: str = Field(default="session", description="Workload type")
    # Runtime config (the fields the contributor pipeline merges)
    model: str | None = Field(default=None, max_length=100, description="LLM model id")
    system_prompt: str | None = Field(default=None, description="System prompt")
    resource_config: dict = Field(default_factory=dict, description="cpu/memory/gpu")
    mcp_servers: list[dict] = Field(default_factory=list, description="MCP servers")
    env_vars: dict[str, str] = Field(default_factory=dict, description="Env vars")
    env_secret_refs: list[str] = Field(default_factory=list, description="Secret refs")
    workload_config: dict = Field(default_factory=dict, description="Workload config")
    # Workspace
    repos: list[dict] = Field(default_factory=list, description="Git repos to clone")
    source: SessionSource | None = Field(default=None, description="Workspace source")
    setup_scripts: list[str] = Field(default_factory=list, description="Setup scripts")
    workspace_layout: dict = Field(default_factory=dict, description="Layout config")
    # User-scope extras (from preset)
    cli_tool: str = Field(default="", description="Target CLI tool")
    terminal_sidecar: dict = Field(default_factory=dict, description="Terminal sidecar")
    skills: list[dict] = Field(default_factory=list, description="Skill definitions")
    rules: list[dict] = Field(default_factory=list, description="Rule definitions")
    integration_ids: list[str] = Field(default_factory=list, description="Integrations")
    created_at: datetime = Field(default_factory=_utc_now, description="Created")
    updated_at: datetime = Field(default_factory=_utc_now, description="Updated")

    # Serialize/accept camelCase over the wire (web-next convention) while keeping
    # snake_case attribute access internally.
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ResidentBackend(StrEnum):
    """Infrastructure substrate that owns a resident runtime."""

    HELMRELEASE = "helmrelease"
    OPENSHELL = "openshell"
    LOCAL = "local"


class ResidentEngine(StrEnum):
    """Agent protocol hosted by a resident runtime."""

    RAVN = "ravn"
    OPENCLAW = "openclaw"
    HERMES = "hermes"


class ResidentDesiredState(StrEnum):
    """Operator-requested resident lifecycle state."""

    RUNNING = "running"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class ResidentObservedState(StrEnum):
    """State last observed from the owning deployment backend."""

    PENDING = "pending"
    DEPLOYING = "deploying"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    FAILED = "failed"
    DELETING = "deleting"


class ResidentCapability(StrEnum):
    """Operations a resident runtime can actually perform."""

    CHAT = "chat"
    SESSION_LIST = "session.list"
    SESSION_CREATE = "session.create"
    SESSION_DELETE = "session.delete"
    STEER = "steer"
    INTERRUPT = "interrupt"
    APPROVALS = "approvals"
    RUNTIME_RESTART = "runtime.restart"
    RUNTIME_SUSPEND = "runtime.suspend"
    LOGS = "logs"
    METRICS = "metrics"
    USAGE = "usage"
    FLOCK = "flock"


class ResidentConditionStatus(StrEnum):
    """Tri-state status for a reconciled resident condition."""

    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class ResidentEndpoint(BaseModel):
    """One normalized endpoint exposed by a resident runtime."""

    kind: str = Field(min_length=1, max_length=64)
    protocol: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1)

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ResidentCondition(BaseModel):
    """Actionable lifecycle condition reported by a deployment backend."""

    type: str = Field(min_length=1, max_length=100)
    status: ResidentConditionStatus
    reason: str = Field(default="", max_length=255)
    message: str = ""
    last_transition_at: datetime = Field(default_factory=_utc_now)

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ResidentLogEntry(BaseModel):
    """One normalized backend log entry for a resident runtime."""

    timestamp_ms: int = Field(ge=0)
    level: str = ""
    source: str = ""
    target: str = ""
    message: str = ""
    fields: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ResidentLogPage(BaseModel):
    """Bounded resident log result returned by a backend adapter."""

    entries: list[ResidentLogEntry] = Field(default_factory=list)
    buffer_total: int = Field(default=0, ge=0)

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ResidentDeploymentProfile(BaseModel):
    """Operator-owned valid resident backend and engine combination."""

    id: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=255)
    description: str = ""
    backend: ResidentBackend
    engine: ResidentEngine
    capabilities: list[ResidentCapability] = Field(default_factory=list)
    default_model: str = ""
    allowed_models: list[str] = Field(default_factory=list)
    model_prefix: str = ""
    labels: list[str] = Field(default_factory=list)
    deployment: dict[str, Any] = Field(default_factory=dict, exclude=True)

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ResidentRuntime(BaseModel):
    """Durable control-plane record for one long-lived resident runtime."""

    id: UUID = Field(default_factory=uuid4)
    owner_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=255)
    persona_name: str = Field(default="", max_length=255)
    model: str = Field(default="", max_length=255)
    backend: ResidentBackend
    engine: ResidentEngine
    profile_id: str = Field(min_length=1, max_length=100)
    flock_id: UUID | None = None
    flock_member_id: UUID | None = None
    flock_role: str = Field(default="", max_length=100)
    flock_peer_id: str = Field(default="", max_length=255)
    desired_state: ResidentDesiredState = ResidentDesiredState.RUNNING
    observed_state: ResidentObservedState = ResidentObservedState.PENDING
    backend_ref: dict[str, Any] = Field(default_factory=dict)
    endpoints: list[ResidentEndpoint] = Field(default_factory=list)
    capabilities: list[ResidentCapability] = Field(default_factory=list)
    conditions: list[ResidentCondition] = Field(default_factory=list)
    message_count: int = Field(default=0, ge=0)
    tokens_used: int = Field(default=0, ge=0)
    cost: Decimal = Field(default=Decimal("0"), ge=0)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ResidentSessionStatus(StrEnum):
    """Normalized activity state for one engine-owned resident session."""

    IDLE = "idle"
    RUNNING = "running"
    FAILED = "failed"


class ResidentSession(BaseModel):
    """Backend-neutral projection of an engine-owned resident session."""

    id: UUID
    resident_id: UUID
    title: str = ""
    model: str = ""
    status: ResidentSessionStatus = ResidentSessionStatus.IDLE
    created_at: datetime | None = None
    updated_at: datetime | None = None
    message_count: int = Field(default=0, ge=0)
    tokens_used: int = Field(default=0, ge=0)
    cost: Decimal = Field(default=Decimal("0"), ge=0)
    chat_endpoint: str = ""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ResourceCategory(StrEnum):
    """Category of a resource type."""

    COMPUTE = "compute"
    ACCELERATOR = "accelerator"
    CUSTOM = "custom"


@dataclass(frozen=True)
class ResourceType:
    """A discoverable resource type available in the cluster."""

    name: str
    resource_key: str  # K8s resource key, e.g. "nvidia.com/gpu"
    display_name: str
    unit: str
    category: ResourceCategory = ResourceCategory.COMPUTE


@dataclass(frozen=True)
class NodeResourceSummary:
    """Resource availability summary for a single node."""

    name: str
    labels: dict[str, str] = field(default_factory=dict)
    allocatable: dict[str, str] = field(default_factory=dict)
    allocated: dict[str, str] = field(default_factory=dict)
    available: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ClusterResourceInfo:
    """Discovered cluster resource types and capacity."""

    resource_types: list[ResourceType] = field(default_factory=list)
    nodes: list[NodeResourceSummary] = field(default_factory=list)


@dataclass(frozen=True)
class TranslatedResources:
    """K8s-native resource specification translated from user-friendly config."""

    requests: dict[str, str] = field(default_factory=dict)
    limits: dict[str, str] = field(default_factory=dict)
    node_selector: dict[str, str] = field(default_factory=dict)
    tolerations: list[dict] = field(default_factory=list)
    runtime_class_name: str | None = None


@dataclass
class SessionSpec:
    """Merged result from all contributors."""

    values: dict[str, Any]
    pod_spec: PodSpecAdditions

    @staticmethod
    def merge(contributions: list) -> SessionSpec:
        """Merge multiple SessionContribution objects into a single spec."""
        merged_values: dict[str, Any] = {}
        merged_pod_spec = PodSpecAdditions()

        for c in contributions:
            _deep_merge(merged_values, c.values)
            if c.pod_spec is not None:
                merged_pod_spec = _merge_pod_specs(merged_pod_spec, c.pod_spec)

        return SessionSpec(values=merged_values, pod_spec=merged_pod_spec)


# PersonalAccessToken — re-exported from shared niuu module
PersonalAccessToken = shared_models.PersonalAccessToken


def _deep_merge(base: dict, override: dict) -> None:
    """Deep-merge override into base in place."""
    for key, value in override.items():
        if key == "mcpServers" and isinstance(base.get(key), list) and isinstance(value, list):
            base[key] = _merge_mcp_server_lists(base[key], value)
            continue
        if (
            key == "credentialMappings"
            and isinstance(base.get(key), list)
            and isinstance(value, list)
        ):
            base[key] = _merge_credential_mapping_lists(base[key], value)
            continue
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _merge_mcp_server_lists(existing: list, override: list) -> list:
    """Merge MCP server lists by ``name``, preserving order and later overrides."""
    merged: list = []
    index_by_name: dict[str, int] = {}

    for entry in list(existing) + list(override):
        if not isinstance(entry, dict):
            merged.append(entry)
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            merged.append(dict(entry))
            continue
        if name in index_by_name:
            merged[index_by_name[name]] = dict(entry)
            continue
        index_by_name[name] = len(merged)
        merged.append(dict(entry))

    return merged


def _merge_credential_mapping_lists(existing: list, override: list) -> list:
    """Merge credential mappings by name while combining env and file fields."""
    merged: list = []
    index_by_name: dict[str, int] = {}
    for entry in list(existing) + list(override):
        if not isinstance(entry, dict):
            merged.append(entry)
            continue
        name = str(entry.get("credentialName") or entry.get("credential_name") or "").strip()
        if not name:
            merged.append(dict(entry))
            continue
        normalized = {
            "credentialName": name,
            "envMappings": dict(entry.get("envMappings") or entry.get("env_mappings") or {}),
            "fileMappings": dict(entry.get("fileMappings") or entry.get("file_mappings") or {}),
        }
        if name not in index_by_name:
            index_by_name[name] = len(merged)
            merged.append(normalized)
            continue
        current = merged[index_by_name[name]]
        current["envMappings"].update(normalized["envMappings"])
        current["fileMappings"].update(normalized["fileMappings"])
    return merged


def _merge_pod_specs(a: PodSpecAdditions, b: PodSpecAdditions) -> PodSpecAdditions:
    """Merge two PodSpecAdditions, concatenating sequences and merging dicts."""
    return PodSpecAdditions(
        volumes=a.volumes + b.volumes,
        volume_mounts=a.volume_mounts + b.volume_mounts,
        labels={**a.labels, **b.labels},
        annotations={**a.annotations, **b.annotations},
        env=a.env + b.env,
        service_account=b.service_account or a.service_account,
        extra_containers=a.extra_containers + b.extra_containers,
        init_containers=a.init_containers + b.init_containers,
    )
