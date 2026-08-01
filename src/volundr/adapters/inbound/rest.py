"""FastAPI REST adapter for session management."""

import asyncio
import contextlib
import json
import logging
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path as FilePath
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from niuu.domain.services.token_scope import OPENSHELL_SESSION_TOKEN_USE, require_scope
from niuu.domain.session_endpoint import public_session_endpoint
from skuld.conversation_shallow import SHALLOW_DETAIL, elide_turns
from volundr.adapters.inbound.auth import extract_principal, require_role
from volundr.config import PermissionAutoApprovalConfig
from volundr.domain.models import (
    Chronicle,
    ChronicleStatus,
    CleanupTarget,
    DevicePlatform,
    DeviceToken,
    ExternalSessionRecord,
    GitProviderType,
    GitSource,
    LocalMountSource,
    ModelProvider,
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
    DeviceTokenRepository,
    EventBroadcaster,
    GitAuthError,
    GitRepoNotFoundError,
    PricingProvider,
)
from volundr.domain.services import (
    ChronicleNotFoundError,
    ChronicleService,
    ExternalSessionAlreadyImportedError,
    ExternalSessionNotFoundError,
    ExternalSessionPathNotAllowedError,
    ExternalSessionProviderNotFoundError,
    ExternalSessionService,
    ExternalSessionWorkspaceError,
    ForgeService,
    ProviderInfo,
    RepoService,
    RepoValidationError,
    SessionAccessDeniedError,
    SessionArchiveNotAvailableError,
    SessionNotFoundError,
    SessionNotRunningError,
    SessionService,
    SessionStateError,
    StatsService,
    TokenService,
)
from volundr.domain.services.permission_auto_approval import (
    evaluate_permission_auto_approval,
)
from volundr.log_aggregate import aggregate_workspace_logs
from volundr.session_archive import load_workspace_transcript

logger = logging.getLogger(__name__)
WORKFLOW_GATE_INTENT_HEADER = "x-niuu-workflow-gate-intent"
DEFAULT_OPENSHELL_INTERNAL_GATEWAY_URL = "http://openshell.openshell.svc.cluster.local:8080"
OPENSHELL_SERVICE_HOST_SUFFIX = ".openshell.localhost"
# How long send_session_message holds the WS open waiting for the broker's
# correlated delivery ACK before reporting the message as "pending" (the broker's
# bounded retry then drives it to delivered/failed). Named so it is not a magic
# number; the broker — not this grace — owns the delivery durability guarantee, so
# a short grace is correct: a no-ACK is reported as pending/accepted, never "sent".
SEND_MESSAGE_ACK_GRACE_SECONDS = 3.0


def _public_session_endpoint(
    endpoint: str | None,
    session_id: str = "",
    *,
    public_host: str = "127.0.0.1",
) -> str | None:
    """Normalize loopback session endpoints for browser-facing clients."""
    return public_session_endpoint(
        endpoint,
        session_id=session_id,
        public_host=public_host,
    )


def _public_workload_url(session: Session, *keys: str) -> str | None:
    """Expose one explicitly configured HTTP(S) workload URL without leaking config."""
    value = next(
        (
            str(session.workload_config.get(key) or "").strip()
            for key in keys
            if str(session.workload_config.get(key) or "").strip()
        ),
        "",
    )
    if not value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    return value


def _server_side_ws_connect_overrides(
    ws_url: str,
    *,
    gateway_url: str = DEFAULT_OPENSHELL_INTERNAL_GATEWAY_URL,
) -> dict[str, object]:
    try:
        parsed = urlsplit(ws_url)
    except ValueError:
        return {}
    if not parsed.hostname or not parsed.hostname.endswith(OPENSHELL_SERVICE_HOST_SUFFIX):
        return {}
    try:
        gateway = urlsplit(gateway_url)
    except ValueError:
        return {}
    if not gateway.hostname:
        return {}
    return {
        "host": gateway.hostname,
        "port": gateway.port or (443 if gateway.scheme == "https" else 80),
        "proxy": None,
    }


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


def _workspace_dir_from_session(session: Session) -> FilePath | None:
    """Resolve a local workspace path from session metadata when possible."""
    workspace_dir = _workspace_dir_from_code_endpoint(session.code_endpoint)
    if workspace_dir is not None:
        return workspace_dir
    if isinstance(session.source, LocalMountSource) and session.source.local_path:
        path = FilePath(session.source.local_path)
        return path if path.exists() else None
    return None


# FAULT-C durable-count cache: `session_id -> (event_log_seq, durable_turn_count)`. The FAULT-C
# reconciliation needs the durable turn count to detect a desynced (short) live body, but rebuilding
# the whole durable transcript on every poll is ~4s on a big session (profiled). The count is stable
# while the event log hasn't grown, so we key the cached count on MAX(seq): a hit at the same seq
# skips the rebuild entirely; a new seq (or a short live body) falls back to the exact rebuild.
_DURABLE_COUNT_CACHE: dict[str, tuple[int, int]] = {}
_DURABLE_COUNT_CACHE_MAX = 512


def _durable_count_cache_put(session_id: str, seq: int, count: int) -> None:
    if (
        len(_DURABLE_COUNT_CACHE) >= _DURABLE_COUNT_CACHE_MAX
        and session_id not in _DURABLE_COUNT_CACHE
    ):
        # Crude bound: drop an arbitrary existing entry (FIFO-ish via iteration order).
        _DURABLE_COUNT_CACHE.pop(next(iter(_DURABLE_COUNT_CACHE)), None)
    _DURABLE_COUNT_CACHE[session_id] = (seq, count)


# Sessions with an in-flight background durable-count warm (dedup so a burst of cold polls spawns
# at most ONE rebuild per session).
_DURABLE_WARMING: set[str] = set()


async def _warm_durable_count(
    forge: ForgeService, session_id: UUID, seq: int, sid_str: str
) -> None:
    """Background: rebuild the durable transcript ONCE to populate the count cache at ``seq`` so
    the next conversation poll can detect a desync WITHOUT blocking the (cold) open. Fire-and-
    forget; never raises into the event loop."""
    try:
        durable = await forge.get_transcript(session_id)
        turns = durable.get("turns") if isinstance(durable, dict) else None
        _durable_count_cache_put(sid_str, seq, len(turns) if isinstance(turns, list) else 0)
    except Exception:  # pragma: no cover - a background warmer must never crash the loop
        pass
    finally:
        _DURABLE_WARMING.discard(sid_str)


