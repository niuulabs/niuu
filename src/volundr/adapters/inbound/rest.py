"""FastAPI REST adapter for session management."""

import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path as FilePath
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from skuld.log_aggregate import aggregate_workspace_logs
from volundr.adapters.inbound.auth import extract_principal, require_role
from volundr.domain.models import (
    Chronicle,
    ChronicleStatus,
    CleanupTarget,
    GitProviderType,
    GitSource,
    Model,
    ModelProvider,
    ModelTier,
    Principal,
    Session,
    SessionActivityState,
    SessionSource,
    SessionStatus,
    TimelineEvent,
    TimelineEventType,
    WorkspaceStatus,
)
from volundr.domain.ports import (
    EventBroadcaster,
    GitAuthError,
    GitRepoNotFoundError,
    PricingProvider,
)
from volundr.domain.services import (
    ChronicleNotFoundError,
    ChronicleService,
    ForgeService,
    ProviderInfo,
    RepoService,
    RepoValidationError,
    SessionAccessDeniedError,
    SessionNotFoundError,
    SessionNotRunningError,
    SessionService,
    SessionStateError,
    StatsService,
    TokenService,
)

logger = logging.getLogger(__name__)


def _public_session_endpoint(endpoint: str | None) -> str | None:
    """Normalize loopback session endpoints for browser-facing clients."""
    if not endpoint:
        return endpoint
    try:
        parsed = urlsplit(endpoint)
    except ValueError:
        return endpoint
    if parsed.hostname != "127.0.0.1":
        return endpoint
    host = os.environ.get("NIUU_SERVER_HOST", "127.0.0.1").strip() or "127.0.0.1"
    public_host = "localhost" if host == "127.0.0.1" else host
    netloc = public_host
    if parsed.port is not None:
        netloc = f"{public_host}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


def _workspace_bulk_delete_session_ids(body: dict) -> list[str]:
    """Accept both snake_case and camelCase workspace bulk-delete payloads."""
    session_ids = body.get("session_ids")
    if session_ids is None:
        session_ids = body.get("sessionIds", [])
    return session_ids


def _workspace_dir_from_code_endpoint(code_endpoint: str | None) -> FilePath | None:
    """Resolve a local workspace path from a file:// code endpoint when possible."""
    if not code_endpoint:
        return None
    try:
        parsed = urlsplit(code_endpoint)
    except ValueError:
        return None
    if parsed.scheme != "file" or not parsed.path:
        return None
    path = FilePath(parsed.path)
    return path if path.exists() else None


_RFC1123_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


class SessionCreate(BaseModel):
    """Request model for creating a session."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=63,
        description="Session name (RFC 1123 label: lowercase alphanumeric and hyphens, "
        "must start and end with alphanumeric, max 63 characters)",
    )

    @field_validator("name")
    @classmethod
    def validate_rfc1123(cls, v: str) -> str:
        """Validate session name is a valid RFC 1123 DNS label."""
        v = v.strip()
        if not v:
            raise ValueError("Session name must not be empty")
        if len(v) > 63:
            raise ValueError("Session name must be at most 63 characters (RFC 1123 hostname limit)")
        if v != v.lower():
            raise ValueError(
                "Session name must be lowercase — uppercase characters are not allowed"
            )
        if " " in v:
            raise ValueError("Session name must not contain spaces — use hyphens instead")
        if v.startswith("-"):
            raise ValueError("Session name must start with a letter or digit, not a hyphen")
        if v.endswith("-"):
            raise ValueError("Session name must end with a letter or digit, not a hyphen")
        if not _RFC1123_RE.match(v):
            raise ValueError(
                "Session name may only contain lowercase letters (a-z), digits (0-9), "
                "and hyphens (-)"
            )
        return v

    model: str = Field(
        default="",
        max_length=100,
        description="LLM model identifier (e.g. claude-sonnet-4-6)",
    )
    source: SessionSource = Field(
        default_factory=GitSource,
        description="Workspace source (git repo or local mount)",
    )
    definition: str | None = Field(
        default=None,
        max_length=100,
        description="Session definition key (e.g. 'skuldClaude', 'skuldCodex')",
    )
    template_name: str | None = Field(
        default=None,
        max_length=255,
        description="Workspace template name to apply",
    )
    profile_name: str | None = Field(
        default=None,
        max_length=255,
        description="Forge profile name for resource allocation",
    )
    preset_id: UUID | None = Field(
        default=None,
        description="Preset ID for runtime configuration",
    )
    workspace_id: UUID | None = Field(
        default=None,
        description="Existing workspace PVC to reuse",
    )
    terminal_restricted: bool = Field(
        default=False,
        description="Whether to restrict terminal access",
    )
    credential_names: list[str] = Field(
        default_factory=list,
        description="Credential names to inject into the pod",
    )
    integration_ids: list[str] = Field(
        default_factory=list,
        description="Integration connection IDs to activate",
    )
    resource_config: dict = Field(
        default_factory=dict,
        description="Resource allocation overrides (cpu, memory, gpu)",
    )
    system_prompt: str = Field(
        default="",
        max_length=100_000,
        description="System prompt appended to Claude's default instructions",
    )
    initial_prompt: str = Field(
        default="",
        max_length=100_000,
        description="Initial user message sent when the CLI starts",
    )
    issue_id: str | None = Field(
        default=None,
        max_length=255,
        description="Issue tracker ID to link to the session",
    )
    issue_url: str | None = Field(
        default=None,
        max_length=2048,
        description="URL of the linked issue in the tracker",
    )
    workload_type: str = Field(
        default="session",
        max_length=100,
        description="Workload type: 'session' (default) or 'ravn_flock' for raiding parties",
    )
    workload_config: dict = Field(
        default_factory=dict,
        description="Workload-specific configuration (e.g. personas, mesh, mimir settings)",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "fix-auth-bug",
                "model": "claude-sonnet-4-6",
                "source": {
                    "type": "git",
                    "repo": "github.com/acme/backend",
                    "branch": "main",
                },
                "profile_name": "default",
            },
        },
    }


class SessionUpdate(BaseModel):
    """Request model for updating a session."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=63,
        description="New session name (RFC 1123 label)",
    )

    @field_validator("name")
    @classmethod
    def validate_rfc1123(cls, v: str | None) -> str | None:
        """Validate session name is a valid RFC 1123 DNS label."""
        if v is None:
            return v
        return SessionCreate.validate_rfc1123(v)

    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="New LLM model identifier",
    )
    branch: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="New git branch to checkout",
    )
    tracker_issue_id: str | None = Field(
        default=None,
        description="Issue tracker identifier to link",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "fix-auth-bug-v2",
                "model": "claude-sonnet-4-6",
                "branch": "fix/auth-bypass",
                "tracker_issue_id": "PROJ-1234",
            },
        },
    }


class SessionStart(BaseModel):
    """Request model for (re)starting a session."""

    profile_name: str | None = Field(
        default=None,
        max_length=255,
        description="Forge profile name to use when starting",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "profile_name": "default",
            },
        },
    }


class DeleteSessionBody(BaseModel):
    """Optional request body for session deletion with cleanup targets."""

    cleanup: list[CleanupTarget] = Field(
        default_factory=list,
        description="Resources to permanently delete alongside the session",
    )


class ActivityReport(BaseModel):
    """Request model for reporting session activity state."""

    state: str = Field(description="Activity state (active/idle/tool_executing)")
    metadata: dict = Field(default_factory=dict, description="Activity metadata")


