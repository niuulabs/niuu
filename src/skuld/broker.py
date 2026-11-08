"""Skuld Broker - WebSocket proxy for Claude Code CLI.

Supports two transport modes (selected via config):
- "sdk": long-lived CLI process connected via --sdk-url WebSocket (default)
- "subprocess": spawns claude -p per message, reads stdout (legacy fallback)
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import signal
import socket
import time
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import WebSocket, WebSocketDisconnect  # noqa: F401

from niuu.domain.conversation_timeline import TIMELINE_KEY, project_timeline, stamp_parts, timeline
from niuu.domain.logging import LoggingConfig
from niuu.domain.outcome import parse_outcome_block
from niuu.domain.peer_message import (
    PEER_MESSAGE_FRAME,
    PeerAddress,
    PeerKind,
    PeerMessage,
    PeerMessageError,
)
from niuu.domain.presented_files import PRESENT_FILE_RPC_TYPE
from niuu.domain.text_projection import projection_revision
from niuu.domain.transcript_reducer import (
    TurnAccumulator,
    apply_assistant_blocks,
    apply_result_content,
    apply_text_delta,
    apply_text_start,
    apply_text_stop,
    apply_thinking_delta,
    apply_tool_result_blocks,
    assistant_turn_id,
    build_assistant_turn,
    result_metadata,
    steering_state_from_frame,
    steering_target_id,
)
from niuu.mesh import mesh_event_prefix
from niuu.mesh.cluster import read_cluster_pub_addresses
from niuu.mesh.discovery_builder import build_discovery_adapters
from niuu.mesh.identity import MeshIdentity
from niuu.observability import get_observability
from niuu.ports.cli import CLITransport
from niuu.utils import import_class, resolve_secret_kwargs
from skuld.activity_reporting import (
    ActivityReportingMixin,
    is_idle_prompt_hook,
    is_turn_boundary_flush,
)
from skuld.channels import (
    ChannelRegistry,
    WebSocketChannel,
)
from skuld.chronicle import ChronicleMixin
from skuld.chronicle_watcher import ChronicleWatcher
from skuld.config import SkuldSettings
from skuld.control_errors import ControlRecoveryError, control_error_frame
from skuld.control_state import (
    load_control_state,
    pending_controls,
    save_control_state,
    unrestored_control,
)
from skuld.conversation_models import (  # noqa: F401
    CHRONICLE_SUMMARY_PROMPT,
    CONVERSATION_HISTORY_DIR,
    CONVERSATION_HISTORY_FILE,
    SUMMARY_TIMEOUT_SECONDS,
    ConversationTurn,
)
from skuld.conversation_read import history_write
from skuld.delivery_claims import claim_message, settle_message
from skuld.delivery_errors import DeliveryNotAcceptedError
from skuld.effort import EffortControlMixin, effort_argument
from skuld.event_log import EventLogMixin
from skuld.file_routes import (  # noqa: F401
    MkdirRequest,
    _parse_diff_output,
    _resolve_root,
    _validate_root,
    delete_file,
    download_file,
    get_diff,
    get_diff_files,
    list_files,
    mkdir,
    register_file_routes,
    upload_file_raw,
    upload_files,
)
from skuld.forge_mcp.integration import ForgeSessionMixin
from skuld.history_hydration import fetch_durable_history, merge_history_turns
from skuld.mesh_follows import MeshFollowMixin
from skuld.mesh_topics import MeshTopics, mesh_topics
from skuld.peer_inbox import PEER_STATE_RPC_TYPE, PeerInboxMixin, PeerMessageUnconfirmedError
from skuld.preview import PreviewForwarder, PreviewPortWatcher
from skuld.room_delivery import RoomDelivery, RoomDeliveryError
from skuld.service_manager import (  # noqa: F401
    ServiceCreateRequest,
    ServiceManager,
    ServiceStatus,
)
from skuld.session_artifacts import (  # noqa: F401
    GitWorkspaceCheckpoint,
    SessionArtifacts,
    _capture_git_workspace_checkpoint,
    _extract_git_commit_info,
    _git_workspace_checkpoint_status,
    _is_git_commit,
    _is_git_push,
    _resolve_git_workspace_root,
)
from skuld.transport_lifecycle import TransportLifecycleMixin
from skuld.websocket_auth import (  # noqa: F401
    WsPrincipal,
    _decode_jwt_claims,
    _extract_bearer_token,
    _extract_token_from_websocket,
    _is_loopback_ws_client,
    _resolve_ws_principal,
)
from skuld.websocket_lifecycle import WebSocketLifecycleMixin
from skuld.workflow_steps import WorkflowStepMixin
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirPublisher

# ---------------------------------------------------------------------------
# In-memory log buffer (Part 2: Pod Log Retrieval)
# ---------------------------------------------------------------------------

_log_buffer: collections.deque[dict] = collections.deque(maxlen=2000)


class _BufferHandler(logging.Handler):
    """Logging handler that appends structured records to an in-memory ring buffer."""

    def emit(self, record: logging.LogRecord) -> None:
        _log_buffer.append(
            {
                "time": self.format(record) if not hasattr(record, "asctime") else "",
                "timestamp": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
        )


def _configure_logging() -> None:
    """Configure logging from LoggingConfig (reads LOG_LEVEL, LOG_FORMAT env vars)."""
    config = LoggingConfig()
    level_name = config.level.upper()
    log_format = config.format.lower()

    level = getattr(logging, level_name, logging.INFO)

    if log_format == "json":
        fmt = (
            '{"time":"%(asctime)s","level":"%(levelname)s",'
            '"logger":"%(name)s","message":"%(message)s"}'
        )
    else:
        fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    logging.basicConfig(level=level, format=fmt)

    # Attach ring buffer handler to root logger so all loggers feed into it
    buffer_handler = _BufferHandler()
    buffer_handler.setLevel(level)
    logging.getLogger().addHandler(buffer_handler)


_configure_logging()
logger = logging.getLogger("skuld.broker")

FORGE_SESSIONS_PATH = "/api/v1/forge/sessions"
FORGE_CHRONICLES_PATH = "/api/v1/forge/chronicles"
FORGE_EVENTS_PATH = "/api/v1/forge/events"
FORGE_TRACE_SPANS_START_PATH = "/api/v1/forge/spans/start"
FORGE_TRACE_SPANS_COMPLETE_PATH = "/api/v1/forge/spans/complete"
FORGE_PERMISSION_AUTO_APPROVAL_EVALUATE_PATH = (
    f"{FORGE_SESSIONS_PATH}/{{session_id}}/permissions/auto-approval/evaluate"
)
#: Durable log frame for an agent from another session joining or leaving the room.
ROOM_MEMBER_FRAME = "room_member"


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


def _non_empty_str(value: object) -> str:
    """Return a stripped string or an empty string for absent/non-string values."""
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _telegram_directed_metadata(data: dict) -> dict[str, Any]:
    """Build the directed-room-message metadata for an inbound Telegram payload."""
    metadata: dict[str, Any] = {
        "source": "telegram",
        "telegram_message_id": str(data.get("message_id") or ""),
        "telegram_reply_to_message_id": str(data.get("reply_to_message_id") or ""),
        "telegram_chat_id": str(data.get("chat_id") or ""),
        "telegram_message_thread_id": str(data.get("message_thread_id") or ""),
        "telegram_date": str(data.get("date") or ""),
    }
    trace_context = data.get("trace_context")
    if isinstance(trace_context, dict) and trace_context:
        metadata["trace_context"] = dict(trace_context)
    reply_context = data.get("reply_context")
    if isinstance(reply_context, dict) and reply_context:
        metadata["reply_context"] = dict(reply_context)
    return metadata


# Teammate panes in agent-teams mode are splits of one window named "main",
# so the window name is not a useful identity.
_GENERIC_PANE_TITLES = {"", "main", "window", "pane", "bash", "zsh", "sh", "fish"}
_GENERIC_PANE_COMMANDS = {
    "",
    "bash",
    "zsh",
    "sh",
    "fish",
    "-bash",
    "-zsh",
    "-sh",
    "login",
    "tmux",
    "node",
    "claude",
    "python",
    "python3",
}


def _short_hostname() -> str:
    """Return the lower-cased short hostname used by tmux's default pane title."""
    try:
        return socket.gethostname().split(".", 1)[0].strip().lower()
    except Exception:
        return ""


def _is_meaningful_pane_title(title: str, window_name: str) -> bool:
    """Return whether a pane title identifies a teammate rather than its runtime."""
    lowered = title.strip().lower()
    if lowered in _GENERIC_PANE_TITLES:
        return False
    if lowered.startswith(("pane ", "window ")):
        return False
    if window_name and lowered == window_name.strip().lower():
        return False
    host = _short_hostname()
    return not (host and lowered == host)


def _teammate_pane_name(data: dict[str, Any]) -> str:
    """Derive a stable human-readable teammate name from tmux pane metadata."""
    window_name = str(data.get("window_name") or "")
    title = str(data.get("pane_title") or "").strip()
    if title and _is_meaningful_pane_title(title, window_name):
        return title
    command = str(data.get("current_command") or "").strip()
    if command and command.lower() not in _GENERIC_PANE_COMMANDS:
        return command
    pane_index = str(data.get("pane_index", "")).strip()
    return f"Teammate {pane_index}" if pane_index else "Teammate"


def _describe_browser_content_block(block: dict[str, Any]) -> str | None:
    """Return a short human-readable description for a browser content block."""
    block_type = str(block.get("type") or "").strip()
    if not block_type:
        return None
    if block_type == "image":
        return "image attachment"
    if block_type == "file":
        return "file attachment"
    return f"{block_type} attachment"


def _normalize_browser_message_content(content: object) -> str:
    """Convert browser message content into a compact text prompt.

    The browser can send either a plain string or structured content blocks,
    including large base64-encoded image attachments. Codex transports only
    accept text, so attachment payloads are summarized instead of stringified.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")

    text_parts: list[str] = []
    attachment_descriptions: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "").strip()
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())
            continue
        description = _describe_browser_content_block(block)
        if description:
            attachment_descriptions.append(description)

    lines: list[str] = []
    if text_parts:
        lines.append("\n\n".join(text_parts))
    if attachment_descriptions:
        counts = collections.Counter(attachment_descriptions)
        attachment_summary = ", ".join(
            f"{count} {label}" if count != 1 else f"1 {label}"
            for label, count in sorted(counts.items())
        )
        lines.append(f"[User attached {attachment_summary}. This transport forwards text only.]")
    return "\n\n".join(lines).strip()


@dataclass
class PendingHelpRequest:
    """A peer's genuine question, awaiting an answer from the commissioner.

    Recorded when a room peer emits a ``help_needed`` frame — the peer is
    blocked on information it cannot responsibly guess. The answer is
    delivered back to the asking peer as a directed room message.
    """

    id: str
    peer_id: str
    persona: str
    summary: str
    reason: str
    recommendation: str
    context: dict[str, Any]
    attempted: list[str]
    source_event_id: str
    task_id: str
    correlation_id: str
    root_correlation_id: str
    requested_at: str
    status: str = "pending"  # pending | answered
    answered_at: str = ""
    answer: str = ""
    answer_source: str = ""


# WebSocket auth helpers are imported above for compatibility.


# Per-message authorization for browser WebSocket traffic, keyed to the
# room role WebSocketLifecycleMixin.handle_websocket resolved and stored on
# the connection's WebSocketChannel (see _room_role_for). This is an
# ALLOWLIST, not a denylist: any message type not explicitly classified here
# defaults to OWNER-only in _message_role_requirement below, EXCEPT the
# "default chat" fallback (case _ in _dispatch_browser_message, which every
# unrecognized/absent "type" already falls into and which was already
# viewer-level content before this gate existed).
#
# tests/test_skuld/test_room_role_message_gating.py asserts these three sets,
# plus the default-chat bucket, cover every case label in
# _dispatch_browser_message's match statement — a new case added there
# without updating this classification fails that test instead of silently
# defaulting to owner-only (safe) or silently being reachable by a viewer
# (unsafe, and the thing this whole gate exists to prevent).
_VIEWER_MESSAGE_TYPES = frozenset(
    {
        "directed_message",
        "steer_active_turn",
        "set_internal_visibility",
        "discover_slash_commands",
        "get_effort",
        "get_runtime_options",
    }
)
# Approver additionally resolves the tool-permission / operator-question wait.
_APPROVER_ONLY_MESSAGE_TYPES = frozenset({"permission_response", "ask_user_answer"})
# Every other named case: mutating transport/session controls, owner only.
_OWNER_ONLY_MESSAGE_TYPES = frozenset(
    {
        "set_permission_mode",
        "rewind_files",
        "mcp_set_servers",
        "terminal_input",
        "terminal_key",
        "terminal_resize",
        "slash_command",
        "interrupt",
        "set_model",
        "set_effort",
        "set_runtime_options",
        "set_max_thinking_tokens",
        "resend_initial_prompt",
        "publish_event",
        # Forge hands another agent's message to this session. It claims a sender, so only the
        # session's owner connection (Forge acting for it) may send one.
        PEER_MESSAGE_FRAME,
    }
)
_ROOM_ROLE_RANK = {"viewer": 0, "approver": 1, "owner": 2}


def _message_role_requirement(msg_type: str | None) -> str:
    """Minimum room role required to send this browser message type.

    A type this repo recognizes as a *command* (any case label in
    _dispatch_browser_message) must be classified in one of the three sets
    above; silence is never "allow". Anything else — no "type" at all, or a
    genuinely unrecognized string — falls through to the default chat
    handler, which is viewer-level, same as any other chat content.
    """
    if msg_type in _OWNER_ONLY_MESSAGE_TYPES:
        return "owner"
    if msg_type in _APPROVER_ONLY_MESSAGE_TYPES:
        return "approver"
    return "viewer"


# Client-controllable metadata keys that let a sub-owner caller impersonate
# another participant or forge continuation state on a directed message.
# ``participant_id`` picks WHICH registered room participant the message is
# attributed to (handle_directed_room_message reads it from metadata, not a
# separate argument). ``reply_context`` can override the SERVER's own
# tracked continuation state: niuu.collaboration.room.route_directed_message
# starts from its stored pending reply_context and then applies the
# caller's metadata OVER it, so a client-supplied reply_context silently
# replaces genuine case-continuation data. ``source`` inside metadata is
# stripped for the same reason as the (already hardcoded) top-level source
# argument these dispatch cases pass. Mirrors
# skuld.broker_api._SUB_OWNER_STRIPPED_METADATA_KEYS for the HTTP path —
# kept as a separate constant rather than a shared import to avoid coupling
# this domain module to the inbound HTTP adapter.
_SUB_OWNER_STRIPPED_METADATA_KEYS = frozenset({"participant_id", "reply_context", "source"})


def _sanitize_directed_metadata(
    role: str, metadata: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Strip client-controllable identity/continuation fields for callers below owner.

    Only the session owner's own client may set ``participant_id``,
    ``reply_context``, or ``source`` inside a directed message's metadata.
    """
    if metadata is None or role == "owner":
        return metadata
    return {k: v for k, v in metadata.items() if k not in _SUB_OWNER_STRIPPED_METADATA_KEYS}


def _exit_own_process() -> None:
    """Graceful SIGTERM to this broker: uvicorn shuts down and flushes the event log."""
    os.kill(os.getpid(), signal.SIGTERM)


