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

from niuu.domain import models as shared_models

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


class UserStatus(StrEnum):
    """Status of a user account."""

    PROVISIONING = "provisioning"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    FAILED = "failed"


class TenantTier(StrEnum):
    """Tier classification for a tenant."""

    DEVELOPER = "developer"
    TEAM = "team"
    ENTERPRISE = "enterprise"


class TenantRole(StrEnum):
    """Role within a tenant."""

    ADMIN = "volundr:admin"
    DEVELOPER = "volundr:developer"
    VIEWER = "volundr:viewer"


@dataclass(frozen=True)
class Tenant:
    """A tenant in the hierarchy."""

    id: str
    path: str
    name: str
    parent_id: str | None = None
    tier: TenantTier = TenantTier.DEVELOPER
    max_sessions: int = 5
    max_storage_gb: int = 50
    created_at: datetime | None = None


@dataclass(frozen=True)
class User:
    """A provisioned user."""

    id: str  # IDP sub claim
    email: str
    display_name: str = ""
    status: UserStatus = UserStatus.ACTIVE
    home_pvc: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class TenantMembership:
    """A user's membership in a tenant."""

    user_id: str
    tenant_id: str
    role: TenantRole = TenantRole.DEVELOPER
    granted_at: datetime | None = None


@dataclass(frozen=True)
class QuotaCheck:
    """Result of a quota check along the tenant ancestor chain."""

    allowed: bool
    tenant_id: str
    max_sessions: int
    current_sessions: int
    reason: str = ""


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
    """Activity state of a running session (orthogonal to lifecycle status)."""

    ACTIVE = "active"
    IDLE = "idle"
    TOOL_EXECUTING = "tool_executing"


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


@dataclass(frozen=True)
class RealtimeEvent:
    """A real-time event for SSE streaming."""

    type: EventType
    data: dict
    timestamp: datetime


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
        description="Current activity state (active/idle/tool_executing)",
    )
    activity_metadata: dict = Field(
        default_factory=dict,
        description="Metadata from the latest activity report",
    )
    workload_type: str = Field(
        default="session",
        description="Workload type used to launch the session",
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

    model_config = {"frozen": False}

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


class TrackerIssue(BaseModel):
    """Issue from an external issue tracker (Linear, Jira, GitHub Issues, etc.)."""

    id: str = Field(
        description="Internal issue ID from the tracker backend",
    )
    identifier: str = Field(
        description="Human-readable issue identifier (e.g. NIU-57)",
    )
    title: str = Field(description="Issue title")
    status: str = Field(
        description="Current issue status (e.g. In Progress, Done)",
    )
    assignee: str | None = Field(
        default=None,
        description="Display name of the assigned user",
    )
    labels: list[str] = Field(
        default_factory=list,
        description="Labels attached to the issue",
    )
    priority: int = Field(
        default=0,
        description="Priority level (0=none, 1=urgent, 4=low)",
    )
    url: str = Field(
        description="Web URL to view the issue in the tracker",
    )

    model_config = {"frozen": False}


class ProjectMapping(BaseModel):
    """Maps a git repo URL to an issue tracker project."""

    id: UUID = Field(
        default_factory=uuid4,
        description="Unique mapping identifier",
    )
    repo_url: str = Field(description="Git repository URL to map")
    project_id: str = Field(description="Issue tracker project ID")
    project_name: str = Field(
        default="",
        description="Human-readable project name",
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        description="Timestamp when the mapping was created",
    )

    model_config = {"frozen": False}


class TrackerConnectionStatus(BaseModel):
    """Connection status for an issue tracker."""

    connected: bool = Field(
        description="Whether the tracker connection is active",
    )
    provider: str = Field(
        description="Tracker provider name (e.g. linear, jira)",
    )
    workspace: str | None = Field(
        default=None,
        description="Workspace or organization name in the tracker",
    )
    user: str | None = Field(
        default=None,
        description="Authenticated user display name",
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
class MCPServerConfig:
    """An available MCP server configuration."""

    name: str
    type: str = "stdio"
    command: str | None = None
    url: str | None = None
    args: list[str] = ()  # type: ignore[assignment]
    description: str = ""

    def __post_init__(self) -> None:
        # Ensure args is always a tuple for immutability
        if not isinstance(self.args, tuple):
            object.__setattr__(self, "args", tuple(self.args))


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


class MountType(StrEnum):
    """How a secret should be mounted into a session pod."""

    ENV_FILE = "env_file"
    FILE = "file"
    TEMPLATE = "template"


@dataclass(frozen=True)
class SecretMountSpec:
    """Specification for how a secret should be mounted."""

    secret_path: str
    mount_type: MountType
    destination: str
    template: str | None = None
    renewal: bool = False


@dataclass(frozen=True)
class SecretProfile:
    """Named collection of secret mount specs for a tenant or user."""

    owner_id: str
    owner_type: str  # "tenant" or "user"
    mounts: tuple[SecretMountSpec, ...] = ()


@dataclass(frozen=True)
class StorageQuota:
    """Storage quota for a user."""

    home_gb: int = 1
    workspace_gb: int = 1


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


@dataclass(frozen=True)
class ProvisioningResult:
    """Result of a user provisioning or reprovisioning operation."""

    success: bool
    user_id: str
    home_pvc: str | None = None
    errors: list[str] = field(default_factory=list)


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


@dataclass(frozen=True)
class SecretInfo:
    """Metadata about an available Kubernetes secret."""

    name: str
    keys: list[str] = ()  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if not isinstance(self.keys, tuple):
            object.__setattr__(self, "keys", tuple(self.keys))


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