class SessionResponse(BaseModel):
    """Response model for a session."""

    id: UUID = Field(description="Unique session identifier")
    name: str = Field(description="Human-readable session name")
    model: str = Field(description="LLM model identifier")
    source: SessionSource = Field(description="Workspace source config")
    status: SessionStatus = Field(description="Current lifecycle status")
    chat_endpoint: str | None = Field(
        description="Skuld chat proxy URL (null when not running)",
    )
    code_endpoint: str | None = Field(
        description="Editor IDE URL (null when not running)",
    )
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last update timestamp")
    last_active: str = Field(description="ISO 8601 last activity timestamp")
    message_count: int = Field(description="Total chat messages exchanged")
    tokens_used: int = Field(description="Total tokens consumed")
    pod_name: str | None = Field(
        description="Kubernetes pod name (null when not running)",
    )
    error: str | None = Field(
        description="Error message if session is in failed state",
    )
    tracker_issue_id: str | None = Field(
        default=None,
        description="Linked issue tracker identifier",
    )
    issue_tracker_url: str | None = Field(
        default=None,
        description="Web URL for the linked issue in the tracker",
    )
    preset_id: UUID | None = Field(
        default=None,
        description="Preset used to configure this session",
    )
    archived_at: str | None = Field(
        default=None,
        description="ISO 8601 archive timestamp",
    )
    owner_id: str | None = Field(
        default=None,
        description="User ID of the session owner",
    )
    tenant_id: str | None = Field(
        default=None,
        description="Tenant ID for multi-tenant isolation",
    )
    activity_state: str | None = Field(
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

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "name": "fix-auth-bug",
                "model": "claude-sonnet-4-6",
                "source": {
                    "type": "git",
                    "repo": "github.com/acme/backend",
                    "branch": "main",
                },
                "status": "running",
                "chat_endpoint": "https://s-abc.volundr.dev/chat",
                "code_endpoint": "https://s-abc.volundr.dev/code",
                "created_at": "2025-01-15T10:30:00Z",
                "updated_at": "2025-01-15T11:45:00Z",
                "last_active": "2025-01-15T11:45:00Z",
                "message_count": 42,
                "tokens_used": 125000,
                "pod_name": "session-a1b2c3d4",
                "error": None,
            },
        },
    }

    @classmethod
    def from_session(cls, session: Session) -> "SessionResponse":
        """Create response from domain model."""
        return cls(
            id=session.id,
            name=session.name,
            model=session.model,
            source=session.source,
            status=session.status,
            chat_endpoint=_public_session_endpoint(session.chat_endpoint),
            code_endpoint=session.code_endpoint,
            created_at=session.created_at.isoformat(),
            updated_at=session.updated_at.isoformat(),
            last_active=(
                session.last_active.isoformat()
                if session.last_active
                else session.created_at.isoformat()
            ),
            message_count=session.message_count,
            tokens_used=session.tokens_used,
            pod_name=session.pod_name,
            error=session.error,
            tracker_issue_id=session.tracker_issue_id,
            issue_tracker_url=session.issue_tracker_url,
            preset_id=session.preset_id,
            archived_at=(session.archived_at.isoformat() if session.archived_at else None),
            owner_id=session.owner_id,
            tenant_id=session.tenant_id,
            activity_state=(session.activity_state.value if session.activity_state else None),
            activity_metadata=session.activity_metadata,
            workload_type=session.workload_type,
        )


class SessionEndpoints(BaseModel):
    """Response model for session endpoints after start."""

    chat_endpoint: str = Field(description="Skuld chat proxy URL")
    code_endpoint: str = Field(description="Editor IDE URL")


class ModelInfo(BaseModel):
    """Response model for available models."""

    id: str = Field(description="Model identifier")
    name: str = Field(description="Human-readable model name")
    description: str = Field(description="Model capabilities description")
    provider: ModelProvider = Field(description="Provider type: cloud or local")
    vendor: str = Field(description="Model vendor/runtime family (e.g. anthropic, openai)")
    tier: ModelTier = Field(description="Model tier classification")
    color: str = Field(description="UI color code for the model badge")
    cost_per_million_tokens: float | None = Field(
        default=None,
        description="Cost per million tokens in USD",
    )
    vram_required: str | None = Field(
        default=None,
        description="VRAM required for local models (e.g. 24GB)",
    )
    session_definition: str | None = Field(
        default=None,
        description="Resolved model-specific runtime override, when configured.",
    )

    @classmethod
    def from_model(cls, model: Model) -> "ModelInfo":
        """Create response from domain model."""
        return cls(
            id=model.id,
            name=model.name,
            description=model.description,
            provider=model.provider,
            vendor=model.vendor,
            tier=model.tier,
            color=model.color,
            cost_per_million_tokens=model.cost_per_million_tokens,
            vram_required=model.vram_required,
            session_definition=model.session_definition,
        )


class ProviderResponse(BaseModel):
    """Response model for a configured git provider."""

    name: str = Field(description="Provider name (e.g. GitHub)")
    type: GitProviderType = Field(description="Git provider type")
    orgs: list[str] = Field(description="Configured organizations")

    @classmethod
    def from_provider_info(cls, info: ProviderInfo) -> "ProviderResponse":
        """Create response from domain model."""
        return cls(name=info.name, type=info.type, orgs=list(info.orgs))


class RepoResponse(BaseModel):
    """Response model for a repository."""

    provider: GitProviderType = Field(description="Git provider type")
    org: str = Field(description="Organization or group name")
    name: str = Field(description="Repository name")
    url: str = Field(description="Web URL for the repository")
    default_branch: str = Field(description="Default branch name")
    branches: list[str] = Field(description="Available branch names")


class ErrorResponse(BaseModel):
    """Response model for errors."""

    detail: str = Field(description="Human-readable error message")


class BrokerChronicleReport(BaseModel):
    """Request model for broker-reported chronicle data at session shutdown."""

    summary: str | None = Field(
        default=None,
        description="AI-generated summary of work accomplished",
    )
    key_changes: list[str] | None = Field(
        default=None,
        description="List of key changes made during the session",
    )
    unfinished_work: str | None = Field(
        default=None,
        description="Incomplete work for the next session",
    )
    duration_seconds: int | None = Field(
        default=None,
        ge=0,
        description="Session duration in seconds",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "summary": "Fixed JWT validation bypass in auth middleware",
                "key_changes": [
                    "Fixed token expiry check in jwt_validator.py",
                    "Added regression test for expired tokens",
                ],
                "unfinished_work": "Refresh token rotation not yet implemented",
                "duration_seconds": 1800,
            },
        },
    }


class ChronicleCreate(BaseModel):
    """Request model for creating a chronicle from a session."""

    session_id: UUID = Field(
        description="Session ID to create a chronicle entry for",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            },
        },
    }


class ChronicleUpdate(BaseModel):
    """Request model for updating a chronicle."""

    summary: str | None = Field(
        default=None,
        description="Updated summary of work accomplished",
    )
    key_changes: list[str] | None = Field(
        default=None,
        description="Updated list of key changes",
    )
    unfinished_work: str | None = Field(
        default=None,
        description="Updated description of incomplete work",
    )
    tags: list[str] | None = Field(
        default=None,
        description="Updated tags for categorization",
    )
    status: ChronicleStatus | None = Field(
        default=None,
        description="New chronicle status (draft or complete)",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "summary": "Fixed JWT validation bypass in auth middleware",
                "key_changes": ["Fixed token expiry check", "Added regression test"],
                "tags": ["bugfix", "security"],
                "status": "complete",
            },
        },
    }