class Broker(
    EffortControlMixin,
    ForgeSessionMixin,
    TransportLifecycleMixin,
    WebSocketLifecycleMixin,
    EventLogMixin,
    ActivityReportingMixin,
    ChronicleMixin,
    WorkflowStepMixin,
    PeerInboxMixin,
    MeshFollowMixin,
):
    """WebSocket broker for Claude Code sessions.

    Transport-agnostic: delegates CLI communication to a CLITransport
    implementation selected by configuration.
    """

    def __init__(
        self,
        settings: SkuldSettings | None = None,
        sleipnir_publisher: SleipnirPublisher | None = None,
    ):
        self._settings = settings or SkuldSettings()
        self._init_workflow_steps()
        self._init_peer_inbox()
        self._init_mesh_follows()
        self._ws_identity = None
        self._ws_authorization = None
        if self._settings.ws_auth.enforce_ownership:
            from identity.ports import AuthorizationPort

            identity = self._settings.ws_auth.identity
            if identity is not None:
                from niuu.ports.identity import HeaderAuthenticationPort

                self._ws_identity = import_class(identity.adapter)(**identity.kwargs)
                if not isinstance(self._ws_identity, HeaderAuthenticationPort):
                    raise TypeError("WebSocket identity must implement HeaderAuthenticationPort")
            auth = self._settings.ws_auth.authorization
            self._ws_authorization = import_class(auth.adapter)(
                **resolve_secret_kwargs(auth.kwargs, auth.secret_kwargs_env)
            )
            if not isinstance(self._ws_authorization, AuthorizationPort):
                raise TypeError("WebSocket authorization must implement AuthorizationPort")

        self._room_role_resolver = None
        if self._settings.ws_auth.room_role_source == "remote":
            from skuld.room_role_port import RoomRoleResolverPort

            # WsAuthConfig's own model_validator already guarantees
            # room_role_remote is set whenever room_role_source == "remote";
            # this is defense-in-depth, matching the isinstance checks above.
            remote = self._settings.ws_auth.room_role_remote
            self._room_role_resolver = import_class(remote.adapter)(
                **resolve_secret_kwargs(remote.kwargs, remote.secret_kwargs_env)
            )
            if not isinstance(self._room_role_resolver, RoomRoleResolverPort):
                raise TypeError("room_role_remote adapter must implement RoomRoleResolverPort")

        self._effort_lock = asyncio.Lock()
        self.session_id = self._settings.session.id
        self.model = self._settings.session.model
        self.workspace_dir = self._settings.workspace_path
        archive_store_cls = import_class(self._settings.archive_store.adapter)
        self._archive_store = archive_store_cls(**self._settings.archive_store.kwargs)
        self.volundr_api_url = self._settings.volundr_api_url
        self._transport: CLITransport | None = None
        self._transport_start_lock = asyncio.Lock()
        self.service_manager: ServiceManager | None = None
        # Live preview (skuld.preview): built at startup when preview.enabled.
        self.preview_watcher: PreviewPortWatcher | None = None
        self.preview_forwarder: PreviewForwarder | None = None
        self._channels = ChannelRegistry()
        self._http_client: httpx.AsyncClient | None = None
        self._http_client_jwt: str | None = None  # JWT used to create _http_client
        self._http_client_auth_header: str = ""
        self._workload_jwt: str | None = None
        self._workload_jwt_expires_at: float = 0.0
        self._sleipnir_publisher: SleipnirPublisher = sleipnir_publisher or InProcessBus()
        self._artifacts = SessionArtifacts(
            saga_id=self._settings.session.saga_id,
            run_id=self._settings.session.run_id,
        )
        self._session_start_reported = False
        self._event_sequence = 0
        # Durable full-fidelity event log (session_event_log). Every CLI frame is
        # buffered here and flushed to Volundr by a background worker, independent
        # of any attached channel — so nothing is dropped when no client is
        # connected, and any client can replay the full transcript from a cursor.
        self._event_log_seq = 0
        self._event_log_buffer: list[dict] = []
        self._event_log_lock = asyncio.Lock()
        self._event_log_task: asyncio.Task[None] | None = None
        self._event_log_stopping = False
        # Set once the stored head is known; until then nothing is flushed.
        self._event_log_head_known = False
        # id(entry) -> (entry, future): callers (Forge MCP notify) waiting to learn
        # whether one specific frame reached Forge. See _watch_event_log_entry.
        self._event_log_receipts: dict[int, tuple[dict, asyncio.Future[bool]]] = {}
        # Per-start runtime dir + loopback secret, session tools and Forge MCP
        # toolbox — created lazily (ForgeSessionMixin).
        self._session_runtime_cache = None
        self._session_tools_cache = None
        self._forge_mcp_toolbox_cache = None
        # What the session's persona was projected into on this start (id, revision, engine,
        # projected arguments and the gaps stated as notes); empty without a persona.
        self._persona_status: dict[str, object] = {}
        self._activity_state: str = "idle"
        # Wall-clock epoch seconds (UTC) of when the session ENTERED its current
        # activity_state. Stamped only when the state actually CHANGES (see
        # _set_activity_state) so re-asserting the same state — heartbeats, etc. —
        # keeps elapsed accurate. Travels to Volundr as ISO8601 ("state_since") so
        # clients can render a live "active for 12s" without re-deriving it.
        self._activity_state_since: float = time.time()
        # Stable anchor for the current turn. It survives temporary attention
        # states and clears only when the turn becomes idle or stops.
        self._turn_started_at: float | None = None
        self._last_activity_report: float = 0.0
        # G10 (F12b/F12c): monotonic time of the newest transport frame, and whether
        # the current prompt has produced any work row yet.
        self._last_transport_row_at: float = time.monotonic()
        self._prompt_work_seen: bool = True
        # request_id -> ISO time each open human gate was first reported (F12d).
        self._control_opened_at: dict[str, str] = {}
        self._activity_report_lock = asyncio.Lock()
        self._activity_report_pending = False
        # Rich context for the CURRENT activity state (e.g. the pending question's
        # kind/request_id/prompt for awaiting_input). Re-sent verbatim by the
        # heartbeat so a heartbeat never strips the question detail or looks like
        # a new attention request. Reset whenever a plain state report arrives.
        self._activity_extra: dict[str, Any] = {}
        # request_id -> kind for every pending human gate (question / permission).
        # Non-empty == the session is blocked waiting on the user.
        self._pending_attention: dict[str, str] = {}
        self._activity_heartbeat_task: asyncio.Task[None] | None = None
        self._conversation_turns: list[ConversationTurn] = []
        self._pending_assistant_content: str = ""
        self._pending_assistant_parts: list[dict] = []
        # Tool details are available before their live preview is sent, including
        # while broadcast yields before the conversation accumulator folds them.
        self._live_tool_details: dict[str, dict[str, dict]] = {}
        self._pending_block_type: str = ""
        self._pending_reasoning_text: str = ""
        # Durable-log seq of the LAST frame folded into the open assistant turn. Drives the
        # SHARED reducer's deterministic turn-id (uuid5(session:seq:role)) so the live turn id
        # equals the id a later log rebuild assigns to the same logical turn (SRD INV-4).
        self._pending_assistant_last_seq: int = 0
        self._pending_assistant_last_ts: datetime | None = None
        self._pending_explicit_human_messages: list[tuple[str, str]] = []
        self._pending_explicit_human_response_count = 0
        self._chronicle_watcher: ChronicleWatcher | None = None
        self._startup_ready = False
        self._startup_state = "created"
        self._startup_failure = ""
        self._peer_pending_commands: dict[str, list[str]] = {}
        self._git_workspace_checkpoint: GitWorkspaceCheckpoint | None = None
        # Genuine agent questions: a peer that emits help_needed is asking the
        # party that commissioned this session for information it cannot
        # responsibly guess. Tracked like gate states so callers (Volundr
        # proxy -> Ting -> A2A) can list them and deliver an answer back to
        # the asking peer as a directed room message.
        self._pending_help_requests: dict[str, PendingHelpRequest] = {}
        self._trace_id = self._parse_trace_uuid(self.session_id)
        self._trace_session_span_id: uuid.UUID | None = None
        self._trace_assistant_span_id: uuid.UUID | None = None
        self._trace_assistant_tool_spans: dict[str, uuid.UUID] = {}
        self._trace_assistant_tool_order: list[str] = []
        self._assistant_pending_commands: dict[str, str] = {}
        self._trace_peer_turn_spans: dict[str, uuid.UUID] = {}
        self._trace_peer_tool_spans: dict[str, list[uuid.UUID]] = {}
        self._pending_permission_requests: dict[str, dict[str, Any]] = {}
        self._permission_auto_approval_tasks: dict[str, asyncio.Task[None]] = {}
        # tmux-reconnect fix: ask_user_question frames are CLI events (not control_request
        # RPCs), so they were broadcast live but NOT in the late-join replay set — a client
        # reconnecting while the agent is blocked on a question saw a "dead"/frozen session.
        # Track outstanding questions here so the reconnect replay re-surfaces the answerable
        # card. Cleared on answer (browser ask_user_answer), in-terminal resolve
        # (ask_user.resolved), and turn completion (result).
        self._pending_ask_user_questions: dict[str, dict[str, Any]] = {}
        self._unrestored_questions: dict[str, dict[str, Any]] = {}
        self._unrestored_permissions: dict[str, dict[str, Any]] = {}

        # Msg ids whose transport delivery task is still in flight (mid-retry, not yet
        # acked). A turn here is "pending" because it has NOT plausibly reached the
        # agent yet, so the non-native pending->active backstop MUST exclude it: a
        # transport frame for some OTHER turn must never flip a still-undelivered steer
        # to "active" (a false "consumed"). On terminal delivery failure that turn ends
        # "failed", never "active" (SRD §3.4 / INV-7). Populated when the deliver task
        # starts, cleared in its finally.
        self._delivering_msg_ids: set[str] = set()
        self._message_claim_tokens: dict[str, str] = {}

        # Live plan + running-agents surfacing (Claude tmux). The latest `plan`
        # frame (Claude's TodoWrite task list) and the set of running agents
        # (Task subagents via agent_update, teammate panes via terminal_pane_*),
        # tracked here so a reconnecting client gets the current plan + fleet,
        # and so GET /api/plan / GET /api/agents can answer from live state.
        self._current_plan: dict[str, Any] | None = None
        self._running_agents: dict[str, dict[str, Any]] = {}
        self._finished_agents: collections.deque[dict[str, Any]] = collections.deque(maxlen=50)
        # Capture the primary REPL by stable pane id; tmux can renumber indexes.
        self._primary_pane_id: str | None = None

        # Mesh adapter — only active when mesh.enabled is True
        self._mesh_adapter: Any = None
        self._mesh_attested: Any = None
        self._mesh_topics: MeshTopics | None = None
        self._mesh_identity: Any = None
        self._mesh_discovery: Any = None

        # Optional mesh-to-collaboration bridge.
        self._collaboration_mesh_bridge: Any = None
        self._observation_relay: Any = None

        # Collaboration is an optional surface. Keep its implementation out of
        # the ordinary ephemeral-session import path.
        self._room_bridge: Any = None
        if self._settings.room.enabled:
            from skuld.collaboration_adapter import (  # noqa: PLC0415
                SkuldCollaborationAdapter,
            )

            self._room_bridge = SkuldCollaborationAdapter(
                config=self._settings.room,
                channels=self._channels,
                emit_frame=self._emit_broker_frame,
                append_turn=self._append_turn,
                report_timeline_event=self._report_timeline_event,
                observe_peer_event=self._observe_room_peer_event,
                publish_presence_event=self._publish_room_presence_event,
                report_usage=self._report_usage,
                deliver_to_session=self._deliver_to_member_session,
                host_identity=self._host_peer_address,
            )
        self._restored_room_members: dict[str, dict[str, Any]] = {}

        # Retrieval reflex (NIU-1059) — lazily built from settings.reflex on
        # first forwarded user message; None when disabled.
        self._reflex_injector: Any = None
        self._reflex_initialised = False

        # JWT identity state — populated on first browser WebSocket connection
        self._user_jwt: str | None = None
        self._user_claims: dict = {}

    @staticmethod
    def _parse_trace_uuid(value: object) -> uuid.UUID:
        """Return a stable UUID for arbitrary identifiers."""
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError, AttributeError):
            return uuid.uuid5(uuid.NAMESPACE_URL, str(value))

    def _conversation_history_path(self) -> Path:
        """Return the path to the conversation history file, scoped to session ID."""
        filename = f"conversation_{self.session_id}.json"
        return Path(self.workspace_dir) / CONVERSATION_HISTORY_DIR / filename

    def _load_conversation_history(self) -> None:
        """Load conversation history from disk if it exists."""
        path = self._conversation_history_path()
        if not path.exists():
            return

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            turns = data.get("turns", [])
            self._conversation_turns = [
                ConversationTurn(
                    id=t.get("id", str(uuid.uuid4())),
                    role=t.get("role", "user"),
                    content=t.get("content", ""),
                    parts=t.get("parts", []),
                    created_at=t.get("created_at", ""),
                    metadata=t.get("metadata", {}),
                    participant_id=t.get("participant_id"),
                    participant_meta=t.get("participant_meta"),
                    thread_id=t.get("thread_id"),
                    visibility=t.get("visibility", "public"),
                )
                for t in turns
            ]
            logger.info(
                "Loaded %d conversation turns from %s",
                len(self._conversation_turns),
                path,
            )
        except Exception:
            logger.warning("Failed to load conversation history from %s", path, exc_info=True)

    def _save_conversation_history(self) -> None:
        """Persist conversation history to disk."""
        path = self._conversation_history_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            turns = [asdict(t) for t in self._conversation_turns]
            turns = project_timeline(turns, self.session_id)
            data = {"turns": turns, "projection_revision": projection_revision(turns)}
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            logger.warning("Failed to save conversation history to %s", path, exc_info=True)

    def _append_turn(self, turn: ConversationTurn) -> dict | None:
        """Append a turn and persist to disk; return its durable-log entry (if logging)."""
        self._conversation_turns.append(turn)
        self._save_conversation_history()
        return self._enqueue_event_log(
            {
                "type": "conversation.turn",
                "turn": asdict(turn),
            }
        )

    def _serialize_in_progress_turn(self) -> dict | None:
        """Reconstruct the in-flight assistant turn as a turn-shaped dict (or None when idle).

        Reads the SAME volatile pending state the flush reads and emits ONE row shaped like
        asdict(ConversationTurn) plus a trailing in_progress flag, appended to the `turns`
        array on BOTH reconnect replay and REST GET. Stable sentinel id so clients dedup
        across polls. visibility:'public' included so the row is shape-identical to asdict()
        on the replay path (web normalizers that read visibility don't choke). Mutation-safe:
        copies part dictionaries and appends the reasoning tail only to the local copy, so
        repeated polls never disturb the eventual real flush; once the turn flushes this
        predicate goes False and it is served once as a real completed turn instead.
        """
        if not (
            self._pending_assistant_content
            or self._pending_assistant_parts
            or self._pending_reasoning_text
        ):
            return None
        parts: list[dict] = [dict(part) for part in self._pending_assistant_parts]
        if self._pending_reasoning_text:
            parts = [
                *parts,
                {"type": "reasoning", "text": self._pending_reasoning_text[-500:]},
            ]
        return {
            "id": "in-progress",
            "role": "assistant",
            "content": self._pending_assistant_content,
            "parts": parts,
            "created_at": (
                self._pending_assistant_last_ts.isoformat()
                if self._pending_assistant_last_ts
                else ""
            ),
            "metadata": {"status": "in_progress"},
            "visibility": "public",
            "in_progress": True,
        }

    def _pending_accumulator(self) -> TurnAccumulator:
        """A SHARED-reducer accumulator view over the broker's volatile pending fields.

        Routes the live (incremental) fold through the SAME state object the batch rebuild
        uses, so a flush builds the turn via the one shared builder — identical id policy,
        reasoning ordering, and metadata schema as a later log rebuild (SRD INV-4).
        """
        return TurnAccumulator(
            content=self._pending_assistant_content,
            parts=self._pending_assistant_parts,
            reasoning=self._pending_reasoning_text,
            last_seq=self._pending_assistant_last_seq,
            last_ts=self._pending_assistant_last_ts,
        )

    def _flush_pending_assistant_turn(self, metadata: dict | None = None) -> None:
        """Save any accumulated assistant content as a conversation turn via the shared reducer."""
        acc = self._pending_accumulator()
        turn = build_assistant_turn(self.session_id, acc, metadata=metadata)
        if turn is None:
            return
        self._append_turn(
            ConversationTurn(
                id=turn["id"],
                role="assistant",
                content=turn["content"],
                parts=turn["parts"],
                created_at=turn["created_at"],
                metadata=turn["metadata"],
            )
        )
        self._pending_assistant_content = ""
        self._pending_assistant_parts = []
        self._pending_reasoning_text = ""
        self._pending_assistant_last_ts = None

    async def _start_mesh_adapter(self) -> None:
        """Build and start the mesh adapter when mesh.enabled is True.

        Uses niuu.mesh functions to build transport and discovery, then wraps
        them in a MeshParticipant for unified lifecycle management (NIU-634).
        """
        from niuu.mesh import (
            build_in_process_mesh,
            build_mesh_from_adapters_list,
        )
        from niuu.mesh.participant import MeshParticipant
        from niuu.mesh.transport_builder import (
            build_nng_transport,
            build_transport,
            resolve_transport_kwargs,
        )
        from skuld.mesh_adapter import MeshPeerIdError, SkuldMeshAdapter, resolve_mesh_peer_id

        mesh_cfg = self._settings.mesh
        # The one id of this session on the mesh: the transport, the certificate Forge signs,
        # discovery and every reply route use it.
        own_peer_id = resolve_mesh_peer_id(mesh_cfg.peer_id, self.session_id)

        def _sleipnir_transport(entry: dict[str, Any]) -> Any:
            transport_name = str(entry.get("transport") or mesh_cfg.transport or "nng")
            kwargs = resolve_transport_kwargs(
                self._settings,
                transport_name,
                service_prefix="skuld",
            )
            return build_transport(transport_name, **kwargs)

        # New configs select transport and discovery independently. Legacy
        # configs keep adapters as discovery entries and use local NNG.
        # Every branch raises when the configured mesh cannot be built.
        if mesh_cfg.discovery_adapters and mesh_cfg.adapters:
            mesh = build_mesh_from_adapters_list(
                adapters=list(mesh_cfg.adapters),
                own_peer_id=own_peer_id,
                rpc_timeout_s=mesh_cfg.rpc_timeout_s,
                rpc_reply_cache_size=mesh_cfg.rpc_reply_cache_size,
                sleipnir_transport_builder=_sleipnir_transport,
                environment_id=mesh_cfg.realm_id,
            )
        elif mesh_cfg.transport == "in_process":
            mesh = build_in_process_mesh(
                own_peer_id,
                mesh_cfg.rpc_timeout_s,
                rpc_reply_cache_size=mesh_cfg.rpc_reply_cache_size,
                environment_id=mesh_cfg.realm_id,
            )
        else:
            from niuu.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter  # noqa: PLC0415

            if mesh_cfg.transport == "nng":
                nng_cfg = getattr(mesh_cfg, "nng", None)
                address = (
                    getattr(nng_cfg, "pub_sub_address", "tcp://127.0.0.1:0")
                    if nng_cfg
                    else "tcp://127.0.0.1:0"
                )
                peer_addresses = read_cluster_pub_addresses(mesh_cfg.adapters)
                transport = build_nng_transport(
                    address=address,
                    service_id=f"skuld:{own_peer_id}",
                    peer_addresses=peer_addresses or None,
                )
            else:
                transport = build_transport(
                    mesh_cfg.transport,
                    **resolve_transport_kwargs(
                        self._settings,
                        mesh_cfg.transport,
                        service_prefix="skuld",
                    ),
                )
            mesh = SleipnirMeshAdapter(
                publisher=transport,
                subscriber=transport,
                own_peer_id=own_peer_id,
                rpc_timeout_s=mesh_cfg.rpc_timeout_s,
                environment_id=mesh_cfg.realm_id,
                rpc_reply_cache_size=mesh_cfg.rpc_reply_cache_size,
            )

        # Who listens and what is published is declared: the persona's `consumes` and
        # `produces` on top of the mesh config. The same declaration is announced to peers.
        self._mesh_topics = mesh_topics(self._settings)
        topics = self._mesh_topics

        # Prove who sends what: sign and verify every message on whatever transport this is.
        if mesh_cfg.attestation.required:
            mesh = await self._attest_mesh(mesh, own_peer_id)
        else:
            logger.warning(
                "mesh.attestation.required is false: this session accepts any claimed sender "
                "on the mesh and its messages carry no proof"
            )

        # Build discovery adapter using shared niuu.mesh.discovery_builder
        own_identity = MeshIdentity(
            peer_id=own_peer_id,
            realm_id=mesh_cfg.realm_id,
            persona=topics.persona_id or mesh_cfg.persona,
            # The session's name, so a peer can address this session by it.
            display_name=self._settings.session.name or "Skuld",
            participant_type="skuld",
            capabilities=list(mesh_cfg.capabilities),
            permission_mode="full_access",
            version="0.1.0",
            consumes_event_types=list(topics.consumes),
            emits_event_types=["code.changed", *topics.produces],
            role=topics.role,
            description=topics.description,
            activity_state=self._activity_state,
            busy=self._coarse_bucket(self._activity_state) == "running",
        )
        # Kept current by `_set_activity_state`, so every heartbeat carries it.
        self._mesh_identity = own_identity
        discovery = build_discovery_adapters(
            adapters_config=list(mesh_cfg.discovery_adapters or mesh_cfg.adapters),
            own_identity=own_identity,
            sleipnir_transport_builder=_sleipnir_transport,
            attestor=self._mesh_attested.attestor if self._mesh_attested is not None else None,
        )

        if self._room_bridge is not None and discovery is not None:
            loop = asyncio.get_running_loop()

            async def _register_discovered_peer(peer: Any) -> None:
                await self._room_bridge.register_mesh_peer(
                    peer_id=peer.peer_id,
                    persona=peer.persona,
                    display_name=getattr(peer, "display_name", "") or peer.persona,
                    participant_type=getattr(peer, "participant_type", "ravn"),
                    subscribes_to=list(getattr(peer, "consumes_event_types", [])),
                    emits=list(getattr(peer, "emits_event_types", [])),
                    tools=list(getattr(peer, "capabilities", [])),
                    environment_id=getattr(peer, "realm_id", "") or mesh_cfg.realm_id,
                    participant_kind="mesh",
                    heartbeat_ttl_s=0.0,
                )

            def _on_join(peer: Any) -> None:
                loop.create_task(
                    _register_discovered_peer(peer),
                    name=f"skuld-room-peer-join-{peer.peer_id}",
                )

            def _on_leave(peer: Any) -> None:
                loop.create_task(
                    self._room_bridge.unregister(peer.peer_id),
                    name=f"skuld-room-peer-leave-{peer.peer_id}",
                )

            await discovery.watch(_on_join, _on_leave)

        participant = MeshParticipant(
            mesh=mesh,
            discovery=discovery,
            peer_id=own_peer_id,
        )

        self._mesh_discovery = discovery
        self._mesh_adapter = SkuldMeshAdapter(
            participant=participant,
            config=mesh_cfg,
            session_id=self.session_id,
            peer_inbox=self,
            consumes_event_types=self._mesh_topics.consumes,
            # Flock members share the workspace, not this broker's loopback secret:
            # their present-file comes over the mesh they already speak.
            rpc_handlers={
                PRESENT_FILE_RPC_TYPE: _present_peer_file,
                PEER_STATE_RPC_TYPE: self.peer_state_rpc,
            },
        )

        if self._mesh_adapter.peer_id != own_identity.peer_id:
            raise MeshPeerIdError(
                f"the mesh runs as {self._mesh_adapter.peer_id!r} but discovery announces "
                f"{own_identity.peer_id!r}; one session has one mesh peer id"
            )

        try:
            await self._mesh_adapter.start()
            logger.info(
                "Mesh adapter started (peer_id=%s)",
                self._mesh_adapter.peer_id,
            )
            # Listen again to what the session followed before a restart.
            await self.apply_restored_follows()

            # Register Skuld + discovered mesh peers as room participants
            # so the browser UI shows them without a direct WebSocket.
            if self._room_bridge is not None:
                await self._room_bridge.register_mesh_peer(
                    peer_id=self._mesh_adapter.peer_id,
                    persona="Skuld",
                    display_name="Skuld",
                    subscribes_to=list(topics.consumes),
                    emits=["code.changed", *topics.produces],
                    tools=list(mesh_cfg.tools),
                    participant_type="skuld",
                    participant_kind="mesh",
                    heartbeat_ttl_s=0.0,
                )
            has_peers = discovery is not None and hasattr(discovery, "peers")
            if self._room_bridge is not None and has_peers:
                for peer in discovery.peers().values():
                    await _register_discovered_peer(peer)

            # Forward already-projected collaboration events from mesh peers
            # through the one configured Skuld surface adapter.
            if self._room_bridge is not None:
                from sleipnir.ports.events import SleipnirSubscriber

                sleipnir_subscriber = self._mesh_adapter.sleipnir_subscriber
                if sleipnir_subscriber is not None and self._mesh_attested is not None:
                    # The room trusts what it shows: only verified mesh events, with the
                    # sender and the projection taken from the proof, not the envelope.
                    sleipnir_subscriber = self._mesh_attested.verified_subscriber(
                        sleipnir_subscriber,
                        event_prefix=mesh_event_prefix(mesh_cfg.realm_id),
                    )
                if sleipnir_subscriber is None:
                    # Fallback: InProcessBus implements both Publisher and Subscriber.
                    # Only use it if it actually satisfies the Subscriber interface.
                    if isinstance(self._sleipnir_publisher, SleipnirSubscriber):
                        sleipnir_subscriber = self._sleipnir_publisher
                    else:
                        logger.warning(
                            "Sleipnir publisher does not implement SleipnirSubscriber"
                            " — collaboration mesh bridge disabled"
                        )
                if sleipnir_subscriber is not None:
                    from niuu.collaboration.mesh import (  # noqa: PLC0415
                        MeshCollaborationBridge,
                    )

                    self._collaboration_mesh_bridge = MeshCollaborationBridge(
                        sleipnir_subscriber,
                        handle_frame=self._room_bridge.handle_collaboration_frame,
                        register_peer=self._room_bridge.register_mesh_peer,
                        has_participant=self._room_bridge.has_participant,
                        session_id=self.session_id,
                        environment_id=mesh_cfg.realm_id,
                    )
                    await self._collaboration_mesh_bridge.start()
                    logger.info(
                        "Collaboration mesh bridge started (session_id=%s)",
                        self.session_id,
                    )

                    # Resident sessions: relay platform events (research/spec/
                    # plan completions, gates) to the resident so the chat
                    # resumes itself when its launched work lands.
                    resident_peer = self._room_default_target_peer_id()
                    if resident_peer and self._settings.observation_relay.enabled:
                        from niuu.collaboration.observation_relay import (  # noqa: PLC0415
                            ObservationRelay,
                        )

                        async def _relay_directed(
                            target: str,
                            content: str,
                            metadata: dict,
                        ) -> str:
                            return await self.handle_directed_room_message(
                                target,
                                content,
                                source="sleipnir",
                                metadata=metadata,
                            )

                        self._observation_relay = ObservationRelay(
                            sleipnir_subscriber,
                            participant=lambda: self._room_bridge.participant(resident_peer),
                            patterns=self._settings.observation_relay.event_patterns,
                            send_directed=_relay_directed,
                            broadcast_notification=self._emit_broker_frame,
                            payload_preview_chars=(
                                self._settings.observation_relay.payload_preview_chars
                            ),
                        )
                        await self._observation_relay.start()

        except Exception as exc:
            logger.error("Mesh adapter start failed: %r", exc, exc_info=True)
            self._mesh_adapter = None
            if self._mesh_attested is not None:
                await self._mesh_attested.signer.stop()
                self._mesh_attested = None
            raise

    async def _attest_mesh(self, mesh: Any, own_peer_id: str) -> Any:
        """Wrap the mesh so it signs and verifies; fatal when this session cannot sign."""
        from skuld.mesh_attestation import (  # noqa: PLC0415
            MeshAttestationUnavailableError,
            attest_mesh,
        )

        if not self.volundr_api_url:
            raise MeshAttestationUnavailableError(
                "the mesh needs Forge to certify this session's mesh identity, and "
                "volundr_api_url is not set. Set it, or set mesh.attestation.required: false "
                "to run the mesh unauthenticated"
            )
        mesh_cfg = self._settings.mesh
        attested = await attest_mesh(
            mesh,
            config=mesh_cfg.attestation,
            peer_id=own_peer_id,
            session_id=str(self.session_id),
            mesh_realm=mesh_cfg.realm_id,
            forge_token=self._settings.forge_mcp.token,
            http_client=self._get_http_client,
            request_timeout_s=self._settings.forge_mcp.forge_request_timeout_s,
            on_refusal=self.record_unverified_mesh_sender,
        )
        self._mesh_attested = attested
        return attested

    def _room_default_target_peer_id(self) -> str:
        """Peer that untargeted browser messages route to (resident sessions)."""
        if self._room_bridge is None:
            return ""
        return self._settings.room.default_target_peer_id.strip()

    def _is_declared_room_routed(self) -> bool:
        """True when configuration declares this broker owns no CLI agent.

        A room's configuration sets this: the room exists to host ravns, so the
        broker must never start a CLI agent of its own even while the room is
        still empty. Without it such a broker lazy-starts Claude on the first
        browser connect and silently answers chat meant for a ravn.
        """
        return self._room_bridge is not None and bool(self._settings.room.routed)

    def _is_room_routed_session(self) -> bool:
        """True when browser traffic flows through the room, not a CLI transport.

        Covers resident sessions (one long-lived ravn behind
        ``room.default_target_peer_id``) and brokers that declare
        ``room.routed``. Neither may lazy-start the broker's own CLI transport.
        """
        return bool(self._room_default_target_peer_id()) or self._is_declared_room_routed()

    def _sole_room_ravn_peer_id(self) -> str:
        """Peer id of the room's only ravn, or "" when that is ambiguous.

        Used solely for declared-routed brokers with no configured default
        target: with exactly one ravn there is no ambiguity about who an
        untargeted message is for. Two or more is genuinely ambiguous and must
        be reported rather than guessed at.
        """
        if self._room_bridge is None:
            return ""
        peer_ids = [
            participant.peer_id
            for participant in self._room_bridge.participants.values()
            if participant.participant_type == "ravn"
        ]
        if len(peer_ids) != 1:
            return ""
        return peer_ids[0]

    def _event_consumer_peer_ids(self, event_type: str) -> set[str]:
        """Return room peer ids that subscribe to ``event_type``."""
        if self._room_bridge is None or not event_type:
            return set()
        peer_ids: set[str] = set()
        for participant in self._room_bridge.participants.values():
            if participant.participant_type == "skuld":
                continue
            if event_type in participant.subscribes_to:
                peer_ids.add(participant.peer_id)
        return peer_ids

    def _event_consumer_ready(self, peer_id: str) -> bool:
        """Return True when an event consumer can receive mesh events."""
        if self._room_bridge is None:
            return True
        participant = self._room_bridge.participants.get(peer_id)
        if participant is not None and getattr(participant, "participant_kind", "") == "mesh":
            return True
        return bool(self._room_bridge.is_connected(peer_id))

    async def _wait_for_event_consumers(
        self,
        event_type: str,
        timeout_s: float,
        *,
        poll_interval_s: float = 0.05,
        settle_s: float = 0.0,
    ) -> bool:
        """Wait until subscribers for an event are connected.

        Events are pub/sub outcomes. If we publish them before the first
        consumer peer finishes its registration, the event can be dropped.
        Interactive events publish as soon as consumers are ready.
        """
        required_peers = self._event_consumer_peer_ids(event_type)
        if not required_peers or self._room_bridge is None:
            return True

        deadline = time.monotonic() + max(0.0, timeout_s)
        missing = {peer_id for peer_id in required_peers if not self._event_consumer_ready(peer_id)}
        if missing:
            logger.info(
                "Mesh event waiting for consumers event_type=%s peers=%s timeout=%.1fs",
                event_type,
                sorted(missing),
                max(0.0, timeout_s),
            )

        while missing and time.monotonic() < deadline:
            await asyncio.sleep(poll_interval_s)
            missing = {
                peer_id for peer_id in required_peers if not self._event_consumer_ready(peer_id)
            }

        if missing:
            logger.error(
                "Mesh event consumers failed to connect before dispatch event_type=%s peers=%s",
                event_type,
                sorted(missing),
            )
            return False

        if settle_s > 0:
            await asyncio.sleep(settle_s)
        logger.info(
            "Mesh event consumers ready event_type=%s peers=%s",
            event_type,
            sorted(required_peers),
        )
        return True

    async def _publish_mesh_event(
        self,
        event_type: str,
        task_description: str,
        *,
        source: str,
        correlation_id: str = "",
        extra_payload: dict[str, Any] | None = None,
    ) -> str:
        """Publish one task event through Skuld's configured mesh adapter."""
        from niuu.mesh.events import MeshEvent, MeshEventType

        if self._mesh_adapter is None:
            raise RuntimeError("Flock mesh is not available")
        event_id = correlation_id or str(uuid.uuid4())
        payload = {
            **(extra_payload or {}),
            "event_type": event_type,
            "session_id": self.session_id,
            "persona": "skuld",
            "prompt": task_description,
            "task_description": task_description,
            "trigger_source": source,
        }
        event = MeshEvent(
            event_id=event_id,
            type=MeshEventType.OUTCOME,
            source=f"skuld:{self._mesh_adapter.peer_id}",
            payload=payload,
            timestamp=datetime.now(UTC),
            urgency=0.8,
            correlation_id=event_id,
            session_id=self.session_id,
            task_id=None if correlation_id else event_id,
            root_correlation_id=self.session_id,
            trace_context=get_observability().inject(),
        )
        await self._mesh_adapter.publish(event, event_type)
        return event_id

    async def handle_publish_mesh_event(
        self,
        event_type: str,
        task_description: str,
        *,
        source: str = "external",
        payload: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> str:
        """Inject an operator event into the current flock through Skuld."""
        event_type = event_type.strip()
        task_description = task_description.strip()
        if not event_type:
            raise ValueError("Event type is required")
        if not task_description:
            raise ValueError("Event task description is required")
        if self._mesh_adapter is None:
            raise RuntimeError("Flock mesh is not available")
        consumers_ready = await self._wait_for_event_consumers(
            event_type,
            20.0,
        )
        if not consumers_ready:
            raise RuntimeError(f"mesh event consumers for {event_type} did not connect")
        routing_metadata = {
            "event_type": event_type,
            "routing": "mesh_event",
            "session_id": self.session_id,
            "root_correlation_id": self.session_id,
        }
        await self.handle_human_room_message(
            task_description,
            source=source,
            request_id=request_id,
            metadata=routing_metadata,
            deliver_to_transport=False,
        )
        return await self._publish_mesh_event(
            event_type,
            task_description,
            source=source,
            correlation_id=request_id or "",
            extra_payload=payload,
        )

    def _observer_peer_id(self) -> str:
        """Return Skuld's room participant id when available."""
        if self._mesh_adapter is not None and getattr(self._mesh_adapter, "peer_id", ""):
            return str(self._mesh_adapter.peer_id)
        if self._room_bridge is None:
            return ""
        for participant in self._room_bridge.participants.values():
            if participant.participant_type == "skuld":
                return participant.peer_id
        return ""

    def _refresh_git_workspace_artifacts(self) -> None:
        """Fold deterministic git workspace state into the session artifact counters."""
        commit_ok, push_ok = _git_workspace_checkpoint_status(self._git_workspace_checkpoint)
        if commit_ok:
            self._artifacts.git_commit_count = max(self._artifacts.git_commit_count, 1)
        if push_ok:
            self._artifacts.git_push_count = max(self._artifacts.git_push_count, 1)

    async def _observe_room_peer_event(
        self,
        peer_id: str,
        event_type: str,
        frame: dict[str, Any],
    ) -> None:
        """Fold a room peer's events into traces, artifacts, outcomes and help requests."""
        if self._room_bridge is None:
            return
        observer_peer_id = self._observer_peer_id()
        if peer_id == observer_peer_id:
            return
        participant = self._room_bridge.participants.get(peer_id)
        peer_label = (
            participant.persona
            if participant is not None and getattr(participant, "persona", "")
            else peer_id
        )

        if event_type == "outcome":
            await self._emit_peer_outcome_pipeline_event(peer_id, frame)
        elif event_type == "help_needed":
            request = self._record_pending_help_request(peer_id, frame)
            if request is None:
                return
            await self._report_peer_help_needed_activity(request, frame)

        if event_type == "task_started":
            metadata = frame.get("metadata", {})
            title = str(metadata.get("title") or frame.get("data") or "task")
            task_id = str(metadata.get("task_id") or frame.get("task_id") or peer_id)
            existing_turn_span_id = self._trace_peer_turn_spans.get(peer_id)
            if existing_turn_span_id is None:
                self._trace_peer_turn_spans[peer_id] = await self._start_trace_span(
                    kind="turn.peer",
                    name=title or peer_label,
                    parent_span_id=self._trace_session_span_id,
                    actor_type="peer",
                    actor_id=peer_id,
                    actor_label=peer_label,
                    attributes={"task_id": task_id},
                )
            return

        if event_type == "tool_start":
            metadata = frame.get("metadata", {})
            tool_input = metadata.get("input") if isinstance(metadata, dict) else {}
            tool_name = (
                str(metadata.get("tool_name") or frame.get("data") or "tool").strip() or "tool"
            )
            if peer_id not in self._trace_peer_turn_spans:
                self._trace_peer_turn_spans[peer_id] = await self._start_trace_span(
                    kind="turn.peer",
                    name=peer_label,
                    parent_span_id=self._trace_session_span_id,
                    actor_type="peer",
                    actor_id=peer_id,
                    actor_label=peer_label,
                    attributes={"source": "tool_start"},
                )
            tool_span_id = await self._start_trace_span(
                kind="tool.call",
                name=tool_name,
                parent_span_id=self._trace_peer_turn_spans.get(peer_id),
                actor_type="peer",
                actor_id=peer_id,
                actor_label=peer_label,
                attributes={"tool_input": tool_input if isinstance(tool_input, dict) else {}},
            )
            if tool_span_id is not None:
                self._trace_peer_tool_spans.setdefault(peer_id, []).append(tool_span_id)
            command = ""
            if isinstance(tool_input, dict):
                command = str(tool_input.get("command") or "").strip()
            if command:
                self._peer_pending_commands.setdefault(peer_id, []).append(command)

        if event_type == "tool_result":
            metadata = frame.get("metadata", {})
            pending = self._peer_pending_commands.get(peer_id, [])
            command = pending.pop(0) if pending else ""
            if not pending:
                self._peer_pending_commands.pop(peer_id, None)
            if command and not bool(metadata.get("is_error")):
                if _is_git_commit(command):
                    self._artifacts.git_commit_count += 1
                elif _is_git_push(command):
                    self._artifacts.git_push_count += 1
            tool_spans = self._trace_peer_tool_spans.get(peer_id, [])
            tool_span_id = tool_spans.pop(0) if tool_spans else None
            if not tool_spans:
                self._trace_peer_tool_spans.pop(peer_id, None)
            await self._finish_trace_span(
                tool_span_id,
                status="failed" if bool(metadata.get("is_error")) else "completed",
                attributes={
                    "command": command,
                    "is_error": bool(metadata.get("is_error")),
                },
            )
        elif event_type in {"response", "task_complete"}:
            tool_spans = self._trace_peer_tool_spans.pop(peer_id, [])
            pending_commands = self._peer_pending_commands.pop(peer_id, [])
            for index, tool_span_id in enumerate(tool_spans):
                command = pending_commands[index] if index < len(pending_commands) else ""
                await self._finish_trace_span(
                    tool_span_id,
                    status="completed",
                    attributes={
                        "command": command,
                        "peer_id": peer_id,
                        "terminal_event": event_type,
                    },
                )
        elif event_type == "error":
            tool_spans = self._trace_peer_tool_spans.pop(peer_id, [])
            pending_commands = self._peer_pending_commands.pop(peer_id, [])
            for index, tool_span_id in enumerate(tool_spans):
                command = pending_commands[index] if index < len(pending_commands) else ""
                await self._finish_trace_span(
                    tool_span_id,
                    status="failed",
                    attributes={
                        "command": command,
                        "peer_id": peer_id,
                        "terminal_event": event_type,
                    },
                )

        if event_type in {"response", "error", "task_complete"}:
            peer_span_id = self._trace_peer_turn_spans.pop(peer_id, None)
            await self._finish_trace_span(
                peer_span_id,
                status="failed" if event_type == "error" else "completed",
                attributes={"event_type": event_type, "peer_id": peer_id},
            )

    async def _emit_peer_outcome_pipeline_event(
        self,
        peer_id: str,
        frame: dict[str, Any],
    ) -> None:
        """Persist a canonical peer outcome into the outer session event stream."""
        data = frame.get("data", {})
        if not isinstance(data, dict):
            return

        metadata = frame.get("metadata", {})
        event_type = str(metadata.get("event_type") or data.get("event_type") or "").strip()
        if not event_type:
            return
        participant = self._room_bridge.participants.get(peer_id)
        persona = (
            participant.persona if participant is not None else str(data.get("persona") or "")
        ).strip()
        fields = data.get("fields")
        if not isinstance(fields, dict):
            nested_outcome = data.get("outcome")
            fields = nested_outcome if isinstance(nested_outcome, dict) else {}

        payload: dict[str, Any] = {
            "peer_id": peer_id,
            "persona": persona,
            "event_type": event_type,
            "canonical_event_type": str(data.get("canonical_event_type") or event_type),
            "fields": fields,
            "valid": bool(data.get("valid", True)),
        }
        verdict = data.get("verdict") or fields.get("verdict")
        if verdict:
            payload["verdict"] = verdict
        summary = data.get("summary") or fields.get("summary")
        if summary:
            payload["summary"] = summary
        files_changed = data.get("files_changed") or fields.get("files_changed")
        if isinstance(files_changed, list) and files_changed:
            payload["files_changed"] = files_changed

        if not data.get("routing_only") and data.get("bubble_up") is not False:
            await self._emit_pipeline_event("outcome", payload)

    def _build_peer_help_needed_payload(
        self,
        peer_id: str,
        frame: dict[str, Any],
    ) -> dict[str, Any] | None:
        data = frame.get("data", {})
        if isinstance(data, str):
            data = {"summary": data}
        if not isinstance(data, dict):
            return None

        participant = self._room_bridge.participants.get(peer_id)
        persona = (
            participant.persona if participant is not None else str(data.get("persona") or "")
        ).strip()
        context = data.get("context")
        if not isinstance(context, dict):
            context = {}

        session_id = str(
            data.get("session_id")
            or context.get("session_id")
            or frame.get("session_id")
            or self.session_id
        ).strip()
        payload: dict[str, Any] = {
            "session_id": session_id,
            "source_event_id": str(frame.get("source_event_id") or ""),
            "task_id": str(frame.get("task_id") or ""),
            "correlation_id": str(frame.get("correlation_id") or ""),
            "root_correlation_id": str(frame.get("root_correlation_id") or ""),
            "persona": persona,
            "summary": str(data.get("summary") or "Agent requested human feedback."),
            "reason": str(data.get("reason") or "needs_context"),
            "recommendation": str(data.get("recommendation") or ""),
            "context": context,
            "target_peer_id": peer_id,
        }
        attempted = data.get("attempted")
        if isinstance(attempted, list):
            payload["attempted"] = [str(item) for item in attempted if str(item).strip()]
        if self._artifacts.run_id:
            payload["run_id"] = self._artifacts.run_id
        if self._artifacts.saga_id:
            payload["saga_id"] = self._artifacts.saga_id
        return payload

    def _record_pending_help_request(
        self, peer_id: str, frame: dict[str, Any]
    ) -> PendingHelpRequest | None:
        """Track a peer's help_needed question so a remote caller can answer it.

        Returns the new request, or ``None`` for a malformed or repeated frame.
        """
        payload = self._build_peer_help_needed_payload(peer_id, frame)
        if payload is None:
            return None
        source_event_id = str(payload.get("source_event_id") or "")
        if source_event_id and any(
            request.peer_id == peer_id and request.source_event_id == source_event_id
            for request in self._pending_help_requests.values()
        ):
            return None
        request = PendingHelpRequest(
            id=uuid.uuid4().hex[:12],
            peer_id=peer_id,
            persona=str(payload.get("persona") or ""),
            summary=str(payload.get("summary") or ""),
            reason=str(payload.get("reason") or ""),
            recommendation=str(payload.get("recommendation") or ""),
            context=dict(payload.get("context") or {}),
            attempted=[str(item) for item in payload.get("attempted") or []],
            source_event_id=source_event_id,
            task_id=str(payload.get("task_id") or ""),
            correlation_id=str(payload.get("correlation_id") or ""),
            root_correlation_id=str(payload.get("root_correlation_id") or ""),
            requested_at=datetime.now(UTC).isoformat(),
        )
        self._pending_help_requests[request.id] = request
        logger.info(
            "Pending help request recorded: id=%s peer=%s persona=%s summary=%s",
            request.id,
            peer_id,
            request.persona,
            request.summary[:200],
        )
        return request

    def list_help_requests(self) -> list[dict[str, Any]]:
        """Return help requests ordered from newest to oldest."""
        ordered = sorted(
            self._pending_help_requests.values(),
            key=lambda request: request.requested_at,
            reverse=True,
        )
        return [asdict(request) for request in ordered]

    async def _complete_help_request(
        self,
        request: PendingHelpRequest,
        answer: str,
        source: str,
    ) -> None:
        request.status = "answered"
        request.answered_at = datetime.now(UTC).isoformat()
        request.answer = answer
        request.answer_source = source
        await self._exit_attention(request.id)

    async def _mark_pending_help_answered(self, peer_id: str, answer: str, source: str) -> None:
        candidates = [
            request
            for request in self._pending_help_requests.values()
            if request.peer_id == peer_id and request.status == "pending"
        ]
        if not candidates:
            return
        request = max(candidates, key=lambda item: item.requested_at)
        await self._complete_help_request(request, answer, source)

    async def answer_help_request(
        self,
        request_id: str,
        answer: str,
        *,
        source: str = "external",
    ) -> str:
        """Deliver an answer to a pending help request's asking peer.

        The answer travels as a directed room message to the peer that asked,
        so it lands in that agent's conversation exactly like a human reply
        would. Returns the routed message id.
        """
        request = self._pending_help_requests.get(request_id)
        if request is None:
            raise ValueError(f"unknown help request: {request_id}")
        if request.status != "pending":
            raise ValueError(f"help request {request_id} is already {request.status}")
        if not answer.strip():
            raise ValueError("answer must not be empty")
        message_id = await self.handle_directed_room_message(
            request.peer_id,
            answer,
            source=source,
            metadata={"help_request_id": request.id},
        )
        await self._complete_help_request(request, answer, source)
        logger.info(
            "Help request %s answered by %s -> peer=%s message=%s",
            request.id,
            source,
            request.peer_id,
            message_id,
        )
        return message_id

    def _host_peer_address(self) -> PeerAddress:
        """This session as a party to a message: its id, name and node."""
        return PeerAddress(
            kind=PeerKind.SESSION,
            id=str(self.session_id),
            name=self._settings.session.name,
            node=socket.gethostname(),
        )

    async def _deliver_to_member_session(self, session_id: str, delivery: RoomDelivery) -> bool:
        """Hand a room delivery to the session a member joined from, as a peer envelope.

        This session calls Forge with its own session credential, so Forge stamps it
        ``relayed_by``; the participant who spoke, the room and the reply route travel as
        the ``peer`` block. A delivery that cannot be made raises: the room's caller learns
        of it, and it is counted, never logged and dropped.
        """
        if not self.volundr_api_url:
            raise RoomDeliveryError(
                f"Cannot reach session {session_id}: volundr_api_url is not set"
            )
        token = self._settings.forge_mcp.token
        if not token:
            raise RoomDeliveryError(
                f"Cannot deliver to session {session_id}: this session holds no Forge session "
                "credential (forge_mcp.token), which Forge needs to stamp it as the room's host. "
                "Enable forge_mcp.session_tokens on the Forge that launched it"
            )
        client = await self._get_http_client()
        try:
            response = await client.post(
                f"{FORGE_SESSIONS_PATH}/{session_id}/messages",
                json={
                    "content": delivery.content,
                    "request_id": delivery.request_id,
                    "peer": delivery.speech.to_body(),
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise RoomDeliveryError(
                f"Could not reach session {session_id} through Forge: {exc}"
            ) from exc
        if response.status_code >= 400:
            remedy = (
                "; this session's credential needs the 'message' grant (forge_mcp.role_grants "
                "or default_grants) to relay room messages"
                if response.status_code == 403
                else ""
            )
            raise RoomDeliveryError(
                f"Forge refused the room message for session {session_id} "
                f"({response.status_code}): {response.text[:500]}{remedy}"
            )
        return True

    async def _report_peer_help_needed_activity(
        self,
        request: PendingHelpRequest,
        frame: dict[str, Any],
    ) -> None:
        """A peer asking a person blocks the session on that person until answered."""
        payload = self._build_peer_help_needed_payload(request.peer_id, frame)
        if payload is None:
            return
        await self._enter_attention(
            request.id,
            "question",
            prompt=request.summary,
            extra_metadata={"help_needed": payload},
        )

    @staticmethod
    def _permission_request_id(data: dict[str, Any]) -> str:
        request_id = data.get("request_id")
        return str(request_id or "").strip()

    @staticmethod
    def _permission_input(data: dict[str, Any]) -> dict[str, Any]:
        input_payload = data.get("input")
        return input_payload if isinstance(input_payload, dict) else {}

    @classmethod
    def _permission_command(cls, data: dict[str, Any]) -> str | None:
        direct_command = data.get("command")
        if isinstance(direct_command, str) and direct_command.strip():
            return direct_command.strip()

        input_payload = cls._permission_input(data)
        for key in ("command", "cmd", "shell_command"):
            value = input_payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _permission_auto_approval_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        command = self._permission_command(data)
        description = data.get("description")
        if not isinstance(description, str) or not description.strip():
            description = command or ""

        tool_name = data.get("tool_name") or data.get("tool")
        if not isinstance(tool_name, str):
            tool_name = ""

        return {
            "request_id": self._permission_request_id(data),
            "tool_name": tool_name,
            "description": description,
            "command": command,
            "input": self._permission_input(data),
        }

    async def _evaluate_permission_auto_approval(
        self,
        data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Ask Forge whether this pending permission may be auto-approved."""
        if not self.volundr_api_url:
            return None

        try:
            client = await self._get_http_client()
            response = await client.post(
                FORGE_PERMISSION_AUTO_APPROVAL_EVALUATE_PATH.format(
                    session_id=self.session_id,
                ),
                json=self._permission_auto_approval_payload(data),
            )
            if response.status_code >= 300:
                logger.warning(
                    "Permission auto-approval policy check failed (%d): %s",
                    response.status_code,
                    response.text[:200],
                )
                return None
            payload = response.json()
        except Exception:
            logger.warning("Permission auto-approval policy check failed", exc_info=True)
            return None

        if not isinstance(payload, dict):
            return None
        return payload

    @staticmethod
    def _decision_delay_seconds(decision: dict[str, Any]) -> float:
        raw_delay = decision.get("delay_seconds", 0)
        try:
            delay = float(raw_delay)
        except (TypeError, ValueError):
            delay = 0.0
        return max(0.0, delay)

    def _clear_pending_permission_request(
        self,
        request_id: str,
        *,
        cancel_auto_approval: bool = True,
    ) -> None:
        self._pending_permission_requests.pop(request_id, None)
        if not cancel_auto_approval:
            return

        task = self._permission_auto_approval_tasks.pop(request_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _track_pending_permission_request(self, data: dict[str, Any]) -> None:
        request_id = self._permission_request_id(data)
        if not request_id:
            return

        self._pending_permission_requests[request_id] = dict(data)

        existing_task = self._permission_auto_approval_tasks.pop(request_id, None)
        if existing_task is not None:
            existing_task.cancel()

        if data.get("auto_approval_allowed") is False:
            # A runtime may request explicit human input/consent rather than
            # tool execution permission. Permission policy cannot supply it.
            self._pending_attention[request_id] = "permission"
            task = asyncio.create_task(
                self._enter_attention(
                    request_id,
                    "permission",
                    prompt=self._attention_prompt_from_permission(data),
                )
            )
        elif not self.volundr_api_url:
            return
        else:
            task = asyncio.create_task(self._auto_approve_permission_request(request_id))
        self._permission_auto_approval_tasks[request_id] = task

        def _cleanup(done: asyncio.Task[None]) -> None:
            current = self._permission_auto_approval_tasks.get(request_id)
            if current is done:
                self._permission_auto_approval_tasks.pop(request_id, None)

        task.add_done_callback(_cleanup)

    def _track_agent_update(self, data: dict[str, Any]) -> None:
        """Fold an `agent_update` frame into the live running-agents set."""
        agent = data.get("agent")
        if not isinstance(agent, dict):
            return
        agent_id = str(agent.get("id") or "")
        if not agent_id:
            return
        if data.get("action") == "stopped":
            tracked = self._running_agents.pop(agent_id, None)
            self._retain_finished_agent(agent_id, tracked, agent)
            return
        self._running_agents[agent_id] = dict(agent)

    def _retain_finished_agent(
        self,
        agent_id: str,
        tracked: dict[str, Any] | None,
        stopped: dict[str, Any],
    ) -> None:
        """Retain a bounded, token-enriched history of completed subagents."""
        merged = {**(tracked or {}), **stopped}
        if merged.get("kind") != "subagent":
            return
        merged.setdefault("ended_at", datetime.now(UTC).isoformat())
        row = self._enrich_agent_row(merged)
        tracker = getattr(self._transport, "agent_usage", None)
        if tracker is not None:
            with suppress(Exception):
                tracker.release(agent_id)
        for existing in list(self._finished_agents):
            if existing.get("id") == agent_id:
                self._finished_agents.remove(existing)
        self._finished_agents.append(row)

    def _enrich_agent_row(self, agent: dict[str, Any]) -> dict[str, Any]:
        """Add cumulative token usage and workflow identity to subagent rows."""
        if agent.get("kind") != "subagent":
            return agent
        tracker = getattr(self._transport, "agent_usage", None)
        if tracker is None:
            return agent
        agent_id = str(agent.get("id") or "")
        if not agent_id:
            return agent
        try:
            tokens = tracker.tokens_for(agent_id)
            workflow = tracker.workflow_for(agent_id)
        except Exception:
            logger.debug("agent usage lookup failed for %s", agent_id, exc_info=True)
            return agent
        if tokens is None and not workflow:
            return agent
        row = dict(agent)
        if tokens is not None:
            row["tokens_used"] = tokens
        if workflow and not row.get("workflow"):
            row["workflow"] = workflow
        return row

    def _track_pane_agent(self, data: dict[str, Any], *, opened: bool) -> None:
        """Treat a non-primary tmux pane as a teammate agent (agent-teams mode).

        The primary pane is the main Claude REPL, not a teammate. Its pane id
        remains stable even when tmux renumbers pane indexes.
        """
        pane_id = str(data.get("pane_id") or "")
        if not pane_id:
            return
        if opened and self._primary_pane_id is None and str(data.get("pane_index", "")) == "0":
            self._primary_pane_id = pane_id
        if self._is_primary_pane(pane_id, data.get("pane_index")):
            return
        if not opened:
            self._running_agents.pop(pane_id, None)
            return
        # The pane's `--agent-name` (agent-teams) is the authoritative teammate identity — it is the
        # name the TeammateIdle finish signal uses. Prefer it; fall back to the pane title/command
        # chain for older CLIs or non-teammate splits that don't carry one.
        agent_name = str(data.get("agent_name") or "").strip()
        self._running_agents[pane_id] = {
            "id": pane_id,
            "kind": "teammate",
            "name": agent_name or _teammate_pane_name(data),
            "status": "running",
            "current_command": str(data.get("current_command") or ""),
        }

    def _is_primary_pane(self, pane_id: str, pane_index: Any) -> bool:
        """Return whether this is the primary REPL pane."""
        if self._primary_pane_id is not None:
            return pane_id == self._primary_pane_id
        return str(pane_index if pane_index is not None else "") == "0"

    def _reap_dead_teammates(self) -> None:
        """Drop teammate rows whose tmux panes no longer exist."""
        live = getattr(self._transport, "live_pane_ids", None)
        if live is None:
            return
        try:
            live_ids = set(live)
        except TypeError:
            return
        stale = [
            agent_id
            for agent_id, agent in self._running_agents.items()
            if agent.get("kind") == "teammate" and agent_id not in live_ids
        ]
        for agent_id in stale:
            self._running_agents.pop(agent_id, None)

    async def _send_permission_control_response(
        self,
        request_id: str,
        response: dict[str, Any],
        *,
        auto_approved: bool,
    ) -> None:
        if not self._transport:
            logger.warning("Cannot respond to permission request; transport is not initialized")
            return

        await self._transport.send_control_response(request_id, response)
        self._clear_pending_permission_request(
            request_id,
            cancel_auto_approval=not auto_approved,
        )
        self._unrestored_permissions.pop(request_id, None)
        self._save_control_state()
        await self._exit_attention(request_id)
        await self._emit_broker_frame(
            {
                "type": "permission_resolved",
                "request_id": request_id,
                "behavior": response.get("behavior", "deny"),
                "auto_approved": auto_approved,
            }
        )

    async def _auto_approve_permission_request(self, request_id: str) -> None:
        initial_request = self._pending_permission_requests.get(request_id)
        if initial_request is None:
            return

        decision = await self._evaluate_permission_auto_approval(initial_request)
        if not decision or decision.get("can_auto_approve") is not True:
            # Policy won't auto-approve — a human must decide. Surface it as a
            # needs-attention gate (auto-approvable requests never flip the
            # session, so they don't spam the user with notifications).
            await self._enter_attention(
                request_id,
                "permission",
                prompt=self._attention_prompt_from_permission(initial_request),
            )
            return

        delay_seconds = self._decision_delay_seconds(decision)
        await self._emit_broker_frame(
            {
                "type": "permission_auto_approval_scheduled",
                "request_id": request_id,
                "decision": decision,
            }
        )
        await asyncio.sleep(delay_seconds)

        latest_request = self._pending_permission_requests.get(request_id)
        if latest_request is None:
            return

        # Re-check immediately before responding so policy changes or denylist
        # additions win over a stale countdown.
        latest_decision = await self._evaluate_permission_auto_approval(latest_request)
        if not latest_decision or latest_decision.get("can_auto_approve") is not True:
            await self._emit_broker_frame(
                {
                    "type": "permission_auto_approval_cancelled",
                    "request_id": request_id,
                    "decision": latest_decision,
                }
            )
            return

        logger.info("Auto-approving permission request %s", _sanitize_log(request_id))
        await self._send_permission_control_response(
            request_id,
            {"behavior": "allow", "updatedInput": {}},
            auto_approved=True,
        )

    @history_write
    async def _handle_cli_event(self, data: dict) -> None:
        """Forward a CLI event to all connected channels."""
        event_type = data.get("type", "unknown")
        self._observe_activity_row(data)

        # Use one observation instant for both the durable event row and the
        # live transcript reduction. Replaying the row will then reproduce the
        # exact same tool timing fields that live clients observed.
        frame_ts = datetime.now(UTC)

        # Room-mode suppression decision, computed BEFORE the enqueue (INV-5): while
        # an explicit human room response is pending, the raw user/assistant/
        # content_block_delta/result transport echoes are dropped from the LIVE
        # broadcast (the same content is surfaced as a room_message via
        # _emit_broker_frame / broadcast_cli_message). A frame that is never
        # broadcast must never be logged either, or it would resurface on
        # cold-read/replay as a duplicate the live viewer never saw. So skip the
        # durable enqueue for exactly those suppressed echoes. Normal (non-room)
        # logging is unchanged: outside a pending explicit response the count is 0,
        # so suppress is False and every frame is logged as before.
        suppress_channel_broadcast = (
            event_type in {"user", "assistant", "content_block_delta", "result"}
            and self._pending_explicit_human_response_count > 0
        )

        # Durably capture EVERY broadcast frame first — before any channel/broadcast
        # logic — so agent output is never lost when no client is attached. The lone
        # exception is a room-suppressed echo (above): not broadcast ⇒ not logged,
        # keeping the durable log == the live stream (INV-5).
        if not suppress_channel_broadcast:
            message = data.get("message")
            blocks = message.get("content") if isinstance(message, dict) else None
            if isinstance(blocks, list):
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    kind = block.get("type")
                    identity = block.get("id") if kind == "tool_use" else block.get("tool_use_id")
                    if kind in {"tool_use", "tool_result"} and isinstance(identity, str):
                        self._live_tool_details.setdefault(identity, {})[kind] = block
            self._enqueue_event_log(data, ts=frame_ts)

        if event_type == "remote_control":
            # A remote-control transport reporting its pairing URL. Surface it as
            # a real assistant turn (conversation history + durable-log replay +
            # live channels) so every client shows the link to hand off to the
            # native app.
            url = str(data.get("url") or "").strip()
            if url:
                await self._surface_remote_control_url(url)
            return

        if event_type == "transport_stopped":
            # The transport died (e.g. its tmux session vanished). Surface a
            # terminal ``stopped`` activity_state with a final report so clients
            # stop showing the last live state frozen forever. Synthetic event —
            # nothing downstream consumes it, so return after reporting.
            reason = data.get("reason", "transport_died")
            extra: dict[str, Any] = {"reason": reason}
            if data.get("exit_status"):
                extra["exit_status"] = data["exit_status"]
            await self._report_activity_state("stopped", extra_metadata=extra)
            if self._settings.exit_when_transport_stops:
                # A broker without its agent serves nothing but accepts messages that
                # would pend forever. Exit (graceful SIGTERM: event logs flush) so the pod
                # manager marks the session stopped and a start launches a fresh agent.
                logger.error(
                    "Agent transport stopped (%s); exiting so the session is reconciled as "
                    "stopped (set exit_when_transport_stops=false to keep serving history)",
                    reason,
                )
                asyncio.get_running_loop().call_soon(_exit_own_process)
            return

        if event_type == "control_request":
            self._track_pending_permission_request(data)
            self._unrestored_permissions.pop(str(data.get("request_id", "")), None)
            self._save_control_state()

        if event_type == "permission_resolved":
            # Native cancellation or another client can settle a request without
            # passing through our response route. Reconnect must not revive it.
            resolved_request_id = str(data.get("request_id", ""))
            if resolved_request_id:
                self._clear_pending_permission_request(resolved_request_id)
                self._unrestored_permissions.pop(resolved_request_id, None)
                self._save_control_state()
                await self._exit_attention(resolved_request_id)

        if event_type == "ask_user_question":
            # The agent is blocked on a human answer. Flip the session to
            # awaiting_input so the platform (and any push fan-out) knows it
            # needs the user — Skuld otherwise stays pinned at active here.
            ask_request_id = str(data.get("request_id", ""))
            # Track for reconnect replay: a client that joins WHILE the agent is
            # blocked must still receive the answerable question (tmux-reconnect fix).
            if ask_request_id:
                self._pending_ask_user_questions[ask_request_id] = dict(data)
                self._unrestored_questions.pop(ask_request_id, None)
                # A native adapter may reissue the same question with a fresh
                # RPC ID. Retire only matching historical text, never another ask.
                for old_id, old in list(self._unrestored_questions.items()):
                    if old.get("questions") == data.get("questions"):
                        self._unrestored_questions.pop(old_id, None)
                self._save_control_state()
            # Mark attention SYNCHRONOUSLY (before scheduling the report task) so
            # the assistant tool_use frame that the tmux bridge emits right after
            # this question cannot schedule an "active" report that clobbers the
            # awaiting_input below — the assistant branch checks _pending_attention.
            if ask_request_id:
                self._pending_attention[ask_request_id] = "question"
            asyncio.create_task(
                self._enter_attention(
                    ask_request_id,
                    "question",
                    prompt=self._attention_prompt_from_questions(data),
                    options=data.get("questions"),
                )
            )

        if event_type == "ask_user_resolved":
            # (`event_type` is the frame's `type` field; the TTY bridge emits
            # type="ask_user_resolved" / event_type="ask_user.resolved".)
            # The question was answered out-of-band (an in-terminal keystroke via the
            # tmux TTY bridge, or the turn ended) — clear server-side attention + the
            # replay entry so the session leaves awaiting_input and a reconnecting
            # client no longer sees a stale question card. Without this the broker's
            # _pending_attention stayed set until the next `result`, pinning the
            # session at awaiting_input.
            resolved_request_id = str(data.get("request_id", ""))
            if resolved_request_id:
                self._pending_ask_user_questions.pop(resolved_request_id, None)
                self._unrestored_questions.pop(resolved_request_id, None)
                self._save_control_state()
                asyncio.create_task(self._exit_attention(resolved_request_id))

        # Plan + running-agents surfacing: keep the latest plan and the live agent
        # set so reconnects + GET /api/plan|agents can answer. The frames are still
        # broadcast normally below for live consumers.
        if event_type == "plan":
            self._current_plan = dict(data)
        elif event_type == "user_confirmed":
            # A transport-originated user_confirmed (the browser path stamps its own turn
            # directly and broadcasts via _emit_broker_frame, not here). Derive the turn's
            # steering_state from the SAME shared policy the log rebuild uses, so the live
            # in-memory turn == the rebuilt turn (INV-4). No-op if the turn isn't ours yet.
            self._stamp_steering_state_from_frame(data)
        elif event_type == "agent_update":
            self._track_agent_update(data)
        elif is_idle_prompt_hook(data):
            # Claude is waiting at its prompt: a stale busy state self-heals (F12b).
            asyncio.create_task(self._heal_on_idle_prompt())
        elif event_type == "terminal_pane_opened":
            self._track_pane_agent(data, opened=True)
        elif event_type == "terminal_pane_closed":
            self._track_pane_agent(data, opened=False)
        elif event_type in ("terminal_prompt_submitted", "user_consumed"):
            # The agent consumed a (correlated) user prompt — flip that steering message from
            # "pending" to "active" and tell live clients via user_active. tmux emits
            # terminal_prompt_submitted (UserPromptSubmit hook); Codex emits user_consumed
            # (turn/started). Both carry the msg_id; _activate_user_turn no-ops if it's absent.
            # Activation emits user_active. Broadcast the consumed frame first
            # below so that live order matches the durable capture order.
            # TURN START — converge clients to "active" immediately, not only when
            # the first assistant token arrives (which can be seconds later). Skip
            # while blocked on a human gate: the answer that unblocks the turn also
            # arrives as a prompt-submit, and we must not clobber awaiting_input
            # before _exit_attention runs.
            # A turn-boundary flush (G10, F12a) is NOT a turn start: the transport
            # emits it after the turn ended, only so a silently absorbed steer's
            # bubble flips. _activate_user_turn below still flips it.
            if not self._pending_attention and not is_turn_boundary_flush(data):
                asyncio.create_task(self._report_activity_state("active"))
        elif event_type in ("result", "error") or (
            event_type == "assistant" and self._assistant_has_content(data)
        ):
            # Transport-agnostic FLOOR so no steer is ever stranded "pending" on a transport we
            # don't precisely wire (grok/sdk/subprocess/opencode) or a Codex edge the FIFO missed:
            # a NON-NATIVE transport that produced assistant content, finished a turn, or terminally
            # errored has either consumed any still-pending steer or can no longer flip it — either
            # way "pending forever" is the wrong state. tmux (native) is EXCLUDED — it owns the
            # precise UserPromptSubmit signal AND streams assistant content for the IN-PROGRESS turn
            # while a mid-turn steer is still queued, so this floor would otherwise flip it early.
            caps = getattr(self._transport, "capabilities", None)
            # Codex's live steering also emits exact user_consumed identities.
            # An old turn's output cannot prove a queued/uncertain send arrived.
            if getattr(caps, "steering_mode", "none") not in {"native", "live"}:
                await self._activate_pending_user_turns_backstop()

        tool_result_only_user_event = event_type == "user" and self._is_tool_result_only_user_event(
            data
        )
        # suppress_channel_broadcast was computed at the top (it also gates the
        # durable enqueue so a never-broadcast room echo is never logged — INV-5).
        num_channels = self._channels.count
        logger.debug(
            "_handle_cli_event: forwarding to %d channel(s) suppress=%s",
            num_channels,
            suppress_channel_broadcast,
        )

        if num_channels == 0:
            logger.warning("_handle_cli_event: no channels to forward event")

        if not suppress_channel_broadcast:
            await self._channels.broadcast(data)
        if event_type in ("terminal_prompt_submitted", "user_consumed"):
            await self._activate_user_turn(data)

        # Record user messages that arrive via the transport (e.g. the
        # initial prompt flushed as a pending message) into conversation
        # history so late-joining browsers see them.
        if event_type == "user" and not tool_result_only_user_event:
            # Timed input may belong to the still-open native turn. It is a
            # presentation boundary, not evidence the assistant/tools finished.
            if timeline(data) is None:
                self._flush_pending_assistant_turn()
                await self._finish_pending_assistant_tool_trace_spans(
                    status="completed",
                    attributes={"reason": "new_user_event"},
                )
                if self._trace_assistant_span_id is not None:
                    await self._finish_trace_span(
                        self._trace_assistant_span_id,
                        status="completed",
                        attributes={"reason": "new_user_event"},
                    )
                    self._trace_assistant_span_id = None
            user_content = ""
            msg = data.get("message", {})
            if isinstance(msg, dict):
                user_content = msg.get("content", "")
            if isinstance(user_content, str) and user_content:
                if self._pending_explicit_human_messages:
                    raw_pending, outbound_pending = self._pending_explicit_human_messages[0]
                    if user_content in {raw_pending, outbound_pending}:
                        self._pending_explicit_human_messages.pop(0)
                        user_content = ""
                if user_content:
                    # Match the SHARED reducer's user-turn id policy: the frame's carried
                    # uuid when present (so live == rebuild), else the deterministic seq id.
                    carried_uid = str(data.get("uuid") or "").strip()
                    user_turn_id = carried_uid or assistant_turn_id(
                        self.session_id, self._event_log_seq, "user"
                    )
                    self._append_turn(
                        ConversationTurn(
                            id=user_turn_id,
                            role="user",
                            content=user_content,
                            created_at=frame_ts.isoformat(),
                            metadata=(
                                {TIMELINE_KEY: dict(timeline(data))} if timeline(data) else {}
                            ),
                        )
                    )

        # When CLI sends system/init, broadcast available commands to browsers.
        # Alongside the bare name lists, include the rich catalog (names +
        # descriptions + argument hints + source) so clients can render a
        # proper `/` autocomplete menu without a follow-up fetch.
        if event_type == "system" and data.get("subtype") == "init":
            # The CLI's system/init frame means the REPL is up and ready — leave
            # ``provisioning`` for ``idle`` (the next real frame refines it). Only
            # transition from provisioning so an init that arrives mid-turn
            # (reconnect / re-init) never clobbers an active/awaiting state.
            if self._activity_state == "provisioning":
                asyncio.create_task(self._report_activity_state("idle"))
            slash_commands = data.get("slash_commands", [])
            skills = data.get("skills", [])
            commands: list[dict] = []
            transport = self._transport
            if transport is not None and transport.capabilities.slash_commands:
                try:
                    # Do NOT force a re-scrape at init: refresh=False returns the cached
                    # catalog when one exists (reconnect / re-init) and only probes the
                    # terminal on a truly fresh session — and even then the probe now
                    # waits for the REPL prompt before typing, so it can't corrupt boot.
                    commands = await self.discover_slash_commands(refresh=False)
                except Exception:
                    logger.debug("slash-command discovery failed at init", exc_info=True)
            if slash_commands or skills or commands:
                await self._emit_broker_frame(
                    {
                        "type": "available_commands",
                        "slash_commands": slash_commands,
                        "skills": skills,
                        "commands": commands,
                    }
                )

        # Report activity state transitions to Volundr
        if event_type == "assistant":
            if self._trace_assistant_span_id is None:
                assistant_label = (
                    self._settings.mesh.persona
                    if getattr(self._settings, "mesh", None) is not None
                    else None
                ) or self._settings.session.name
                self._trace_assistant_span_id = await self._start_trace_span(
                    kind="turn.assistant",
                    name="assistant turn",
                    parent_span_id=self._trace_session_span_id,
                    actor_type="assistant",
                    actor_id=self._observer_peer_id() or self.session_id,
                    actor_label=assistant_label,
                    attributes={"model": self.model},
                )
            await self._start_assistant_tool_trace_spans(data)
            # Don't report "active" while a human gate is pending: the tmux bridge
            # emits the AskUserQuestion tool_use as an assistant frame right after
            # surfacing the question, and an "active" report here would clobber the
            # awaiting_input the session must hold while blocked on the human.
            if not self._pending_attention:
                asyncio.create_task(self._report_activity_state("active"))
            # Emit room activity for CLI participant so room UI shows "thinking"
            if self._room_bridge is not None and self._mesh_adapter is not None:
                await self._room_bridge.broadcast_cli_activity(
                    self._mesh_adapter.peer_id, "thinking"
                )
        elif tool_result_only_user_event:
            await self._finish_assistant_tool_trace_spans_from_user_event(data)
            # Persist tool_result blocks onto the open assistant turn so the saved
            # conversation carries tool OUTPUT (not just the call) for read-back — via the
            # SHARED reducer transition (the same enrichment a later log rebuild applies).
            tr_msg = data.get("message", {})
            tr_blocks = tr_msg.get("content", []) if isinstance(tr_msg, dict) else []
            # D1: ``frame_ts`` (== this frame's durable row ts) closes each tool — it stamps
            # the tool_result part AND back-fills ended_at/duration_ms onto the open tool_use
            # part. The accumulator shares the pending parts LIST object, and the back-fill
            # mutates those part dicts in place, so both the in-progress serialization and the
            # eventual flush carry the completed timing.
            acc = self._pending_accumulator()
            part_start = len(acc.parts)
            apply_tool_result_blocks(acc, tr_blocks, ts=frame_ts)
            stamp_parts(self._pending_assistant_parts, data, start=part_start)
            self._pending_assistant_last_seq = self._event_log_seq
            self._pending_assistant_last_ts = frame_ts
        elif event_type == "result":
            # A turn that reaches result is no longer blocked on the user; drop
            # any stale pending gates so a later heartbeat can't resurrect
            # awaiting_input.
            self._pending_attention.clear()
            self._pending_ask_user_questions.clear()
            for permission_id in list(self._pending_permission_requests):
                self._clear_pending_permission_request(permission_id)
            self._unrestored_questions.clear()
            self._unrestored_permissions.clear()
            self._save_control_state()
            asyncio.create_task(self._report_activity_state("idle"))
            if self._pending_explicit_human_response_count == 0:
                asyncio.create_task(self._on_result_publish_mesh())

        # Track conversation from assistant messages.
        # The SDK WebSocket protocol sends complete messages as type=assistant
        # with content blocks already resolved (not as streaming deltas).
        # We also handle the HTTP streaming format (content_block_delta, result)
        # for backward compatibility.
        if event_type == "assistant":
            # ACCUMULATE this assistant frame's blocks (text / thinking / tool_use) across the
            # messages of one turn via the SHARED reducer transition — the SAME fold a later log
            # rebuild applies — so the saved turn carries tool calls + reasoning and reloads
            # byte-identically. Parts are reset on flush, not per message. The accumulator shares
            # the parts list object, so block appends land directly on the pending state; only
            # ``content`` (a str) is written back.
            message = data.get("message", {})
            content_blocks = message.get("content", [])
            acc = self._pending_accumulator()
            # D1: ``frame_ts`` (== this frame's durable row ts) is the tool_use start stamp.
            part_start = len(acc.parts)
            apply_assistant_blocks(acc, content_blocks, ts=frame_ts)
            stamp_parts(acc.parts, data, start=part_start)
            self._pending_assistant_content = acc.content
            self._pending_assistant_last_seq = self._event_log_seq
            self._pending_assistant_last_ts = frame_ts

        # Reserve/close text anchors as part of the same shared fold as raw replay.
        if event_type in ("content_block_start", "content_block_stop"):
            acc = self._pending_accumulator()
            part_start = len(acc.parts)
            if event_type == "content_block_start":
                apply_text_start(acc, data)
            else:
                apply_text_stop(acc, data)
            stamp_parts(acc.parts, data, start=part_start)
            self._pending_assistant_content = acc.content
            self._pending_assistant_last_seq = self._event_log_seq
            self._pending_assistant_last_ts = frame_ts

        # HTTP streaming format: accumulate deltas
        if event_type == "content_block_delta":
            if not self._pending_attention:
                asyncio.create_task(self._report_activity_state("active"))
            delta = data.get("delta", {})
            delta_type = delta.get("type", "")
            # SHARED reducer delta transitions (same fold a later log rebuild applies).
            acc = self._pending_accumulator()
            part_start = len(acc.parts)
            if delta_type == "thinking_delta":
                apply_thinking_delta(acc, delta.get("thinking", ""))
                self._pending_reasoning_text = acc.reasoning
            else:
                apply_text_delta(acc, delta.get("text", ""), frame=data)
                self._pending_assistant_content = acc.content
            stamp_parts(acc.parts, data, start=part_start)
            self._pending_assistant_last_seq = self._event_log_seq
            self._pending_assistant_last_ts = frame_ts

        # Accumulate artifacts from assistant tool_use events
        if event_type == "assistant":
            tool_events = self._artifacts.record_tool_use(data)
            if tool_events and not self._pending_attention:
                asyncio.create_task(self._report_activity_state("tool_executing"))
            # Enrich tool events with tool_result data (exit codes, git info)
            self._artifacts.enrich_from_tool_result(data, tool_events)
            for tool_ev in tool_events:
                # Clean up internal fields before reporting
                tool_ev.pop("_pending_git", None)
                tool_ev["t"] = self._artifacts.duration_seconds
                self._artifacts.observe_tool_event(tool_ev)
                asyncio.create_task(self._report_timeline_event(tool_ev))

                # Emit to event pipeline
                pipeline_type = self._classify_pipeline_event(tool_ev)
                asyncio.create_task(
                    self._emit_pipeline_event(
                        pipeline_type,
                        tool_ev,
                    )
                )

        # Capture error events
        if event_type == "error":
            error_msg = (
                data.get("error", {}).get("message", "")
                if isinstance(data.get("error"), dict)
                else data.get("content", str(data.get("error", "Unknown error")))
            )
            visible_error = str(error_msg) or "Unknown error"
            if self._pending_assistant_content or self._pending_assistant_parts:
                self._flush_pending_assistant_turn(
                    metadata={"status": "error", "messageType": "error"}
                )
            # Deterministic id (uuid5(session:error_frame_seq:assistant)) so the live error
            # turn id matches the id a log rebuild assigns to the same error frame (INV-4).
            self._append_turn(
                ConversationTurn(
                    id=assistant_turn_id(self.session_id, self._event_log_seq),
                    role="assistant",
                    content=visible_error,
                    parts=[{"type": "text", "text": visible_error}],
                    metadata={"status": "error", "messageType": "error"},
                )
            )
            asyncio.create_task(
                self._report_activity_state(
                    "error",
                    extra_metadata={"error": visible_error},
                )
            )
            asyncio.create_task(
                self._report_timeline_event(
                    {
                        "t": self._artifacts.duration_seconds,
                        "type": "error",
                        "label": visible_error[:120],
                    }
                )
            )

        # Report usage on result events and track turn count
        if event_type == "result":
            self._artifacts.record_result()
            asyncio.create_task(self._report_usage(data))
            explicit_room_reply = self._pending_explicit_human_response_count > 0

            # Inject the result frame's text into the OPEN turn via the SHARED reducer policy —
            # the SAME fold a later log rebuild applies — so a tool_use-only turn closed by a
            # result with text surfaces that text identically on live and rebuild (INV-4/FR-3).
            acc = self._pending_accumulator()
            part_start = len(acc.parts)
            apply_result_content(acc, data)
            stamp_parts(acc.parts, data, start=part_start)
            self._pending_assistant_content = acc.content
            # Build metadata from the result frame via the SHARED reducer so the live turn's
            # {usage,cost,model,stop_reason} schema is byte-identical to a later log rebuild.
            turn_metadata = result_metadata(data)
            # The result frame closes the turn — stamp its seq so the deterministic turn id
            # (uuid5(session:seq:role)) matches the id a log rebuild assigns to this turn.
            self._pending_assistant_last_seq = self._event_log_seq
            self._pending_assistant_last_ts = frame_ts

            # Capture content before flush clears it
            content = self._pending_assistant_content or data.get("result", "")
            if explicit_room_reply:
                self._pending_assistant_content = ""
                self._pending_assistant_parts = []
                self._pending_reasoning_text = ""
            else:
                self._flush_pending_assistant_turn(metadata=turn_metadata)
            self._live_tool_details.clear()

            # Emit CLI turn as room_message so it shows participant color
            if self._room_bridge is not None and self._mesh_adapter is not None and content:
                await self._room_bridge.broadcast_cli_message(self._mesh_adapter.peer_id, content)
                await self._room_bridge.broadcast_cli_activity(self._mesh_adapter.peer_id, "idle")
            if explicit_room_reply and self._pending_explicit_human_response_count > 0:
                self._pending_explicit_human_response_count -= 1

            first_line = ""
            if content:
                for line in content.strip().splitlines():
                    stripped = line.strip()
                    if stripped:
                        first_line = stripped[:80]
                        break
            message_label = first_line or f"Turn {self._artifacts.turn_count}"

            # Report message timeline event with total tokens
            model_usage = data.get("modelUsage", {})
            total_tokens = 0
            total_input = 0
            total_output = 0
            result_model = None
            result_cost = None
            for model_id, usage in model_usage.items():
                result_model = model_id
                inp = (
                    usage.get("inputTokens", 0)
                    + usage.get("cacheReadInputTokens", 0)
                    + usage.get("cacheCreationInputTokens", 0)
                )
                out = usage.get("outputTokens", 0)
                total_input += inp
                total_output += out
                total_tokens += inp + out
                if usage.get("costUSD") is not None:
                    result_cost = (result_cost or 0) + usage["costUSD"]

            if total_tokens > 0:
                self._artifacts.total_tokens += total_tokens
                asyncio.create_task(
                    self._report_timeline_event(
                        {
                            "t": self._artifacts.duration_seconds,
                            "type": "message",
                            "label": message_label,
                            "tokens": total_tokens,
                        }
                    )
                )

                # Emit message_assistant to event pipeline
                asyncio.create_task(
                    self._emit_pipeline_event(
                        "message_assistant",
                        {
                            "content_length": len(content),
                            "content_preview": content[:200],
                            "finish_reason": data.get("stop_reason", "end_turn"),
                            "turn": self._artifacts.turn_count,
                        },
                        tokens_in=total_input,
                        tokens_out=total_output,
                        cost=result_cost,
                        model=result_model,
                    )
                )

                # Emit token_usage to event pipeline
                asyncio.create_task(
                    self._emit_pipeline_event(
                        "token_usage",
                        {
                            "provider": "cloud",
                            "model": result_model or self.model,
                            "tokens_in": total_input,
                            "tokens_out": total_output,
                        },
                        tokens_in=total_input,
                        tokens_out=total_output,
                        cost=result_cost,
                        model=result_model or self.model,
                    )
                )
            if self._trace_assistant_span_id is not None:
                await self._finish_pending_assistant_tool_trace_spans(
                    status="completed",
                    attributes={"reason": "assistant_turn_completed"},
                )
                await self._finish_trace_span(
                    self._trace_assistant_span_id,
                    status="completed",
                    attributes={
                        "finish_reason": data.get("stop_reason", "end_turn"),
                        "tokens_in": total_input,
                        "tokens_out": total_output,
                        "model": result_model or self.model,
                    },
                )
                self._trace_assistant_span_id = None

    # Maps control message types to the TransportCapabilities field that
    # must be True for the control to be forwarded.
    _CONTROL_CAPABILITY_MAP: dict[str, str] = {
        "interrupt": "interrupt",
        "steer_active_turn": "steer",
        "set_model": "set_model",
        "get_runtime_options": "runtime_options",
        "set_runtime_options": "runtime_options",
        "set_max_thinking_tokens": "set_thinking_tokens",
        "set_permission_mode": "set_permission_mode",
        "rewind_files": "rewind_files",
        "mcp_set_servers": "mcp_set_servers",
        "terminal_input": "terminal_input",
        "terminal_key": "terminal_keys",
        "terminal_resize": "terminal_resize",
        "slash_command": "slash_commands",
        "discover_slash_commands": "slash_commands",
    }

    def _room_role_for(self, sender_ws: WebSocket | None) -> str:
        """Return the verified room role for the channel behind *sender_ws*.

        ``sender_ws is None`` means this dispatch did not originate from a
        live browser connection (an internally-synthesized call, e.g. replay
        or resume) — those call sites already established their own
        authority before reaching here, so they are trusted as "owner".  A
        live connection whose channel cannot be found (torn down mid-dispatch,
        or never registered — protocol2 registers it only after synchronous
        history capture) gets the least-privilege default, "viewer": a
        missing channel must never read as elevated privilege.
        """
        if sender_ws is None:
            return "owner"
        for ch in self._channels.channels:
            if isinstance(ch, WebSocketChannel) and ch.ws is sender_ws:
                return ch.room_role
        return "viewer"

    def _reply_context_consumption_allowed(self, target_peer_id: str, actual_role: str) -> bool:
        """Return whether *actual_role* may deliver into *target_peer_id*'s reply wait.

        A directed message (explicit ``directed_message``, default chat routed
        to the room's default target, or the equivalent HTTP room/direct call)
        to a peer with a pending ``reply_context``
        (niuu.collaboration.room.set_reply_context, set when a Ravn case
        suspends waiting for an operator) DELIVERS that reply and consumes the
        context — functionally the same act as answering
        ``ask_user_answer``/``permission_response``, which is already
        approver-only. Ordinary chat to a peer with NO pending reply is
        unaffected; this only blocks the specific peer/moment a case is
        actually waiting. Shared by both the WS dispatch and the
        ``/api/room/direct`` HTTP route so a viewer cannot reach the same
        privileged act by calling the HTTP surface directly.
        """
        if self._room_bridge is None:
            return True
        if target_peer_id not in self._room_bridge.pending_reply_peer_ids():
            return True
        return _ROOM_ROLE_RANK[actual_role] >= _ROOM_ROLE_RANK["approver"]

    async def _guard_reply_context_consumption(
        self,
        target_peer_id: str,
        actual_role: str,
        data: dict,
        sender_ws: WebSocket | None,
    ) -> bool:
        """WS wrapper: deny and notify the sender when the predicate above fails.

        Returns True when the send may proceed.
        """
        if self._reply_context_consumption_allowed(target_peer_id, actual_role):
            return True
        logger.warning(
            "_dispatch_browser_message: denied reply-context consumption for peer=%s role=%s",
            _sanitize_log(target_peer_id),
            actual_role,
        )
        if sender_ws:
            await self._send_broker_frame_to(
                sender_ws,
                control_error_frame(
                    "This participant is awaiting an operator reply; only the "
                    "session owner or an approver may answer it",
                    data,
                    code="forbidden",
                ),
            )
        return False

    @history_write
    async def _dispatch_browser_message(
        self,
        data: dict,
        sender_ws: WebSocket | None = None,
    ) -> None:
        """Route a browser WebSocket message to the appropriate handler."""
        if not isinstance(data, dict):
            raise ValueError("A browser message must be a JSON object")
        if "type" in data and not isinstance(data["type"], str):
            raise ValueError("A browser message type must be a string")
        if not self._transport:
            raise RuntimeError("The session transport is not ready")

        msg_type = data.get("type")
        logger.info(
            "_dispatch_browser_message: type=%s, transport_alive=%s",
            _sanitize_log(msg_type),
            self._transport.is_alive,
        )

        required_role = _message_role_requirement(msg_type)
        actual_role = self._room_role_for(sender_ws)
        if _ROOM_ROLE_RANK[actual_role] < _ROOM_ROLE_RANK[required_role]:
            error_msg = f"{msg_type or 'this message'} requires the {required_role} room role"
            logger.warning(
                "_dispatch_browser_message: denied type=%s role=%s requires=%s",
                _sanitize_log(msg_type),
                actual_role,
                required_role,
            )
            if sender_ws:
                await self._send_broker_frame_to(
                    sender_ws, control_error_frame(error_msg, data, code="forbidden")
                )
            return

        # Guard: reject control messages the transport does not support.
        cap_field = self._CONTROL_CAPABILITY_MAP.get(msg_type or "")
        forge_command = msg_type == "discover_slash_commands" or (
            msg_type == "slash_command"
            and str(data.get("command") or "").strip().lstrip("/").split(maxsplit=1)[:1]
            == ["effort"]
        )
        if cap_field and not forge_command and not getattr(self._transport.capabilities, cap_field):
            error_msg = f"{msg_type} not supported by this transport"
            logger.warning("_dispatch_browser_message: %s", _sanitize_log(error_msg))
            if sender_ws:
                await self._send_broker_frame_to(
                    sender_ws, control_error_frame(error_msg, data, code="unsupported_control")
                )
            return

        match msg_type:
            # Phase 2: permission response from browser
            case "permission_response":
                request_id = data.get("request_id", "")
                if request_id in self._unrestored_permissions:
                    raise ControlRecoveryError("This approval belongs to a previous native process")
                behavior = data.get("behavior", "deny")
                # An approver (below owner) may allow/deny the pending tool
                # call, but must not also rewrite it: updated_input can
                # change WHAT command runs, and updated_permissions can grant
                # a broader mode (e.g. bypassPermissions) than this one
                # decision implies. Only the owner may set either.
                response = {
                    "behavior": behavior,
                    "updatedInput": data.get("updated_input", {}) if actual_role == "owner" else {},
                }
                if actual_role == "owner" and data.get("updated_permissions"):
                    response["updatedPermissions"] = data["updated_permissions"]
                if "content" in data:
                    # Opaque structured content for native form elicitations.
                    response["content"] = data["content"]
                await self._send_permission_control_response(
                    str(request_id),
                    response,
                    auto_approved=False,
                )

            # AskUserQuestion: a human answered a question the agent asked.
            # Resolves the blocking can_use_tool future in SDKTransport.
            case "ask_user_answer":
                if data.get("request_id") in self._unrestored_questions:
                    raise ControlRecoveryError(
                        "This question survived in history, but its native control was not "
                        "restored. Wait for the agent to reissue it or inspect the native session."
                    )
                await self._transport.send_control(
                    "ask_user_answer",
                    request_id=data.get("request_id", ""),
                    answers=data.get("answers", []),
                )
                # The human answered — the session is no longer blocked. Drop the
                # replay entry too so a later reconnect doesn't re-surface an
                # already-answered question.
                answered_request_id = str(data.get("request_id", ""))
                self._pending_ask_user_questions.pop(answered_request_id, None)
                self._save_control_state()
                await self._exit_attention(answered_request_id)

            # Phase 3: interrupt current turn
            case "get_effort" | "set_effort":
                await self.handle_effort(
                    str(data.get("effort") or "") if msg_type == "set_effort" else "",
                    request_id=self._extract_request_id(data),
                )

            case "get_runtime_options" | "set_runtime_options":
                await self.handle_runtime_options(
                    data.get("options", {}) if msg_type == "set_runtime_options" else None,
                    refresh=bool(data.get("refresh", False)),
                    request_id=self._extract_request_id(data),
                )

            case "interrupt":
                await self._transport.send_control("interrupt")

            # Phase 3: steer the current turn with additional user guidance
            case "steer_active_turn":
                content = str(data.get("content") or "").strip()
                if content:
                    await self._transport.send_control("steer", content=content)

            # Phase 3: change model mid-session
            case "set_model":
                model = data.get("model", "")
                if model:
                    if self._transport.capabilities.runtime_options:
                        await self.handle_runtime_options(
                            {"model": model}, request_id=self._extract_request_id(data)
                        )
                    else:
                        await self._transport.send_control("set_model", model=model)

            # Phase 3: change thinking budget
            case "set_max_thinking_tokens":
                tokens = data.get("max_thinking_tokens", 0)
                await self._transport.send_control(
                    "set_max_thinking_tokens",
                    max_thinking_tokens=tokens,
                )

            # Per-channel filter: hide/show tool_use + tool_result blocks
            case "set_internal_visibility":
                visible = bool(data.get("visible", False))
                if sender_ws is not None:
                    for ch in self._channels.channels:
                        if (
                            isinstance(ch, WebSocketChannel)
                            and getattr(ch, "ws", None) is sender_ws
                        ):
                            ch.set_show_internal(visible)
                            break

            # Phase 3: change permission mode at runtime
            case "set_permission_mode":
                mode = data.get("mode", "")
                if mode:
                    await self._transport.send_control(
                        "set_permission_mode",
                        permissionMode=mode,
                    )

            # Phase 3: rewind file changes to checkpoint
            case "rewind_files":
                await self._transport.send_control("rewind_files")

            # Phase 3: inject or reconfigure MCP servers
            case "mcp_set_servers":
                servers = data.get("servers", [])
                await self._transport.send_control(
                    "mcp_set_servers",
                    servers=servers,
                )

            # Interactive terminal transports: raw input, key presses, and
            # resize events. These are intentionally transport controls, not
            # chat turns, so workflows can drive slash menus/history/editing.
            case "terminal_input":
                await self._transport.send_control(
                    "terminal_input",
                    data=data.get("data", data.get("text", "")),
                    enter=bool(data.get("enter", False)),
                    pane_id=data.get("pane_id", ""),
                )

            case "terminal_key":
                await self._transport.send_control(
                    "terminal_key",
                    key=data.get("key", ""),
                    keys=data.get("keys", []),
                    pane_id=data.get("pane_id", ""),
                )

            case "terminal_resize":
                await self._transport.send_control(
                    "terminal_resize",
                    cols=data.get("cols", data.get("columns", 0)),
                    rows=data.get("rows", 0),
                    pane_id=data.get("pane_id", ""),
                )

            case "slash_command":
                command = str(data.get("command") or "").strip().lstrip("/")
                if command.split(maxsplit=1)[:1] == ["effort"]:
                    argument = effort_argument(
                        "/" + command + " " + str(data.get("arguments") or data.get("args") or "")
                    )
                    await self.handle_effort(
                        argument or "", request_id=self._extract_request_id(data)
                    )
                    return
                await self._transport.send_control(
                    "slash_command",
                    command=data.get("command", ""),
                    arguments=data.get("arguments", data.get("args", "")),
                    pane_id=data.get("pane_id", ""),
                )

            case "discover_slash_commands":
                commands = await self.discover_slash_commands(
                    refresh=bool(data.get("refresh", True))
                )
                if sender_ws is not None:
                    await self._send_broker_frame_to(
                        sender_ws,
                        {
                            "type": "slash_commands",
                            "commands": commands,
                            "count": len(commands),
                        },
                    )

            # Another agent's message, from Forge (a session message, or a room's relay to a
            # member session). Answered with user_delivered / user_delivery_failed for the
            # request_id once the message is durably in the inbox.
            case "peer_message":
                await self._accept_peer_message_frame(data)

            # Room: forward a directed message to a specific Ravn participant
            case "directed_message":
                if self._room_bridge is None:
                    logger.warning("directed_message received but room mode is disabled")
                    if sender_ws:
                        await self._send_broker_frame_to(
                            sender_ws,
                            control_error_frame("Room mode is not enabled", data),
                        )
                    return
                target = data.get("targetPeerId", "")
                content = data.get("content", "")
                if not target or not content:
                    return
                if not await self._guard_reply_context_consumption(
                    str(target), actual_role, data, sender_ws
                ):
                    return
                try:
                    await self.handle_directed_room_message(
                        str(target),
                        str(content),
                        source="browser",
                        request_id=self._extract_request_id(data),
                        metadata=_sanitize_directed_metadata(
                            actual_role,
                            data.get("metadata")
                            if isinstance(data.get("metadata"), dict)
                            else None,
                        ),
                    )
                except LookupError as exc:
                    if sender_ws:
                        await self._send_broker_frame_to(sender_ws, control_error_frame(exc, data))

            case "resend_initial_prompt":
                try:
                    message_id = await self.handle_resend_initial_prompt(
                        source="browser",
                        metadata=(
                            data.get("metadata") if isinstance(data.get("metadata"), dict) else None
                        ),
                    )
                except (ValueError, RuntimeError) as exc:
                    if sender_ws:
                        await self._send_broker_frame_to(sender_ws, control_error_frame(exc, data))
                    return
                if sender_ws:
                    await self._send_broker_frame_to(
                        sender_ws,
                        {"type": "room_prompt_resent", "message_id": message_id},
                    )

            case "publish_event":
                try:
                    event_id = await self.handle_publish_mesh_event(
                        str(data.get("eventType") or data.get("event_type") or ""),
                        str(data.get("content") or data.get("prompt") or ""),
                        source="browser",
                        payload=data.get("payload")
                        if isinstance(data.get("payload"), dict)
                        else None,
                        request_id=self._extract_request_id(data),
                    )
                except (ValueError, RuntimeError) as exc:
                    if sender_ws:
                        await self._send_broker_frame_to(sender_ws, control_error_frame(exc, data))
                    return
                if sender_ws:
                    await self._send_broker_frame_to(
                        sender_ws,
                        {
                            "type": "mesh_event_published",
                            "event_id": event_id,
                            "event_type": str(
                                data.get("eventType") or data.get("event_type") or ""
                            ),
                        },
                    )

            # Default: treat as user message (backward compat with {"content": "..."})
            case _:
                message = data.get("content", "")
                if not message:
                    return
                content_str = _normalize_browser_message_content(message)
                if not content_str:
                    return

                # Telegram carries peer targeting, reply correlation, and W3C
                # trace context outside the browser's nested metadata object.
                # Give the channel adapter first refusal before the resident's
                # default browser route so that context is not discarded.
                # sender_ws is None ONLY for the real Telegram channel's own
                # on_message callback (see chronicle.py's TelegramChannel
                # wiring) — a live browser connection claiming
                # {"source": "telegram"} must never reach this "answer a
                # pending operator wait" shortcut just by asserting it.
                if sender_ws is None:
                    if await self._try_route_pending_help_reply(data, content_str):
                        return
                    if await self._try_route_single_room_peer_message(data, content_str):
                        return

                # Resident sessions: untargeted messages route to the
                # configured default participant as directed messages. Normalize
                # structured content blocks (text + attachments) the same way
                # the CLI transport path does — a bare str() would deliver a
                # Python repr of the block list (base64 blobs inline).
                default_target = self._room_default_target_peer_id()
                if not default_target and self._is_declared_room_routed():
                    # A declared-routed broker has no CLI agent to fall through
                    # to, so an unresolvable target must be reported, never
                    # dropped into a transport that does not exist.
                    default_target = self._sole_room_ravn_peer_id()
                    if not default_target:
                        error_msg = (
                            "No room participant to deliver this message to. Set "
                            "room.default_target_peer_id, or address a participant "
                            "explicitly with a directed_message."
                        )
                        logger.info("_dispatch_browser_message: %s", error_msg)
                        if sender_ws:
                            await self._send_broker_frame_to(
                                sender_ws, {"type": "error", "content": error_msg}
                            )
                        return
                if default_target:
                    if not await self._guard_reply_context_consumption(
                        default_target, actual_role, data, sender_ws
                    ):
                        return
                    request_id = data.get("request_id")
                    request_id = request_id if isinstance(request_id, str) and request_id else None
                    # A live browser connection can never claim "telegram" —
                    # only the real Telegram channel dispatches with no
                    # sender_ws (see the sender_ws is None guard above).
                    incoming_source = str(data.get("source") or "").strip().lower()
                    is_telegram = sender_ws is None and incoming_source == "telegram"
                    source = "telegram" if is_telegram else "browser"
                    if source == "telegram":
                        metadata = _telegram_directed_metadata(data)
                    else:
                        metadata = _sanitize_directed_metadata(
                            actual_role,
                            data.get("metadata")
                            if isinstance(data.get("metadata"), dict)
                            else None,
                        )
                    try:
                        await self.handle_directed_room_message(
                            default_target,
                            content_str,
                            source=source,
                            request_id=request_id,
                            metadata=metadata,
                        )
                    except LookupError as exc:
                        if sender_ws:
                            await self._send_broker_frame_to(
                                sender_ws, {"type": "error", "content": str(exc)}
                            )
                    return

                # Record user turn in conversation history
                msg_id = str(uuid.uuid4())
                request_id = data.get("request_id")
                request_id = request_id if isinstance(request_id, str) and request_id else None
                if request_id and self.volundr_api_url:
                    msg_id = str(
                        uuid.uuid5(uuid.NAMESPACE_URL, f"{self.session_id}:message:{request_id}")
                    )
                    token = str(uuid.uuid4())
                    claim = await claim_message(
                        await self._get_http_client(),
                        session_id=self.session_id,
                        request_id=request_id,
                        content=content_str,
                        token=token,
                        timeout=self._settings.delivery.attempt_timeout_seconds,
                        attempts=self._settings.delivery.max_attempts,
                    )
                    if not claim["claimed"] or claim["status"] != "pending":
                        await self._emit_delivery_ack(
                            request_id,
                            msg_id,
                            claim["status"],
                            error=claim.get("error"),
                            settle_claim=False,
                        )
                        return
                    self._message_claim_tokens[request_id] = token
                await self._submit_user_turn(
                    content_str, msg_id=msg_id, request_id=request_id, source="browser"
                )

    async def _accept_peer_message_frame(self, data: dict) -> None:
        """Take a ``peer_message`` frame into the inbox and acknowledge it by ``request_id``."""
        request_id = data.get("request_id")
        request_id = request_id if isinstance(request_id, str) and request_id else None
        raw = data.get("envelope")
        message_id = str(raw.get("message_id") or "") if isinstance(raw, dict) else ""
        try:
            envelope = PeerMessage.from_wire(raw)
            acceptance = await self.accept_peer_message(envelope, request_id)
        except PeerMessageUnconfirmedError as exc:
            # Forge may have stored it: "pending", never "failed", so the sender is told to
            # retry with the same request_id instead of sending it again as a new message.
            await self._emit_delivery_ack(request_id, message_id, "pending", error=str(exc))
            return
        except PeerMessageError as exc:
            await self._emit_delivery_ack(request_id, message_id, "failed", error=str(exc))
            return
        await self._emit_delivery_ack(request_id, acceptance.message_id, "delivered")

    async def _submit_user_turn(
        self,
        content_str: str,
        *,
        msg_id: str,
        request_id: str | None,
        source: str,
        extra_metadata: dict[str, Any] | None = None,
    ) -> asyncio.Task[None]:
        """Record a user turn, echo it to every client and deliver it to the transport.

        The one path a human message and a workflow step's instructions both take, so a step
        is steered into the harness exactly as a person's message is: same durable turn, same
        bounded-retry delivery, same acknowledgement. Returns the delivery task, which ends
        once the outcome is decided (delivered, or the turn marked failed).
        """
        now = datetime.now(UTC)
        input_timeline = self._enqueue_human_turn_event(
            content_str, msg_id, request_id=request_id, ts=now
        )
        self._append_turn(
            ConversationTurn(
                id=msg_id,
                role="user",
                content=content_str,
                created_at=now.isoformat(),
                # The message has been accepted but not yet consumed by the
                # agent. It rides as "pending" (the client renders it greyed /
                # italic) until the correlated UserPromptSubmit flips it to
                # "active". Survives reconnect + REST since metadata is
                # serialized with the turn.
                metadata={
                    "steering_state": "pending",
                    **({"request_id": request_id} if request_id else {}),
                    **({TIMELINE_KEY: input_timeline} if input_timeline else {}),
                    **(extra_metadata or {}),
                },
            )
        )
        # Mirror the human turn into the durable event log so log-only
        # transcript replay (web/iOS) includes it.
        await self._complete_trace_span(
            kind="turn.user",
            name=content_str[:120] or "user turn",
            parent_span_id=self._trace_session_span_id,
            actor_type="user",
            actor_id=request_id or msg_id,
            actor_label="user",
            attributes={"source": source},
            started_at=now,
            ended_at=now,
        )

        # Echo back to all browsers so the message is confirmed
        # immediately (rendering as a user turn) — the actual transport
        # delivery happens asynchronously below.
        await self._emit_broker_frame(
            {
                "type": "user_confirmed",
                "id": msg_id,
                "content": content_str,
                "request_id": request_id,
                "steering_state": "pending",
                "created_at": now.isoformat(),
                **({TIMELINE_KEY: input_timeline} if input_timeline else {}),
            }
        )

        # Fire-and-forget so the WS receive loop isn't pinned on a turn that may
        # run for minutes (the transport serialises its own deliveries, so order
        # holds). BUG-3 / INV-7: the delivery is wrapped so its OUTCOME is durable
        # and observable — bounded retry on a transient failure, a VISIBLE failed
        # turn on terminal failure — instead of being silently swallowed on a
        # wedged input channel while the API says "sent". The redirect-vs-send
        # routing is recomputed AT DELIVERY TIME (not snapshotted here) so a turn
        # boundary that moves between accept and delivery routes correctly.
        return asyncio.create_task(
            self._deliver_user_message_and_ack(
                content_str,
                request_id=request_id,
                msg_id=msg_id,
            ),
            name=f"transport-deliver-{msg_id}",
        )

    async def _try_route_pending_help_reply(self, data: dict, content: str) -> bool:
        """Route a Telegram reply to the single Ravn peer waiting on help_needed."""
        if self._room_bridge is None:
            return False
        return await self._route_telegram_to_single_peer(
            data,
            content,
            candidates=self._room_bridge.pending_help_peer_ids(),
            log_message=(
                "_dispatch_browser_message: routed Telegram reply to pending help peer_id=%s"
            ),
        )

    async def _try_route_single_room_peer_message(self, data: dict, content: str) -> bool:
        """Route Telegram text to the sole connected Ravn room participant."""
        if self._room_bridge is None:
            return False
        return await self._route_telegram_to_single_peer(
            data,
            content,
            candidates=tuple(self._room_bridge.participants.keys()),
            log_message=(
                "_dispatch_browser_message: routed Telegram message to sole room peer_id=%s"
            ),
        )

    async def _route_telegram_to_single_peer(
        self,
        data: dict,
        content: str,
        *,
        candidates: tuple[str, ...],
        log_message: str,
    ) -> bool:
        """Deliver Telegram input to its ForceReply target or sole candidate."""
        if str(data.get("source") or "").strip().lower() != "telegram":
            return False
        requested_peer_id = str(data.get("target_peer_id") or "").strip()
        if requested_peer_id and requested_peer_id in candidates:
            target_peer_id = requested_peer_id
        elif len(candidates) == 1:
            target_peer_id = candidates[0]
        else:
            return False
        carrier = data.get("trace_context")
        if not isinstance(carrier, dict):
            carrier = {}
        telemetry = get_observability()
        try:
            with telemetry.span(
                "skuld.operator.reply.route",
                attributes={
                    "skuld.help.peer_id": target_peer_id,
                    "skuld.help.pending_candidates": len(candidates),
                    "skuld.operator.source": "telegram",
                },
                carrier=carrier,
            ):
                metadata = _telegram_directed_metadata(data)
                metadata["trace_context"] = telemetry.inject() or carrier
                await self.handle_directed_room_message(
                    target_peer_id,
                    content,
                    source="telegram",
                    metadata=metadata,
                )
                await self._mark_pending_help_answered(target_peer_id, content, "telegram")
                telemetry.count(
                    "skuld.operator.replies",
                    attributes={"source": "telegram", "outcome": "routed"},
                )
        except LookupError:
            return False
        logger.info(log_message, _sanitize_log(target_peer_id))
        return True

    async def _safe_transport_control(
        self,
        transport: object,
        subtype: str,
        **kwargs: object,
    ) -> None:
        """Wrap transport.send_control so background failures surface to the UI."""
        try:
            await transport.send_control(subtype, **kwargs)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.exception("Transport send_control failed in background task")
            try:
                await self._emit_broker_frame(control_error_frame(exc, {"type": subtype, **kwargs}))
            except Exception:
                logger.debug("Failed to broadcast transport control error", exc_info=True)

    def _retrieval_reflex_injector(self) -> Any:
        """Lazily build the retrieval reflex injector from reflex config (NIU-1059)."""
        if self._reflex_initialised:
            return self._reflex_injector
        self._reflex_initialised = True
        from mimir.reflex import build_reflex_injector  # noqa: PLC0415

        self._reflex_injector = build_reflex_injector(self._settings)
        return self._reflex_injector

    async def _apply_retrieval_reflex(self, message: str) -> str:
        """Prefix known Mímir entity pointers onto a forwarded user message.

        Fail-open: any reflex failure logs an ERROR and returns the message
        unchanged — the reflex must never crash or block a turn.
        """
        try:
            injector = self._retrieval_reflex_injector()
            if injector is None:
                return message
            return await injector.apply(message, self.session_id)
        except Exception:
            logger.error(
                "retrieval reflex failed for session %s — forwarding message without pointers",
                self.session_id,
                exc_info=True,
            )
            return message

    async def _safe_transport_send(self, transport: object, message: str) -> None:
        """Wrap transport.send_message so background-task failures are surfaced.

        NOTE: currently UNUSED by the live inbound steering path — that goes through
        ``_deliver_user_message_and_ack`` (which threads msg_id/request_id for the pending→active
        flip). Retained for reflex tests / future call sites; if it is ever re-wired into the
        steering path it must forward ``msg_id``/``request_id`` to keep the correlation intact.
        Any error here is logged AND broadcast to all channels so the user sees something rather
        than a silent stall.
        """
        try:
            outbound = await self._apply_retrieval_reflex(message)
            await transport.send_message(outbound)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.exception("Transport send_message failed in background task")
            try:
                await self._emit_broker_frame({"type": "error", "content": str(exc)})
            except Exception:
                logger.debug("Failed to broadcast transport error to channels", exc_info=True)

    def _resolve_delivery_routing(self) -> bool:
        """Decide redirect-vs-send for an inbound message AT DELIVERY TIME (INV-7).

        Routing used to be snapshotted on the receive loop, but delivery runs later
        in a separate task: the turn boundary can move (an idle session becomes busy,
        or a turn ends) between accept and delivery, so a stale snapshot would steer
        into a dead turn or start a spurious one. Recompute against the live transport
        state instead. ``native`` transports always use their input route (the CLI
        inserts queued input itself); other steer-capable transports only route into
        an active turn. The delivery method separately honors live versus replacement
        semantics: a live steer must never be translated into interruption.
        """
        if not self._transport:
            return False
        caps = self._transport.capabilities
        steer_capable = getattr(caps, "steer", False) is True
        if not steer_capable:
            return False
        native_steering = getattr(caps, "steering_mode", "none") == "native"
        turn_active = getattr(self._transport, "is_turn_active", False) is True
        return native_steering or turn_active

    async def _attempt_transport_delivery(
        self, content: str, *, msg_id: str, request_id: str | None
    ) -> None:
        """One delivery attempt. Re-resolves routing so a moved turn boundary routes
        correctly, then sends with a per-attempt timeout so a wedged input channel is
        bounded (and therefore retryable) instead of hanging the delivery forever."""
        timeout = self._settings.delivery.attempt_timeout_seconds
        if self._resolve_delivery_routing():
            # Live steering appends to the existing turn; redirect may cancel and
            # replace it. Honor the transport contract, not a model/harness name.
            # Preserve correlation for both app-server acceptance and native hooks.
            mode = getattr(self._transport.capabilities, "steering_mode", "none")
            control = "steer" if mode == "live" else "redirect"
            accepted = await asyncio.wait_for(
                self._transport.send_control(
                    control, content=content, msg_id=msg_id, request_id=request_id
                ),
                timeout=timeout,
            )
            if accepted is False:
                raise DeliveryNotAcceptedError(
                    "Transport rejected the message before accepting input"
                )
            return
        # Idle / non-native transports (Codex turn/start, SDK, …). Thread the ids so a
        # transport that CAN correlate a consumption signal (Codex turn/started) flips
        # the bubble; transports that can't simply ignore the kwargs.
        outbound = await self._apply_retrieval_reflex(content)
        sending = self._transport.send_message(outbound, msg_id=msg_id, request_id=request_id)
        if getattr(self._transport.capabilities, "send_runs_turn", False) is True:
            # The send returns when the whole turn is over (SDK, ``claude -p``). Cutting it at
            # the attempt timeout would cancel a healthy turn and run it again; the transport's
            # own turn timeout is what bounds it, and that one is loud.
            accepted = await sending
        else:
            accepted = await asyncio.wait_for(sending, timeout=timeout)
        if accepted is False:
            raise DeliveryNotAcceptedError("Transport rejected the message before accepting input")

    async def _deliver_user_message_and_ack(
        self,
        content: str,
        *,
        request_id: str | None,
        msg_id: str,
    ) -> None:
        """Deliver a user message to the transport with BOUNDED RETRY and a durable,
        observable outcome (SRD FR-5 / INV-7).

        The inbound steering path used to fire-and-forget the transport call and never
        confirm it reached the agent: after a WS crash/reconnect a wedged tmux input
        channel silently swallowed the message while Volundr still returned HTTP 200
        "sent", AND the user turn was left ``pending`` forever. This now:

        * recomputes redirect-vs-send routing at delivery time (no stale snapshot);
        * retries a transient send failure (wedged ``_send_lock``, transport warming)
          up to ``delivery.max_attempts`` with exponential backoff;
        * on success emits ``user_delivered`` (the pending->active flip is driven later
          by the correlated consumption signal);
        * on TERMINAL failure flips the user turn to a VISIBLE ``failed`` state
          (persisted + broadcast) and emits ``user_delivery_failed`` — never leaving the
          turn silently ``pending``.

        A message that arrives while the agent is blocked on an AskUserQuestion is still
        delivered, but flagged ``blocked_on_question`` so the client can tell the user to
        answer the open question rather than assume the steer landed.
        """
        if await self._deliver_effort_command(content, msg_id, request_id):
            return
        pending_q = len(self._pending_ask_user_questions)
        if pending_q:
            logger.warning(
                "user message delivered while %d ask_user_question(s) pending "
                "(request_id=%s) — a free-text steer may not start a turn until the "
                "question is answered",
                pending_q,
                _sanitize_log(request_id),
            )

        cfg = self._settings.delivery
        claimed_delivery = request_id in self._message_claim_tokens
        # A refusal before input crosses the provider boundary can safely retry.
        # A lost acknowledgement cannot: the provider might already be executing.
        attempts = cfg.max_attempts
        backoff = cfg.initial_backoff_seconds
        last_error: Exception | None = None
        # Mark the turn as in-flight so the non-native pending->active backstop EXCLUDES
        # it: while we are still retrying, the agent could not have consumed this steer,
        # so an unrelated transport frame must not flip it to "active". Cleared only once
        # the outcome is decided (delivered ack emitted, or the turn marked failed) so the
        # failure path stamps "failed" before the turn becomes backstop-eligible again.
        self._delivering_msg_ids.add(msg_id)
        try:
            for attempt in range(1, attempts + 1):
                try:
                    await self._attempt_transport_delivery(
                        content, msg_id=msg_id, request_id=request_id
                    )
                    last_error = None
                    break
                except Exception as exc:  # noqa: BLE001 - any send failure is retryable/terminal
                    last_error = exc
                    logger.warning(
                        "user message delivery attempt %d/%d failed (request_id=%s): %s",
                        attempt,
                        attempts,
                        _sanitize_log(request_id),
                        _sanitize_log(str(exc)),
                    )
                    if attempt >= attempts or (
                        claimed_delivery and not isinstance(exc, DeliveryNotAcceptedError)
                    ):
                        break
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * cfg.backoff_multiplier, cfg.max_backoff_seconds)

            if last_error is not None:
                if claimed_delivery and not isinstance(last_error, DeliveryNotAcceptedError):
                    await self._emit_delivery_ack(
                        request_id,
                        msg_id,
                        "pending",
                        error="Native delivery was not confirmed. Check the conversation "
                        "before continuing; the same request ID will not resend this message.",
                        settle_claim=False,
                    )
                    return
                await self._fail_user_delivery(msg_id, request_id, last_error)
                return
        finally:
            self._delivering_msg_ids.discard(msg_id)

        await self._emit_delivery_ack(
            request_id,
            msg_id,
            "blocked_on_question" if pending_q else "delivered",
            pending_questions=pending_q,
        )

    async def _fail_user_delivery(
        self, msg_id: str, request_id: str | None, error: Exception
    ) -> None:
        """Terminal delivery failure: flip the turn to a VISIBLE ``failed`` state and
        surface it. The turn is NEVER left silently ``pending`` (INV-7)."""
        logger.exception(
            "user message delivery failed after retries (request_id=%s)",
            _sanitize_log(request_id),
            exc_info=error,
        )
        if self._mark_user_turn_failed(msg_id):
            self._save_conversation_history()
        await self._emit_delivery_ack(request_id, msg_id, "failed", error=str(error))
        try:
            await self._emit_broker_frame(
                control_error_frame(error, {"request_id": request_id}, code="user_delivery_failed")
            )
        except Exception:
            logger.debug("Failed to broadcast delivery error", exc_info=True)

    def _mark_user_turn_failed(self, msg_id: str) -> bool:
        """Flip the in-memory user turn ``msg_id`` to steering_state=failed. Returns True
        if it changed (so the caller can persist once). Does NOT persist or broadcast.

        A turn already ``active`` (the agent consumed it before the retry loop gave up —
        a benign race) is left active; only a still-undelivered turn flips to failed."""
        for turn in self._conversation_turns:
            if turn.id == msg_id and turn.role == "user":
                if turn.metadata.get("steering_state") == "active":
                    return False
                if turn.metadata.get("steering_state") == "failed":
                    return False
                turn.metadata["steering_state"] = "failed"
                return True
        return False

    async def _emit_delivery_ack(
        self,
        request_id: str | None,
        msg_id: str,
        status: str,
        *,
        error: str | None = None,
        pending_questions: int = 0,
        settle_claim: bool = True,
    ) -> None:
        """Broadcast a steering delivery ACK (BUG-3).

        The HTTP ``/messages`` bridge waits for this to confirm the message actually
        reached the agent; live UI clients can also use it to clear a "sending…" state or
        warn the user to answer an open question first.
        """
        token = self._message_claim_tokens.get(request_id) if request_id else None
        if settle_claim and token and status != "pending":
            try:
                await settle_message(
                    await self._get_http_client(),
                    session_id=self.session_id,
                    request_id=request_id,
                    token=token,
                    status="failed" if status == "failed" else "delivered",
                    error=error,
                    timeout=self._settings.delivery.attempt_timeout_seconds,
                )
            except (httpx.HTTPError, ValueError):
                logger.exception("Could not persist message delivery outcome")
                status = "pending"
                error = (
                    "The native send returned, but Forge could not persist its delivery outcome."
                )
            else:
                self._message_claim_tokens.pop(request_id, None)
        event: dict[str, Any] = {
            "type": {"failed": "user_delivery_failed", "pending": "user_delivery_pending"}.get(
                status, "user_delivered"
            ),
            "status": status,
            "id": msg_id,
        }
        if request_id:
            event["request_id"] = request_id
        if error:
            event["error"] = error
        if pending_questions:
            event["pending_questions"] = pending_questions
        try:
            await self._emit_broker_frame(event)
        except Exception:
            logger.debug("Failed to broadcast delivery ack", exc_info=True)

    async def _activate_user_turn(self, data: dict) -> None:
        """Flip a steering message from "pending" to "active" once the agent has
        actually consumed it.

        ``user_delivered`` only means the text was typed into the pane; the message
        then sits in the CLI's own input queue until it is inserted into the flow.
        The (correlated) ``terminal_prompt_submitted`` — driven by Claude's
        UserPromptSubmit hook — is the genuine "now in the conversation" signal. We
        persist ``steering_state=active`` on the turn (so reconnect + REST reflect it)
        and broadcast a ``user_active`` event keyed by ``id`` so a live client can flip
        that specific bubble immediately, without waiting for the next REST poll.
        """
        msg_id = data.get("msg_id")
        if not isinstance(msg_id, str) or not msg_id:
            return
        request_id = data.get("request_id")
        stamp = timeline(data)
        if stamp is not None:
            for turn in self._conversation_turns:
                if turn.id == msg_id and turn.role == "user":
                    turn.metadata.setdefault("steering_accepted_at", stamp["observed_at"])
                    break
        if self._mark_user_turn_active(msg_id):
            self._save_conversation_history()
        await self._broadcast_user_active(msg_id, request_id)

    def _stamp_steering_state_from_frame(self, data: dict) -> None:
        """Stamp a user turn's steering_state from a logged steering-ACK frame, using the SAME
        shared policy the log rebuild applies (INV-4). Idempotent and silent if the frame is not
        a steering transition or no matching user turn exists yet."""
        state = steering_state_from_frame(str(data.get("type", "")), data)
        if state is None:
            return
        target = steering_target_id(str(data.get("type", "")), data)
        if not target:
            return
        for turn in self._conversation_turns:
            if turn.id == target and turn.role == "user":
                if turn.metadata.get("steering_state") != state:
                    turn.metadata["steering_state"] = state
                return

    def _mark_user_turn_active(self, msg_id: str) -> bool:
        """Flip the in-memory user turn ``msg_id`` to steering_state=active. Returns True if it
        changed (so the caller can persist once). Does NOT persist or broadcast."""
        for turn in self._conversation_turns:
            if turn.id == msg_id and turn.role == "user":
                if turn.metadata.get("steering_state") != "active":
                    turn.metadata["steering_state"] = "active"
                    return True
                return False
        return False

    async def _broadcast_user_active(self, msg_id: str, request_id: object = None) -> None:
        """Tell live clients a steering message went active so they flip that specific bubble."""
        event: dict[str, Any] = {"type": "user_active", "id": msg_id}
        for turn in self._conversation_turns:
            if turn.id == msg_id and turn.role == "user":
                event["created_at"] = turn.created_at
                if turn.metadata.get("steering_accepted_at"):
                    event["accepted_at"] = turn.metadata["steering_accepted_at"]
                break
        if isinstance(request_id, str) and request_id:
            event["request_id"] = request_id
        try:
            await self._emit_broker_frame(event)
        except Exception:
            logger.debug("Failed to broadcast user_active", exc_info=True)

    @staticmethod
    def _assistant_has_content(data: dict) -> bool:
        """True only when an assistant frame carries non-empty content. Codex's empty turn/started
        assistant frame (content: []) must NOT trip the backstop — the precise user_consumed handles
        that turn — so an empty frame is treated as no content."""
        message = data.get("message")
        if not isinstance(message, dict):
            return False
        content = message.get("content")
        return isinstance(content, list) and len(content) > 0

    async def _activate_pending_user_turns_backstop(self) -> None:
        """Flip every still-pending user turn the transport could plausibly have consumed to
        active (the non-native floor). Persists once and broadcasts one user_active per flipped
        turn. Idempotent: turns already active are skipped, so an overlap with a precise signal
        sends no redundant work.

        EXCLUDES turns whose delivery task is still in flight (``_delivering_msg_ids``): a steer
        mid-retry has NOT reached the agent, so flipping it here would report a never-delivered
        message as "active" (a false "consumed", SRD §3.4). Such a turn ends "failed" when its
        delivery terminally fails — never "active"."""
        flipped: list[str] = []
        for turn in self._conversation_turns:
            if turn.role != "user" or turn.metadata.get("steering_state") != "pending":
                continue
            if turn.id in self._delivering_msg_ids:
                continue
            turn.metadata["steering_state"] = "active"
            flipped.append(turn.id)
        if not flipped:
            return
        self._save_conversation_history()
        for msg_id in flipped:
            await self._broadcast_user_active(msg_id)

    async def handle_claude_hook(self, payload: dict[str, Any]) -> None:
        """Ingest a Claude Code hook payload into the normal event pipeline.

        Interactive tmux sessions can configure Claude Code HTTP hooks to POST
        here. The payload schema is owned by Claude Code, so we keep the raw
        body intact and add only stable routing fields.
        """
        transport_hook = getattr(self._transport, "handle_claude_hook", None)
        if callable(transport_hook):
            try:
                handled = await transport_hook(payload)
            except Exception:
                logger.warning("Transport Claude hook handler failed", exc_info=True)
            else:
                if handled:
                    return

        event_name = (
            payload.get("hook_event_name")
            or payload.get("hookEventName")
            or payload.get("event")
            or payload.get("hook_event")
            or "unknown"
        )
        await self._handle_cli_event(
            {
                "type": "claude_hook",
                "event_type": "claude.hook",
                "hook_event_name": str(event_name),
                "payload": payload,
            }
        )

    async def discover_slash_commands(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        """Return slash commands available through the current transport."""
        if self._transport is None:
            return []

        commands = []
        if self._transport.capabilities.slash_commands:
            commands = await self._transport.discover_slash_commands(refresh=refresh)
            if not commands:
                commands = getattr(self._transport, "slash_commands", [])
                if callable(commands):
                    commands = commands()
        normalized = self._normalize_slash_commands(commands)
        return [
            {
                "name": "/effort",
                "command": "effort",
                "kind": "command",
                "source": "forge",
                "description": "Show effort or set a supported level: /effort xhigh",
            },
            *[item for item in normalized if item["name"] != "/effort"],
        ]

    @staticmethod
    def _normalize_slash_commands(raw_commands: Any) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        if not isinstance(raw_commands, list):
            return normalized
        for item in raw_commands:
            if isinstance(item, str):
                raw_name = item
                description = ""
                kind = "command"
                source = "transport"
            elif isinstance(item, dict):
                raw_name = str(item.get("name") or item.get("command") or "")
                description = str(item.get("description") or "")
                kind = str(item.get("kind") or "command")
                source = str(item.get("source") or "transport")
            else:
                continue
            raw_name = raw_name.strip()
            if not raw_name:
                continue
            name = raw_name if raw_name.startswith("/") else f"/{raw_name}"
            if name in seen:
                continue
            seen.add(name)
            normalized.append(
                {
                    "name": name,
                    "command": name[1:],
                    "description": description.strip(),
                    "kind": kind,
                    "source": source,
                    **{
                        key: item[key]
                        for key in ("argument_hint", "method", "capability", "applies_to")
                        if isinstance(item, dict) and isinstance(item.get(key), str)
                    },
                }
            )
        return normalized

    async def handle_human_room_message(
        self,
        content: str,
        *,
        source: str = "external",
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        participant_id: str | None = None,
        deliver_to_transport: bool = True,
    ) -> str:
        """Record and broadcast a human-originated room message."""
        if not content.strip():
            raise ValueError("content is required")
        participant_meta = None
        participant = None
        if participant_id:
            if self._room_bridge is None:
                raise RuntimeError("Room mode is not enabled")
            participant = self._room_bridge.participants.get(participant_id)
            if participant is None:
                raise LookupError(f"Unknown room participant: {participant_id}")
            participant_meta = asdict(participant)

        msg_id = str(uuid.uuid4())
        metadata_payload = metadata or {}
        self._append_turn(
            ConversationTurn(
                id=msg_id,
                role="user",
                content=content,
                participant_id=participant_id,
                participant_meta=participant_meta,
                thread_id=_non_empty_str(metadata_payload.get("thread_id")),
                visibility=_non_empty_str(metadata_payload.get("visibility")) or "public",
                metadata=metadata_payload,
            )
        )
        now = datetime.now(UTC)
        await self._complete_trace_span(
            kind="turn.user",
            name=content[:120] or "human turn",
            parent_span_id=self._trace_session_span_id,
            actor_type="user",
            actor_id=source,
            actor_label=source,
            attributes={"source": source, "metadata": metadata_payload},
            started_at=now,
            ended_at=now,
        )
        event = {
            "type": "user_confirmed",
            "id": msg_id,
            "content": content,
            "source": source,
            "metadata": metadata_payload,
        }
        if request_id:
            event["request_id"] = request_id
        if participant_id:
            event["participantId"] = participant_id
            event["participant"] = participant_meta
        thread_id = _non_empty_str(metadata_payload.get("thread_id"))
        if thread_id:
            event["threadId"] = thread_id
        await self._emit_broker_frame(event)
        if self._room_bridge is not None and participant is not None:
            for room_id in participant.room_ids:
                await self._room_bridge.record_huddle_message(
                    room_id=room_id,
                    environment_id=participant.environment_id,
                    message_id=msg_id,
                    participant_id=participant.peer_id,
                    role="user",
                    content=content,
                    visibility=event.get("visibility", "public"),
                    thread_id=thread_id,
                    metadata=metadata_payload,
                )
        if deliver_to_transport:
            await self._send_explicit_human_message_to_transport(content)
        return msg_id

    async def handle_session_room_message(
        self, caller_session_id: str, room_id: str, content: str
    ) -> str:
        """Record a message a member session says in a room, as said by that session.

        ``caller_session_id`` is the session Forge verified. It is accepted only when a
        participant seated in ``room_id`` joined from that session; anyone else is refused
        with the reason. Nothing about the speaker comes from ``content``.
        """
        if self._room_bridge is None:
            raise RuntimeError("Room mode is not enabled")
        member = next(
            (
                participant
                for participant in self._room_bridge.participants.values()
                if participant.session_id == caller_session_id and room_id in participant.room_ids
            ),
            None,
        )
        if member is None:
            raise PermissionError(
                f"Session {caller_session_id} is not a member of room {room_id} on this "
                "session; ask the room's host to join it first"
            )
        return await self.handle_human_room_message(
            content,
            source="session",
            participant_id=member.peer_id,
            metadata={"speaker_session_id": caller_session_id, "room_id": room_id},
            deliver_to_transport=True,
        )

    async def handle_directed_room_message(
        self,
        target_peer_id: str,
        content: str,
        *,
        source: str = "external",
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Record and route a directed human message to a room participant."""
        if self._room_bridge is None:
            raise RuntimeError("Room mode is not enabled")
        if not target_peer_id.strip():
            raise ValueError("target_peer_id is required")
        if not content.strip():
            raise ValueError("content is required")

        participant = self._room_bridge.participants.get(target_peer_id)
        display_target = participant.persona if participant else target_peer_id
        target_prefix = f"@{display_target}"
        routing_metadata = dict(metadata or {})
        if self.session_id:
            routing_metadata.setdefault("session_id", self.session_id)
            routing_metadata.setdefault("root_correlation_id", self.session_id)
        if "reply_context" not in routing_metadata:
            recent_context = self._recent_room_message_context(target_peer_id)
            if recent_context:
                routing_metadata["recent_room_context"] = recent_context
        rendered_content = (
            content if content.lstrip().startswith(target_prefix) else f"{target_prefix} {content}"
        )
        msg_id = await self.handle_human_room_message(
            rendered_content,
            source=source,
            request_id=request_id,
            metadata=routing_metadata,
            participant_id=_non_empty_str(routing_metadata.get("participant_id")),
            deliver_to_transport=False,
        )

        delivered = await self._room_bridge.route_directed_message(
            target_peer_id,
            content,
            metadata=routing_metadata,
        )
        if (
            not delivered
            and participant is not None
            and participant.participant_kind == "mesh"
            and self._mesh_adapter is not None
        ):
            telemetry = get_observability()
            carrier = routing_metadata.get("trace_context")
            if not isinstance(carrier, dict):
                carrier = {}
            attributes = {
                "skuld.room.target_peer_id": target_peer_id,
                "skuld.room.source": source,
                "skuld.room.message_id": msg_id,
            }
            try:
                with telemetry.span(
                    "skuld.room.directed_message",
                    attributes=attributes,
                    carrier=carrier,
                ):
                    routing_metadata["trace_context"] = telemetry.inject() or carrier
                    response = await self._mesh_adapter.send_directed_message(
                        target_peer_id,
                        content,
                        metadata=routing_metadata,
                    )
                    status = str(response.get("status") or "error").strip().lower()
                    telemetry.set_attributes({**attributes, "skuld.room.status": status})
                    if status != "accepted":
                        error = str(response.get("error") or status)
                        raise RuntimeError(
                            f"Mesh-directed room message was not accepted by "
                            f"{target_peer_id}: {error}"
                        )
                    delivered = True
            except Exception as exc:
                logger.warning(
                    "Mesh-directed room message failed target=%s: %s",
                    target_peer_id,
                    exc,
                    exc_info=True,
                )
                raise RuntimeError(
                    f"Mesh-directed room message failed for {target_peer_id}: {exc}"
                ) from exc
        if not delivered:
            raise LookupError(f"Unknown room participant: {target_peer_id}")
        return msg_id

    def _recent_room_message_context(self, target_peer_id: str) -> dict[str, str]:
        """Return one bounded prior message from the addressed room participant."""
        for turn in reversed(self._conversation_turns):
            if turn.role != "assistant" or turn.participant_id != target_peer_id:
                continue
            content = turn.content.strip()
            if not content:
                continue
            return {
                "message_id": turn.id,
                "participant_id": target_peer_id,
                "content": content[:4096],
            }
        return {}

    async def handle_resend_initial_prompt(
        self,
        *,
        source: str = "external",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Resend the configured initial prompt into an active room session."""
        if self._room_bridge is None:
            raise RuntimeError("Room mode is not enabled")
        prompt = self._settings.session.initial_prompt.strip()
        if not prompt:
            raise ValueError("No initial prompt is configured")

        metadata_payload = {
            **(metadata or {}),
            "resend_prompt": True,
            "initial_prompt": True,
        }
        return await self.handle_human_room_message(
            prompt,
            source=source,
            metadata=metadata_payload,
            deliver_to_transport=True,
        )

    async def join_environment(
        self,
        *,
        participant_id: str,
        display_name: str,
        environment_id: str,
        role: str = "observer",
        room_id: str = "",
        capabilities: list[str] | None = None,
        surfaces: list[str] | None = None,
        environment_action_authorities: list[str] | None = None,
        participant_type: str = "human",
        session_id: str = "",
        subscribes_to: list[str] | None = None,
        notice_context: str = "",
    ) -> dict[str, Any]:
        """Join a person or an agent from another session to the live room state."""
        if self._room_bridge is None:
            raise RuntimeError("Room mode is not enabled")
        meta = await self._room_bridge.join_environment(
            participant_id,
            display_name=display_name,
            environment_id=environment_id,
            role=role,
            room_id=room_id,
            capabilities=capabilities,
            surfaces=surfaces,
            environment_action_authorities=environment_action_authorities,
            participant_type=participant_type,
            session_id=session_id,
            subscribes_to=subscribes_to,
            notice_context=notice_context,
        )
        if session_id:
            # This room goes with its session, so the seat is logged to survive a restart.
            await self._emit_broker_frame(
                {
                    "type": ROOM_MEMBER_FRAME,
                    "joined": True,
                    "participant_id": participant_id,
                    "member": {
                        "display_name": display_name,
                        "environment_id": environment_id,
                        "role": role,
                        "participant_type": participant_type,
                        "session_id": session_id,
                        "subscribes_to": subscribes_to or [],
                        "notice_context": notice_context,
                    },
                }
            )
        return asdict(meta)

    async def heartbeat_environment(
        self,
        *,
        participant_id: str,
        status: str | None = None,
        wakefulness: str | None = None,
        attention_state: str | None = None,
    ) -> dict[str, Any]:
        """Record heartbeat/state for a joined room participant."""
        if self._room_bridge is None:
            raise RuntimeError("Room mode is not enabled")
        meta = await self._room_bridge.heartbeat(
            participant_id,
            status=status,
            wakefulness=wakefulness,
            attention_state=attention_state,
        )
        if meta is None:
            raise LookupError(f"Unknown room participant: {participant_id}")
        return asdict(meta)

    async def leave_environment(
        self,
        *,
        participant_id: str,
        reason: str = "left",
    ) -> None:
        """End a joined participant's membership of the live room state."""
        if self._room_bridge is None:
            raise RuntimeError("Room mode is not enabled")
        participant = self._room_bridge.participant(participant_id)
        await self._room_bridge.leave_environment(participant_id, reason=reason)
        if participant is not None and participant.session_id:
            await self._emit_broker_frame(
                {"type": ROOM_MEMBER_FRAME, "joined": False, "participant_id": participant_id}
            )

    async def close_environment_huddle(
        self,
        *,
        room_id: str,
        reason: str = "closed",
        summary: str = "",
    ) -> dict[str, Any]:
        """Close a live Environment huddle and publish its transcript."""
        if self._room_bridge is None:
            raise RuntimeError("Room mode is not enabled")
        return await self._room_bridge.close_environment_huddle(
            room_id=room_id,
            reason=reason,
            summary=summary,
        )

    def require_room_capability(self, participant_id: str, capability: str) -> None:
        """Raise when a room participant cannot perform a control action."""
        if self._room_bridge is None:
            raise RuntimeError("Room mode is not enabled")
        self._room_bridge.require_participant_capability(participant_id, capability)

    async def _send_explicit_human_message_to_transport(self, content: str) -> None:
        """Deliver an explicit human room message to Skuld's own transport."""
        if self._transport is None:
            logger.warning("No transport available for explicit human room message")
            return
        outbound = self._format_room_message_for_skuld(content)
        self._pending_explicit_human_messages.append((content, outbound))
        self._pending_explicit_human_response_count += 1
        await self._ensure_transport_started()
        await self._transport.send_message(outbound)

    def _format_room_message_for_skuld(self, content: str) -> str:
        """Wrap an explicit human room message with flock context for Skuld."""
        if self._room_bridge is None:
            return content

        participants = sorted(
            self._room_bridge.participants.values(),
            key=lambda participant: (
                0 if participant.participant_type == "skuld" else 1,
                participant.persona.lower(),
            ),
        )
        lines = [
            "You are Skuld, the observer/coordinator for an active flock session.",
            (
                "Answer as the session observer using the live flock context below, "
                "not as a generic standalone chat."
            ),
            "",
            "Current room participants:",
        ]
        for participant in participants:
            display = participant.display_name or participant.persona or participant.peer_id
            status = participant.status or "idle"
            lines.append(
                f"- {display} (peer_id={participant.peer_id}, "
                f"type={participant.participant_type}, status={status})"
            )
        lines.extend(
            [
                "",
                "Human message from an external communication interface:",
                content,
            ]
        )
        return "\n".join(lines)

    async def _publish_room_presence_event(self, event: SleipnirEvent) -> None:
        """Publish canonical room presence events through the existing Sleipnir bus."""
        await self._sleipnir_publisher.publish(event)

    def get_room_participants(self, environment_id: str | None = None) -> list[dict[str, Any]]:
        """Return the current room participants as plain dictionaries."""
        if self._room_bridge is None:
            return []
        return self._room_bridge.environment_roster(environment_id=environment_id)

    def get_communication_routes(self) -> list[dict[str, Any]]:
        """Return active external communication routes exposed by this broker."""
        routes: list[dict[str, Any]] = []
        for channel in self._channels.channels:
            route_getter = getattr(channel, "communication_route", None)
            if not callable(route_getter):
                continue
            route = route_getter()
            if isinstance(route, dict):
                routes.append(route)
        return routes

    def _extract_and_store_outcome(self) -> None:
        """Extract an outcome while preserving the historic broker patch target."""
        transcript = self._build_transcript()
        if not transcript:
            return
        try:
            outcome = parse_outcome_block(transcript)
            if outcome is None:
                return
            self._artifacts.structured_outcome = outcome.fields
            self._artifacts.outcome_valid = outcome.valid
        except Exception:
            logger.warning("Failed to extract outcome block from transcript", exc_info=True)

    async def _hydrate_conversation_history(self) -> None:
        """Recover a resumed native session's durable prefix before the CLI starts."""
        if (
            not self._settings.history_hydration_enabled
            or (
                not self._settings.session.resume_session_id
                and not self._settings.room.enabled
                and not self._settings.workflow_steps.enabled
                and not self._settings.peer_inbox.enabled
            )
            or not self.volundr_api_url
            or not self._settings.event_log_enabled
        ):
            return
        try:
            durable = await fetch_durable_history(
                await self._get_http_client(),
                session_id=self.session_id,
                path=self.FORGE_LOG_PATH_TEMPLATE.format(sid=self.session_id),
                timeout_seconds=self._settings.history_hydration_timeout_seconds,
                page_size=self._settings.history_hydration_page_size,
                max_frames=self._settings.history_hydration_max_frames,
                max_bytes=self._settings.history_hydration_max_bytes,
                on_frames=self._restore_durable_runtime_state,
            )
            merged = merge_history_turns(
                durable, [asdict(turn) for turn in self._conversation_turns]
            )
            hydrated = [ConversationTurn(**turn) for turn in merged]
        except Exception as exc:
            if self._settings.workflow_steps.enabled:
                raise RuntimeError(
                    "The durable history of this workflow step host could not be restored; "
                    "refusing to start without knowing which step is open"
                ) from exc
            if self._settings.peer_inbox.enabled:
                self._refuse_peer_messages_after_failed_restore(exc)
            logger.warning(
                "Durable history hydration skipped for session %s; preserving local cache: %s",
                self.session_id,
                exc,
            )
            return
        # Direct cache replacement, never _append_turn: replayed frames are
        # already durable and must not be emitted/persisted a second time.
        self._conversation_turns = hydrated
        self._save_conversation_history()
        logger.info("Hydrated %d conversation turns from durable history", len(hydrated))
        await self._rejoin_room_members()

    def _control_state_path(self) -> Path:
        return self._conversation_history_path().with_name(f"controls_{self.session_id}.json")

    def _save_control_state(self) -> None:
        try:
            save_control_state(
                self._control_state_path(),
                {**self._unrestored_questions, **self._pending_ask_user_questions},
                {**self._unrestored_permissions, **self._pending_permission_requests},
            )
        except (OSError, TypeError, ValueError):
            logger.exception("Failed to persist pending control state")

    def _restore_durable_controls(self, frames: list) -> None:
        questions, permissions = pending_controls(frames)
        self._unrestored_questions = {
            identity: unrestored_control(frame) for identity, frame in questions.items()
        }
        self._unrestored_permissions = {
            identity: unrestored_control(frame) for identity, frame in permissions.items()
        }
        self._save_control_state()

    def _restore_durable_runtime_state(self, frames: list) -> None:
        """Restore broker-owned state from the frozen durable-log horizon."""
        self._restore_durable_controls(frames)
        self._restore_durable_workflow_steps(frames)
        self._restore_durable_peer_inbox(frames)
        self._restore_durable_mesh_follows(frames)
        self._restore_durable_room_members(frames)

    def _restore_durable_room_members(self, frames: list) -> None:
        """Work out who holds a seat from another session, by their latest join or leave."""
        for frame in frames:
            if getattr(frame, "kind", "") != ROOM_MEMBER_FRAME:
                continue
            participant_id = frame.payload["participant_id"]
            if frame.payload["joined"]:
                self._restored_room_members[participant_id] = frame.payload["member"]
            else:
                self._restored_room_members.pop(participant_id, None)

    async def _rejoin_room_members(self) -> None:
        """Seat members again: their membership did not end because this session restarted."""
        if self._room_bridge is None:
            return
        for participant_id, member in self._restored_room_members.items():
            await self._room_bridge.join_environment(participant_id, **member)

    def _load_control_state(self) -> None:
        try:
            self._unrestored_questions, self._unrestored_permissions = load_control_state(
                self._control_state_path()
            )
        except (OSError, TypeError, ValueError):
            logger.exception("Failed to recover pending control state")


async def _present_peer_file(message: dict) -> dict:
    """A flock member's present-file over the mesh (bound late: the API binds the broker)."""
    from skuld.broker_api import present_peer_file  # noqa: PLC0415

    return await present_peer_file(message)


# Global broker instance.
broker = Broker(SkuldSettings())


# API imports remain late because they bind the completed Broker instance.
# isort: off
from skuld import broker_api as _broker_api  # noqa: E402
from skuld.broker_api import (  # noqa: E402, F401
    lifespan,
    health,
    ready,
    websocket_endpoint,
    cli_websocket_endpoint,
    get_broker_logs,
    get_aggregate_logs,
    get_conversation_history,
    get_tool_result,
    download_send_user_file,
    _SlashCommandRequest,
    get_capabilities,
    get_plan,
    get_agents,
    get_slash_commands,
    send_slash_command,
    receive_claude_hook,
    create_service,
    list_services,
    get_service,
    delete_service,
    get_service_logs,
    restart_service,
    _presented_staging_dir,
    _rebuild_presented_registry,
    _resume_presented_uploads,
    present_file,
    _build_present_file_turn,
    download_presented_file,
    _SendMessageRequest,
    _RoomMessageRequest,
    _DirectedRoomMessageRequest,
    _ResendPromptRequest,
    _RoomJoinRequest,
    _RoomHeartbeatRequest,
    _RoomLeaveRequest,
    _RoomCloseRequest,
    _RoomCapabilityRequest,
    send_message_to_session,
    send_room_message,
    send_directed_room_message,
    resend_room_initial_prompt,
    join_room,
    heartbeat_room,
    leave_room,
    close_room,
    require_room_capability,
    get_room_participants,
    get_communication_routes,
    _TokenRedactFilter,
    app,
    _presented_registry,
)
# isort: on

_broker_api.bind_broker(lambda: broker, _log_buffer)


def main() -> None:
    """Run the broker server."""
    import uvicorn

    from niuu.observability import install_uvicorn_log_redaction

    install_uvicorn_log_redaction()
    settings = SkuldSettings()
    logger.info("Starting Skuld broker on %s:%d", settings.host, settings.port)
    uvicorn.run(app, host=settings.host, port=settings.port, access_log=False)


if __name__ == "__main__":
    main()