def _live_transcript_is_renderable(payload: object) -> bool:
    """True if a live-pod conversation result is worth returning verbatim.

    BUG-2: a tmux session whose WS crashed mid-turn keeps an alive pod that answers
    /conversation/history with HTTP 200 but an empty / seed-only body — returning that
    verbatim renders a "dead" session. This gate lets such a body fall through to the
    durable-log rebuild WITHOUT discarding a healthy STILL-STREAMING first turn (which is
    legitimately seed-only but `is_active` / has a `last_activity` hint).
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("turns"), list):
        return False
    turns = payload["turns"]
    if payload.get("is_active") or (payload.get("last_activity") or ""):
        return True  # streaming / thinking — keep verbatim
    if not turns:
        return False
    has_assistant = any(isinstance(t, dict) and t.get("role") == "assistant" for t in turns)
    if not has_assistant and len(turns) <= 1:
        return False  # seed-only, not active -> rebuild from the durable log
    return True


def _live_body_has_in_progress_turn(payload: object) -> bool:
    """True if the live conversation body carries a still-running in_progress turn.

    The FAULT C count-vs-durable reconciliation must NOT run while the live body holds
    an in_progress turn, or it can drop the live streaming turn in favour of a durable
    rebuild that merely happens to count more turns. The broker's `is_active` /
    `last_activity` streaming hints are NOT a reliable proxy: a tool_use-only assistant
    block (the entire tool-execution window) leaves those flags empty while
    `_serialize_in_progress_turn()` still emits the running turn. So gate on the turn
    payload itself — the last turn marked `in_progress` (or `metadata.status ==
    'in_progress'`) — rather than the buffer flags.
    """
    if not isinstance(payload, dict):
        return False
    turns = payload.get("turns")
    if not isinstance(turns, list) or not turns:
        return False
    last = turns[-1]
    if not isinstance(last, dict):
        return False
    if last.get("in_progress") is True:
        return True
    metadata = last.get("metadata")
    return isinstance(metadata, dict) and metadata.get("status") == "in_progress"


def _session_proxy_url(base_url: str, *path_segments: str) -> str:
    """Build a validated session-pod URL from a trusted base and encoded path segments."""
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Session proxy target must be an absolute http(s) URL")

    segments = [quote(segment, safe="") for segment in path_segments]
    base_path = parsed.path.rstrip("/")
    suffix = "/".join(segments)
    if base_path and suffix:
        path = f"{base_path}/{suffix}"
    elif base_path:
        path = base_path
    elif suffix:
        path = f"/{suffix}"
    else:
        path = "/"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _server_side_http_proxy_target(
    url: str,
    *,
    gateway_url: str = DEFAULT_OPENSHELL_INTERNAL_GATEWAY_URL,
) -> tuple[str, dict[str, str]]:
    """Route an OpenShell service URL through the in-cluster gateway."""
    parsed = urlsplit(url)
    if not parsed.hostname or not parsed.hostname.endswith(OPENSHELL_SERVICE_HOST_SUFFIX):
        return url, {}

    gateway = urlsplit(gateway_url)
    if not gateway.scheme or not gateway.netloc:
        raise ValueError("OpenShell internal gateway must be an absolute URL")

    routed_url = urlunsplit(
        (gateway.scheme, gateway.netloc, parsed.path, parsed.query, parsed.fragment)
    )
    return routed_url, {"Host": parsed.netloc}


def _fallback_workspace_logs(
    session: Session,
    *,
    lines: int,
    level: str,
    participants: set[str] | None,
    query: str,
) -> dict | None:
    """Read logs directly from a local file:// workspace when available."""
    workspace_dir = _workspace_dir_from_session(session)
    if workspace_dir is None:
        return None
    payload = aggregate_workspace_logs(
        workspace_dir,
        lines=lines,
        level=level,
        participants=participants,
        query=query,
    )
    payload["session_id"] = str(session.id)
    return payload


def _fallback_workspace_transcript(session: Session) -> dict | None:
    """Read the persisted transcript directly from a local file:// workspace."""
    workspace_dir = _workspace_dir_from_session(session)
    if workspace_dir is None:
        return None
    return load_workspace_transcript(workspace_dir, str(session.id))


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
    persona_name: str = Field(
        default="",
        max_length=255,
        description="Persona from the authenticated user's Ravn persona catalog",
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
    launch_spec: str | None = Field(
        default=None,
        max_length=255,
        description="Launch spec name (system) to apply",
    )
    launch_spec_id: UUID | None = Field(
        default=None,
        description="User launch spec id for runtime configuration",
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
        description="Workload type: 'session' (default) or 'ravn_flock' for runing parties",
    )
    workload_config: dict = Field(
        default_factory=dict,
        description="Workload-specific configuration (e.g. personas, mesh, mimir settings)",
    )

    @model_validator(mode="before")
    @classmethod
    def _alias_session_definition(cls, data: object) -> object:
        """Accept ``session_definition`` as a backwards-compatible alias for ``definition``."""
        if not isinstance(data, dict):
            return data
        if data.get("definition"):
            return data
        alias_value = data.get("session_definition")
        if alias_value:
            merged = dict(data)
            merged["definition"] = alias_value
            return merged
        return data

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
                "launch_spec": "default",
            },
        },
    }


class PermissionAutoApprovalCheck(BaseModel):
    """Request model for checking one permission request against server policy."""

    request_id: str | None = Field(default=None, max_length=255)
    tool_name: str = Field(default="", max_length=255)
    description: str = Field(default="", max_length=4096)
    command: str | None = Field(default=None, max_length=100_000)
    input: dict[str, Any] = Field(default_factory=dict)


class PermissionAutoApprovalResponse(BaseModel):
    """Server-side decision for a permission request auto approval."""

    can_auto_approve: bool
    reason: str
    command: str | None = None
    delay_seconds: int
    matched_pattern: str | None = None


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

    launch_spec: str | None = Field(
        default=None,
        max_length=255,
        description="Launch spec name to use when starting",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "launch_spec": "default",
            },
        },
    }


class SessionImportRequest(BaseModel):
    """Request model for importing an external CLI session."""

    provider: str = Field(
        min_length=1,
        max_length=100,
        description="External session provider key (e.g. 'claude-code', 'codex')",
    )
    external_id: str = Field(
        min_length=1,
        max_length=255,
        description="Native session/thread identifier to import",
    )
    name: str | None = Field(
        default=None,
        max_length=255,
        description="Optional session name; derived from the harness when omitted",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "provider": "claude-code",
                "external_id": "2e877b9f-4b8a-4d46-8f00-03f6163addd5",
                "name": "imported-debug-session",
            },
        },
    }


class ExternalSessionResponse(BaseModel):
    """Response model for a discoverable external CLI session."""

    provider: str = Field(description="Provider key that discovered the session")
    harness: str = Field(description="CLI harness owning the session (claude, codex)")
    external_id: str = Field(description="Native session/thread identifier")
    workspace_path: str = Field(description="Working directory the session ran in")
    title: str = Field(description="Short human-readable summary")
    model: str = Field(description="Model the session used, when known")
    created_at: str | None = Field(description="ISO 8601 start timestamp, when known")
    updated_at: str | None = Field(description="ISO 8601 last-activity timestamp")
    live: bool = Field(description="Whether the session appears to be actively running")
    workspace_exists: bool = Field(description="Whether the workspace still exists on disk")
    workspace_allowed: bool = Field(
        description="Whether the workspace passes the allowed mount prefix policy",
    )
    imported_session_id: UUID | None = Field(
        description="Volundr session id when already imported",
    )
    importable: bool = Field(
        description=(
            "Whether the session can be imported right now (workspace present, "
            "allowed by policy, and not already imported)"
        ),
    )

    @classmethod
    def from_record(cls, record: ExternalSessionRecord) -> "ExternalSessionResponse":
        """Create response from domain model."""
        return cls(
            provider=record.provider,
            harness=record.harness,
            external_id=record.external_id,
            workspace_path=record.workspace_path,
            title=record.title,
            model=record.model,
            created_at=record.created_at.isoformat() if record.created_at else None,
            updated_at=record.updated_at.isoformat() if record.updated_at else None,
            live=record.live,
            workspace_exists=record.workspace_exists,
            workspace_allowed=record.workspace_allowed,
            imported_session_id=record.imported_session_id,
            importable=(
                record.workspace_exists
                and record.workspace_allowed
                and record.imported_session_id is None
            ),
        )


class DeleteSessionBody(BaseModel):
    """Optional request body for session deletion with cleanup targets."""

    cleanup: list[CleanupTarget] = Field(
        default_factory=list,
        description="Resources to permanently delete alongside the session",
    )