class ChronicleResponse(BaseModel):
    """Response model for a chronicle."""

    id: UUID = Field(description="Unique chronicle identifier")
    session_id: UUID | None = Field(
        description="Session that produced this chronicle",
    )
    status: ChronicleStatus = Field(description="Draft or complete")
    project: str = Field(description="Project name from the repository")
    repo: str = Field(description="Git repository URL")
    branch: str = Field(description="Git branch used during the session")
    model: str = Field(description="LLM model used during the session")
    config_snapshot: dict = Field(
        description="Session configuration snapshot",
    )
    summary: str | None = Field(
        description="AI-generated summary of work accomplished",
    )
    key_changes: list[str] = Field(
        description="Significant changes made during the session",
    )
    unfinished_work: str | None = Field(
        description="Incomplete work for the next session",
    )
    token_usage: int = Field(description="Total tokens consumed")
    cost: float | None = Field(description="Estimated cost in USD")
    duration_seconds: int | None = Field(
        description="Wall-clock session duration in seconds",
    )
    tags: list[str] = Field(description="User-defined tags")
    parent_chronicle_id: UUID | None = Field(
        description="Parent chronicle for reforge chains",
    )
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last update timestamp")

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
                "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "status": "complete",
                "project": "backend",
                "repo": "github.com/acme/backend",
                "branch": "fix/auth-bug",
                "model": "claude-sonnet-4-6",
                "config_snapshot": {},
                "summary": "Fixed JWT validation bypass",
                "key_changes": [
                    "Fixed token expiry check",
                    "Added regression test",
                ],
                "unfinished_work": None,
                "token_usage": 125000,
                "cost": 0.47,
                "duration_seconds": 1800,
                "tags": ["bugfix", "security"],
                "parent_chronicle_id": None,
                "created_at": "2025-01-15T10:30:00Z",
                "updated_at": "2025-01-15T11:00:00Z",
            },
        },
    }

    @classmethod
    def from_chronicle(cls, chronicle: Chronicle) -> "ChronicleResponse":
        """Create response from domain model."""
        return cls(
            id=chronicle.id,
            session_id=chronicle.session_id,
            status=chronicle.status,
            project=chronicle.project,
            repo=chronicle.repo,
            branch=chronicle.branch,
            model=chronicle.model,
            config_snapshot=chronicle.config_snapshot,
            summary=chronicle.summary,
            key_changes=chronicle.key_changes,
            unfinished_work=chronicle.unfinished_work,
            token_usage=chronicle.token_usage,
            cost=float(chronicle.cost) if chronicle.cost is not None else None,
            duration_seconds=chronicle.duration_seconds,
            tags=chronicle.tags,
            parent_chronicle_id=chronicle.parent_chronicle_id,
            created_at=chronicle.created_at.isoformat(),
            updated_at=chronicle.updated_at.isoformat(),
        )


class TimelineEventResponse(BaseModel):
    """Response model for a single timeline event."""

    t: int = Field(description="Seconds elapsed since session start")
    type: str = Field(description="Event type (session, message, file, etc.)")
    label: str = Field(description="Display text for the event")
    tokens: int | None = Field(
        default=None,
        description="Tokens consumed (message events)",
    )
    action: str | None = Field(
        default=None,
        description="File action (created, modified, deleted)",
    )
    ins: int | None = Field(
        default=None,
        description="Lines inserted (file events)",
    )
    del_: int | None = Field(
        default=None,
        alias="del",
        description="Lines deleted (file events)",
    )
    hash: str | None = Field(
        default=None,
        description="Short commit hash (git events)",
    )
    exit: int | None = Field(
        default=None,
        description="Exit code (terminal events)",
    )

    model_config = {"populate_by_name": True}


class TimelineFileResponse(BaseModel):
    """Response model for a file summary in the timeline."""

    path: str = Field(description="File path relative to the workspace")
    status: str = Field(description="Change status: new, mod, or del")
    ins: int = Field(description="Total lines inserted")
    del_: int = Field(alias="del", description="Total lines deleted")

    model_config = {"populate_by_name": True}


class TimelineCommitResponse(BaseModel):
    """Response model for a commit summary in the timeline."""

    hash: str = Field(description="Short commit hash")
    msg: str = Field(description="Commit message")
    time: str = Field(description="Wall clock time (e.g. 14:35)")


class TimelineResponseModel(BaseModel):
    """Response model for the full timeline."""

    events: list[TimelineEventResponse] = Field(
        description="Ordered list of timeline events",
    )
    files: list[TimelineFileResponse] = Field(
        description="Aggregated file change summaries",
    )
    commits: list[TimelineCommitResponse] = Field(
        description="Commit summaries",
    )
    token_burn: list[int] = Field(
        description="Token usage per time bucket",
    )


class TimelineEventCreate(BaseModel):
    """Request model for adding a timeline event."""

    t: int = Field(..., ge=0, description="Seconds elapsed since session start")
    type: str = Field(
        ...,
        pattern="^(session|message|file|git|terminal|error)$",
        description="Event type",
    )
    label: str = Field(..., min_length=1, description="Display text")
    tokens: int | None = Field(
        default=None,
        ge=0,
        description="Tokens consumed (message events)",
    )
    action: str | None = Field(
        default=None,
        pattern="^(created|modified|deleted)$",
        description="File action (created, modified, deleted)",
    )
    ins: int | None = Field(
        default=None,
        ge=0,
        description="Lines inserted (file events)",
    )
    del_: int | None = Field(
        default=None,
        ge=0,
        alias="del",
        description="Lines deleted (file events)",
    )
    hash: str | None = Field(
        default=None,
        max_length=40,
        description="Short commit hash (git events)",
    )
    exit_code: int | None = Field(
        default=None,
        alias="exit",
        description="Exit code (terminal events)",
    )

    model_config = {"populate_by_name": True}


class StatsResponse(BaseModel):
    """Response model for aggregate statistics."""

    active_sessions: int = Field(description="Currently running sessions")
    total_sessions: int = Field(description="Total sessions (all statuses)")
    tokens_today: int = Field(description="Tokens consumed today")
    local_tokens: int = Field(description="Tokens from local models today")
    cloud_tokens: int = Field(description="Tokens from cloud models today")
    cost_today: float = Field(description="Total cloud cost today in USD")


class TokenUsageReport(BaseModel):
    """Request model for reporting token usage."""

    tokens: int = Field(
        ...,
        gt=0,
        description="Number of tokens used",
    )
    provider: str = Field(
        ...,
        pattern="^(cloud|local)$",
        description="Model provider (cloud or local)",
    )
    model: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Model identifier",
    )
    message_count: int = Field(
        default=1,
        ge=1,
        description="Number of messages in this usage report",
    )
    cost: float | None = Field(
        default=None,
        ge=0,
        description="Pre-calculated cost in USD (from CLI)",
    )


class TokenUsageResponse(BaseModel):
    """Response model for token usage record."""

    id: str = Field(description="Usage record identifier")
    session_id: str = Field(description="Session the usage belongs to")
    recorded_at: str = Field(description="ISO 8601 timestamp of recording")
    tokens: int = Field(description="Number of tokens used")
    provider: str = Field(description="Model provider (cloud or local)")
    model: str = Field(description="Model identifier")
    cost: float | None = Field(description="Cost in USD (null for local)")


