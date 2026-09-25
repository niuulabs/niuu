"""FastAPI REST adapter for session management."""

import asyncio
import contextlib
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path as FilePath
from typing import Any, Literal
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from niuu.domain.history_paging import InvalidHistoryCursorError
from niuu.domain.json_text import json_text_safe
from niuu.domain.services.token_scope import (
    OPENSHELL_SESSION_TOKEN_USE,
    SKULD_GATE_RESOLVE_SCOPE,
    VALKYRIE_BUILD_TOKEN_USE,
    require_scope,
)
from niuu.domain.session_endpoint import public_session_endpoint
from niuu.domain.text_projection import projection_revision
from niuu.room_access import ROOM_ROLE_HEADER
from skuld.conversation_shallow import SHALLOW_DETAIL, elide_turns, is_elided_input
from skuld.conversation_snapshot import (
    ConversationSnapshotTooLargeError,
    prepare_history_page,
    prepare_recent_snapshot,
)
from skuld.tool_result_preview import (
    PreviewCache,
    PreviewUnavailableError,
    extract_image_bytes,
    generate_preview_jpeg,
    warm_previews_from_turns,
)
from volundr.adapters.inbound.auth import extract_principal, require_role
from volundr.adapters.inbound.rest_projects import (
    create_projects_router,
    create_session_projects_router,
    project_result,
)
from volundr.config import PermissionAutoApprovalConfig
from volundr.domain.execution_catalog import (
    ExecutionCatalogError,
    ExecutionCatalogNotFoundError,
    ExecutionPlanMismatchError,
    ExecutionSelectionError,
)
from volundr.domain.history_import import (
    HistoryImportConflictError,
    HistoryImportError,
    HistoryImportSessionNotFoundError,
    HistoryImportValidationError,
)
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
from volundr.domain.projects import SessionCoordination
from volundr.domain.services import (
    ChronicleAccessDeniedError,
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
    SessionCapacityError,
    SessionEventStream,
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
from volundr.domain.services.session_participants import SessionParticipantService
from volundr.domain.session_participants import SessionParticipant
from volundr.domain.session_read_state import (
    SessionReadState,
    SessionReadStateChange,
    SessionReadStateConflictError,
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

# Disk root for generated tool-result image previews. ~/.niuu is the platform's
# durable local-state home (workspaces, forge-state.json), so previews survive
# restarts by construction. Tests inject their own PreviewCache via create_router.
_PREVIEW_CACHE_ROOT = FilePath("~/.niuu/preview-cache").expanduser()

# A tool_use_id's result is immutable — a regenerated preview is byte-equivalent —
# so previews are safely long-lived cacheable at every layer (incl. URLSession).
_PREVIEW_RESPONSE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}


def _execution_catalog_http_error(
    exc: ExecutionCatalogError | ExecutionSelectionError,
) -> HTTPException:
    """Map only explicit execution-catalog failures to stable public responses."""
    if isinstance(exc, (ExecutionSelectionError, ExecutionCatalogNotFoundError)):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid compute execution selection",
        )
    if isinstance(exc, ExecutionPlanMismatchError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Compute execution selection conflicts with its durable allocation",
        )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Compute execution catalog is unavailable",
    )


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


# FAULT-C durable cache: `session_id -> (event_log_seq, durable_turn_count, durable_tail_id)`.
# The FAULT-C reconciliation needs the durable turn count to detect a desynced (short) live body,
# but rebuilding the whole durable transcript on every poll is ~4s on a big session (profiled).
# The count is stable while the event log hasn't grown, so we key the cached values on MAX(seq):
# a hit at the same seq skips the rebuild entirely; a new seq (or a short live body) falls back
# to the exact rebuild. `durable_tail_id` is the id of the rebuild's last non-in-progress turn —
# the SEGMENTATION-vs-DESYNC discriminator (see the tail-id gate at the decision site).
_DURABLE_COUNT_CACHE: dict[str, tuple[int, int, str | None]] = {}
_DURABLE_COUNT_CACHE_MAX = 512


def _durable_count_cache_put(session_id: str, seq: int, count: int, tail_id: str | None) -> None:
    if (
        len(_DURABLE_COUNT_CACHE) >= _DURABLE_COUNT_CACHE_MAX
        and session_id not in _DURABLE_COUNT_CACHE
    ):
        # Crude bound: drop an arbitrary existing entry (FIFO-ish via iteration order).
        _DURABLE_COUNT_CACHE.pop(next(iter(_DURABLE_COUNT_CACHE)), None)
    _DURABLE_COUNT_CACHE[session_id] = (seq, count, tail_id)