class ActivityReport(BaseModel):
    """Request model for reporting session activity state."""

    state: str = Field(description="Activity state (active/idle/tool_executing/awaiting_input)")
    state_since: datetime | None = Field(
        default=None,
        description=(
            "ISO8601 UTC timestamp of when the session entered this state. "
            "Optional for backward-compat with older brokers that omit it."
        ),
    )
    metadata: dict = Field(default_factory=dict, description="Activity metadata")

    @field_validator("metadata", mode="before")
    @classmethod
    def _coerce_metadata(cls, value: object) -> dict:
        """Tolerate a null / JSON-string / non-dict ``metadata`` instead of 422-ing the
        whole heartbeat. A 30s activity heartbeat that gets rejected freezes the session's
        ui_status/last_active for every client (FORGE tmux-reconnect bug), so we coerce a
        malformed-but-recoverable metadata to ``{}`` rather than drop the state update."""
        if value is None:
            return {}
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (ValueError, TypeError):
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return value if isinstance(value, dict) else {}


class DeviceRegistration(BaseModel):
    """Request model for registering a push device."""

    platform: str = Field(description="Device platform (ios/android/web)")
    token: str = Field(min_length=1, max_length=512, description="Push token / endpoint")
    app_bundle_id: str | None = Field(default=None, description="APNs topic (bundle id) to target")


class DeviceResponse(BaseModel):
    """Response model for a registered push device."""

    id: UUID
    platform: str
    token: str
    app_bundle_id: str | None = None
    created_at: str
    updated_at: str

    @classmethod
    def from_device(cls, device: DeviceToken) -> "DeviceResponse":
        return cls(
            id=device.id,
            platform=device.platform.value,
            token=device.token,
            app_bundle_id=device.app_bundle_id,
            created_at=device.created_at.isoformat(),
            updated_at=device.updated_at.isoformat(),
        )