class WorkspaceResponse(BaseModel):
    """Response model for a workspace."""

    id: UUID = Field(description="Unique workspace identifier")
    session_id: UUID = Field(description="Session this workspace belongs to")
    user_id: str = Field(description="Owner user ID")
    tenant_id: str = Field(description="Tenant ID")
    pvc_name: str = Field(description="Kubernetes PVC name")
    status: str = Field(description="Workspace status (active, archived)")
    size_gb: int = Field(description="Allocated storage in GB")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    archived_at: str | None = Field(
        description="ISO 8601 archive timestamp",
    )
    deleted_at: str | None = Field(
        description="ISO 8601 deletion timestamp",
    )
    session_name: str | None = Field(None, description="Name of the associated session")
    source_url: str | None = Field(None, description="Git repository URL if applicable")
    source_ref: str | None = Field(None, description="Git branch/ref if applicable")

    @classmethod
    def from_workspace(cls, ws, session=None) -> "WorkspaceResponse":
        # Prefer workspace-stored metadata; fall back to session lookup.
        session_name = ws.name
        source_url = ws.source_url
        source_ref = ws.source_ref
        if session is not None:
            if not session_name:
                session_name = session.name
            if not source_url and hasattr(session.source, "repo"):
                source_url = session.source.repo
            if not source_ref and hasattr(session.source, "branch"):
                source_ref = session.source.branch
        return cls(
            id=ws.id,
            session_id=ws.session_id,
            user_id=ws.user_id,
            tenant_id=ws.tenant_id,
            pvc_name=ws.pvc_name,
            status=ws.status.value if hasattr(ws.status, "value") else ws.status,
            size_gb=ws.size_gb,
            created_at=ws.created_at.isoformat() if ws.created_at else "",
            archived_at=ws.archived_at.isoformat() if ws.archived_at else None,
            deleted_at=ws.deleted_at.isoformat() if ws.deleted_at else None,
            session_name=session_name,
            source_url=source_url,
            source_ref=source_ref,
        )