def _last_settled_turn_id(turns: object) -> str | None:
    """Id of the last turn that is NOT an in-progress row (the comparable transcript tail).

    Used by the FAULT-C tail-id gate: a broker that is genuinely BEHIND the durable log
    (resumed from a stale snapshot) is missing trailing turns, so its settled tail id differs
    from the rebuild's. A broker whose body merely SEGMENTS the same content differently
    (e.g. `present_file` deliveries folded vs standalone) still ends on the same settled turn.
    Interrupted-flush rows count as real tail (a crashed turn the live body lost IS a desync).
    """
    if not isinstance(turns, list):
        return None
    for t in reversed(turns):
        if not isinstance(t, dict):
            continue
        if t.get("in_progress") is True:
            continue
        metadata = t.get("metadata")
        if isinstance(metadata, dict) and metadata.get("status") == "in_progress":
            continue
        tid = str(t.get("id") or "").strip()
        return tid or None
    return None


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
        _durable_count_cache_put(
            sid_str,
            seq,
            len(turns) if isinstance(turns, list) else 0,
            _last_settled_turn_id(turns),
        )
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

    coordination: SessionCoordination | None = None
    dispatch_id: UUID | None = None

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

    integration_ids: list[str] | None = Field(
        default=None,
        description="Integration connection IDs to use on restart; omitted preserves the selection",
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
    turn_started_at: datetime | None = Field(
        default=None,
        description=(
            "ISO8601 UTC timestamp of when the CURRENT turn started (the user's "
            "prompt landing). Stable across intra-turn state flips; null when no "
            "turn is in flight. Optional (older brokers omit it → null persisted, "
            "clients fall back to state_since)."
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

    read_state: SessionReadState | None = None

    coordination: SessionCoordination | None = None

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
        description="Failure reason or non-terminal runtime status detail",
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
            "(provisioning/active/idle/tool_executing/awaiting_input/stopped/error)"
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
    turn_started_at: str | None = Field(
        default=None,
        description=(
            "ISO 8601 UTC timestamp of when the CURRENT turn started (the user's "
            "prompt landing). Stable across intra-turn active/tool_executing "
            "flips; null when no turn is in flight. Clients anchor the RUNNING "
            "elapsed to this (falling back to activity_state_since when null)."
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
            read_state=session.read_state,
            id=session.id,
            coordination=session.coordination,
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
            turn_started_at=(
                session.turn_started_at.isoformat() if session.turn_started_at else None
            ),
            activity_metadata=session.activity_metadata,
            needs_attention=session.needs_attention,
            workload_type=session.workload_type,
            origin=session.origin,
            external_session_id=session.external_session_id,
            cli_session_id=session.cli_session_id,
        )


class SessionRoomInfo(BaseModel):
    """The caller's own durable grant on a session they participate in."""

    role: str = Field(description="observer/teacher/debugger/approver")
    participant_status: str = Field(description="invited/active/revoked")
    expires_at: str | None = Field(default=None, description="ISO 8601 grant expiry, if any")


class SessionRoomSummary(BaseModel):
    """Room-visibility shape for a session the caller participates in but does
    not own. Deliberately excludes everything ``SessionResponse`` exposes
    beyond identity/status: repo, branch, local_path, code_endpoint, pod_name,
    error, activity_metadata, and external ids stay owner/admin-only — Cedar
    authorizes this shape through ``read_room``, never ``read`` (see
    ForgeService.list_participant_only_sessions). The web client uses
    ``room.role`` to decide whether it can attach at all, and if so with what
    proxied capability; the actual attach/message gating still runs
    server-side (session proxy + Skuld broker), this is only for rendering.
    """

    kind: Literal["room_summary"] = "room_summary"
    id: UUID = Field(description="Unique session identifier")
    name: str = Field(description="Human-readable session name")
    status: SessionStatus = Field(description="Current lifecycle status")
    owner_id: str | None = Field(default=None, description="User ID of the session owner")
    room: SessionRoomInfo


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
    """Response model for aggregate statistics. "Today" is the current UTC day."""

    active_sessions: int = Field(default=0, description="Currently running sessions")
    total_sessions: int = Field(default=0, description="Total sessions (all statuses)")
    sessions_today: int = Field(default=0, description="Sessions created today (UTC)")
    tokens_today: int = Field(default=0, description="Tokens consumed today (UTC)")
    local_tokens: int = Field(default=0, description="Tokens from local models today (UTC)")
    cloud_tokens: int = Field(default=0, description="Tokens from cloud models today (UTC)")
    cost_today: float = Field(default=0.0, description="Total cloud cost today (UTC) in USD")
    sparklines: dict[str, list[float]] = Field(
        default_factory=dict,
        description=(
            "Historical KPI samples for lightweight dashboard sparklines; "
            "sessionsToday holds one count per UTC day, oldest first, ending today"
        ),
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
    preview_cache: PreviewCache | None = None,
    project_service=None,
    runtime_build: dict | None = None,
    runtime_health_timeout: float = 3.0,
    history_max_turns: int = 15,
    history_max_bytes: int = 256 * 1024,
    session_participant_service: SessionParticipantService,
) -> APIRouter:
    """Create FastAPI router with session, stats, token, repo, and SSE endpoints."""
    router = APIRouter(prefix=prefix)
    if preview_cache is None:
        preview_cache = PreviewCache(_PREVIEW_CACHE_ROOT)
    event_stream = None
    if broadcaster is not None:
        if stats_service is None:
            raise ValueError(
                "The session event stream computes stats_updated per subscriber; "
                "pass stats_service together with broadcaster"
            )
        event_stream = SessionEventStream(broadcaster, session_service, stats_service)

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
        can confirm which version is live. Existing Skuld brokers may still run an older
        build; inspect /sessions/{id}/runtime-version for their loaded identity."""
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
        project_service=project_service,
        session_participant_service=session_participant_service,
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

    async def _optional_principal(request: Request, *, strict: bool = False) -> Principal | None:
        """Extract principal if identity is configured, else return None.

        Allows dev mode (no IDP) to work without auth headers while
        production deployments enforce tenant/ownership scoping.

        ``strict`` is for endpoints where an unidentified reader is meaningless
        (e.g. per-reader inbox state): even a dev-mode deployment with no IDP
        configured must still 401 rather than silently pick a reader.

        When a principal is found, also ensures the user row exists
        via the identity adapter's JIT provisioning.
        """
        identity = getattr(request.app.state, "identity", None)
        if identity is None:
            if strict:
                raise HTTPException(status_code=401, detail="Reader identity is required")
            return None

        from volundr.adapters.inbound.auth import extract_principal

        principal = await extract_principal(request)

        from niuu.ports.identity import UserProvisioningError

        try:
            await identity.get_or_provision_user(principal)
        except UserProvisioningError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="User provisioning unavailable",
                headers={"Retry-After": "5"},
            ) from exc

        return principal

    def _strict_identity_enabled(request: Request) -> bool:
        identity = getattr(request.app.state, "identity", None)
        if identity is None:
            return False
        from volundr.adapters.outbound.identity import AllowAllIdentityAdapter

        return not isinstance(identity, AllowAllIdentityAdapter)

    @contextlib.contextmanager
    def _chronicle_errors():
        """Map chronicle scoping and authorization failures onto HTTP statuses.

        History outside the caller's scope is reported as not found, so its
        existence is not disclosed; history the caller can see but may not
        change is forbidden.
        """
        try:
            yield
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        except (ChronicleAccessDeniedError, SessionAccessDeniedError) as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except (ChronicleNotFoundError, SessionNotFoundError) as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc

    async def _check_room_access(
        session: Session, principal: Principal | None, action: str
    ) -> None:
        """Authorize a room-scoped action (``read_room``/``resolve_gate``).

        Delegates to ``SessionParticipantService`` so an ACTIVE, unexpired
        participant grant (not just the owner/admin) is honoured.
        ``session_participant_service`` is a required dependency of this
        router (see ``create_router``'s signature) — there is no degraded
        owner/admin-only path when it is missing, because that would
        silently narrow every room grant back to ownership alone the moment
        the wiring drifted, which is exactly the failure mode this service
        exists to prevent.
        """
        await session_participant_service.check_room_access(session, principal, action)

    if project_service is not None:

        async def project_principal(request: Request):
            return await _optional_principal(request)

        router.include_router(create_projects_router(project_service, project_principal))
        router.include_router(create_session_projects_router(project_service, project_principal))

    @router.get("/feature-flags", tags=["Features"])
    async def get_feature_flags(request: Request) -> dict:
        """Return feature flags derived from server configuration.

        Lets the frontend adapt its UI based on what the backend supports
        (e.g. local mounts are only meaningful in k3s / CLI mode).
        """
        settings = request.app.state.settings
        admin = request.app.state.admin_settings
        storage = request.app.state.storage
        return {
            "local_mounts_enabled": settings.local_mounts.enabled,
            "file_manager_enabled": admin.get("storage", {}).get("file_manager_enabled", True),
            "home_volumes_supported": storage.supports_home_volumes,
            "mini_mode": settings.local_mounts.mini_mode,
            "local_mounts_allowed_prefixes": settings.local_mounts.allowed_prefixes,
            "capabilities": {"local_session_scope": True},
            "projects_enabled": project_service is not None,
            "project_assignment_enabled": project_service is not None,
            "project_contract_version": 1 if project_service is not None else 0,
            "project_instance_id": project_service.instance_id if project_service else None,
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
        except httpx.HTTPError as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not fetch branches from the Git provider; try again shortly.",
            ) from e
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )

    def _workspace_forge(request: Request) -> ForgeService:
        """Bind the shared Forge facade to the request-scoped workspace service."""
        return forge.with_workspace_service(request.app.state.workspace_service)

    def _coordination_filtered(
        sessions: list[Session],
        *,
        project_id: UUID | None,
        role: str | None,
        parent_session_id: UUID | None,
        parent_instance_id: str | None,
    ) -> list[Session]:
        if project_id is not None:
            sessions = [
                s for s in sessions if s.coordination and s.coordination.project_id == project_id
            ]
        if role is not None:
            sessions = [s for s in sessions if s.coordination and s.coordination.role == role]
        if parent_session_id is not None or parent_instance_id is not None:
            sessions = [
                s
                for s in sessions
                if s.coordination
                and s.coordination.parent
                and (
                    parent_session_id is None
                    or s.coordination.parent.session_id == parent_session_id
                )
                and (
                    parent_instance_id is None
                    or s.coordination.parent.instance_id == parent_instance_id
                )
            ]
        return sessions

    def _room_summary(session: Session, grant: SessionParticipant) -> SessionRoomSummary:
        return SessionRoomSummary(
            id=session.id,
            name=session.name,
            status=session.status,
            owner_id=session.owner_id,
            room=SessionRoomInfo(
                role=grant.role.value,
                participant_status=grant.status.value,
                expires_at=grant.expires_at.isoformat() if grant.expires_at else None,
            ),
        )

    @router.get("/sessions", response_model=None, tags=["Sessions"])
    async def list_sessions(
        request: Request,
        response: Response,
        scope: Literal["local", "guild"] = Query(
            default="local", description="Standalone Forge always serves its own local sessions"
        ),
        status_filter: SessionStatus | None = Query(
            default=None, alias="status", description="Filter by session status"
        ),
        include_archived: bool = Query(
            default=False, description="Include archived sessions in results"
        ),
        project_id: UUID | None = Query(default=None),
        role: str | None = Query(default=None),
        parent_session_id: UUID | None = Query(default=None),
        parent_instance_id: str | None = Query(default=None),
    ) -> list[SessionResponse | SessionRoomSummary]:
        """List all sessions. Archived sessions are excluded by default.

        A session the caller owns (or is tenant-admin/viewer for) renders as
        the full ``SessionResponse``. A session the caller only actively
        participates in (an invited, accepted session_participants grant)
        renders as the smaller ``SessionRoomSummary`` instead — it is
        authorized through Cedar's ``read_room``, never ``read``, so it must
        never carry what ``read`` alone unlocks (repo/branch/local_path,
        pod_name, error, activity_metadata, external ids).
        """
        response.headers["X-Forge-Session-Scope"] = "local"
        principal = await _optional_principal(request)
        if principal is None and _strict_identity_enabled(request):
            return []
        sessions = await forge.list_sessions(
            status=status_filter,
            include_archived=include_archived,
            principal=principal,
        )
        sessions = _coordination_filtered(
            sessions,
            project_id=project_id,
            role=role,
            parent_session_id=parent_session_id,
            parent_instance_id=parent_instance_id,
        )
        sessions = await forge.with_read_states(sessions, principal)
        result: list[SessionResponse | SessionRoomSummary] = [
            _session_response(s) for s in sessions
        ]

        room_pairs = await forge.list_participant_only_sessions(
            status=status_filter,
            include_archived=include_archived,
            principal=principal,
            exclude_ids=frozenset(s.id for s in sessions),
        )
        room_sessions = _coordination_filtered(
            [s for s, _grant in room_pairs],
            project_id=project_id,
            role=role,
            parent_session_id=parent_session_id,
            parent_instance_id=parent_instance_id,
        )
        room_session_ids = {s.id for s in room_sessions}
        result.extend(_room_summary(s, g) for s, g in room_pairs if s.id in room_session_ids)
        return result

    @router.get(
        "/sessions/stream",
        responses={503: {"model": ErrorResponse}},
        tags=["Sessions"],
    )
    async def stream_sessions(
        request: Request,
        scope: Literal["local"] = Query(
            default="local",
            description=(
                "A standalone Forge streams only its own sessions. Guild aggregation "
                "is served by the Niuu host's /api/v1/forge/sessions/stream."
            ),
        ),
    ) -> StreamingResponse:
        """Stream real-time session updates via Server-Sent Events (SSE).

        This endpoint provides a real-time stream of session events including:
        - session_created: When a new session is created
        - session_updated: When a session is updated (status, activity, etc.)
        - session_deleted: When a session is deleted
        - stats_updated: Periodic stats updates (every 30s)
        - heartbeat: Keep-alive signal (every 30s)

        Session events are scoped like ``GET /sessions``: a caller receives
        events for its own sessions, or for its tenant's sessions as a tenant
        admin. ``stats_updated`` carries the figures ``GET /stats`` returns to
        the same caller.

        Events are formatted as SSE:
        ```
        event: session_updated
        data: {"id": "...", "status": "running", ...}

        ```
        """
        if event_stream is None:
            logger.warning("SSE stream requested but broadcaster is None")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Event streaming not available",
            )

        principal = await _optional_principal(request)
        try:
            event_stream.authorize(principal)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

        client_host = request.client.host if request.client else "unknown"
        logger.info("SSE stream: client connected from %s", client_host)

        async def event_generator():
            event_count = 0
            try:
                async for event in event_stream.subscribe(principal):
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
                "X-Forge-Session-Scope": "local",
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
        except HistoryImportValidationError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        except HistoryImportError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        return _session_response(session)

    @router.post("/sessions/{session_id}/history/import", tags=["Sessions"])
    async def import_session_history(request: Request, session_id: UUID) -> dict:
        """Backfill a recovered native session before starting it.

        Uses the session's stored provider and native ID. Existing conversation
        data or an active writer causes a conflict; identical retries are safe.
        """
        if external_session_service is None:
            raise HTTPException(status_code=503, detail="External session discovery not available")
        principal = await _optional_principal(request)
        try:
            return await external_session_service.backfill_session(session_id, principal)
        except (SessionAccessDeniedError, ExternalSessionPathNotAllowedError) as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        except (
            HistoryImportSessionNotFoundError,
            ExternalSessionProviderNotFoundError,
            ExternalSessionNotFoundError,
        ) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except HistoryImportConflictError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except (HistoryImportValidationError, ExternalSessionWorkspaceError) as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        except HistoryImportError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

    @router.post(
        "/sessions",
        response_model=SessionResponse,
        status_code=status.HTTP_201_CREATED,
        responses={
            422: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
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
            started = await project_result(
                forge.create_and_start_session(data, principal=principal)
            )
        except RepoValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(e),
            )
        except SessionCapacityError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            )
        except SessionStateError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            )
        except (ExecutionCatalogError, ExecutionSelectionError) as e:
            raise _execution_catalog_http_error(e) from e
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
            await forge.ensure_access(session, principal, "read")
        except SessionAccessDeniedError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied to session {session_id}",
            )

        session = (await forge.with_read_states([session], principal))[0]
        return _session_response(session)

    @router.get(
        "/sessions/{session_id}/room",
        response_model=SessionRoomSummary,
        responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
        tags=["Sessions"],
    )
    async def get_session_room(
        request: Request, session_id: UUID = Path(description="Unique session identifier")
    ) -> SessionRoomSummary:
        """Room-scoped session view for a participant who does not own the session.

        ``GET /sessions/{id}`` requires "read" (owner/admin/tenant-viewer
        only) and 403s a participant. This is the room-scoped equivalent,
        authorized by "read_room" instead — the same action that lets a
        participant open the room's websocket, gates, and transcript. The
        owner/admin may call it too; Cedar grants them read_room as well.
        """
        session = await forge.get_session(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        principal = await _optional_principal(request)
        try:
            await _check_room_access(session, principal, "read_room")
        except PermissionError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication is required to open this room",
            )
        except SessionAccessDeniedError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied to session {session_id}",
            )

        grant = None
        if principal is not None:
            grant = await session_participant_service.get_own_grant(session_id, principal.user_id)
        if grant is not None and grant.is_active:
            return _room_summary(session, grant)
        # No active grant: read_room passed some other way (owner/admin),
        # which Cedar only ever grants that role without a room_* attribute
        # match.
        return SessionRoomSummary(
            id=session.id,
            name=session.name,
            status=session.status,
            owner_id=session.owner_id,
            room=SessionRoomInfo(role="owner", participant_status="active"),
        )

    @router.get("/sessions/{session_id}/runtime-version", tags=["Sessions"])
    async def get_runtime_version(request: Request, session_id: UUID) -> dict:
        """Inspect the loaded gateway, without restarting it or trusting stored activity."""
        session = await forge.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        principal = await _optional_principal(request)
        try:
            await forge.ensure_access(session, principal, "read")
        except SessionAccessDeniedError:
            raise HTTPException(status_code=403, detail="Access denied") from None
        result = {"available": runtime_build, "current": None, "state": "unavailable"}
        if not session.chat_endpoint:
            result["state"] = "not_running"
            return result
        _, base_url = await forge.get_session_proxy_target(session_id)
        url, headers = _http_proxy_target(_session_proxy_url(base_url, "health"))
        if auth := request.headers.get("authorization"):
            headers["Authorization"] = auth
        try:
            async with httpx.AsyncClient(timeout=runtime_health_timeout) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                health = response.json()
            if not isinstance(health, dict) or health.get("session_id") != str(session_id):
                raise HTTPException(
                    status_code=502, detail="Gateway identity does not match session"
                )
            result["current"] = {
                key: health.get(key) for key in ("revision", "build", "source_sha256", "dirty")
            }
            current_hash = health.get("source_sha256")
            available_hash = (runtime_build or {}).get("source_sha256")
            if current_hash and available_hash:
                result["state"] = "current" if current_hash == available_hash else "different"
            else:
                result["state"] = "unknown"
        except (httpx.HTTPError, ValueError):
            # A failed probe is explicitly unknown, never evidence that a gateway stopped.
            result["state"] = "unavailable"
        return result

    @router.get(
        "/sessions/{session_id}/read-state", response_model=SessionReadState, tags=["Sessions"]
    )
    async def get_session_read_state(request: Request, session_id: UUID) -> SessionReadState:
        principal = await _optional_principal(request, strict=True)
        if principal is None:
            raise HTTPException(status_code=401, detail="Reader identity is required")
        try:
            return await forge.get_read_state(session_id, principal)
        except SessionNotFoundError:
            raise HTTPException(status_code=404, detail="Session not found") from None
        except SessionAccessDeniedError:
            raise HTTPException(status_code=403, detail="Access denied") from None

    @router.patch(
        "/sessions/{session_id}/read-state", response_model=SessionReadState, tags=["Sessions"]
    )
    async def change_session_read_state(
        request: Request,
        session_id: UUID,
        change: SessionReadStateChange,
    ) -> SessionReadState:
        principal = await _optional_principal(request, strict=True)
        if principal is None:
            raise HTTPException(status_code=401, detail="Reader identity is required")
        try:
            return await forge.change_read_state(session_id, change, principal)
        except SessionNotFoundError:
            raise HTTPException(status_code=404, detail="Session not found") from None
        except SessionAccessDeniedError:
            raise HTTPException(status_code=403, detail="Access denied") from None
        except SessionReadStateConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

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
            await forge.ensure_access(session, principal, "read")
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
            422: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["Sessions"],
    )
    @router.post(
        "/sessions/{session_id}/resume",
        response_model=SessionResponse,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
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
                integration_ids=data.integration_ids if data else None,
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
        except SessionCapacityError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            )
        except SessionStateError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            )
        except (ExecutionCatalogError, ExecutionSelectionError) as e:
            raise _execution_catalog_http_error(e) from e

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
                session_id,
                activity_state,
                data.metadata,
                state_since=data.state_since,
                turn_started_at=data.turn_started_at,
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
        """Get aggregate statistics over the sessions the caller may list.

        Scoped like ``GET /sessions``: the caller's own sessions, or its
        tenant's sessions as a tenant admin.
        """
        principal = await _optional_principal(request)
        try:
            stats = await forge.get_stats(principal)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
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
        if not isinstance(content, str) or not content.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="content is required",
            )

        # Keep the caller's identity across a timeout/retry. The broker atomically
        # claims it in durable storage before dispatching to the native process.
        import re

        req_id = body.get("request_id") or str(uuid4())
        if not isinstance(req_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", req_id):
            raise HTTPException(status_code=400, detail="Invalid request_id")

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
        # An instruction sender needs delivery ACKs, not the whole conversation.
        # Old proxies only forward history=recent, still avoiding full replay;
        # protocol2 gateways honor none and omit history entirely. Never resend.
        endpoint = urlsplit(ws_url)
        query = [
            (k, v)
            for k, v in parse_qsl(endpoint.query, keep_blank_values=True)
            if k not in {"history", "history_protocol", "history_delivery"}
        ]
        query.extend(
            (("history", "recent"), ("history_protocol", "2"), ("history_delivery", "none"))
        )
        ws_url = urlunsplit(endpoint._replace(query=urlencode(query)))
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
        delivery: dict[str, Any] | None = None
        dispatch_attempted = False
        try:
            async with connect(ws_url, **connect_kwargs) as ws:
                # Send immediately. Draining startup traffic *before* sending can
                # delay or drop the first user turn for transports that emit
                # welcome or capability events while still coming online.
                dispatch_attempted = True
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
                            and frame.get("type")
                            in (
                                "user_delivered",
                                "user_delivery_failed",
                                "user_delivery_pending",
                                "error",
                            )
                        ):
                            return frame

                with contextlib.suppress(Exception):
                    delivery = await asyncio.wait_for(
                        _await_ack(), timeout=SEND_MESSAGE_ACK_GRACE_SECONDS
                    )
        except Exception as e:
            if dispatch_attempted:
                # The send may have reached the broker before the socket failed.
                # Preserve the identity so the caller can inspect/retry that same
                # claim without executing a new instruction.
                return JSONResponse(
                    status_code=status.HTTP_202_ACCEPTED,
                    content={
                        "status": "pending",
                        "delivery": "pending",
                        "session_id": str(session_id),
                        "request_id": req_id,
                        "message": "Delivery is unconfirmed; check this request before retrying.",
                    },
                )
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

        if isinstance(delivery, dict) and delivery.get("type") == "error":
            code = delivery.get("code")
            error_status = {
                "request_id_conflict": status.HTTP_409_CONFLICT,
                "message_claim_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
            }.get(code, status.HTTP_502_BAD_GATEWAY)
            return JSONResponse(
                status_code=error_status,
                content={
                    "status": "failed",
                    "delivery": "failed",
                    "request_id": req_id,
                    "session_id": str(session_id),
                    "code": code,
                    "message": delivery.get("content") or "Message was not accepted",
                },
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
        if not isinstance(delivery, dict) or delivery.get("type") == "user_delivery_pending":
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
        after: int = Query(
            -1,
            ge=-1,
            description=(
                "P2 incremental fetch (2026-07-12): return only turns with ABSOLUTE index > "
                "`after` (-1 = disabled). Sessions are append-only, so a client that cached the "
                "settled prefix asks for `after=<lastCachedIndex>` and pays only for new turns. "
                "Takes precedence over limit/before; window_offset = after+1 keeps absolute "
                "indices derivable."
            ),
        ),
        max_bytes: int = Query(
            0,
            ge=0,
            description="Optional recent-activity byte budget; 0 returns the complete page. "
            "Oversized single turns are marked history_preview and remain loadable on demand.",
        ),
        after_id: str | None = Query(
            None,
            description=(
                "Seam identity echo for `after` (2026-07-12): the id the client holds for the "
                "turn AT index `after`. If the server's turn at that index has a different id, "
                "the client's cached index space no longer matches (transcript re-segmented or "
                "rewritten) — the response then carries empty turns with window_offset=-1, which "
                "the client's seam guard rejects, triggering a clean windowed refetch instead of "
                "appending re-served turns as phantom messages."
            ),
        ),
        history_protocol: int = Query(0),
        cursor: str | None = Query(None),
        turn_id: str | None = Query(None),
    ) -> dict:
        """Return conversation history from a live session or stopped-session workspace.

        When ``detail=shallow`` the heavy ``tool_result`` payloads are elided to
        placeholders on BOTH the live-pod path (the broker elides at the source)
        and the durable-log rebuild fallback (elided here), so the shape is
        identical regardless of which path served the transcript.
        """
        if cursor and history_protocol != 2:
            raise HTTPException(400, "cursor requires history_protocol=2")
        if history_protocol == 2 and (before or after >= 0 or after_id or turn_id):
            raise HTTPException(
                400, "Protocol2 cursors cannot be mixed with legacy seek parameters"
            )
        if history_protocol == 2:
            limit = min(limit or history_max_turns, history_max_turns)
            max_bytes = min(max_bytes or history_max_bytes, history_max_bytes)
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
            if turn_id is not None:
                turn = next((t for t in all_turns if t.get("id") == turn_id), None)
                if turn is None:
                    raise HTTPException(404, "History item no longer exists in this projection")
                return {
                    "turn": json_text_safe(turn),
                    "projection_revision": payload.get("projection_revision")
                    or projection_revision(all_turns),
                }
            if history_protocol == 2:
                if payload.get("history_protocol") == 2:
                    # The gateway applied the cursor to the COMPLETE projection.
                    # Re-slicing this page would corrupt offsets/fingerprints.
                    return payload
                try:
                    return prepare_history_page(
                        {
                            **payload,
                            "head_seq": payload.get("head_seq"),
                            "history_source": "legacy_gateway"
                            if fetch_ms is not None
                            else "archive",
                        },
                        session_id=str(session_id),
                        max_turns=limit,
                        max_bytes=max_bytes,
                        cursor=cursor,
                    )
                except InvalidHistoryCursorError as exc:
                    raise HTTPException(
                        409,
                        {
                            "code": "history_cursor_invalid",
                            "recovery": "recent",
                            "history_protocol": 2,
                        },
                    ) from exc
                except ConversationSnapshotTooLargeError as exc:
                    raise HTTPException(
                        413, {"code": "history_page_too_large", "recovery": "retry"}
                    ) from exc
                except ValueError as exc:
                    raise HTTPException(400, "Malformed history cursor") from exc
            # Compute over the complete prefix BEFORE pagination/shallow elision.
            payload = {
                **payload,
                "projection_revision": projection_revision(all_turns),
            }
            total = len(all_turns)
            if after >= 0:
                # P2 incremental window: everything past the client's cached prefix. An `after`
                # at/beyond the end returns an empty list with the true total — the no-op poll.
                start = min(after + 1, total)
                turns = all_turns[start:]
                window_offset = start
                if after_id and after < total:
                    anchor = all_turns[after]
                    anchor_id = (
                        str(anchor.get("id") or "").strip() if isinstance(anchor, dict) else ""
                    )
                    if anchor_id and anchor_id != after_id:
                        # Seam identity mismatch: the turn at the client's cached seam is not
                        # the turn the client cached (re-segmentation / rewrite). Return an
                        # explicitly-invalid window; the client's `window_offset == seam`
                        # guard rejects it and falls back to a clean windowed refetch.
                        logger.info(
                            "[seam] after_id mismatch session=%s after=%d held=%s server=%s",
                            _sanitize_log(session_id),
                            int(after),
                            _sanitize_log(after_id),
                            _sanitize_log(anchor_id),
                        )
                        turns = []
                        window_offset = -1
            elif limit > 0:
                end = max(0, total - before)
                start = max(0, end - limit)
                turns = all_turns[start:end]
                window_offset = start  # older turns available before this window
            else:
                turns = all_turns
                window_offset = 0
            turns = json_text_safe(turns)
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
            if max_bytes and turns and window_offset >= 0:
                out = prepare_recent_snapshot(out, max_bytes=max_bytes, max_turns=len(turns))
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

        principal = await _optional_principal(request)
        try:
            await forge.ensure_access(session, principal, "read")
        except SessionAccessDeniedError as exc:
            raise HTTPException(403, f"Access denied to session {session_id}") from exc

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
                # WEDGE GRACE (2026-07-12): a huge session's broker can stall for seconds
                # mid-append (it synchronously rewrites its whole conversation snapshot — a
                # 496MB file on lexi-frontend-presentation), and a 10s read timeout here sent
                # the client to the durable fallback, flipping the served turn space under a
                # cached client. Give a busy-but-alive broker 20s to answer; a DEAD pod still
                # fails fast (3s connect).
                params: dict[str, str | int] = {"detail": detail}
                if history_protocol == 2:
                    params.update(history_protocol=2, limit=limit, max_bytes=max_bytes)
                    if cursor:
                        params["cursor"] = cursor
                if turn_id is not None:
                    params["turn_id"] = turn_id
                async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=3.0)) as client:
                    response = await client.get(
                        proxy_url,
                        headers=headers,
                        params=params,
                    )
                    if history_protocol == 2 and response.status_code != 200:
                        # A typed stale/busy response must not silently select a
                        # different history source and pretend its cursor worked.
                        try:
                            error = response.json().get("detail", "History unavailable")
                        except (ValueError, AttributeError):
                            error = "History unavailable"
                        raise HTTPException(response.status_code, error)
                    response.raise_for_status()
                    live = response.json()
                    # Broker round-trip: build + (source) elide + serialize + localhost transfer +
                    # JSON parse. For an old broker that ignores `detail` this is the FULL payload.
                    fetch_ms = (time.perf_counter() - t_fetch) * 1000.0
                    if isinstance(live, dict) and (
                        _live_transcript_is_renderable(live)
                        or live.get("history_protocol") == 2
                        or (turn_id is not None and live.get("turns"))
                    ):
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
                        if not (
                            is_streaming
                            or live.get("history_has_in_progress")
                            or _live_body_has_in_progress_turn(live)
                            or turn_id is not None
                        ):
                            live_turns = live.get("turns") if isinstance(live, dict) else None
                            live_count = len(live_turns) if isinstance(live_turns, list) else 0
                            if live.get("history_protocol") == 2:
                                live_count = live["total_turns"]
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
                            warm = cached is not None and seq > 0 and cached[0] == seq
                            durable_count = cached[1] if warm and cached else None
                            durable_tail = cached[2] if warm and cached else None
                            live_tail = (
                                live.get("history_settled_tail_id")
                                if live.get("history_protocol") == 2
                                else _last_settled_turn_id(live_turns)
                            )
                            if durable_count is None:
                                # COLD: serve live immediately; warm the count off the request path.
                                timings["fault_c_optimistic"] = 1.0
                                if seq > 0 and sid_str not in _DURABLE_WARMING:
                                    _DURABLE_WARMING.add(sid_str)
                                    asyncio.create_task(
                                        _warm_durable_count(forge, session_id, seq, sid_str)
                                    )
                            elif durable_count > live_count and durable_tail != live_tail:
                                # WARM + desync: rebuild to return the fuller durable body (rare).
                                #
                                # TAIL-ID GATE (2026-07-12, the flip-flop fix): a higher durable
                                # count alone is NOT a desync. The rebuild can legitimately SEGMENT
                                # the same content into more turns than the live body (verified:
                                # `present_file` deliveries were durable-only standalone turns, 31
                                # vs 25 on the same session) — preferring durable then FLIPS the
                                # served index space on every idle↔active transition, which a
                                # windowed client renders as a full reshuffle and an incremental
                                # (`after=`) client renders as PHANTOM re-appended turns. A broker
                                # that is genuinely BEHIND (resumed from a stale snapshot, crashed
                                # tail) ends on a DIFFERENT last settled turn — so only a tail-id
                                # mismatch, not a count surplus, selects the durable body.
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
                                rebuilt_tail = _last_settled_turn_id(durable_turns)
                                _durable_count_cache_put(sid_str, seq, rebuilt, rebuilt_tail)
                                if (
                                    durable is not None
                                    and rebuilt > live_count
                                    and rebuilt_tail != live_tail
                                ):
                                    logger.info(
                                        "Live conversation for session %s is short "
                                        "(%d turns, tail=%s) vs durable rebuild "
                                        "(%d turns, tail=%s); preferring durable "
                                        "(FAULT C desync)",
                                        _sanitize_log(session_id),
                                        live_count,
                                        _sanitize_log(live_tail),
                                        rebuilt,
                                        _sanitize_log(rebuilt_tail),
                                    )
                                    return _maybe_elide(durable)
                            else:
                                # Warm + live complete (durable not ahead, or same settled
                                # tail = segmentation-only difference) — no rebuild, serve live.
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

    @router.get("/sessions/{session_id}/conversation/turns/{turn_id}", tags=["Sessions"])
    async def get_conversation_item(request: Request, session_id: UUID, turn_id: str) -> dict:
        """Explicit full-item expansion, never used for automatic history recovery."""
        return await get_conversation(
            request=request,
            session_id=session_id,
            detail="full",
            limit=0,
            before=0,
            after=-1,
            max_bytes=0,
            after_id=None,
            history_protocol=0,
            cursor=None,
            turn_id=turn_id,
        )

    async def _fetch_full_tool_result(
        request: Request,
        session: Session,
        session_id: UUID,
        tool_use_id: str,
    ) -> tuple[dict | None, list | None]:
        """Resolve one FULL tool_result block through three sources, in order:

        1. the live pod's per-id lazy-load endpoint (``api/conversation/tool-result/{id}``):
           new brokers only; a 200 short-circuits and returns ``(result, None)``;
        2. the live pod's FULL conversation-history (``api/conversation/history?detail=full``):
           the recovery path for an OLD broker that predates the per-id endpoint. An old
           broker ignores ``detail`` and serves every tool_result inline, so its history
           carries the large image results a durable rebuild can MISS. This is load-bearing:
           on the lexi-frontend-presentation session the per-id endpoint 404s and the durable
           rebuild misses 69/70 image ids, yet all are present full-base64 in the history;
        3. the durable-log rebuild (``forge.get_transcript``) -> workspace fallback: for
           stopped/seed-only sessions with no reachable pod.

        Returns ``(result, warm_turns)`` where ``result`` is the
        ``{tool_use_id, content, is_error}`` dict (or None when absent) and ``warm_turns`` is
        the turn list the caller's preview warm-pass should reuse: the live-history turns
        (source 2) or the rebuilt durable turns (source 3), whichever it fell through to. It
        is None ONLY when the per-id endpoint (source 1) answered directly (nothing to warm).
        """

        def _scan(turns: list) -> dict | None:
            # P1 input elision (2026-07-12): the response also carries the matching tool_use's
            # FULL ``input`` so an elided-input card can expand from the same lazy fetch. The
            # use and result blocks live in DIFFERENT turns (assistant emits the use; the
            # result rides the next user event), so collect both across the whole scan. An
            # input-only hit (tool still running, result not yet emitted) now answers 200 with
            # empty content instead of 404 — the expand shows the input immediately.
            found_result: dict | None = None
            found_input = None
            for turn in turns:
                parts = turn.get("parts") if isinstance(turn, dict) else None
                for block in parts or []:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use" and block.get("id") == tool_use_id:
                        candidate = block.get("input")
                        if candidate is not None and not is_elided_input(candidate):
                            found_input = candidate
                    elif (
                        block.get("type") == "tool_result"
                        and block.get("tool_use_id") == tool_use_id
                    ):
                        found_result = block
                if found_result is not None and found_input is not None:
                    break
            if found_result is None and found_input is None:
                return None
            out = {
                "tool_use_id": tool_use_id,
                "content": (found_result or {}).get("content", ""),
                "is_error": bool((found_result or {}).get("is_error", False)),
            }
            if found_input is not None:
                out["input"] = found_input
            return out

        # Live-history turns recovered from an old broker (source 2). Handed back as the warm-pass
        # source even when the specific id was not found there, since they are still a richer source
        # than the durable rebuild (which may be a rebuild-miss for big images).
        live_turns: list | None = None

        if session.chat_endpoint:
            try:
                _, base_url = await forge.get_session_proxy_target(session_id)
                headers = {}
                auth = request.headers.get("authorization")
                if auth:
                    headers["Authorization"] = auth
                tool_result_url, routing_headers = _http_proxy_target(
                    _session_proxy_url(base_url, "api", "conversation", "tool-result", tool_use_id)
                )
                headers.update(routing_headers)
                # Generous timeout: source 2 pulls the WHOLE inline transcript from an old
                # broker (tens of MB for an image-heavy session), which the tight 10s
                # conversation timeout can clip. Paid once per session (single-flight + warm).
                async with httpx.AsyncClient(timeout=45.0) as client:
                    # (1) Fast path: the per-id lazy-load endpoint (new brokers only).
                    response = await client.get(
                        tool_result_url,
                        headers=headers,
                    )
                    if response.status_code != status.HTTP_404_NOT_FOUND:
                        response.raise_for_status()
                        payload = response.json()
                        if isinstance(payload, dict):
                            return payload, None
                    # (2) 404: an OLD broker that never had the per-id endpoint. Its FULL
                    # conversation-history still carries every tool_result inline (it ignores
                    # `detail`), and it is the ONLY live source for the large image results a
                    # durable rebuild misses. Scan it, and hand the turns back so the caller's
                    # warm-pass fills the whole session from this one fetch.
                    history_url, history_routing_headers = _http_proxy_target(
                        _session_proxy_url(base_url, "api", "conversation", "history")
                    )
                    history_headers = {**headers, **history_routing_headers}
                    history = await client.get(
                        history_url, headers=history_headers, params={"detail": "full"}
                    )
                    if history.status_code != status.HTTP_404_NOT_FOUND:
                        history.raise_for_status()
                        body = history.json()
                        turns = body.get("turns") if isinstance(body, dict) else None
                        if isinstance(turns, list):
                            live_turns = turns
                            found = _scan(turns)
                            if found is not None:
                                return found, turns
            except (ValueError, httpx.HTTPStatusError, httpx.RequestError) as e:
                logger.info(
                    "Live tool-result fetch failed for session %s; trying durable log: %s",
                    _sanitize_log(session_id),
                    _sanitize_log(e),
                )

        # (3) Durable-log rebuild → workspace fallback (stopped/seed-only sessions).
        try:
            transcript = await forge.get_transcript(session_id)
        except (RuntimeError, ValueError):
            transcript = _fallback_workspace_transcript(session) or {"turns": []}
        turns = transcript.get("turns", []) if isinstance(transcript, dict) else []
        found = _scan(turns)
        if found is not None:
            return found, turns
        # Not in the durable log either. Prefer the live-history turns for the warm-pass when we
        # have them (they carry the big inline images the durable rebuild dropped).
        if live_turns is not None:
            return None, live_turns
        return None, turns

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
        session = await forge.get_session(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        found, _ = await _fetch_full_tool_result(request, session, session_id, tool_use_id)
        if found is not None:
            return found
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"tool_result not found: {tool_use_id}",
        )

    @router.get(
        "/sessions/{session_id}/tool-result/{tool_use_id}/preview",
        tags=["Sessions"],
        responses={
            404: {"model": ErrorResponse},
            501: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
        },
    )
    async def get_tool_result_preview(
        request: Request,
        session_id: UUID = Path(description="Unique session identifier"),
        tool_use_id: str = Path(description="tool_use_id of the image result"),
        image_index: int = Query(default=0, ge=0, description="Image within a multi-image result"),
    ) -> Response:
        """Return a scaled-down JPEG preview of an image tool_result.

        Generated on FIRST request (full envelope resolved via the same
        live-pod → durable-fallback chain as ``get_tool_result``, then
        Pillow-downscaled to a ~400px longest edge) and cached on disk keyed by
        tool_use_id — later connects serve it in milliseconds. When the miss was
        served by a durable-transcript rebuild, that one rebuild warms previews
        for EVERY image tool_result in the session. 404 when the result is
        absent or not an image (the client falls back to the full fetch);
        501 when Pillow is unavailable.
        """
        sid = str(session_id)
        # Keep existing first-image cache keys compatible with native clients.
        cache_key = tool_use_id if image_index == 0 else f"{tool_use_id}:image:{image_index}"

        def _jpeg_response(data: bytes) -> Response:
            return Response(
                content=data,
                media_type="image/jpeg",
                headers=dict(_PREVIEW_RESPONSE_HEADERS),
            )

        cached = preview_cache.get(sid, cache_key)
        if cached is not None:
            return _jpeg_response(cached)
        if preview_cache.is_non_image(sid, cache_key):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"tool_result is not an image: {tool_use_id}",
            )

        session = await forge.get_session(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )

        # Single-flight per session: the client fires 3-4 preview requests in
        # parallel, and each old-broker cache miss costs a multi-second durable
        # rebuild — the first holder pays it once (and warms the whole session);
        # the waiters wake into cache hits.
        async with preview_cache.session_lock(sid):
            cached = preview_cache.get(sid, cache_key)
            if cached is not None:
                return _jpeg_response(cached)

            found, durable_turns = await _fetch_full_tool_result(
                request, session, session_id, tool_use_id
            )
            try:
                if durable_turns is not None:
                    # WARM PASS: reuse the single expensive durable rebuild for
                    # every image in the session (Pillow work off the event loop).
                    warmed = await asyncio.to_thread(
                        warm_previews_from_turns, preview_cache, sid, durable_turns
                    )
                    if warmed:
                        logger.info(
                            "Warmed %d tool-result preview(s) for session %s",
                            warmed,
                            _sanitize_log(session_id),
                        )
                    cached = preview_cache.get(sid, cache_key)
                    if cached is not None:
                        return _jpeg_response(cached)

                if found is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"tool_result not found: {tool_use_id}",
                    )

                try:
                    extracted = extract_image_bytes(found.get("content"), image_index=image_index)
                except ValueError:
                    logger.warning(
                        "Corrupt image base64 in tool_result %s (session %s)",
                        _sanitize_log(tool_use_id),
                        _sanitize_log(session_id),
                    )
                    extracted = None
                if extracted is None:
                    preview_cache.mark_non_image(sid, cache_key)
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"tool_result is not an image: {tool_use_id}",
                    )
                try:
                    jpeg = await asyncio.to_thread(
                        generate_preview_jpeg,
                        extracted[0],
                        max_edge=preview_cache.max_edge,
                    )
                except ValueError:
                    logger.warning(
                        "Undecodable image tool_result %s (session %s)",
                        _sanitize_log(tool_use_id),
                        _sanitize_log(session_id),
                    )
                    preview_cache.mark_non_image(sid, cache_key)
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"tool_result image is undecodable: {tool_use_id}",
                    ) from None
            except PreviewUnavailableError:
                raise HTTPException(
                    status_code=status.HTTP_501_NOT_IMPLEMENTED,
                    detail="Preview generation unavailable: Pillow is not installed",
                ) from None
            preview_cache.put(sid, cache_key, jpeg)
            return _jpeg_response(jpeg)

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
        principal = await _optional_principal(request)
        try:
            # read_room OR read: the gate list is room state, visible to any
            # active session_participants grant (read_room), but must not
            # take away what a tenant volundr:viewer already had under
            # plain "read" — session-viewer grants "read", never "read_room".
            await _check_room_access(session, principal, "read_room")
        except PermissionError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication is required to read gates for this session",
            )
        except SessionAccessDeniedError:
            try:
                await forge.ensure_access(session, principal, "read")
            except SessionAccessDeniedError:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to read gates for this session",
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
        # An empty owner_id must never grant access: delegate to the same
        # Cedar-backed authorization path every other mutating session route
        # uses. "resolve_gate" (not "update"): only the owner/admin or an
        # ACTIVE, unexpired session_participants grant with role=approver may
        # resolve a gate (session-owner / session-participant-approver).
        try:
            await _check_room_access(session, principal, "resolve_gate")
        except PermissionError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication is required to resolve gates for this session",
            )
        except SessionAccessDeniedError:
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
        # "approver" is the minimum tier check_room_access("resolve_gate") above
        # could have just accepted (session-owner or session-participant-approver
        # are Cedar's only permit rules for it), so it always satisfies the
        # broker's own ROOM_ROLES_MAY_RESOLVE_GATES gate. This header is only
        # trustworthy when it survives to the broker unmodified: the mini-mode
        # session proxy forwards it as-is, but the Skuld Gateway (Kubernetes)
        # strips any client-supplied copy on the way in (it has no JWT claim
        # to re-derive it from) and this stamped copy is no exception — see
        # charts/skuld/templates/httproute.yaml.
        headers[ROOM_ROLE_HEADER] = "approver"
        # For the Gateway-routed hop, authenticate with a scoped workload
        # token instead (architecture.md's workload-identity exception):
        # Envoy's "workload" JWT provider (securitypolicy.yaml) verifies it
        # independently of the stripped header. Skipped only when workload
        # identity issuance itself is unconfigured for this deployment (an
        # operator decision — e.g. mini mode, which dials the broker pod
        # directly and never needs it); that is not a fallback, since the
        # header path above is the deployment's own chosen mechanism then.
        workload_identity_service = getattr(request.app.state, "workload_identity_service", None)
        if workload_identity_service is not None and workload_identity_service.enabled:
            issued = workload_identity_service.issue_token(
                principal=principal,
                workload_subject=f"forge-gate-resolve:{session_id}",
                workload_name="volundr-rest",
                audiences=[],
                token_use=VALKYRIE_BUILD_TOKEN_USE,
                claims={"scopes": [SKULD_GATE_RESOLVE_SCOPE], "gate_id": gate_id},
            )
            headers["Authorization"] = f"Bearer {issued.token}"

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
            await forge.ensure_access(session, principal, "read")
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
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
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
        The caller must be allowed ``report_chronicle`` on the session's history.
        """
        principal = await _optional_principal(request)
        with _chronicle_errors():
            chronicle = await forge.create_or_update_chronicle_from_broker(
                session_id=session_id,
                principal=principal,
                summary=data.summary,
                key_changes=data.key_changes,
                unfinished_work=data.unfinished_work,
                duration_seconds=data.duration_seconds,
            )
        return ChronicleResponse.from_chronicle(chronicle)

    # --- Chronicle endpoints ---
    #
    # Chronicles are scoped like GET /sessions: a caller sees its own history,
    # or its tenant's as a tenant admin. History outside that scope is 404;
    # history the caller sees but may not change is 403.

    @router.get(
        "/chronicles",
        response_model=list[ChronicleResponse],
        responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["Chronicles"],
    )
    async def list_chronicles(
        request: Request,
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
        """List the chronicles the caller may read, with optional filters."""
        tag_list = [t.strip() for t in tags.split(",")] if tags else None
        principal = await _optional_principal(request)
        with _chronicle_errors():
            chronicles = await forge.list_chronicles(
                principal=principal,
                project=project,
                repo=repo,
                model=model_name,
                tags=tag_list,
                limit=limit,
                offset=offset,
            )
        return [ChronicleResponse.from_chronicle(c) for c in chronicles]

    @router.post(
        "/chronicles",
        response_model=ChronicleResponse,
        status_code=status.HTTP_201_CREATED,
        responses={
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["Chronicles"],
    )
    async def create_chronicle(request: Request, data: ChronicleCreate) -> ChronicleResponse:
        """Create a chronicle from a session's current state."""
        principal = await _optional_principal(request)
        with _chronicle_errors():
            chronicle = await forge.create_chronicle(data.session_id, principal=principal)
        return ChronicleResponse.from_chronicle(chronicle)

    @router.get(
        "/chronicles/{chronicle_id}",
        response_model=ChronicleResponse,
        responses={
            401: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["Chronicles"],
    )
    async def get_chronicle(
        request: Request,
        chronicle_id: UUID = Path(description="Unique chronicle identifier"),
    ) -> ChronicleResponse:
        """Get a chronicle by ID."""
        principal = await _optional_principal(request)
        with _chronicle_errors():
            chronicle = await forge.get_chronicle(chronicle_id, principal=principal)
        if chronicle is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Chronicle not found: {chronicle_id}",
            )
        return ChronicleResponse.from_chronicle(chronicle)

    @router.patch(
        "/chronicles/{chronicle_id}",
        response_model=ChronicleResponse,
        responses={
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["Chronicles"],
    )
    async def update_chronicle(
        request: Request,
        chronicle_id: UUID = Path(description="Unique chronicle identifier"),
        data: ChronicleUpdate = ...,
    ) -> ChronicleResponse:
        """Update a chronicle's mutable fields."""
        principal = await _optional_principal(request)
        with _chronicle_errors():
            chronicle = await forge.update_chronicle(
                chronicle_id,
                principal=principal,
                summary=data.summary,
                key_changes=data.key_changes,
                unfinished_work=data.unfinished_work,
                tags=data.tags,
                status=data.status,
            )
        return ChronicleResponse.from_chronicle(chronicle)

    @router.delete(
        "/chronicles/{chronicle_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["Chronicles"],
    )
    async def delete_chronicle(
        request: Request,
        chronicle_id: UUID = Path(description="Unique chronicle identifier"),
    ) -> None:
        """Delete a chronicle."""
        principal = await _optional_principal(request)
        with _chronicle_errors():
            await forge.delete_chronicle(chronicle_id, principal=principal)

    @router.post(
        "/chronicles/{chronicle_id}/reforge",
        response_model=SessionResponse,
        responses={
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["Chronicles"],
    )
    async def reforge_chronicle(
        request: Request,
        chronicle_id: UUID = Path(description="Unique chronicle identifier"),
    ) -> SessionResponse:
        """Relaunch a session from a chronicle entry, owned by the caller."""
        principal = await _optional_principal(request)
        try:
            with _chronicle_errors():
                session = await forge.reforge_chronicle(chronicle_id, principal=principal)
        except RepoValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(e),
            ) from e
        except SessionCapacityError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            ) from e
        return _session_response(session)

    @router.get(
        "/chronicles/{chronicle_id}/chain",
        response_model=list[ChronicleResponse],
        responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["Chronicles"],
    )
    async def get_chronicle_chain(
        request: Request,
        chronicle_id: UUID = Path(description="Unique chronicle identifier"),
    ) -> list[ChronicleResponse]:
        """Get the reforge chain for a chronicle, limited to what the caller may read."""
        principal = await _optional_principal(request)
        with _chronicle_errors():
            chain = await forge.get_chronicle_chain(chronicle_id, principal=principal)
        return [ChronicleResponse.from_chronicle(c) for c in chain]

    @router.get(
        "/sessions/{session_id}/chronicle",
        response_model=ChronicleResponse,
        responses={
            401: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["Chronicles"],
    )
    async def get_session_chronicle(
        request: Request,
        session_id: UUID = Path(description="Unique session identifier"),
    ) -> ChronicleResponse:
        """Get the most recent chronicle for a session."""
        principal = await _optional_principal(request)
        with _chronicle_errors():
            chronicle = await forge.get_session_chronicle(session_id, principal=principal)
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
            401: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["Timeline"],
    )
    async def get_timeline(
        request: Request,
        session_id: UUID = Path(description="Session identifier for timeline lookup"),
    ) -> TimelineResponseModel:
        """Get the event timeline for a session's chronicle."""
        principal = await _optional_principal(request)
        with _chronicle_errors():
            timeline = await forge.get_timeline(session_id, principal=principal)
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
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
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
        """Add a timeline event for a session's chronicle, creating one if needed.

        The caller must be allowed ``report_timeline`` on the session's history;
        nothing is written before that is established.
        """
        principal = await _optional_principal(request)
        with _chronicle_errors():
            stored = await forge.add_timeline_event(
                session_id,
                principal=principal,
                t=data.t,
                type=TimelineEventType(data.type),
                label=data.label,
                tokens=data.tokens,
                action=data.action,
                ins=data.ins,
                del_=data.del_,
                hash=data.hash,
                exit_code=data.exit_code,
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
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
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
        """Get git diff for a file in a session workspace via Skuld.

        The caller must be allowed to ``read`` the session.
        """
        if base not in ("last-commit", "default-branch"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Invalid base parameter: {base}. Must be 'last-commit' or 'default-branch'"
                ),
            )

        principal = await _optional_principal(request)
        with _chronicle_errors():
            await forge.get_authorized_session(session_id, principal=principal, action="read")

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