class SessionResponse(BaseModel):
    """Response model for a session."""

    id: UUID = Field(description="Unique session identifier")
    name: str = Field(description="Human-readable session name")
    model: str = Field(description="LLM model identifier")
    persona_name: str = Field(default="", description="Persona attached at launch")
    source: SessionSource = Field(description="Workspace source config")
    status: SessionStatus = Field(description="Current lifecycle status")
    chat_endpoint: str | None = Field(
        description="Skuld chat proxy URL (null when not running)",
    )
    code_endpoint: str | None = Field(
        description="Editor IDE URL (null when not running)",
    )
    a2a_card_url: str | None = Field(
        default=None,
        serialization_alias="a2aCardUrl",
        description="Standard Agent Card URL when this workflow session is A2A-addressable",
    )
    a2a_endpoint_url: str | None = Field(
        default=None,
        serialization_alias="a2aEndpointUrl",
        description="Preferred A2A protocol endpoint when explicitly published by the workload",
    )
    environment_id: str | None = Field(
        default=None,
        serialization_alias="environmentId",
        description="Environment containing the addressable workflow session",
    )
    a2a_visibility: str = Field(
        default="user",
        serialization_alias="a2aVisibility",
        description="Explicit Agent Directory visibility scope",
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
    launch_spec_id: UUID | None = Field(
        default=None,
        description="User launch spec used to configure this session",
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
        description=(
            "Current activity state "
            "(provisioning/active/idle/tool_executing/awaiting_input/stopped)"
        ),
    )
    activity_state_since: str | None = Field(
        default=None,
        description=(
            "ISO 8601 UTC timestamp of when the session entered its current "
            "activity_state (null if never reported). Lets clients render an "
            "accurate elapsed time for the current state."
        ),
    )
    activity_metadata: dict = Field(
        default_factory=dict,
        description="Metadata from the latest activity report",
    )
    needs_attention: bool = Field(
        default=False,
        description=(
            "True when the session is blocked waiting on the user "
            "(activity_state == awaiting_input). Lets clients and the iOS widget "
            "highlight 'needs you' sessions without re-deriving the rule."
        ),
    )
    workload_type: str = Field(
        default="session",
        description="Workload type used to launch the session",
    )
    origin: str = Field(
        default="volundr",
        description="Where the session originated (volundr, claude, codex)",
    )
    external_session_id: str | None = Field(
        default=None,
        description="Native CLI session id for imported sessions",
    )
    cli_session_id: str | None = Field(
        default=None,
        description="Captured CLI/agent conversation id; present once the session is resumable",
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
    def from_session(
        cls,
        session: Session,
        *,
        public_host: str = "127.0.0.1",
    ) -> "SessionResponse":
        """Create response from domain model."""
        return cls(
            id=session.id,
            name=session.name,
            model=session.model,
            persona_name=str(session.workload_config.get("persona") or ""),
            source=session.source,
            status=session.status,
            chat_endpoint=_public_session_endpoint(
                session.chat_endpoint,
                str(session.id),
                public_host=public_host,
            ),
            code_endpoint=session.code_endpoint,
            a2a_card_url=_public_workload_url(
                session,
                "a2aCardUrl",
                "a2a_card_url",
            ),
            a2a_endpoint_url=_public_workload_url(
                session,
                "a2aEndpointUrl",
                "a2a_endpoint_url",
            ),
            environment_id=(
                str(
                    session.workload_config.get("environmentId")
                    or session.workload_config.get("environment_id")
                    or ""
                ).strip()
                or None
            ),
            a2a_visibility=(
                str(
                    session.workload_config.get("a2aVisibility")
                    or session.workload_config.get("a2a_visibility")
                    or "user"
                ).strip()
                or "user"
            ),
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
            launch_spec_id=session.launch_spec_id,
            archived_at=(session.archived_at.isoformat() if session.archived_at else None),
            owner_id=session.owner_id,
            tenant_id=session.tenant_id,
            activity_state=(session.activity_state.value if session.activity_state else None),
            activity_state_since=(
                session.activity_state_since.isoformat() if session.activity_state_since else None
            ),
            activity_metadata=session.activity_metadata,
            needs_attention=session.needs_attention,
            workload_type=session.workload_type,
            origin=session.origin,
            external_session_id=session.external_session_id,
            cli_session_id=session.cli_session_id,
        )


class SessionEndpoints(BaseModel):
    """Response model for session endpoints after start."""

    chat_endpoint: str = Field(description="Skuld chat proxy URL")
    code_endpoint: str = Field(description="Editor IDE URL")


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


class WorkflowGateResolveRequest(BaseModel):
    """Request body for resolving a live workflow gate."""

    decision: str
    notes: str = ""
    source: str = "human"


class HelpAnswerRequest(BaseModel):
    """Request body answering a session peer's pending help request."""

    answer: str
    source: str = "external"


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

    active_sessions: int = Field(default=0, description="Currently running sessions")
    total_sessions: int = Field(default=0, description="Total sessions (all statuses)")
    sessions_today: int = Field(default=0, description="Sessions created today")
    tokens_today: int = Field(default=0, description="Tokens consumed today")
    local_tokens: int = Field(default=0, description="Tokens from local models today")
    cloud_tokens: int = Field(default=0, description="Tokens from cloud models today")
    cost_today: float = Field(default=0.0, description="Total cloud cost today in USD")
    sparklines: dict[str, list[float]] = Field(
        default_factory=dict,
        description="Historical KPI samples for lightweight dashboard sparklines",
    )


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
    archive_service=None,
    *,
    external_session_service: ExternalSessionService | None = None,
    device_repository: DeviceTokenRepository | None = None,
    prefix: str = "/api/v1/forge",
    server_public_host: str = "127.0.0.1",
    openshell_internal_gateway_url: str = DEFAULT_OPENSHELL_INTERNAL_GATEWAY_URL,
) -> APIRouter:
    """Create FastAPI router with session, stats, token, repo, and SSE endpoints."""
    router = APIRouter(prefix=prefix)

    def _session_response(session: Session) -> SessionResponse:
        return SessionResponse.from_session(session, public_host=server_public_host)

    def _http_proxy_target(url: str) -> tuple[str, dict[str, str]]:
        return _server_side_http_proxy_target(
            url,
            gateway_url=openshell_internal_gateway_url,
        )

    @router.get("/version", tags=["Forge"])
    async def forge_version() -> dict:
        """Identify the running Forge API build (git sha / branch) so an operator
        can confirm which version is live. Skuld brokers run from the same
        checkout, so the same sha applies to the broker (which also reports it in
        its ``system/init`` event)."""
        from niuu.build_info import build_info

        return {"service": "forge-api", **build_info()}

    forge = ForgeService(
        session_service,
        stats_service=stats_service,
        token_service=token_service,
        pricing_provider=pricing_provider,
        repo_service=repo_service,
        chronicle_service=chronicle_service,
        archive_service=archive_service,
    )

    def _require_bound_workload_session(request: Request, session_id: UUID) -> None:
        if request.headers.get("x-auth-token-use") != OPENSHELL_SESSION_TOKEN_USE:
            return
        if request.headers.get("x-auth-workload-session-id") == str(session_id):
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="OpenShell workload token is not bound to this session",
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

    def _strict_identity_enabled(request: Request) -> bool:
        identity = getattr(request.app.state, "identity", None)
        if identity is None:
            return False
        from volundr.adapters.outbound.identity import AllowAllIdentityAdapter

        return not isinstance(identity, AllowAllIdentityAdapter)

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
            "local_mounts_allowed_prefixes": settings.local_mounts.allowed_prefixes,
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
        if principal is None and _strict_identity_enabled(request):
            return []
        sessions = await forge.list_sessions(
            status=status_filter,
            include_archived=include_archived,
            principal=principal,
        )
        return [_session_response(s) for s in sessions]

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

    @router.get(
        "/external-sessions",
        response_model=list[ExternalSessionResponse],
        responses={
            404: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["Sessions"],
    )
    async def list_external_sessions(
        provider: str | None = Query(
            default=None,
            description="Restrict discovery to a single provider key",
        ),
    ) -> list[ExternalSessionResponse]:
        """List CLI sessions discoverable on the host (Claude Code, Codex).

        Sessions already imported into Volundr carry their Volundr session
        id in ``imported_session_id``. Live sessions are flagged via a
        recent-activity heuristic on the harness's session store.
        """
        if external_session_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="External session discovery not available",
            )
        try:
            records = await external_session_service.list_external_sessions(provider=provider)
        except ExternalSessionProviderNotFoundError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            )
        return [ExternalSessionResponse.from_record(record) for record in records]

    @router.post(
        "/sessions/import",
        response_model=SessionResponse,
        status_code=status.HTTP_201_CREATED,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["Sessions"],
    )
    async def import_external_session(
        request: Request,
        data: SessionImportRequest,
    ) -> SessionResponse:
        """Import an external CLI session as a stopped Volundr session.

        The imported session keeps pointing at its original working
        directory and resumes the native CLI session when started.
        """
        if external_session_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="External session discovery not available",
            )
        principal = await _optional_principal(request)
        try:
            session = await external_session_service.import_session(
                provider=data.provider,
                external_id=data.external_id,
                name=data.name,
                principal=principal,
            )
        except (ExternalSessionProviderNotFoundError, ExternalSessionNotFoundError) as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            )
        except ExternalSessionAlreadyImportedError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            )
        except ExternalSessionPathNotAllowedError as e:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(e),
            )
        except ExternalSessionWorkspaceError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(e),
            )
        return _session_response(session)

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
    async def create_session(
        request: Request,
        data: SessionCreate,
        _build_scope: None = Depends(require_scope("forge:session:create")),
    ) -> SessionResponse:
        """Create and start a new session.

        Creates the session record then immediately starts its pods.
        If launch_spec is set, the launch spec provides defaults for
        repo/branch/model and is passed to the pod manager to build task_args.

        A Valkyrie build token must carry the ``forge:session:create`` scope;
        ordinary human PATs and workload tokens are unaffected.
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
        return _session_response(started)

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

        return _session_response(session)

    @router.post(
        "/sessions/{session_id}/permissions/auto-approval/evaluate",
        response_model=PermissionAutoApprovalResponse,
        responses={404: {"model": ErrorResponse}},
        tags=["Sessions"],
    )
    async def evaluate_session_permission_auto_approval(
        request: Request,
        data: PermissionAutoApprovalCheck,
        session_id: UUID = Path(description="Unique session identifier"),
    ) -> PermissionAutoApprovalResponse:
        """Evaluate whether a permission request may be auto-approved by policy."""
        session = await forge.get_session(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )

        principal = await _optional_principal(request)
        try:
            await forge.ensure_access(session, principal, "view")
        except SessionAccessDeniedError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied to session {session_id}",
            )

        app_settings = getattr(request.app.state, "settings", None)
        policy = getattr(app_settings, "permission_auto_approval", None)
        if not isinstance(policy, PermissionAutoApprovalConfig):
            policy = PermissionAutoApprovalConfig()

        decision = evaluate_permission_auto_approval(
            command=data.command,
            input=data.input,
            policy=policy,
        )
        return PermissionAutoApprovalResponse(
            can_auto_approve=decision.can_auto_approve,
            reason=decision.reason,
            command=decision.command,
            delay_seconds=decision.delay_seconds,
            matched_pattern=decision.matched_pattern,
        )

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
            return _session_response(session)
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
    @router.post(
        "/sessions/{session_id}/resume",
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
        launch_spec in the body overrides the default launch spec.
        ``/resume`` is an alias for ``/start``; imported sessions resume
        their native CLI session via the recorded external session id.
        """
        launch_spec = data.launch_spec if data else None
        principal = await _optional_principal(request)
        try:
            session = await forge.start_session(
                session_id,
                launch_spec=launch_spec,
                principal=principal,
            )
            return _session_response(session)
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
            return _session_response(session)
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
        session_activity SSE event for downstream consumers (e.g. Ting).
        """
        _require_bound_workload_session(request, session_id)
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
            updated = await forge.update_activity(
                session_id, activity_state, data.metadata, state_since=data.state_since
            )
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
        except Exception as e:
            # FAULT A defence: previously this swallowed the error and the endpoint
            # still returned 204, so a real persistence failure masqueraded as
            # success and the broker never learned delivery failed. Surface a 500
            # so the broker can retry. (After the facade fix this path should never
            # trigger in practice.)
            logger.exception("Activity update failed for session %s", _sanitize_log(session_id))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Activity update failed for session {session_id}",
            ) from e

    @router.post(
        "/devices",
        response_model=DeviceResponse,
        status_code=status.HTTP_201_CREATED,
        responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["Devices"],
    )
    async def register_device(request: Request, data: DeviceRegistration) -> DeviceResponse:
        """Register a push device for the authenticated user.

        The iOS app/widget calls this so a session that needs attention can fan
        a push out to it. Idempotent on (owner, token).
        """
        if device_repository is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Device registration not available",
            )
        principal = await _optional_principal(request)
        if principal is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required to register a device",
            )
        try:
            platform = DevicePlatform(data.platform)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Invalid device platform: {data.platform}",
            )
        device = DeviceToken(
            owner_id=principal.user_id,
            platform=platform,
            token=data.token,
            app_bundle_id=data.app_bundle_id,
        )
        stored = await device_repository.upsert(device)
        return DeviceResponse.from_device(stored)

    @router.get(
        "/devices",
        response_model=list[DeviceResponse],
        responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["Devices"],
    )
    async def list_devices(request: Request) -> list[DeviceResponse]:
        """List the authenticated user's registered push devices."""
        if device_repository is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Device registration not available",
            )
        principal = await _optional_principal(request)
        if principal is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )
        devices = await device_repository.list_for_owner(principal.user_id)
        return [DeviceResponse.from_device(d) for d in devices]

    @router.delete(
        "/devices/{token}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["Devices"],
    )
    async def unregister_device(
        request: Request,
        token: str = Path(description="The push token to unregister"),
    ) -> None:
        """Unregister a push device for the authenticated user."""
        if device_repository is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Device registration not available",
            )
        principal = await _optional_principal(request)
        if principal is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )
        await device_repository.delete(principal.user_id, token)

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
            return _session_response(session)
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
            return _session_response(session)
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

    @router.get("/stats", response_model=StatsResponse, tags=["Models & Stats"])
    async def get_stats(request: Request) -> StatsResponse:
        """Get aggregate statistics for the dashboard."""
        if _strict_identity_enabled(request):
            principal = await _optional_principal(request)
            if principal is None:
                return StatsResponse()
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
            sessions_today=stats.sessions_today,
            tokens_today=stats.tokens_today,
            local_tokens=stats.local_tokens,
            cloud_tokens=stats.cloud_tokens,
            cost_today=float(stats.cost_today),
            sparklines=stats.sparklines or {},
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
        _require_bound_workload_session(request, session_id)
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
        request: Request,
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
            headers = {}
            auth = request.headers.get("authorization")
            if auth:
                headers["Authorization"] = auth
            proxy_url, routing_headers = _http_proxy_target(
                _session_proxy_url(base_url, "api", "logs")
            )
            headers.update(routing_headers)
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    proxy_url,
                    params={"lines": lines, "level": level},
                    headers=headers,
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
        request: Request,
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
        """Return aggregated logs from a live session or stopped-session workspace."""
        session = await forge.get_session(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )

        try:
            if session.chat_endpoint:
                _, base_url = await forge.get_session_proxy_target(session_id)
                headers = {}
                auth = request.headers.get("authorization")
                if auth:
                    headers["Authorization"] = auth
                proxy_url, routing_headers = _http_proxy_target(
                    _session_proxy_url(base_url, "api", "logs", "aggregate")
                )
                headers.update(routing_headers)
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(
                        proxy_url,
                        params={
                            "lines": lines,
                            "level": level,
                            "participants": participants,
                            "query": query,
                        },
                        headers=headers,
                    )
                    response.raise_for_status()
                    return response.json()
        except (ValueError, httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.info(
                "Falling back to workspace logs for session %s: %s",
                _sanitize_log(session_id),
                _sanitize_log(e),
            )

        try:
            requested_participants = (
                {item.strip() for item in participants.split(",") if item.strip()}
                if participants
                else None
            )
            return await forge.get_aggregated_logs(
                session_id,
                lines=lines,
                level=level,
                participants=requested_participants,
                query=query,
            )
        except RuntimeError as e:
            fallback = _fallback_workspace_logs(
                session,
                lines=lines,
                level=level,
                participants=requested_participants,
                query=query,
            )
            if fallback is not None:
                return fallback
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(e) or "Session archive service not available",
            ) from None

    async def _proxy_session_api(
        request: Request,
        session_id: UUID,
        *path_segments: str,
        timeout: float = 30.0,
    ) -> httpx.Response:
        try:
            _, base_url = await forge.get_session_proxy_target(session_id)
        except LookupError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            ) from None
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {session_id} has no active endpoint",
            ) from None

        headers: dict[str, str] = {}
        for name in ("authorization", "content-type", "accept"):
            value = request.headers.get(name)
            if value:
                headers[name] = value
        body = await request.body()
        try:
            proxy_url, routing_headers = _http_proxy_target(
                _session_proxy_url(base_url, "api", *path_segments)
            )
            headers.update(routing_headers)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.request(
                    request.method,
                    proxy_url,
                    params=list(request.query_params.multi_items()),
                    headers=headers,
                    content=body if body else None,
                )
                response.raise_for_status()
                return response
        except httpx.HTTPStatusError as e:
            logger.warning(
                "Session API proxy failed for session %s path=%s status=%d",
                _sanitize_log(session_id),
                _sanitize_log("/".join(path_segments)),
                e.response.status_code,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to fetch session API: {e.response.status_code}",
            ) from None
        except httpx.RequestError as e:
            logger.warning(
                "Session API proxy connection failed for session %s path=%s: %s",
                _sanitize_log(session_id),
                _sanitize_log("/".join(path_segments)),
                _sanitize_log(e),
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not connect to session pod: {e}",
            ) from None

    @router.get("/sessions/{session_id}/files", tags=["Sessions"])
    async def list_session_files(
        request: Request,
        session_id: UUID = Path(description="Unique session identifier"),
    ) -> dict:
        """List files in a live session through the owning Skuld broker."""
        response = await _proxy_session_api(request, session_id, "files")
        payload = response.json()
        return payload if isinstance(payload, dict) else {"entries": []}

    @router.get("/sessions/{session_id}/files/download", tags=["Sessions"])
    async def download_session_file(
        request: Request,
        session_id: UUID = Path(description="Unique session identifier"),
    ) -> Response:
        """Download a file from a live session through the owning Skuld broker."""
        response = await _proxy_session_api(request, session_id, "files", "download")
        headers = {}
        disposition = response.headers.get("content-disposition")
        if disposition:
            headers["content-disposition"] = disposition
        return Response(
            content=response.content,
            media_type=response.headers.get("content-type", "application/octet-stream"),
            headers=headers,
        )

    @router.get("/sessions/{session_id}/files/presented/{file_id}", tags=["Sessions"])
    async def download_presented_file(
        request: Request,
        session_id: UUID = Path(description="Unique session identifier"),
        file_id: str = Path(description="Opaque presented-file id (present-file command)"),
    ) -> Response:
        """Download a presented file (present-file command) by opaque id via the owning broker."""
        response = await _proxy_session_api(request, session_id, "files", "presented", file_id)
        headers = {}
        disposition = response.headers.get("content-disposition")
        if disposition:
            headers["content-disposition"] = disposition
        return Response(
            content=response.content,
            media_type=response.headers.get("content-type", "application/octet-stream"),
            headers=headers,
        )

    @router.post("/sessions/{session_id}/files/upload", tags=["Sessions"])
    async def upload_session_files(
        request: Request,
        session_id: UUID = Path(description="Unique session identifier"),
    ) -> dict:
        """Upload one or more files to a live session through the owning Skuld broker."""
        response = await _proxy_session_api(request, session_id, "files", "upload")
        payload = response.json()
        return payload if isinstance(payload, dict) else {"entries": []}

    @router.put("/sessions/{session_id}/files/upload", tags=["Sessions"])
    async def write_session_file(
        request: Request,
        session_id: UUID = Path(description="Unique session identifier"),
    ) -> dict:
        """Write a file body to a live session through the owning Skuld broker."""
        response = await _proxy_session_api(request, session_id, "files", "upload")
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @router.post("/sessions/{session_id}/files/mkdir", tags=["Sessions"])
    async def mkdir_session_file(
        request: Request,
        session_id: UUID = Path(description="Unique session identifier"),
    ) -> dict:
        """Create a directory in a live session through the owning Skuld broker."""
        response = await _proxy_session_api(request, session_id, "files", "mkdir")
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @router.delete("/sessions/{session_id}/files", tags=["Sessions"])
    async def delete_session_file(
        request: Request,
        session_id: UUID = Path(description="Unique session identifier"),
    ) -> dict:
        """Delete a file in a live session through the owning Skuld broker."""
        response = await _proxy_session_api(request, session_id, "files")
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

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
    ) -> Response:
        """Send a user message to a running session via its WebSocket.

        INV-7 delivery contract: the response distinguishes delivered from
        not-delivered. 200 ``status=delivered`` means the transport accepted the
        message; 202 ``status=pending`` means the broker accepted it and its bounded
        retry is still driving delivery (NOT a confirmed send); a terminal transport
        rejection raises 502; an unreachable pod raises 409. ``unconfirmed`` is never
        returned as success.
        """
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

        connect_kwargs: dict[str, object] = {"open_timeout": 10}
        if ws_url.startswith("wss://"):
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            connect_kwargs["ssl"] = ssl_ctx
        connect_kwargs.update(
            _server_side_ws_connect_overrides(
                ws_url,
                gateway_url=openshell_internal_gateway_url,
            )
        )

        # INV-7: correlate this message with the broker's delivery ACK so the response
        # distinguishes DELIVERED from NOT-delivered — a 200 means the transport actually
        # accepted the message, not merely the broker socket. The broker emits
        # user_delivered / user_delivery_failed tagged with this request_id once the
        # transport accepts (or, after bounded retry, terminally rejects) the message.
        # If no ACK arrives within the grace, the broker has ACCEPTED the message and its
        # retry loop is still driving delivery: we report 202 "pending" (NOT "sent"), so
        # the caller never reads an undelivered message as success.
        req_id = str(uuid4())
        delivery: dict[str, Any] | None = None
        try:
            async with connect(ws_url, **connect_kwargs) as ws:
                # Send immediately. Draining startup traffic *before* sending can
                # delay or drop the first user turn for transports that emit
                # welcome or capability events while still coming online.
                await ws.send(
                    json.dumps({"type": "user", "content": content, "request_id": req_id})
                )

                # Hold the socket open until the broker ACKs delivery for THIS request_id,
                # or a bounded grace elapses. Draining other frames meanwhile lets a
                # just-restarted broker finish warming its transport on this very
                # connection instead of racing a close (which silently drops the message).
                async def _await_ack() -> dict[str, Any] | None:
                    while True:
                        raw = await ws.recv()
                        try:
                            frame = json.loads(raw)
                        except (ValueError, TypeError):
                            continue
                        if (
                            isinstance(frame, dict)
                            and frame.get("request_id") == req_id
                            and frame.get("type") in ("user_delivered", "user_delivery_failed")
                        ):
                            return frame

                with contextlib.suppress(Exception):
                    delivery = await asyncio.wait_for(
                        _await_ack(), timeout=SEND_MESSAGE_ACK_GRACE_SECONDS
                    )
        except Exception as e:
            # The endpoint looked live (chat_endpoint set) but the broker socket
            # is unreachable — the pod is gone. Reconcile the row so its stale
            # RUNNING/endpoint self-heals, then fail deterministically (409) so
            # the caller never reads this as a successful send (no false 'sent').
            reconciled = await forge.reconcile_session(session_id)
            detail = (
                f"Session {session_id} is no longer reachable; "
                "reconciled to "
                f"{reconciled.status.value if reconciled else 'unknown'}. "
                f"Message not delivered: {e}"
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=detail,
            )

        # Terminal failure: the broker exhausted its bounded retry and the transport
        # rejected the message. Surface as an error — never a 200 "sent" (INV-7).
        if isinstance(delivery, dict) and delivery.get("type") == "user_delivery_failed":
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Session did not accept the message: "
                    f"{delivery.get('error') or 'delivery failed'}"
                ),
            )

        # No ACK within the grace: the broker ACCEPTED the message and its retry loop
        # is still driving delivery. This is NOT a confirmed send — report 202 pending
        # so the caller can tell it is in flight, not delivered (INV-7).
        if not isinstance(delivery, dict):
            return JSONResponse(
                status_code=status.HTTP_202_ACCEPTED,
                content={
                    "status": "pending",
                    "session_id": str(session_id),
                    "request_id": req_id,
                    "delivery": "pending",
                },
            )

        # Transport accepted the message (delivered, or delivered-but-blocked on an open
        # question). 200 + delivery="delivered" is the only success contract.
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "delivered",
                "session_id": str(session_id),
                "request_id": req_id,
                "delivery": str(delivery.get("status") or "delivered"),
            },
        )

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
        detail: str = Query(
            "full",
            description=(
                "'shallow' elides heavy tool_result content to lazy-load "
                "placeholders; fetch a full result via "
                "/sessions/{id}/tool-result/{tool_use_id}."
            ),
        ),
        limit: int = Query(
            0,
            ge=0,
            description="Window to the last N turns (0 = all). The response carries total_turns "
            "+ window_offset; fetch older turns with `before`.",
        ),
        before: int = Query(
            0,
            ge=0,
            description="Skip N turns from the end before applying `limit` (Show-earlier paging).",
        ),
    ) -> dict:
        """Return conversation history from a live session or stopped-session workspace.

        When ``detail=shallow`` the heavy ``tool_result`` payloads are elided to
        placeholders on BOTH the live-pod path (the broker elides at the source)
        and the durable-log rebuild fallback (elided here), so the shape is
        identical regardless of which path served the transcript.
        """
        shallow = detail == SHALLOW_DETAIL
        timings: dict[str, float] = {}

        def _maybe_elide(payload: dict, fetch_ms: float | None = None) -> dict:
            """WINDOW (when limit>0) + elide (when shallow) + attach a server-side perf summary.

            Windowing slices the turns to the LAST `limit` (skipping `before` from the end for
            Show-earlier paging) and reports `total_turns` + `window_offset` so the client can show
            "N earlier" and page older — this is the big TRANSIT win (a 1.7MB/82-turn open becomes
            ~300KB/15 turns). The perf `_prep` block + `[perf]` log make prep-vs-transit visible."""
            if not isinstance(payload, dict) or not isinstance(payload.get("turns"), list):
                return payload
            all_turns = payload["turns"]
            total = len(all_turns)
            if limit > 0:
                end = max(0, total - before)
                start = max(0, end - limit)
                turns = all_turns[start:end]
                window_offset = start  # older turns available before this window
            else:
                turns = all_turns
                window_offset = 0
            reelide_ms = 0.0
            if shallow:
                t = time.perf_counter()
                turns = elide_turns(turns)
                reelide_ms = (time.perf_counter() - t) * 1000.0
            prep = dict(payload.get("_prep") or {})
            prep.update(timings)
            if fetch_ms is not None:
                prep["volundr_fetch_ms"] = round(fetch_ms, 1)
            prep["volundr_reelide_ms"] = round(reelide_ms, 1)
            out = {
                **payload,
                "turns": turns,
                "total_turns": total,
                "window_offset": window_offset,
                "_prep": prep,
            }
            logger.info(
                "[perf] conversation session=%s shallow=%s fetch=%sms reelide=%.1fms "
                "broker_build=%sms broker_elide=%sms turns=%s",
                _sanitize_log(session_id),
                shallow,
                (f"{fetch_ms:.1f}" if fetch_ms is not None else "-"),
                reelide_ms,
                prep.get("build_ms", "-"),
                prep.get("elide_ms", "-"),
                prep.get("turns", len(out.get("turns", []))),
            )
            return out

        t_sess = time.perf_counter()
        session = await forge.get_session(session_id)
        timings["session_lookup_ms"] = round((time.perf_counter() - t_sess) * 1000.0, 1)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )

        try:
            if session.chat_endpoint:
                t_tgt = time.perf_counter()
                _, base_url = await forge.get_session_proxy_target(session_id)
                timings["proxy_target_ms"] = round((time.perf_counter() - t_tgt) * 1000.0, 1)

                headers = {}
                auth = request.headers.get("authorization")
                if auth:
                    headers["Authorization"] = auth

                t_fetch = time.perf_counter()
                proxy_url, routing_headers = _http_proxy_target(
                    _session_proxy_url(base_url, "api", "conversation", "history")
                )
                headers.update(routing_headers)
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(
                        proxy_url,
                        headers=headers,
                        params={"detail": detail},
                    )
                    response.raise_for_status()
                    live = response.json()
                    # Broker round-trip: build + (source) elide + serialize + localhost transfer +
                    # JSON parse. For an old broker that ignores `detail` this is the FULL payload.
                    fetch_ms = (time.perf_counter() - t_fetch) * 1000.0
                    if _live_transcript_is_renderable(live):
                        # FAULT C: a resumed/restarted broker can be desynced from
                        # the durable log and serve FEWER turns than the durable
                        # rebuild (partial history for a running session). Only when
                        # the live body has NO in_progress/streaming turn do we
                        # compare turn counts and prefer the durable rebuild iff it
                        # has STRICTLY MORE turns. We gate PRIMARILY on the actual
                        # turn payload (last turn `in_progress` /
                        # `metadata.status == 'in_progress'`) — the broker's
                        # is_active / last_activity buffer flags are NOT a reliable
                        # proxy: a tool_use-only assistant block (the entire
                        # tool-execution window) leaves those flags empty while the
                        # body still carries the live in_progress turn, so the proxy
                        # alone would wrongly run reconciliation and could drop it.
                        # We ALSO keep the streaming-flag guard so a body that
                        # signals active streaming is never reconciled.
                        is_streaming = bool(
                            isinstance(live, dict)
                            and (live.get("is_active") or (live.get("last_activity") or ""))
                        )
                        if not (is_streaming or _live_body_has_in_progress_turn(live)):
                            live_turns = live.get("turns") if isinstance(live, dict) else None
                            live_count = len(live_turns) if isinstance(live_turns, list) else 0
                            # CHEAP GATE + OPTIMISTIC OPEN: detecting a desync (broker serving
                            # fewer turns than the durable log) needs the durable turn count, but
                            # rebuilding the whole transcript for it is ~4s on a big session. Cache
                            # the count keyed on MAX(seq): a warm hit answers instantly; a COLD miss
                            # serves the live body NOW and warms the count in the BACKGROUND, so the
                            # open never blocks on the rebuild. A real desync (rare; only after a
                            # broker resume) self-heals on the next reconcile poll once warm.
                            sid_str = str(session_id)
                            seq = await forge.durable_latest_seq(session_id)
                            cached = _DURABLE_COUNT_CACHE.get(sid_str)
                            durable_count = (
                                cached[1]
                                if (cached is not None and seq > 0 and cached[0] == seq)
                                else None
                            )
                            if durable_count is None:
                                # COLD: serve live immediately; warm the count off the request path.
                                timings["fault_c_optimistic"] = 1.0
                                if seq > 0 and sid_str not in _DURABLE_WARMING:
                                    _DURABLE_WARMING.add(sid_str)
                                    asyncio.create_task(
                                        _warm_durable_count(forge, session_id, seq, sid_str)
                                    )
                            elif durable_count > live_count:
                                # WARM + desync: rebuild to return the fuller durable body (rare).
                                t_dur = time.perf_counter()
                                try:
                                    durable = await forge.get_transcript(session_id)
                                except (RuntimeError, ValueError):
                                    durable = None
                                timings["fault_c_rebuild_ms"] = round(
                                    (time.perf_counter() - t_dur) * 1000.0, 1
                                )
                                durable_turns = (
                                    durable.get("turns") if isinstance(durable, dict) else None
                                )
                                rebuilt = (
                                    len(durable_turns) if isinstance(durable_turns, list) else 0
                                )
                                _durable_count_cache_put(sid_str, seq, rebuilt)
                                if durable is not None and rebuilt > live_count:
                                    logger.info(
                                        "Live conversation for session %s is short "
                                        "(%d turns) vs durable rebuild (%d turns); "
                                        "preferring durable (FAULT C desync)",
                                        _sanitize_log(session_id),
                                        live_count,
                                        rebuilt,
                                    )
                                    return _maybe_elide(durable)
                            else:
                                # Warm + live complete (durable not ahead) — no rebuild.
                                timings["fault_c_cached"] = 1.0
                        # A freshly-restarted broker already elided at the source;
                        # re-eliding here is idempotent (placeholders have no
                        # content) and lets a long-running broker that predates
                        # this code serve shallow without a disruptive restart.
                        return _maybe_elide(live, fetch_ms=fetch_ms)
                    # BUG-2: an alive-but-empty / seed-only pod (e.g. a tmux session whose
                    # WS crashed mid-turn) returns HTTP 200 with nothing renderable. Don't
                    # short-circuit on it — fall through to the durable-log rebuild below.
                    logger.info(
                        "Live conversation for session %s is empty/seed-only; "
                        "falling through to durable-log rebuild",
                        _sanitize_log(session_id),
                    )
        except (ValueError, httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.info(
                "Falling back to workspace transcript for session %s: %s",
                _sanitize_log(session_id),
                _sanitize_log(e),
            )

        try:
            return _maybe_elide(await forge.get_transcript(session_id))
        except RuntimeError as e:
            fallback = _fallback_workspace_transcript(session)
            if fallback is not None:
                return _maybe_elide(fallback)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(e) or "Session archive service not available",
            ) from None
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )

    @router.get(
        "/sessions/{session_id}/tool-result/{tool_use_id}",
        tags=["Sessions"],
        responses={
            404: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
        },
    )
    async def get_tool_result(
        request: Request,
        session_id: UUID = Path(description="Unique session identifier"),
        tool_use_id: str = Path(description="tool_use_id of the result to fetch"),
    ) -> dict:
        """Return one full tool_result block by tool_use_id.

        The lazy-load target for a shallow conversation: when a client expands an
        elided placeholder it fetches the full content here. Proxies the live
        session pod; falls back to scanning the durable transcript for
        stopped/seed-only sessions. 404 when the result is absent.
        """

        def _scan(turns: list) -> dict | None:
            for turn in turns:
                parts = turn.get("parts") if isinstance(turn, dict) else None
                for block in parts or []:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_result"
                        and block.get("tool_use_id") == tool_use_id
                    ):
                        return {
                            "tool_use_id": tool_use_id,
                            "content": block.get("content", ""),
                            "is_error": bool(block.get("is_error", False)),
                        }
            return None

        session = await forge.get_session(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )

        if session.chat_endpoint:
            try:
                _, base_url = await forge.get_session_proxy_target(session_id)
                headers = {}
                auth = request.headers.get("authorization")
                if auth:
                    headers["Authorization"] = auth
                proxy_url, routing_headers = _http_proxy_target(
                    _session_proxy_url(base_url, "api", "conversation", "tool-result", tool_use_id)
                )
                headers.update(routing_headers)
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(
                        proxy_url,
                        headers=headers,
                    )
                    if response.status_code != status.HTTP_404_NOT_FOUND:
                        response.raise_for_status()
                        return response.json()
                    # 404 from the live pod → fall through to the durable scan
                    # (the pod may have rebooted with a seed-only transcript).
            except (ValueError, httpx.HTTPStatusError, httpx.RequestError) as e:
                logger.info(
                    "Live tool-result fetch failed for session %s; trying durable log: %s",
                    _sanitize_log(session_id),
                    _sanitize_log(e),
                )

        try:
            transcript = await forge.get_transcript(session_id)
        except (RuntimeError, ValueError):
            transcript = _fallback_workspace_transcript(session) or {"turns": []}
        found = _scan(transcript.get("turns", []) if isinstance(transcript, dict) else [])
        if found is not None:
            return found
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"tool_result not found: {tool_use_id}",
        )

    @router.get(
        "/sessions/{session_id}/workflow/gates",
        tags=["Sessions"],
        responses={
            404: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
        },
    )
    async def get_workflow_gates(
        request: Request,
        session_id: UUID = Path(description="Unique session identifier"),
    ) -> dict:
        """Return native workflow gate state for a live session."""
        session = await forge.get_session(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        if not session.chat_endpoint:
            return {"gates": []}

        try:
            _, base_url = await forge.get_session_proxy_target(session_id)
            headers = {}
            auth = request.headers.get("authorization")
            if auth:
                headers["Authorization"] = auth
            proxy_url, routing_headers = _http_proxy_target(
                _session_proxy_url(base_url, "api", "workflow", "gates")
            )
            headers.update(routing_headers)
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    proxy_url,
                    headers=headers,
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            logger.warning(
                "Workflow gate proxy returned error for session %s: %s",
                _sanitize_log(session_id),
                _sanitize_log(e),
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to fetch workflow gates from session pod: {e}",
            )
        except (ValueError, httpx.RequestError):
            # Couldn't reach the pod — reconcile the stale row and fail
            # deterministically (409) instead of a misleading bad-gateway.
            reconciled = await forge.reconcile_session(session_id)
            logger.warning(
                "Workflow gate pod unreachable for session %s; reconciled to %s",
                _sanitize_log(session_id),
                reconciled.status.value if reconciled else "unknown",
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Session {session_id} is no longer reachable; "
                    f"reconciled to {reconciled.status.value if reconciled else 'unknown'}."
                ),
            )

    @router.post(
        "/sessions/{session_id}/workflow/gates/{gate_id}/resolve",
        tags=["Sessions"],
        responses={
            400: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
        },
    )
    async def resolve_workflow_gate(
        request: Request,
        body: WorkflowGateResolveRequest,
        session_id: UUID = Path(description="Unique session identifier"),
        gate_id: str = Path(description="Workflow gate identifier"),
    ) -> dict:
        """Resolve a pending native workflow gate for a live session."""
        principal = await extract_principal(request)
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
        if session.owner_id and session.owner_id != principal.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to resolve gates for this session",
            )

        headers = {}
        auth = request.headers.get("authorization")
        if auth:
            headers["Authorization"] = auth
        intent = request.headers.get(WORKFLOW_GATE_INTENT_HEADER)
        if intent:
            headers[WORKFLOW_GATE_INTENT_HEADER] = intent

        try:
            proxy_url, routing_headers = _http_proxy_target(
                _session_proxy_url(base_url, "api", "workflow", "gates", gate_id, "resolve")
            )
            headers.update(routing_headers)
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    proxy_url,
                    headers=headers,
                    json=body.model_dump(),
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            detail = e.response.text[:500]
            raise HTTPException(status_code=status_code, detail=detail)
        except httpx.RequestError as e:
            # Pod unreachable — reconcile the stale row and fail deterministically
            # (409) so the caller never thinks the gate resolved.
            reconciled = await forge.reconcile_session(session_id)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Session {session_id} is no longer reachable; "
                    f"reconciled to {reconciled.status.value if reconciled else 'unknown'}. "
                    f"Gate not resolved: {e}"
                ),
            )

    @router.get(
        "/sessions/{session_id}/help/requests",
        tags=["Sessions"],
        responses={
            404: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
        },
    )
    async def get_session_help_requests(
        request: Request,
        session_id: UUID = Path(description="Unique session identifier"),
    ) -> dict:
        """Return pending peer help requests (agent questions) for a live session."""
        session = await forge.get_session(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        principal = await _optional_principal(request)
        try:
            await forge.ensure_access(session, principal, "view")
        except SessionAccessDeniedError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied to session {session_id}",
            )
        if not session.chat_endpoint:
            return {"requests": []}
        try:
            _, base_url = await forge.get_session_proxy_target(session_id)
            headers = {}
            auth = request.headers.get("authorization")
            if auth:
                headers["Authorization"] = auth
            proxy_url, routing_headers = _http_proxy_target(
                _session_proxy_url(base_url, "api", "help", "requests")
            )
            headers.update(routing_headers)
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(proxy_url, headers=headers)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to fetch help requests from session pod: {e}",
            )
        except (ValueError, httpx.RequestError):
            return {"requests": []}

    @router.post(
        "/sessions/{session_id}/help/requests/{request_id}/answer",
        tags=["Sessions"],
        responses={
            400: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
        },
    )
    async def answer_session_help_request(
        request: Request,
        body: HelpAnswerRequest,
        session_id: UUID = Path(description="Unique session identifier"),
        request_id: str = Path(description="Help request identifier"),
    ) -> dict:
        """Answer a pending peer help request in a live session."""
        principal = await extract_principal(request)
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
        if session.owner_id and session.owner_id != principal.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to answer help requests for this session",
            )

        headers = {}
        auth = request.headers.get("authorization")
        if auth:
            headers["Authorization"] = auth
        try:
            proxy_url, routing_headers = _http_proxy_target(
                _session_proxy_url(base_url, "api", "help", "requests", request_id, "answer")
            )
            headers.update(routing_headers)
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    proxy_url,
                    headers=headers,
                    json=body.model_dump(),
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text[:500])
        except httpx.RequestError as e:
            reconciled = await forge.reconcile_session(session_id)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Session {session_id} is no longer reachable; "
                    f"reconciled to {reconciled.status.value if reconciled else 'unknown'}. "
                    f"Help request not answered: {e}"
                ),
            )

    @router.get(
        "/sessions/{session_id}/transcript",
        tags=["Sessions"],
        responses={
            404: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def get_session_transcript(
        session_id: UUID = Path(description="Unique session identifier"),
    ) -> dict:
        """Return the persisted session transcript directly from workspace storage."""
        try:
            return await forge.get_transcript(session_id)
        except LookupError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        except SessionArchiveNotAvailableError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            )
        except RuntimeError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(e),
            )

    @router.get(
        "/sessions/{session_id}/transcript/download",
        tags=["Sessions"],
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def download_session_transcript(
        session_id: UUID = Path(description="Unique session identifier"),
        format: str = Query(default="md", pattern="^(md|json)$"),
    ) -> FileResponse:
        """Download the persisted transcript as Markdown or JSON."""
        try:
            path = await forge.get_transcript_download_path(session_id, format)
        except LookupError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        except SessionArchiveNotAvailableError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            )
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )
        except RuntimeError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(e),
            )

        media_type = "text/markdown; charset=utf-8" if format == "md" else "application/json"
        filename = f"session-{session_id}-transcript.{format}"
        expected_artifact = f"transcript.{format}"
        if path.name != expected_artifact:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Resolved transcript artifact has an unexpected name",
            )
        download_root = os.path.realpath(os.path.abspath(os.fspath(path.parent)))
        checked_path = os.path.realpath(os.path.abspath(os.fspath(path)), strict=True)
        download_prefix = download_root.rstrip(os.sep) + os.sep
        if checked_path == download_root:
            safe_download_path = download_root
        elif checked_path.startswith(download_prefix):
            safe_download_path = checked_path
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Resolved transcript artifact escapes its archive directory",
            )
        return FileResponse(safe_download_path, media_type=media_type, filename=filename)

    @router.get(
        "/sessions/{session_id}/archive",
        tags=["Sessions"],
        responses={
            404: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def get_session_archive_manifest(
        session_id: UUID = Path(description="Unique session identifier"),
    ) -> dict:
        """Return the workspace-backed archive manifest for a session."""
        try:
            return await forge.get_archive_manifest(session_id)
        except LookupError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        except SessionArchiveNotAvailableError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            )
        except RuntimeError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
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
            return _session_response(session)
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
            proxy_url, routing_headers = _http_proxy_target(
                _session_proxy_url(base_url, "api", "diff")
            )
            proxy_headers.update(routing_headers)
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    proxy_url,
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