def create_router(
    session_service: SessionService,
    stats_service: StatsService | None = None,
    token_service: TokenService | None = None,
    pricing_provider: PricingProvider | None = None,
    broadcaster: EventBroadcaster | None = None,
    repo_service: RepoService | None = None,
    chronicle_service: ChronicleService | None = None,
    *,
    prefix: str = "/api/v1/forge",
) -> APIRouter:
    """Create FastAPI router with session, stats, token, repo, and SSE endpoints."""
    router = APIRouter(prefix=prefix)
    forge = ForgeService(
        session_service,
        stats_service=stats_service,
        token_service=token_service,
        pricing_provider=pricing_provider,
        repo_service=repo_service,
        chronicle_service=chronicle_service,
    )

    async def _optional_principal(request: Request) -> Principal | None:
        """Extract principal if identity is configured, else return None.

        Allows dev mode (no IDP) to work without auth headers while
        production deployments enforce tenant/ownership scoping.

        When a principal is found, also ensures the user row exists
        via the identity adapter's JIT provisioning.
        """
        identity = getattr(request.app.state, "identity", None)
        if identity is None:
            return None

        from volundr.adapters.inbound.auth import extract_principal

        try:
            principal = await extract_principal(request)
        except HTTPException:
            return None

        try:
            await identity.get_or_provision_user(principal)
        except Exception:
            logger.warning("JIT user provisioning failed for %s", principal.user_id, exc_info=True)

        return principal

    @router.get("/feature-flags", tags=["Features"])
    async def get_feature_flags(request: Request) -> dict:
        """Return feature flags derived from server configuration.

        Lets the frontend adapt its UI based on what the backend supports
        (e.g. local mounts are only meaningful in k3s / CLI mode).
        """
        settings = request.app.state.settings
        admin = request.app.state.admin_settings
        return {
            "local_mounts_enabled": settings.local_mounts.enabled,
            "file_manager_enabled": admin.get("storage", {}).get("file_manager_enabled", True),
            "mini_mode": settings.local_mounts.mini_mode,
        }

    @router.get("/repos/branches", response_model=list[str], tags=["Repositories"])
    async def list_branches(request: Request, repo_url: str = Query(...)) -> list[str]:
        """Legacy Volundr compatibility route for repository branch listings."""
        if repo_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Repo service not available",
            )

        user_id = request.headers.get("x-auth-user-id")

        try:
            return await repo_service.list_branches(repo_url, user_id=user_id)
        except GitAuthError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(e),
            )
        except GitRepoNotFoundError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            )
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )

    async def _optional_user_id(request: Request) -> str | None:
        """Extract user_id from auth principal if available, else None."""
        principal = await _optional_principal(request)
        if principal is None:
            return None
        return principal.user_id

    def _workspace_forge(request: Request) -> ForgeService:
        """Bind the shared Forge facade to the request-scoped workspace service."""
        return forge.with_workspace_service(request.app.state.workspace_service)

    @router.get("/sessions", response_model=list[SessionResponse], tags=["Sessions"])
    async def list_sessions(
        request: Request,
        status_filter: SessionStatus | None = Query(
            default=None, alias="status", description="Filter by session status"
        ),
        include_archived: bool = Query(
            default=False, description="Include archived sessions in results"
        ),
    ) -> list[SessionResponse]:
        """List all sessions. Archived sessions are excluded by default."""
        principal = await _optional_principal(request)
        sessions = await forge.list_sessions(
            status=status_filter,
            include_archived=include_archived,
            principal=principal,
        )
        return [SessionResponse.from_session(s) for s in sessions]

    @router.get(
        "/sessions/stream",
        responses={503: {"model": ErrorResponse}},
        tags=["Sessions"],
    )
    async def stream_sessions(request: Request) -> StreamingResponse:
        """Stream real-time session updates via Server-Sent Events (SSE).

        This endpoint provides a real-time stream of session events including:
        - session_created: When a new session is created
        - session_updated: When a session is updated (status, activity, etc.)
        - session_deleted: When a session is deleted
        - stats_updated: Periodic stats updates (every 30s)
        - heartbeat: Keep-alive signal (every 30s)

        Events are formatted as SSE:
        ```
        event: session_updated
        data: {"id": "...", "status": "running", ...}

        ```
        """
        if broadcaster is None:
            logger.warning("SSE stream requested but broadcaster is None")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Event streaming not available",
            )

        client_host = request.client.host if request.client else "unknown"
        logger.info("SSE stream: client connected from %s", client_host)

        async def event_generator():
            event_count = 0
            try:
                async for event in broadcaster.subscribe():
                    # Check if client disconnected
                    if await request.is_disconnected():
                        logger.info(
                            "SSE stream: client %s disconnected after %d events",
                            client_host,
                            event_count,
                        )
                        break

                    # Format as SSE
                    event_data = json.dumps(event.data)
                    event_count += 1
                    logger.info(
                        "SSE stream: sending event #%d type=%s to client %s",
                        event_count,
                        event.type.value,
                        client_host,
                    )
                    yield f"event: {event.type.value}\ndata: {event_data}\n\n"
            except asyncio.CancelledError:
                logger.info(
                    "SSE stream: connection cancelled for client %s after %d events",
                    client_host,
                    event_count,
                )

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post(
        "/sessions/archive-stopped",
        response_model=list[str],
        tags=["Sessions"],
    )
    async def archive_stopped_sessions() -> list[str]:
        """Bulk archive all stopped sessions."""
        archived_ids = await forge.archive_stopped_sessions()
        return [str(uid) for uid in archived_ids]

    @router.post(
        "/sessions",
        response_model=SessionResponse,
        status_code=status.HTTP_201_CREATED,
        responses={
            422: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
        tags=["Sessions"],
    )
    async def create_session(request: Request, data: SessionCreate) -> SessionResponse:
        """Create and start a new session.

        Creates the session record then immediately starts its pods.
        If template_name is set, the template provides defaults for
        repo/branch/model. The profile (explicit or from template) is
        passed to the pod manager to build task_args.
        """
        principal = await _optional_principal(request)
        try:
            started = await forge.create_and_start_session(data, principal=principal)
        except RepoValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(e),
            )
        except SessionStateError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            )
        return SessionResponse.from_session(started)

    @router.get(
        "/sessions/{session_id}",
        response_model=SessionResponse,
        responses={404: {"model": ErrorResponse}},
        tags=["Sessions"],
    )
    async def get_session(
        request: Request, session_id: UUID = Path(description="Unique session identifier")
    ) -> SessionResponse:
        """Get a session by ID."""
        session = await forge.get_session(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )

        principal = await _optional_principal(request)
        try:
            await forge.ensure_access(session, principal)
        except SessionAccessDeniedError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied to session {session_id}",
            )

        return SessionResponse.from_session(session)

    @router.put(
        "/sessions/{session_id}",
        response_model=SessionResponse,
        responses={404: {"model": ErrorResponse}},
        tags=["Sessions"],
    )
    async def update_session(
        request: Request,
        session_id: UUID = Path(description="Unique session identifier"),
        data: SessionUpdate = ...,
    ) -> SessionResponse:
        """Update a session."""
        principal = await _optional_principal(request)
        try:
            session = await forge.update_session(
                session_id=session_id,
                name=data.name,
                model=data.model,
                branch=data.branch,
                tracker_issue_id=data.tracker_issue_id,
                principal=principal,
            )
            return SessionResponse.from_session(session)
        except SessionAccessDeniedError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied to session {session_id}",
            )
        except SessionNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )

    @router.delete(
        "/sessions/{session_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={404: {"model": ErrorResponse}},
        tags=["Sessions"],
    )
    async def delete_session(
        request: Request,
        session_id: UUID = Path(description="Unique session identifier"),
        body: DeleteSessionBody | None = None,
    ) -> None:
        """Delete a session with optional resource cleanup."""
        principal = await _optional_principal(request)
        cleanup_targets = body.cleanup if body else []
        try:
            deleted = await forge.delete_session(
                session_id,
                principal=principal,
                cleanup_targets=cleanup_targets,
            )
        except SessionAccessDeniedError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied to session {session_id}",
            )
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )

    @router.post(
        "/sessions/{session_id}/start",
        response_model=SessionResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
        tags=["Sessions"],
    )
    async def start_session(
        request: Request,
        session_id: UUID = Path(description="Unique session identifier"),
        data: SessionStart | None = None,
    ) -> SessionResponse:
        """Restart a session's pods.

        Used to relaunch a stopped or failed session. An optional
        profile_name in the body overrides the default profile.
        """
        profile_name = data.profile_name if data else None
        principal = await _optional_principal(request)
        try:
            session = await forge.start_session(
                session_id,
                profile_name=profile_name,
                principal=principal,
            )
            return SessionResponse.from_session(session)
        except SessionAccessDeniedError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied to session {session_id}",
            )
        except SessionNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        except SessionStateError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            )

    @router.post(
        "/sessions/{session_id}/stop",
        response_model=SessionResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
        tags=["Sessions"],
    )
    async def stop_session(
        request: Request, session_id: UUID = Path(description="Unique session identifier")
    ) -> SessionResponse:
        """Stop a session's pods."""
        principal = await _optional_principal(request)
        try:
            session = await forge.stop_session(session_id, principal=principal)
            return SessionResponse.from_session(session)
        except SessionAccessDeniedError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied to session {session_id}",
            )
        except SessionNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        except SessionStateError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            )

    @router.post(
        "/sessions/{session_id}/activity",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
        tags=["Sessions"],
    )
    async def report_activity(
        request: Request,
        data: ActivityReport,
        session_id: UUID = Path(description="Unique session identifier"),
    ) -> None:
        """Report a session activity state change from Skuld.

        Updates the session's activity_state and broadcasts a
        session_activity SSE event for downstream consumers (e.g. Tyr).
        """
        # Authorization: caller must own the session
        principal = await _optional_principal(request)
        session = await forge.get_session(session_id)
        if session is not None and principal is not None:
            try:
                await forge.ensure_access(session, principal, "report_activity")
            except SessionAccessDeniedError:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Not authorized to report activity for session {session_id}",
                )

        try:
            activity_state = SessionActivityState(data.state)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Invalid activity state: {data.state}",
            )

        logger.info(
            "Activity report: session=%s state=%s metadata=%s",
            _sanitize_log(session_id),
            _sanitize_log(data.state),
            _sanitize_log(data.metadata),
        )
        try:
            updated = await forge.update_activity(session_id, activity_state, data.metadata)
            logger.info(
                "Activity updated: session=%s state=%s broadcaster=%s",
                _sanitize_log(session_id),
                updated.activity_state,
                forge.has_broadcaster,
            )
        except SessionNotFoundError:
            logger.warning("Activity report for unknown session: %s", _sanitize_log(session_id))
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        except Exception:
            logger.exception("Activity update failed for session %s", _sanitize_log(session_id))

    @router.patch(
        "/sessions/{session_id}/archive",
        response_model=SessionResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
        tags=["Sessions"],
    )
    async def archive_session(
        request: Request, session_id: UUID = Path(description="Unique session identifier")
    ) -> SessionResponse:
        """Archive a session. Stops pod if running."""
        principal = await _optional_principal(request)
        try:
            session = await forge.archive_session(
                session_id,
                principal=principal,
            )
            return SessionResponse.from_session(session)
        except SessionAccessDeniedError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied to session {session_id}",
            )
        except SessionNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        except SessionStateError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            )

    @router.patch(
        "/sessions/{session_id}/restore",
        response_model=SessionResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
        tags=["Sessions"],
    )
    async def restore_session(
        request: Request, session_id: UUID = Path(description="Unique session identifier")
    ) -> SessionResponse:
        """Restore an archived session to stopped state."""
        principal = await _optional_principal(request)
        try:
            session = await forge.restore_session(
                session_id,
                principal=principal,
            )
            return SessionResponse.from_session(session)
        except SessionAccessDeniedError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied to session {session_id}",
            )
        except SessionNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        except SessionStateError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            )

    @router.get("/models", response_model=list[ModelInfo], tags=["Models & Stats"])
    async def list_models() -> list[ModelInfo]:
        """List available models."""
        try:
            models = forge.list_models()
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            )
        return [ModelInfo.from_model(m) for m in models]

    @router.get("/stats", response_model=StatsResponse, tags=["Models & Stats"])
    async def get_stats() -> StatsResponse:
        """Get aggregate statistics for the dashboard."""
        try:
            stats = await forge.get_stats()
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            )
        return StatsResponse(
            active_sessions=stats.active_sessions,
            total_sessions=stats.total_sessions,
            tokens_today=stats.tokens_today,
            local_tokens=stats.local_tokens,
            cloud_tokens=stats.cloud_tokens,
            cost_today=float(stats.cost_today),
        )

    @router.post(
        "/sessions/{session_id}/usage",
        response_model=TokenUsageResponse,
        status_code=status.HTTP_201_CREATED,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["Sessions"],
    )
    async def report_token_usage(
        request: Request,
        session_id: UUID = Path(description="Unique session identifier"),
        data: TokenUsageReport = ...,
    ) -> TokenUsageResponse:
        """Report token usage for a session."""
        if token_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Token service not available",
            )

        # Authorization: caller must own the session
        principal = await _optional_principal(request)
        session = await forge.get_session(session_id)
        if session is not None and principal is not None:
            try:
                await forge.ensure_access(session, principal, "report_usage")
            except SessionAccessDeniedError:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Not authorized to report usage for session {session_id}",
                )

        provider = ModelProvider(data.provider)

        try:
            record = await forge.record_usage(
                session_id=session_id,
                tokens=data.tokens,
                provider=provider,
                model=data.model,
                message_count=data.message_count,
                cost=data.cost,
            )
            return TokenUsageResponse(
                id=str(record.id),
                session_id=str(record.session_id),
                recorded_at=record.recorded_at.isoformat(),
                tokens=record.tokens,
                provider=record.provider.value,
                model=record.model,
                cost=float(record.cost) if record.cost is not None else None,
            )
        except SessionNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        except SessionNotRunningError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            )

    @router.get(
        "/sessions/{session_id}/logs",
        responses={
            404: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
        },
        tags=["Sessions"],
    )
    async def get_session_logs(
        session_id: UUID = Path(description="Unique session identifier"),
        lines: int = Query(
            default=100,
            ge=1,
            le=2000,
            description="Number of log lines to retrieve",
        ),
        level: str = Query(
            default="DEBUG",
            description="Minimum log level (DEBUG, INFO, WARNING, ERROR)",
        ),
    ) -> dict:
        """Proxy log retrieval from a running session pod.

        Fetches logs from the Skuld broker's in-memory log buffer via its
        ``GET /api/logs`` endpoint.
        """
        try:
            _, base_url = await forge.get_session_proxy_target(session_id)
        except LookupError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {session_id} has no active endpoint",
            )

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{base_url}/api/logs",
                    params={"lines": lines, "level": level},
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            logger.warning("Log proxy failed for session %s: %s", _sanitize_log(session_id), e)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to fetch logs from session pod: {e.response.status_code}",
            )
        except httpx.RequestError as e:
            logger.warning(
                "Log proxy connection failed for session %s: %s",
                _sanitize_log(session_id),
                e,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not connect to session pod: {e}",
            )

    @router.get(
        "/sessions/{session_id}/logs/aggregate",
        responses={
            404: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
        },
        tags=["Sessions"],
    )
    async def get_session_logs_aggregate(
        session_id: UUID = Path(description="Unique session identifier"),
        lines: int = Query(
            default=200,
            ge=1,
            le=5000,
            description="Number of interleaved log lines to retrieve",
        ),
        level: str = Query(
            default="DEBUG",
            description="Minimum log level (DEBUG, INFO, WARNING, ERROR)",
        ),
        participants: str | None = Query(
            default=None,
            description="Comma-separated participant ids to include",
        ),
        query: str = Query(
            default="",
            description="Case-insensitive text filter applied to participant, source, and message",
        ),
    ) -> dict:
        """Proxy aggregated broker + flock logs from a running session pod."""
        try:
            session, base_url = await forge.get_session_proxy_target(session_id)
        except LookupError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {session_id} has no active endpoint",
            )

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{base_url}/api/logs/aggregate",
                    params={
                        "lines": lines,
                        "level": level,
                        "participants": participants,
                        "query": query,
                    },
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == status.HTTP_404_NOT_FOUND:
                workspace_dir = _workspace_dir_from_code_endpoint(session.code_endpoint)
                if workspace_dir is not None:
                    requested_participants = (
                        {item.strip() for item in participants.split(",") if item.strip()}
                        if participants
                        else None
                    )
                    payload = aggregate_workspace_logs(
                        workspace_dir,
                        lines=lines,
                        level=level,
                        participants=requested_participants,
                        query=query,
                    )
                    payload["session_id"] = str(session.id)
                    return payload
            logger.warning(
                "Aggregate log proxy failed for session %s: %s",
                _sanitize_log(session_id),
                e,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to fetch aggregate logs from session pod: {e.response.status_code}",
            )
        except httpx.RequestError as e:
            logger.warning(
                "Aggregate log proxy connection failed for session %s: %s",
                _sanitize_log(session_id),
                e,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not connect to session pod: {e}",
            )

    @router.post(
        "/sessions/{session_id}/messages",
        tags=["Sessions"],
        responses={
            404: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
        },
    )
    async def send_session_message(
        request: Request,
        session_id: UUID = Path(description="Unique session identifier"),
        body: dict = ...,
    ) -> dict:
        """Send a user message to a running session via its WebSocket."""
        import ssl

        from websockets.asyncio.client import connect

        content = body.get("content", "")
        if not content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="content is required",
            )

        # Verify the caller owns this session
        principal = await extract_principal(request)
        try:
            session, _ = await forge.get_session_proxy_target(session_id)
        except LookupError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {session_id} has no active endpoint",
            )
        if session.owner_id and session.owner_id != principal.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to message this session",
            )

        # Build WS URL with access token
        ws_url = session.chat_endpoint
        auth = request.headers.get("authorization", "")
        token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
        if token:
            sep = "&" if "?" in ws_url else "?"
            ws_url = f"{ws_url}{sep}access_token={token}"

        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        try:
            async with connect(ws_url, ssl=ssl_ctx, open_timeout=10) as ws:
                # Drain any pending messages from the server
                try:
                    while True:
                        await asyncio.wait_for(ws.recv(), timeout=1)
                except TimeoutError:
                    pass
                # Send the user message
                await ws.send(json.dumps({"type": "user", "content": content}))
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to send message to session: {e}",
            )

        return {"status": "sent", "session_id": str(session_id)}

    @router.get(
        "/sessions/{session_id}/conversation",
        tags=["Sessions"],
        responses={
            404: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
        },
    )
    async def get_conversation(
        request: Request,
        session_id: UUID = Path(description="Unique session identifier"),
    ) -> dict:
        """Proxy conversation history retrieval from a running session pod."""
        try:
            _, base_url = await forge.get_session_proxy_target(session_id)
        except LookupError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {session_id} has no active endpoint",
            )

        # Forward the caller's auth to the session pod
        headers = {}
        auth = request.headers.get("authorization")
        if auth:
            headers["Authorization"] = auth

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{base_url}/api/conversation/history",
                    headers=headers,
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to fetch conversation from session pod: {e.response.status_code}",
            )
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not connect to session pod: {e}",
            )

        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )

    # --- Chronicle ingestion from broker ---

    @router.post(
        "/sessions/{session_id}/chronicle",
        response_model=ChronicleResponse,
        status_code=status.HTTP_201_CREATED,
        responses={
            404: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["Chronicles"],
    )
    async def report_chronicle(
        request: Request,
        session_id: UUID = Path(description="Unique session identifier"),
        data: BrokerChronicleReport = ...,
    ) -> ChronicleResponse:
        """Ingest chronicle data reported by the Skuld broker at shutdown.

        Creates a new DRAFT chronicle or enriches an existing one.
        Mirrors the ``/sessions/{id}/usage`` pattern for token reporting.
        """
        # Authorization: caller must own the session
        principal = await _optional_principal(request)
        session = await forge.get_session(session_id)
        if session is not None and principal is not None:
            try:
                await forge.ensure_access(session, principal, "report_chronicle")
            except SessionAccessDeniedError:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Not authorized to report chronicle for session {session_id}",
                )

        try:
            chronicle = await forge.create_or_update_chronicle_from_broker(
                session_id=session_id,
                summary=data.summary,
                key_changes=data.key_changes,
                unfinished_work=data.unfinished_work,
                duration_seconds=data.duration_seconds,
            )
            return ChronicleResponse.from_chronicle(chronicle)
        except RuntimeError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(e),
            )
        except SessionNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )

    # --- Chronicle endpoints ---

    @router.get("/chronicles", response_model=list[ChronicleResponse], tags=["Chronicles"])
    async def list_chronicles(
        project: str | None = Query(
            default=None,
            description="Filter by project name",
        ),
        repo: str | None = Query(
            default=None,
            description="Filter by repository URL",
        ),
        model_name: str | None = Query(
            default=None,
            description="Filter by LLM model name",
        ),
        tags: str | None = Query(
            default=None,
            description="Comma-separated tags to filter by",
        ),
        limit: int = Query(
            default=50,
            ge=1,
            le=200,
            description="Maximum number of results to return",
        ),
        offset: int = Query(
            default=0,
            ge=0,
            description="Number of results to skip for pagination",
        ),
    ) -> list[ChronicleResponse]:
        """List chronicles with optional filters."""
        tag_list = [t.strip() for t in tags.split(",")] if tags else None
        try:
            chronicles = await forge.list_chronicles(
                project=project,
                repo=repo,
                model=model_name,
                tags=tag_list,
                limit=limit,
                offset=offset,
            )
        except RuntimeError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(e),
            )
        return [ChronicleResponse.from_chronicle(c) for c in chronicles]

    @router.post(
        "/chronicles",
        response_model=ChronicleResponse,
        status_code=status.HTTP_201_CREATED,
        responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["Chronicles"],
    )
    async def create_chronicle(data: ChronicleCreate) -> ChronicleResponse:
        """Create a chronicle from a session's current state."""
        try:
            chronicle = await forge.create_chronicle(data.session_id)
            return ChronicleResponse.from_chronicle(chronicle)
        except RuntimeError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(e),
            )
        except SessionNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {data.session_id}",
            )

    @router.get(
        "/chronicles/{chronicle_id}",
        response_model=ChronicleResponse,
        responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["Chronicles"],
    )
    async def get_chronicle(
        chronicle_id: UUID = Path(description="Unique chronicle identifier"),
    ) -> ChronicleResponse:
        """Get a chronicle by ID."""
        try:
            chronicle = await forge.get_chronicle(chronicle_id)
        except RuntimeError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(e),
            )
        if chronicle is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Chronicle not found: {chronicle_id}",
            )
        return ChronicleResponse.from_chronicle(chronicle)

    @router.patch(
        "/chronicles/{chronicle_id}",
        response_model=ChronicleResponse,
        responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["Chronicles"],
    )
    async def update_chronicle(
        chronicle_id: UUID = Path(description="Unique chronicle identifier"),
        data: ChronicleUpdate = ...,
    ) -> ChronicleResponse:
        """Update a chronicle's mutable fields."""
        try:
            chronicle = await forge.update_chronicle(
                chronicle_id,
                summary=data.summary,
                key_changes=data.key_changes,
                unfinished_work=data.unfinished_work,
                tags=data.tags,
                status=data.status,
            )
            return ChronicleResponse.from_chronicle(chronicle)
        except RuntimeError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(e),
            )
        except ChronicleNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Chronicle not found: {chronicle_id}",
            )

    @router.delete(
        "/chronicles/{chronicle_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["Chronicles"],
    )
    async def delete_chronicle(
        chronicle_id: UUID = Path(description="Unique chronicle identifier"),
    ) -> None:
        """Delete a chronicle."""
        try:
            deleted = await forge.delete_chronicle(chronicle_id)
        except RuntimeError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(e),
            )
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Chronicle not found: {chronicle_id}",
            )

    @router.post(
        "/chronicles/{chronicle_id}/reforge",
        response_model=SessionResponse,
        responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["Chronicles"],
    )
    async def reforge_chronicle(
        chronicle_id: UUID = Path(description="Unique chronicle identifier"),
    ) -> SessionResponse:
        """Relaunch a session from a chronicle entry."""
        try:
            session = await forge.reforge_chronicle(chronicle_id)
            return SessionResponse.from_session(session)
        except RuntimeError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(e),
            )
        except ChronicleNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Chronicle not found: {chronicle_id}",
            )

    @router.get(
        "/chronicles/{chronicle_id}/chain",
        response_model=list[ChronicleResponse],
        responses={503: {"model": ErrorResponse}},
        tags=["Chronicles"],
    )
    async def get_chronicle_chain(
        chronicle_id: UUID = Path(description="Unique chronicle identifier"),
    ) -> list[ChronicleResponse]:
        """Get the full reforge chain for a chronicle."""
        try:
            chain = await forge.get_chronicle_chain(chronicle_id)
        except RuntimeError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(e),
            )
        return [ChronicleResponse.from_chronicle(c) for c in chain]

    @router.get(
        "/sessions/{session_id}/chronicle",
        response_model=ChronicleResponse,
        responses={
            404: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["Chronicles"],
    )
    async def get_session_chronicle(
        session_id: UUID = Path(description="Unique session identifier"),
    ) -> ChronicleResponse:
        """Get the most recent chronicle for a session."""
        try:
            chronicle = await forge.get_session_chronicle(session_id)
        except RuntimeError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(e),
            )
        if chronicle is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No chronicle found for session: {session_id}",
            )
        return ChronicleResponse.from_chronicle(chronicle)

    # --- Timeline endpoints ---

    @router.get(
        "/chronicles/{session_id}/timeline",
        response_model=TimelineResponseModel,
        responses={
            404: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["Timeline"],
    )
    async def get_timeline(
        session_id: UUID = Path(description="Session identifier for timeline lookup"),
    ) -> TimelineResponseModel:
        """Get the event timeline for a session's chronicle."""
        try:
            timeline = await forge.get_timeline(session_id)
        except RuntimeError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(e),
            )
        if timeline is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No chronicle data for session: {session_id}",
            )
        return TimelineResponseModel(
            events=[
                TimelineEventResponse(
                    t=ev.t,
                    type=ev.type.value,
                    label=ev.label,
                    tokens=ev.tokens,
                    action=ev.action,
                    ins=ev.ins,
                    **{"del": ev.del_},
                    hash=ev.hash,
                    exit=ev.exit_code,
                )
                for ev in timeline.events
            ],
            files=[
                TimelineFileResponse(
                    path=f.path,
                    status=f.status,
                    ins=f.ins,
                    **{"del": f.del_},
                )
                for f in timeline.files
            ],
            commits=[
                TimelineCommitResponse(hash=c.hash, msg=c.msg, time=c.time)
                for c in timeline.commits
            ],
            token_burn=timeline.token_burn,
        )

    @router.post(
        "/chronicles/{session_id}/timeline",
        response_model=TimelineEventResponse,
        status_code=status.HTTP_201_CREATED,
        responses={
            404: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["Timeline"],
    )
    async def add_timeline_event(
        request: Request,
        session_id: UUID = Path(description="Session identifier for timeline lookup"),
        data: TimelineEventCreate = ...,
    ) -> TimelineEventResponse:
        """Add a timeline event for a session's chronicle."""
        try:
            chronicle = await forge.ensure_session_chronicle(session_id)
        except RuntimeError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(e),
            )
        except SessionNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )

        # Authorization: caller must own the session
        principal = await extract_principal(request)
        session = await forge.get_session(session_id)
        if session is not None:
            try:
                await forge.ensure_access(session, principal, "report_timeline")
            except SessionAccessDeniedError:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Not authorized to report timeline for session {session_id}",
                )

        event = TimelineEvent(
            id=uuid4(),
            chronicle_id=chronicle.id,
            session_id=session_id,
            t=data.t,
            type=TimelineEventType(data.type),
            label=data.label,
            tokens=data.tokens,
            action=data.action,
            ins=data.ins,
            del_=data.del_,
            hash=data.hash,
            exit_code=data.exit_code,
            created_at=datetime.now(UTC),
        )

        try:
            stored = await forge.add_timeline_event(session_id, event)
        except RuntimeError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Timeline service not available",
            )

        return TimelineEventResponse(
            t=stored.t,
            type=stored.type.value,
            label=stored.label,
            tokens=stored.tokens,
            action=stored.action,
            ins=stored.ins,
            **{"del": stored.del_},
            hash=stored.hash,
            exit=stored.exit_code,
        )

    # --- Diff proxy endpoint (called by web UI) ---

    @router.get(
        "/chronicles/{session_id}/diff",
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
        },
        tags=["Chronicles"],
    )
    async def get_chronicle_diff(
        request: Request,
        session_id: UUID = Path(description="Session identifier for diff lookup"),
        file: str = Query(
            ...,
            description="File path relative to workspace",
        ),
        base: str = Query(
            default="last-commit",
            description="Diff base: last-commit or default-branch",
        ),
    ) -> dict:
        """Get git diff for a file in a session workspace via Skuld."""
        if base not in ("last-commit", "default-branch"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Invalid base parameter: {base}. Must be 'last-commit' or 'default-branch'"
                ),
            )

        try:
            _, base_url = await forge.get_session_proxy_target(session_id)
        except LookupError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {session_id} has no active endpoint",
            )

        # Forward auth header so the proxy can reach Skuld through envoy
        proxy_headers: dict[str, str] = {}
        auth_header = request.headers.get("authorization")
        if auth_header:
            proxy_headers["Authorization"] = auth_header

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{base_url}/api/diff",
                    params={"file": file, "base": base},
                    headers=proxy_headers,
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            logger.warning(
                "Diff proxy failed for session %s: %s",
                _sanitize_log(session_id),
                e,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(f"Failed to fetch diff from session pod: {e.response.status_code}"),
            )
        except httpx.RequestError as e:
            logger.warning(
                "Diff proxy connection failed for session %s: %s",
                _sanitize_log(session_id),
                e,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not connect to session pod: {e}",
            )

    # ── Workspace endpoints ─────────────────────────────────────────

    @router.get(
        "/workspaces",
        response_model=list[WorkspaceResponse],
        tags=["Workspaces"],
    )
    async def list_workspaces(
        request: Request,
        status_filter: str | None = Query(
            None,
            alias="status",
            description="Filter by workspace status (active, archived)",
        ),
    ):
        """List the current user's workspaces."""
        principal = await _optional_principal(request)
        if principal is None:
            return []
        workspace_forge = _workspace_forge(request)
        ws_status = WorkspaceStatus(status_filter) if status_filter else None
        workspaces = await workspace_forge.list_workspaces(
            user_id=principal.user_id,
            status=ws_status,
        )
        sessions_map = await workspace_forge.get_sessions_for_workspaces(workspaces)
        return [
            WorkspaceResponse.from_workspace(ws, sessions_map.get(ws.session_id))
            for ws in workspaces
        ]

    @router.delete(
        "/workspaces/{session_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["Workspaces"],
    )
    async def delete_workspace(
        request: Request,
        session_id: UUID = Path(description="Session identifier whose workspace to delete"),
    ):
        """Delete a workspace PVC by session ID."""
        principal = await _optional_principal(request)
        workspace_forge = _workspace_forge(request)
        # Verify ownership before deleting
        workspaces = await workspace_forge.list_workspaces(
            user_id=principal.user_id if principal else "",
        )
        if not any(str(ws.session_id) == str(session_id) for ws in workspaces):
            raise HTTPException(status_code=404, detail="Workspace not found")
        deleted = await workspace_forge.delete_workspace_by_session(str(session_id))
        if not deleted:
            raise HTTPException(status_code=404, detail="Workspace not found")

    @router.get(
        "/admin/workspaces",
        response_model=list[WorkspaceResponse],
        tags=["Admin"],
    )
    async def list_all_workspaces(
        request: Request,
        user_id: str | None = Query(
            None,
            description="Filter by user ID (IDP sub claim)",
        ),
        status_filter: str | None = Query(
            None,
            alias="status",
            description="Filter by workspace status (active, archived)",
        ),
        _: Principal = Depends(require_role("volundr:admin")),
    ):
        """List all workspaces (admin only)."""
        workspace_forge = _workspace_forge(request)
        ws_status = WorkspaceStatus(status_filter) if status_filter else None
        if user_id:
            workspaces = await workspace_forge.list_workspaces(user_id=user_id, status=ws_status)
        else:
            workspaces = await workspace_forge.list_all_workspaces(ws_status)
        sessions_map = await workspace_forge.get_sessions_for_workspaces(workspaces)
        return [
            WorkspaceResponse.from_workspace(ws, sessions_map.get(ws.session_id))
            for ws in workspaces
        ]

    @router.post(
        "/workspaces/bulk-delete",
        status_code=status.HTTP_200_OK,
        tags=["Workspaces"],
    )
    async def bulk_delete_workspaces(
        request: Request,
        body: dict,
    ):
        """Delete multiple workspaces by session IDs."""
        principal = await _optional_principal(request)
        session_ids = _workspace_bulk_delete_session_ids(body)
        if not session_ids:
            return {"deleted": 0, "failed": []}

        workspace_forge = _workspace_forge(request)
        user_workspaces = await workspace_forge.list_workspaces(
            user_id=principal.user_id if principal else "",
        )
        owned_session_ids = {str(ws.session_id) for ws in user_workspaces}

        deleted = 0
        failed = []
        for sid in session_ids:
            if str(sid) not in owned_session_ids:
                failed.append({"session_id": sid, "error": "Not found or not owned"})
                continue
            try:
                ok = await workspace_forge.delete_workspace_by_session(str(sid))
                if ok:
                    deleted += 1
                else:
                    failed.append({"session_id": sid, "error": "Not found"})
            except Exception:
                logger.error("Failed to delete workspace for session %s", _sanitize_log(sid))
                failed.append({"session_id": sid, "error": "Internal error"})
        return {"deleted": deleted, "failed": failed}

    @router.post(
        "/admin/workspaces/bulk-delete",
        status_code=status.HTTP_200_OK,
        tags=["Admin"],
    )
    async def admin_bulk_delete_workspaces(
        request: Request,
        body: dict,
        _: Principal = Depends(require_role("volundr:admin")),
    ):
        """Delete multiple workspaces by session IDs (admin)."""
        session_ids = _workspace_bulk_delete_session_ids(body)
        if not session_ids:
            return {"deleted": 0, "failed": []}

        workspace_forge = _workspace_forge(request)
        deleted = 0
        failed = []
        for sid in session_ids:
            try:
                ok = await workspace_forge.delete_workspace_by_session(str(sid))
                if ok:
                    deleted += 1
                else:
                    failed.append({"session_id": sid, "error": "Not found"})
            except Exception:
                logger.error("Failed to delete workspace for session %s", _sanitize_log(sid))
                failed.append({"session_id": sid, "error": "Internal error"})
        return {"deleted": deleted, "failed": failed}

    return router
