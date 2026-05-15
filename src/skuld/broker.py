"""Skuld Broker - WebSocket proxy for Claude Code CLI.

Supports two transport modes (selected via config):
- "sdk": long-lived CLI process connected via --sdk-url WebSocket (default)
- "subprocess": spawns claude -p per message, reads stdout (legacy fallback)
"""

import asyncio
import base64
import collections
import inspect
import json
import logging
import os
import re
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from niuu.domain.logging import LoggingConfig
from niuu.domain.outcome import parse_outcome_block
from niuu.mesh.cluster import read_cluster_pub_addresses
from niuu.mesh.discovery_builder import build_discovery_adapters
from niuu.mesh.identity import MeshIdentity
from niuu.ports.cli import CLITransport
from niuu.utils import import_class
from skuld.channels import (
    ChannelRegistry,
    TelegramChannel,
    WebSocketChannel,
)
from skuld.chronicle_watcher import ChronicleWatcher
from skuld.config import SkuldSettings
from skuld.room_bridge import RoomBridge
from skuld.room_mesh_bridge import RoomMeshBridge
from skuld.service_manager import (
    ServiceCreateRequest,
    ServiceManager,
    ServiceStatus,
)
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.catalog import ravn_session_ended
from sleipnir.ports.events import SleipnirPublisher
from volundr.log_aggregate import aggregate_workspace_logs

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


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


def _resolve_git_workspace_root(workspace_dir: str) -> Path:
    """Resolve the actual checkout root for git-backed workspaces."""
    workspace = Path(workspace_dir).resolve()
    repo_dir = workspace / "repo"
    if (repo_dir / ".git").exists():
        return repo_dir
    return workspace


# ---------------------------------------------------------------------------
# Session artifacts & summary prompt (Part: Chronicle Summary Generation)
# ---------------------------------------------------------------------------

_GIT_COMMIT_PREFIXES = ("git commit", "git -c ", "git -C ")

# Matches git commit output like: [main e4f7a21] fix: some message
_GIT_COMMIT_OUTPUT_RE = re.compile(r"\[[\w/-]+\s+([a-f0-9]{7,})\]\s+(.+)")
_PEER_WATCHDOG_POLL_SECONDS = 5.0
_PEER_WATCHDOG_SILENCE_SECONDS = 45.0
_PEER_WATCHDOG_TOOL_SILENCE_SECONDS = 90.0


def _is_git_commit(cmd: str) -> bool:
    """Return True if a Bash command is a git commit invocation."""
    stripped = cmd.lstrip()
    if stripped.startswith(_GIT_COMMIT_PREFIXES):
        return True
    # Handle chained commands: git add . && git commit -m "..."
    return "git commit" in stripped


def _extract_git_commit_info(output: str) -> tuple[str, str] | None:
    """Extract commit hash and message from git commit output.

    Returns (hash, message) tuple or None if not found.
    """
    match = _GIT_COMMIT_OUTPUT_RE.search(output)
    if not match:
        return None
    return match.group(1), match.group(2)


@dataclass
class SessionArtifacts:
    """In-memory accumulator for session activity during the broker's lifetime.

    Populated passively from events flowing through ``_handle_cli_event``.
    """

    files_changed: list[str] = field(default_factory=list)
    turn_count: int = 0
    started_at: float = field(default_factory=time.monotonic)
    total_tokens: int = 0
    structured_outcome: dict[str, Any] | None = None
    outcome_valid: bool = False
    saga_id: str | None = None
    raid_id: str | None = None
    _known_files: set[str] = field(default_factory=set)
    _pending_tool_results: dict[str, dict] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> int:
        return int(time.monotonic() - self.started_at)

    def _classify_tool(self, tool_name: str, tool_input: dict) -> dict | None:
        """Classify a single tool_use block into a timeline event dict.

        Returns None when the tool doesn't map to a timeline event.
        """
        file_path = tool_input.get("file_path") or tool_input.get("path")

        if tool_name in ("Edit", "Write", "NotebookEdit"):
            if tool_name == "Edit":
                # Edit always modifies an existing file
                action = "modified"
                if file_path:
                    self._known_files.add(file_path)
            elif file_path and file_path in self._known_files:
                action = "modified"
            elif file_path:
                action = "created"
                self._known_files.add(file_path)
            else:
                action = "created"
            return {"type": "file", "label": file_path or tool_name, "action": action}

        if tool_name == "Read":
            # Track files we've seen for created/modified classification
            if file_path:
                self._known_files.add(file_path)
            return None

        if tool_name != "Bash":
            return None

        cmd = tool_input.get("command", "")
        if _is_git_commit(cmd):
            # Store pending; will be enriched by tool_result
            return {"type": "git", "label": cmd[:80] or "git commit", "_pending_git": True}

        return {"type": "terminal", "label": cmd[:80] or "bash"}

    def record_tool_use(self, data: dict) -> list[dict]:
        """Extract file paths from tool_use events (Write, Edit, etc.).

        Returns a list of timeline-reportable tool events extracted
        from the content blocks.

        Handles both the HTTP streaming format (``data["content"]``)
        and the SDK WebSocket format (``data["message"]["content"]``).
        """
        tool_events: list[dict] = []
        content = data.get("content", [])
        if not isinstance(content, list) or not content:
            # SDK WebSocket transport nests content under message.content
            msg = data.get("message")
            if isinstance(msg, dict):
                content = msg.get("content", [])
        if not isinstance(content, list):
            return tool_events

        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue

            tool_name = block.get("name", "")
            tool_input = block.get("input", {})
            tool_use_id = block.get("id", "")
            file_path = tool_input.get("file_path") or tool_input.get("path")

            if file_path and file_path not in self.files_changed:
                self.files_changed.append(file_path)

            event = self._classify_tool(tool_name, tool_input)
            if event:
                # Store tool_use_id for matching with tool_result
                if tool_use_id:
                    event["_tool_use_id"] = tool_use_id
                tool_events.append(event)

        return tool_events

    def enrich_from_tool_result(self, data: dict, tool_events: list[dict]) -> None:
        """Enrich pending tool events with data from tool_result blocks.

        Extracts exit codes for terminal events and commit info for git events
        from the corresponding tool_result content blocks.
        """
        content = data.get("content", [])
        if not isinstance(content, list):
            return

        # Build a map of tool_result blocks by tool_use_id
        result_map: dict[str, dict] = {}
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            use_id = block.get("tool_use_id", "")
            if use_id:
                result_map[use_id] = block

        for event in tool_events:
            use_id = event.pop("_tool_use_id", "")
            if not use_id or use_id not in result_map:
                continue

            result_block = result_map[use_id]
            result_content = result_block.get("content", "")
            if isinstance(result_content, list):
                # Extract text from content blocks
                result_content = " ".join(
                    b.get("text", "") for b in result_content if isinstance(b, dict)
                )

            if event.get("type") == "git" and event.pop("_pending_git", False):
                commit_info = _extract_git_commit_info(result_content)
                if commit_info:
                    event["hash"] = commit_info[0]
                    event["label"] = commit_info[1]

            if event.get("type") == "terminal":
                # Extract exit code — look for explicit exit code in result
                exit_code = self._extract_exit_code(result_block)
                if exit_code is not None:
                    event["exit"] = exit_code

    @staticmethod
    def _extract_exit_code(result_block: dict) -> int | None:
        """Extract exit code from a tool_result block.

        The SDK transport includes exit code info in the result block.
        """
        # Check for explicit exit_code field
        if "exit_code" in result_block:
            return result_block["exit_code"]

        # Check content for exit code pattern
        content = result_block.get("content", "")
        if isinstance(content, str):
            # Check for error indicator — if tool_result has is_error
            if result_block.get("is_error"):
                return 1
            return 0

        # For list content, check is_error flag
        if result_block.get("is_error"):
            return 1
        return 0

    def record_result(self) -> None:
        """Increment turn counter on each result event."""
        self.turn_count += 1


@dataclass
class PeerWatchState:
    """Tracks one active flock peer task for Skuld's silence watchdog."""

    peer_id: str
    task_id: str
    title: str
    started_at: float
    last_progress_at: float
    last_status: str = "busy"
    warned: bool = False


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

_AUTH_HEADER = "authorization"
_BEARER_PREFIX = "bearer "


def _decode_jwt_claims(token: str) -> dict:
    """Decode JWT payload without signature verification.

    Skuld does not verify signatures — that is Envoy's / the API gateway's
    job.  We only decode to extract user identity claims for API forwarding.
    """
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        # JWT base64url → standard base64
        payload_b64 = parts[1]
        # Add padding
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        return json.loads(payload_bytes)
    except Exception:
        return {}


def _extract_bearer_token(headers: dict[str, str]) -> str | None:
    """Extract Bearer token from an Authorization header value."""
    auth = headers.get(_AUTH_HEADER, "")
    if auth.lower().startswith(_BEARER_PREFIX):
        return auth[len(_BEARER_PREFIX) :].strip()
    return None


def _extract_token_from_websocket(websocket: WebSocket) -> str | None:
    """Extract JWT from WebSocket connection.

    Checks (in order):
    1. Authorization header (Bearer token) — preferred, works with Envoy
    2. x-auth-* headers injected by Envoy sidecar
    3. access_token query parameter — browser fallback
    """
    header_items = websocket.headers.items()
    if inspect.iscoroutine(header_items):
        header_items.close()
        header_items = ()
    elif inspect.isawaitable(header_items):
        header_items = ()
    headers = {k.lower(): v for k, v in header_items}

    # 1. Bearer token from Authorization header
    token = _extract_bearer_token(headers)
    if token:
        return token

    # 2. If Envoy x-auth-* headers are present, we don't have the raw JWT
    #    but we have the validated claims — return None (caller uses headers).

    # 3. Query parameter fallback (browser WebSocket can't set headers)
    query_get = getattr(websocket.query_params, "get", None)
    if not callable(query_get):
        return None
    query_token = query_get("access_token")
    if inspect.iscoroutine(query_token):
        query_token.close()
        return None
    if inspect.isawaitable(query_token):
        return None
    return query_token


CONVERSATION_HISTORY_DIR = ".skuld"
CONVERSATION_HISTORY_FILE = "conversation.json"


@dataclass
class ConversationTurn:
    """A single turn in the conversation history."""

    id: str
    role: str  # "user" | "assistant"
    content: str
    parts: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict = field(default_factory=dict)
    # Multi-participant fields (None in single-agent mode)
    participant_id: str | None = None
    participant_meta: dict | None = None
    thread_id: str | None = None
    visibility: str = "public"  # "public" | "internal"


CHRONICLE_SUMMARY_PROMPT = """\
Summarize this coding session in JSON format. Be concise.
Respond ONLY with the JSON object, no markdown fencing, no commentary.

{
  "summary": "One paragraph describing what was accomplished in this session.",
  "key_changes": ["file_or_component: brief description of change", ...],
  "unfinished_work": "Description of anything left incomplete, or null if done."
}
"""

SUMMARY_TIMEOUT_SECONDS = 15


class Broker:
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
        self.session_id = self._settings.session.id
        self.model = self._settings.session.model
        self.workspace_dir = self._settings.workspace_path
        archive_store_cls = import_class(self._settings.archive_store.adapter)
        self._archive_store = archive_store_cls(**self._settings.archive_store.kwargs)
        self.volundr_api_url = self._settings.volundr_api_url
        self._transport: CLITransport | None = None
        self.service_manager: ServiceManager | None = None
        self._channels = ChannelRegistry()
        self._http_client: httpx.AsyncClient | None = None
        self._http_client_jwt: str | None = None  # JWT used to create _http_client
        self._sleipnir_publisher: SleipnirPublisher = sleipnir_publisher or InProcessBus()
        self._artifacts = SessionArtifacts(
            saga_id=self._settings.session.saga_id,
            raid_id=self._settings.session.raid_id,
        )
        self._session_start_reported = False
        self._event_sequence = 0
        self._activity_state: str = "idle"
        self._last_activity_report: float = 0.0
        self._conversation_turns: list[ConversationTurn] = []
        self._pending_assistant_content: str = ""
        self._pending_assistant_parts: list[dict] = []
        self._pending_block_type: str = ""
        self._pending_reasoning_text: str = ""
        self._pending_explicit_human_messages: list[tuple[str, str]] = []
        self._pending_explicit_human_response_count = 0
        self._chronicle_watcher: ChronicleWatcher | None = None
        self._peer_watchdog_task: asyncio.Task[None] | None = None
        self._peer_watches: dict[str, PeerWatchState] = {}

        # Mesh adapter — only active when mesh.enabled is True
        self._mesh_adapter: Any = None

        # Room mesh bridge — translates ravn.mesh.* Sleipnir events to room wire events.
        # Active when both mesh.enabled and room.enabled are True.
        self._room_mesh_bridge: RoomMeshBridge | None = None

        # Room bridge — only active when room.enabled is True
        self._room_bridge: RoomBridge | None = (
            RoomBridge(
                config=self._settings.room,
                channels=self._channels,
                append_turn=self._append_turn,
                report_timeline_event=self._report_timeline_event,
                observe_peer_event=self._observe_room_peer_event,
            )
            if self._settings.room.enabled
            else None
        )

        # JWT identity state — populated on first browser WebSocket connection
        self._user_jwt: str | None = None
        self._user_claims: dict = {}

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
            data = {"turns": [asdict(t) for t in self._conversation_turns]}
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            logger.warning("Failed to save conversation history to %s", path, exc_info=True)

    def _append_turn(self, turn: ConversationTurn) -> None:
        """Append a turn and persist to disk."""
        self._conversation_turns.append(turn)
        self._save_conversation_history()

    def _flush_pending_assistant_turn(self, metadata: dict | None = None) -> None:
        """Save any accumulated assistant content as a conversation turn."""
        content = self._pending_assistant_content
        if not content:
            return

        # Flush remaining reasoning
        if self._pending_reasoning_text:
            summary = self._pending_reasoning_text[-500:]
            self._pending_assistant_parts.append({"type": "reasoning", "text": summary})

        parts = self._pending_assistant_parts if self._pending_assistant_parts else []
        self._append_turn(
            ConversationTurn(
                id=str(uuid.uuid4()),
                role="assistant",
                content=content,
                parts=parts,
                metadata=metadata or {},
            )
        )
        self._pending_assistant_content = ""
        self._pending_assistant_parts = []
        self._pending_reasoning_text = ""

    async def _ensure_workflow_prompt_turn(self) -> None:
        """Persist the workflow trigger prompt without executing it locally."""
        prompt = self._settings.session.initial_prompt
        if not prompt:
            return

        if any(turn.role == "user" and turn.content == prompt for turn in self._conversation_turns):
            return

        turn_id = str(uuid.uuid4())
        self._append_turn(
            ConversationTurn(
                id=turn_id,
                role="user",
                content=prompt,
            )
        )
        await self._channels.broadcast(
            {
                "type": "user_confirmed",
                "id": turn_id,
                "content": prompt,
            }
        )

    async def _start_mesh_adapter(self) -> None:
        """Build and start the mesh adapter when mesh.enabled is True.

        Uses niuu.mesh functions to build transport and discovery, then wraps
        them in a MeshParticipant for unified lifecycle management (NIU-634).
        """
        from niuu.mesh import build_in_process_mesh, resolve_peer_id
        from niuu.mesh.participant import MeshParticipant
        from niuu.mesh.transport_builder import build_nng_transport
        from skuld.mesh_adapter import SkuldMeshAdapter

        mesh_cfg = self._settings.mesh
        own_peer_id = resolve_peer_id(mesh_cfg.peer_id)

        # Build mesh transport (nng preferred, in-process fallback)
        mesh = None
        if mesh_cfg.transport != "in_process":
            try:
                from ravn.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter  # noqa: PLC0415

                nng_cfg = getattr(mesh_cfg, "nng", None)
                address = (
                    getattr(nng_cfg, "pub_sub_address", "tcp://127.0.0.1:0")
                    if nng_cfg
                    else "tcp://127.0.0.1:0"
                )
                peer_addresses = read_cluster_pub_addresses(mesh_cfg.adapters)
                nng = build_nng_transport(
                    address=address,
                    service_id=f"skuld:{own_peer_id}",
                    peer_addresses=peer_addresses or None,
                )
                if nng is not None:
                    mesh = SleipnirMeshAdapter(
                        publisher=nng,
                        subscriber=nng,
                        own_peer_id=own_peer_id,
                        rpc_timeout_s=mesh_cfg.rpc_timeout_s,
                    )
            except ImportError:
                logger.warning("mesh: nng transport not available, falling back to in-process")

        if mesh is None:
            mesh = build_in_process_mesh(own_peer_id, mesh_cfg.rpc_timeout_s)

        # Build discovery adapter using shared niuu.mesh.discovery_builder
        own_identity = MeshIdentity(
            peer_id=mesh_cfg.peer_id or self.session_id or "skuld",
            realm_id="",
            persona=mesh_cfg.persona,
            capabilities=list(mesh_cfg.capabilities),
            permission_mode="full_access",
            version="0.1.0",
        )
        discovery = build_discovery_adapters(
            adapters_config=mesh_cfg.adapters,
            own_identity=own_identity,
        )

        participant = MeshParticipant(
            mesh=mesh,
            discovery=discovery,
            peer_id=own_peer_id,
        )

        self._mesh_adapter = SkuldMeshAdapter(
            participant=participant,
            transport=self._transport,
            config=mesh_cfg,
            session_id=self.session_id,
        )

        try:
            await self._mesh_adapter.start()
            logger.info(
                "Mesh adapter started (peer_id=%s)",
                self._mesh_adapter.peer_id,
            )

            # Register Skuld + discovered mesh peers as room participants
            # so the browser UI shows them without a direct WebSocket.
            if self._room_bridge is not None:
                await self._room_bridge.register_mesh_peer(
                    peer_id=self._mesh_adapter.peer_id,
                    persona="Skuld",
                    display_name="Skuld",
                    subscribes_to=list(mesh_cfg.consumes_event_types),
                    emits=["code.changed"],
                    tools=list(mesh_cfg.tools),
                    participant_type="skuld",
                )
            has_peers = discovery is not None and hasattr(discovery, "peers")
            if self._room_bridge is not None and has_peers:
                for peer in discovery.peers().values():
                    await self._room_bridge.register_mesh_peer(
                        peer_id=peer.peer_id,
                        persona=peer.persona,
                        display_name=peer.persona,
                        subscribes_to=list(getattr(peer, "consumes_event_types", [])),
                        emits=list(getattr(peer, "emits_event_types", [])),
                        tools=list(getattr(peer, "capabilities", [])),
                    )

            # Start room mesh bridge so outcomes from any mesh peer flow to the
            # room UI via Sleipnir — eliminates the dual-publish pattern.
            if self._room_bridge is not None:
                from sleipnir.ports.events import SleipnirSubscriber

                sleipnir_subscriber = self._mesh_adapter.sleipnir_subscriber
                if sleipnir_subscriber is None:
                    # Fallback: InProcessBus implements both Publisher and Subscriber.
                    # Only use it if it actually satisfies the Subscriber interface.
                    if isinstance(self._sleipnir_publisher, SleipnirSubscriber):
                        sleipnir_subscriber = self._sleipnir_publisher
                    else:
                        logger.warning(
                            "Sleipnir publisher does not implement SleipnirSubscriber"
                            " — RoomMeshBridge disabled"
                        )
                if sleipnir_subscriber is not None:
                    self._room_mesh_bridge = RoomMeshBridge(
                        subscriber=sleipnir_subscriber,
                        room_bridge=self._room_bridge,
                        session_id=self.session_id,
                    )
                    await self._room_mesh_bridge.start()
                    logger.info("RoomMeshBridge started (session_id=%s)", self.session_id)

        except Exception as exc:
            logger.error("Mesh adapter start failed: %r", exc, exc_info=True)
            self._mesh_adapter = None

    def _has_workflow_trigger(self) -> bool:
        cfg = self._settings.workflow_trigger
        return bool(cfg.enabled and cfg.event_type and self._settings.session.initial_prompt)

    def _is_room_only_workflow_session(self) -> bool:
        """Return True when flock workflow sessions should stay room-only.

        In these sessions, browser traffic should observe and direct mesh peers
        through the room bridge. Lazy-starting Skuld's own CLI transport on
        browser connect would spawn a second agent and confuse session state.
        """
        return bool(self._has_workflow_trigger() and self._settings.room.enabled)

    async def _publish_workflow_trigger(self) -> None:
        """Publish the initial Tyr task into the flock as a mesh outcome event."""
        if self._mesh_adapter is None or not self._has_workflow_trigger():
            return

        from ravn.domain.events import RavnEvent, RavnEventType

        cfg = self._settings.workflow_trigger
        delay_s = max(0.0, float(cfg.startup_delay_s or 0.0))
        if delay_s:
            logger.info(
                "Workflow trigger waiting %.1fs for flock subscribers before mesh dispatch",
                delay_s,
            )
            await asyncio.sleep(delay_s)
        event = RavnEvent(
            type=RavnEventType.OUTCOME,
            source=f"skuld:{self._mesh_adapter.peer_id}",
            payload={
                "event_type": cfg.event_type,
                "session_id": self.session_id,
                "persona": "skuld",
                "summary": f"Workflow dispatch: {cfg.label or cfg.event_type}",
                "task_description": self._settings.session.initial_prompt,
                "trigger_source": cfg.source,
                "workflow_trigger_label": cfg.label,
                "workflow_trigger_node_id": cfg.node_id,
                "workspace_path": self.workspace_dir,
            },
            timestamp=datetime.now(UTC),
            urgency=0.8,
            correlation_id=self.session_id,
            session_id=self.session_id,
            root_correlation_id=self.session_id,
        )
        await self._mesh_adapter.publish(event, cfg.event_type)
        logger.info(
            "Workflow trigger dispatched onto mesh event_type=%s node_id=%s",
            cfg.event_type,
            cfg.node_id,
        )

    def _build_transport_kwargs(self) -> dict:
        """Return superset of kwargs that any transport constructor might need."""
        return {
            "workspace_dir": self.workspace_dir,
            "model": self.model,
            "sdk_port": self._settings.port,
            "session_id": self.session_id,
            "skip_permissions": self._settings.skip_permissions,
            "agent_teams": self._settings.agent_teams,
            "system_prompt": self._settings.session.system_prompt,
            "initial_prompt": (
                "" if self._has_workflow_trigger() else self._settings.session.initial_prompt
            ),
            "mcp_servers": self._settings.mcp_servers,
        }

    def _create_transport(self) -> CLITransport:
        """Create the configured CLI transport via dynamic import.

        Uses ``transport_adapter`` from settings (a fully-qualified class path).
        Legacy ``cli_type`` / ``transport`` fields are resolved to the correct
        adapter path by the config validator before this method is called.
        """
        adapter_path = self._settings.transport_adapter
        if "." not in adapter_path:
            raise ValueError(
                f"Invalid transport_adapter '{adapter_path}': "
                "must be a fully-qualified class path "
                "(e.g. 'skuld.transports.sdk_websocket.SdkWebSocketTransport')"
            )

        try:
            cls = import_class(adapter_path)
        except (ImportError, AttributeError) as exc:
            raise ValueError(f"Cannot load transport adapter '{adapter_path}': {exc}") from exc

        kwargs = self._build_transport_kwargs()
        sig = inspect.signature(cls)
        filtered = {k: v for k, v in kwargs.items() if k in sig.parameters}
        logger.info("Using %s (adapter: %s)", cls.__name__, adapter_path)
        return cls(**filtered)

    async def _auto_start_transport(self) -> None:
        """Background-task wrapper around ``self._transport.start()``.

        SubprocessTransport (the default for skuldClaude) sends the initial
        prompt to a fresh Claude process and streams back assistant events,
        but never echoes the user's own prompt back as an event. Without an
        explicit synthesis the chat UI sees the assistant's reply with no
        user turn before it. We append a user turn to conversation history
        (so late-joining browsers see it via replay) and broadcast
        ``user_confirmed`` so any already-connected channel renders it
        immediately.
        """
        prompt = self._settings.session.initial_prompt
        if prompt and not any(
            t.role == "user" and t.content == prompt for t in self._conversation_turns
        ):
            turn_id = str(uuid.uuid4())
            self._append_turn(
                ConversationTurn(id=turn_id, role="user", content=prompt)
            )
            try:
                await self._channels.broadcast(
                    {"type": "user_confirmed", "id": turn_id, "content": prompt}
                )
            except Exception:
                logger.debug(
                    "Initial-prompt user_confirmed broadcast failed", exc_info=True
                )

        try:
            await self._transport.start()
            logger.info("Transport auto-started successfully")
        except Exception:
            logger.error("Transport auto-start failed", exc_info=True)

    async def startup(self) -> None:
        """Initialize the broker on startup."""
        logger.info("Broker starting for session %s", self.session_id)
        logger.info("Transport adapter: %s", self._settings.transport_adapter)

        if self.volundr_api_url:
            logger.info("Token usage reporting enabled: %s", self.volundr_api_url)
        else:
            logger.warning("VOLUNDR_API_URL not set — token usage will not be reported")

        # Ensure workspace directory exists
        os.makedirs(self.workspace_dir, exist_ok=True)

        # Load conversation history from disk
        self._load_conversation_history()

        # Initialize transport
        self._transport = self._create_transport()
        self._transport.on_event(self._handle_cli_event)

        # Initialize service manager
        self.service_manager = ServiceManager(self.workspace_dir)
        await self.service_manager.init()
        logger.info("Service manager initialized")

        # Initialize Telegram channel if configured
        await self._init_telegram_channel()

        # Start chronicle watcher (tails JSONL session files for terminal mode)
        if self._settings.chronicle_watcher_enabled and self.volundr_api_url:
            workspace_slug = self.workspace_dir.replace("/", "-")
            watch_dir = Path.home() / ".claude" / "projects" / workspace_slug
            self._chronicle_watcher = ChronicleWatcher(
                session_id=self.session_id,
                watch_dir=watch_dir,
                api_base_url=self.volundr_api_url,
                http_headers=self._build_auth_headers(),
                debounce_ms=self._settings.chronicle_watcher_debounce_ms,
            )
            asyncio.create_task(self._chronicle_watcher.start())
            logger.info("Chronicle watcher started for %s", watch_dir)

        # Auto-start transport when an initial prompt is configured
        # (dispatched sessions should begin work immediately, not wait
        # for a browser to connect). Run as a background task so the
        # lifespan returns promptly and uvicorn binds — otherwise the
        # transport's first turn (which can take seconds to minutes)
        # blocks the HTTP listener and the chat UI gets 502s.
        if self._settings.session.initial_prompt:
            if self._has_workflow_trigger():
                logger.info(
                    "Workflow trigger configured — holding initial prompt for mesh dispatch"
                )
                await self._ensure_workflow_prompt_turn()
            else:
                logger.info("Initial prompt configured — auto-starting transport in background")
                asyncio.create_task(self._auto_start_transport())

        # Start mesh adapter if enabled (after transport is ready)
        if self._settings.mesh.enabled:
            await self._start_mesh_adapter()
            if self._has_workflow_trigger():
                await self._publish_workflow_trigger()
        elif self._has_workflow_trigger():
            logger.warning("Workflow trigger configured but mesh is disabled — skipping dispatch")

        if self._room_bridge is not None and self._settings.mesh.enabled:
            self._peer_watchdog_task = asyncio.create_task(self._peer_watchdog_loop())

    async def shutdown(self) -> None:
        """Clean up on shutdown.

        Reports chronicle summary to Volundr API before stopping the
        transport, so the CLI process is still alive for summary generation.
        """
        logger.info("Broker shutting down")

        # Stop chronicle watcher first (flush pending events)
        if self._chronicle_watcher:
            await self._chronicle_watcher.stop()

        if self._peer_watchdog_task is not None:
            self._peer_watchdog_task.cancel()
            await asyncio.gather(self._peer_watchdog_task, return_exceptions=True)
            self._peer_watchdog_task = None

        # Report chronicle BEFORE stopping the transport (CLI must be alive)
        await self._report_chronicle()
        await self._write_workspace_archive()

        # Stop room mesh bridge before mesh adapter
        if self._room_mesh_bridge is not None:
            await self._room_mesh_bridge.stop()
            self._room_mesh_bridge = None

        # Stop mesh adapter before transport (deregister from discovery)
        if self._mesh_adapter is not None:
            await self._mesh_adapter.stop()
            self._mesh_adapter = None

        # Close all message channels (browser WebSockets, Telegram, etc.)
        await self._channels.close_all()

        # Stop transport
        if self._transport:
            await self._transport.stop()

        # Close HTTP client
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

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

    async def _observe_room_peer_event(
        self,
        peer_id: str,
        event_type: str,
        frame: dict[str, Any],
    ) -> None:
        """Track flock peer progress so Skuld can surface silent failures."""
        if self._room_bridge is None:
            return
        observer_peer_id = self._observer_peer_id()
        if peer_id == observer_peer_id:
            return

        now = time.monotonic()
        watch = self._peer_watches.get(peer_id)

        if event_type == "task_started":
            metadata = frame.get("metadata", {})
            title = str(metadata.get("title") or frame.get("data") or "task")
            task_id = str(metadata.get("task_id") or frame.get("task_id") or peer_id)
            self._peer_watches[peer_id] = PeerWatchState(
                peer_id=peer_id,
                task_id=task_id,
                title=title,
                started_at=now,
                last_progress_at=now,
                last_status="busy",
            )
            return

        if watch is None:
            return

        watch.last_progress_at = now
        watch.warned = False
        if event_type == "tool_start":
            watch.last_status = "tool_executing"
        elif event_type in {"thought", "thinking"}:
            watch.last_status = "thinking"
        elif event_type in {"tool_result", "task_complete"}:
            watch.last_status = "idle"
        elif event_type == "error":
            watch.last_status = "error"
            self._peer_watches.pop(peer_id, None)
        elif event_type in {"response", "outcome", "help_needed"}:
            self._peer_watches.pop(peer_id, None)

    async def _peer_watchdog_loop(self) -> None:
        """Warn in chat when a flock peer accepted work but goes quiet."""
        try:
            while True:
                await asyncio.sleep(_PEER_WATCHDOG_POLL_SECONDS)
                await self._check_peer_watchdog_once()
        except asyncio.CancelledError:
            return

    async def _check_peer_watchdog_once(self) -> None:
        """Run one silence-watchdog pass for active flock peers."""
        now = time.monotonic()
        for peer_id, watch in list(self._peer_watches.items()):
            threshold = (
                _PEER_WATCHDOG_TOOL_SILENCE_SECONDS
                if watch.last_status == "tool_executing"
                else _PEER_WATCHDOG_SILENCE_SECONDS
            )
            silence_seconds = now - watch.last_progress_at
            if silence_seconds < threshold or watch.warned:
                continue
            watch.warned = True
            await self._emit_peer_silence_warning(watch, int(silence_seconds))

    async def _emit_peer_silence_warning(
        self,
        watch: PeerWatchState,
        silence_seconds: int,
    ) -> None:
        """Surface a stalled-peer warning in chat and peer status."""
        if self._room_bridge is None:
            return

        participant = self._room_bridge.participants.get(watch.peer_id)
        peer_name = (
            participant.display_name
            or participant.persona
            or watch.peer_id
        ) if participant is not None else watch.peer_id
        status_hint = (
            "while waiting on a tool result"
            if watch.last_status == "tool_executing"
            else "after accepting work"
        )
        content = (
            f"Skuld watchdog: `{peer_name}` has shown no visible progress for "
            f"{silence_seconds}s {status_hint} on `{watch.title}`. "
            "The agent may be blocked on an upstream LLM/backend failure or a stalled tool."
        )
        await self._room_bridge.broadcast_cli_activity(
            watch.peer_id,
            "blocked",
            f"silent for {silence_seconds}s",
        )
        observer_peer_id = self._observer_peer_id()
        if observer_peer_id:
            await self._room_bridge.broadcast_cli_message(
                observer_peer_id,
                content,
                is_error=True,
            )
        await self._report_timeline_event(
            {
                "t": self._artifacts.duration_seconds,
                "type": "error",
                "label": content[:120],
            }
        )

    async def _handle_cli_event(self, data: dict) -> None:
        """Forward a CLI event to all connected channels."""
        event_type = data.get("type", "unknown")
        suppress_channel_broadcast = (
            event_type in {"user", "assistant", "content_block_delta", "result"}
            and self._pending_explicit_human_response_count > 0
        )
        num_channels = self._channels.count
        logger.debug(
            "_handle_cli_event: type=%s, forwarding to %d channel(s) suppress=%s",
            event_type,
            num_channels,
            suppress_channel_broadcast,
        )

        if num_channels == 0:
            logger.warning(
                "_handle_cli_event: no channels to forward type=%s",
                event_type,
            )

        if not suppress_channel_broadcast:
            await self._channels.broadcast(data)

        # Record user messages that arrive via the transport (e.g. the
        # initial prompt flushed as a pending message) into conversation
        # history so late-joining browsers see them.
        if event_type == "user":
            # A user event means the previous assistant turn is complete.
            # Flush any pending assistant content as a saved turn.
            self._flush_pending_assistant_turn()

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
                    self._append_turn(
                        ConversationTurn(
                            id=str(uuid.uuid4()),
                            role="user",
                            content=user_content,
                        )
                    )

        # When CLI sends system/init, broadcast available commands to browsers
        if event_type == "system" and data.get("subtype") == "init":
            slash_commands = data.get("slash_commands", [])
            skills = data.get("skills", [])
            if slash_commands or skills:
                await self._channels.broadcast(
                    {
                        "type": "available_commands",
                        "slash_commands": slash_commands,
                        "skills": skills,
                    }
                )

        # Report activity state transitions to Volundr
        if event_type == "assistant":
            asyncio.create_task(self._report_activity_state("active"))
            # Emit room activity for CLI participant so room UI shows "thinking"
            if self._room_bridge is not None and self._mesh_adapter is not None:
                await self._room_bridge.broadcast_cli_activity(
                    self._mesh_adapter.peer_id, "thinking"
                )
        elif event_type == "result":
            asyncio.create_task(self._report_activity_state("idle"))
            if self._pending_explicit_human_response_count == 0:
                asyncio.create_task(self._on_result_publish_mesh())

        # Track conversation from assistant messages.
        # The SDK WebSocket protocol sends complete messages as type=assistant
        # with content blocks already resolved (not as streaming deltas).
        # We also handle the HTTP streaming format (content_block_delta, result)
        # for backward compatibility.
        if event_type == "assistant":
            # Extract content from the assistant message
            message = data.get("message", {})
            content_blocks = message.get("content", [])
            if isinstance(content_blocks, list) and content_blocks:
                text_parts = []
                reasoning_parts = []
                for block in content_blocks:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and block.get("text"):
                        text_parts.append(block["text"])
                    elif block.get("type") == "thinking" and block.get("thinking"):
                        reasoning_parts.append(block["thinking"])
                text_content = "\n".join(text_parts)
                if text_content:
                    self._pending_assistant_content = text_content
                    self._pending_assistant_parts = []
                    if reasoning_parts:
                        # Keep last 500 chars of reasoning as summary
                        combined = "\n".join(reasoning_parts)
                        self._pending_assistant_parts.append(
                            {"type": "reasoning", "text": combined[-500:]}
                        )
                    self._pending_assistant_parts.append({"type": "text", "text": text_content})

        # HTTP streaming format: accumulate deltas
        if event_type == "content_block_delta":
            delta = data.get("delta", {})
            delta_type = delta.get("type", "")
            if delta_type == "thinking_delta":
                thinking = delta.get("thinking", "")
                if thinking:
                    self._pending_reasoning_text += thinking
            else:
                text = delta.get("text", "")
                if text:
                    self._pending_assistant_content += text

        # Accumulate artifacts from assistant tool_use events
        if event_type == "assistant":
            tool_events = self._artifacts.record_tool_use(data)
            if tool_events:
                asyncio.create_task(self._report_activity_state("tool_executing"))
            # Enrich tool events with tool_result data (exit codes, git info)
            self._artifacts.enrich_from_tool_result(data, tool_events)
            for tool_ev in tool_events:
                # Clean up internal fields before reporting
                tool_ev.pop("_pending_git", None)
                tool_ev["t"] = self._artifacts.duration_seconds
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
            asyncio.create_task(
                self._report_timeline_event(
                    {
                        "t": self._artifacts.duration_seconds,
                        "type": "error",
                        "label": str(error_msg)[:120] or "Unknown error",
                    }
                )
            )

        # Report usage on result events and track turn count
        if event_type == "result":
            self._artifacts.record_result()
            asyncio.create_task(self._report_usage(data))
            explicit_room_reply = self._pending_explicit_human_response_count > 0

            # Flush pending assistant turn (HTTP streaming format sends result)
            if not self._pending_assistant_content:
                # Try to extract from result event itself
                self._pending_assistant_content = data.get("result", "")
                if not self._pending_assistant_content:
                    for block in data.get("content", []):
                        if isinstance(block, dict) and block.get("type") == "text":
                            self._pending_assistant_content = block.get("text", "")
                            break
            # Build metadata from result event
            model_usage_for_turn = data.get("modelUsage", {})
            result_cost = None
            result_model = None
            for model_id, usage in model_usage_for_turn.items():
                result_model = model_id
                if usage.get("costUSD") is not None:
                    result_cost = (result_cost or 0) + usage["costUSD"]

            # Capture content before flush clears it
            content = self._pending_assistant_content or data.get("result", "")
            if explicit_room_reply:
                self._pending_assistant_content = ""
                self._pending_assistant_parts = []
                self._pending_reasoning_text = ""
            else:
                self._flush_pending_assistant_turn(
                    metadata={
                        "usage": model_usage_for_turn,
                        "cost": result_cost,
                        "model": result_model,
                    }
                )

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

    # Maps control message types to the TransportCapabilities field that
    # must be True for the control to be forwarded.
    _CONTROL_CAPABILITY_MAP: dict[str, str] = {
        "interrupt": "interrupt",
        "set_model": "set_model",
        "set_max_thinking_tokens": "set_thinking_tokens",
        "set_permission_mode": "set_permission_mode",
        "rewind_files": "rewind_files",
        "mcp_set_servers": "mcp_set_servers",
    }

    async def _dispatch_browser_message(
        self,
        data: dict,
        sender_ws: WebSocket | None = None,
    ) -> None:
        """Route a browser WebSocket message to the appropriate handler."""
        if not self._transport:
            logger.warning("_dispatch_browser_message: transport is None, dropping message")
            return

        msg_type = data.get("type")
        logger.info(
            "_dispatch_browser_message: type=%s, transport_alive=%s",
            _sanitize_log(msg_type),
            self._transport.is_alive,
        )

        # Guard: reject control messages the transport does not support.
        cap_field = self._CONTROL_CAPABILITY_MAP.get(msg_type or "")
        if cap_field and not getattr(self._transport.capabilities, cap_field):
            error_msg = f"{msg_type} not supported by this transport"
            logger.warning("_dispatch_browser_message: %s", _sanitize_log(error_msg))
            if sender_ws:
                await sender_ws.send_json({"type": "error", "content": error_msg})
            return

        match msg_type:
            # Phase 2: permission response from browser
            case "permission_response":
                request_id = data.get("request_id", "")
                behavior = data.get("behavior", "deny")
                response = {
                    "behavior": behavior,
                    "updatedInput": data.get("updated_input", {}),
                }
                if data.get("updated_permissions"):
                    response["updatedPermissions"] = data["updated_permissions"]
                await self._transport.send_control_response(request_id, response)

            # Phase 3: interrupt current turn
            case "interrupt":
                await self._transport.send_control("interrupt")

            # Phase 3: change model mid-session
            case "set_model":
                model = data.get("model", "")
                if model:
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

            # Room: forward a directed message to a specific Ravn participant
            case "directed_message":
                if self._room_bridge is None:
                    logger.warning("directed_message received but room mode is disabled")
                    if sender_ws:
                        await sender_ws.send_json(
                            {"type": "error", "content": "Room mode is not enabled"}
                        )
                    return
                target = data.get("targetPeerId", "")
                content = data.get("content", "")
                if not target or not content:
                    return
                await self._room_bridge.route_directed_message(target, content)

            # Default: treat as user message (backward compat with {"content": "..."})
            case _:
                message = data.get("content", "")
                if not message:
                    return

                if self._is_room_only_workflow_session():
                    error_msg = (
                        "Direct chat is disabled for flock workflow sessions. "
                        "Target a mesh peer instead."
                    )
                    logger.info("_dispatch_browser_message: %s", error_msg)
                    if sender_ws:
                        await sender_ws.send_json({"type": "error", "content": error_msg})
                    return

                # Record user turn in conversation history
                content_str = message if isinstance(message, str) else json.dumps(message)
                msg_id = str(uuid.uuid4())
                self._append_turn(
                    ConversationTurn(
                        id=msg_id,
                        role="user",
                        content=content_str,
                    )
                )

                # Echo back to all browsers so the message is confirmed
                # immediately (rendering as a user turn) — the actual transport
                # delivery happens asynchronously below.
                await self._channels.broadcast(
                    {
                        "type": "user_confirmed",
                        "id": msg_id,
                        "content": content_str,
                    }
                )

                # send_message holds a per-instance lock for the entire
                # turn. Awaiting inline blocks the WS receive loop, so the
                # user can't queue a follow-up while Claude is running.
                # Fire-and-forget: the transport's lock still serialises
                # invocations, so ordering is preserved; we just don't pin
                # the WS handler waiting for a turn that may take minutes.
                asyncio.create_task(
                    self._safe_transport_send(self._transport, message),
                    name=f"transport-send-{msg_id}",
                )

    async def _safe_transport_send(
        self, transport: object, message: str
    ) -> None:
        """Wrap transport.send_message so background-task failures are surfaced.

        Used by the fire-and-forget path in ``_dispatch_browser_message`` —
        without this wrapper, a transport error would only show up as an
        ``asyncio.create_task`` exception. Any error here is logged AND
        broadcast to all currently connected channels so the user sees
        something in the chat UI rather than a silent stall.
        """
        try:
            await transport.send_message(message)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.exception("Transport send_message failed in background task")
            try:
                await self._channels.broadcast(
                    {"type": "error", "content": str(exc)}
                )
            except Exception:
                logger.debug(
                    "Failed to broadcast transport error to channels", exc_info=True
                )

    async def handle_human_room_message(
        self,
        content: str,
        *,
        source: str = "external",
        metadata: dict[str, Any] | None = None,
        deliver_to_transport: bool = True,
    ) -> str:
        """Record and broadcast a human-originated room message."""
        if not content.strip():
            raise ValueError("content is required")

        msg_id = str(uuid.uuid4())
        self._append_turn(
            ConversationTurn(
                id=msg_id,
                role="user",
                content=content,
            )
        )
        await self._channels.broadcast(
            {
                "type": "user_confirmed",
                "id": msg_id,
                "content": content,
                "source": source,
                "metadata": metadata or {},
            }
        )
        if deliver_to_transport:
            await self._send_explicit_human_message_to_transport(content)
        return msg_id

    async def handle_directed_room_message(
        self,
        target_peer_id: str,
        content: str,
        *,
        source: str = "external",
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
        rendered_content = f"@{display_target} {content}"
        msg_id = await self.handle_human_room_message(
            rendered_content,
            source=source,
            metadata=metadata,
            deliver_to_transport=False,
        )

        delivered = await self._room_bridge.route_directed_message(target_peer_id, content)
        if not delivered:
            raise LookupError(f"Unknown room participant: {target_peer_id}")
        return msg_id

    async def _send_explicit_human_message_to_transport(self, content: str) -> None:
        """Deliver an explicit human room message to Skuld's own transport."""
        if self._transport is None:
            logger.warning("No transport available for explicit human room message")
            return
        outbound = self._format_room_message_for_skuld(content)
        self._pending_explicit_human_messages.append((content, outbound))
        self._pending_explicit_human_response_count += 1
        if not self._transport.is_alive:
            logger.info("Starting transport for explicit human room message")
            await self._transport.start()
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

    def get_room_participants(self) -> list[dict[str, Any]]:
        """Return the current room participants as plain dictionaries."""
        if self._room_bridge is None:
            return []
        return [asdict(participant) for participant in self._room_bridge.participants.values()]

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

    def _build_auth_headers(self) -> dict[str, str]:
        """Build authentication headers for Volundr API calls.

        Priority:
        1. VOLUNDR_API_TOKEN (long-lived PAT injected by Infisical) — preferred
           because user JWTs expire after minutes while PATs last for months.
        2. User JWT from WebSocket connection (fallback for dev/local)
        3. Empty (dev mode — no auth, Volundr backend must accept)
        """
        service_token = os.environ.get("VOLUNDR_API_TOKEN", "")
        if service_token:
            return {"Authorization": f"Bearer {service_token}"}

        if self._user_jwt:
            return {"Authorization": f"Bearer {self._user_jwt}"}

        logger.debug("No auth token available — requests will be unauthenticated")
        return {}

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Lazy-init HTTP client for Volundr API calls.

        Recreates the client when the JWT changes so the Authorization
        header stays current.
        """
        if self._http_client is not None and self._http_client_jwt != self._user_jwt:
            await self._http_client.aclose()
            self._http_client = None

        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=self.volundr_api_url,
                timeout=10.0,
                headers=self._build_auth_headers(),
            )
            self._http_client_jwt = self._user_jwt
        return self._http_client

    def _next_sequence(self) -> int:
        """Return a monotonically increasing sequence number."""
        seq = self._event_sequence
        self._event_sequence += 1
        return seq

    async def _emit_pipeline_event(
        self,
        event_type: str,
        data: dict,
        *,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost: float | None = None,
        duration_ms: int | None = None,
        model: str | None = None,
    ) -> None:
        """Emit a raw event to the Volundr event pipeline.

        Fires as a background task — must not raise or block the WebSocket.
        """
        if not self.volundr_api_url:
            return

        client = await self._get_http_client()
        from datetime import datetime

        payload = {
            "session_id": self.session_id,
            "event_type": event_type,
            "timestamp": datetime.now(UTC).isoformat(),
            "data": data,
            "sequence": self._next_sequence(),
        }

        if tokens_in is not None:
            payload["tokens_in"] = tokens_in
        if tokens_out is not None:
            payload["tokens_out"] = tokens_out
        if cost is not None:
            payload["cost"] = cost
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if model is not None:
            payload["model"] = model

        try:
            response = await client.post(FORGE_EVENTS_PATH, json=payload)
            if response.status_code < 300:
                logger.debug("Pipeline event emitted: %s", event_type)
            else:
                logger.debug(
                    "Pipeline event failed (%d): %s",
                    response.status_code,
                    response.text[:200],
                )
        except Exception:
            logger.debug("Failed to emit pipeline event: %s", event_type, exc_info=True)

    async def _report_usage(self, result_data: dict) -> None:
        """Report token usage from a CLI result event to the Volundr API.

        Fires as a background task — must not raise or block the WebSocket.
        """
        if not self.volundr_api_url:
            return

        model_usage = result_data.get("modelUsage", {})
        if not model_usage:
            logger.debug("No modelUsage in result event, skipping usage report")
            return

        client = await self._get_http_client()
        url = f"{FORGE_SESSIONS_PATH}/{self.session_id}/usage"

        for model_id, usage in model_usage.items():
            tokens = (
                usage.get("inputTokens", 0)
                + usage.get("outputTokens", 0)
                + usage.get("cacheReadInputTokens", 0)
                + usage.get("cacheCreationInputTokens", 0)
            )
            if tokens <= 0:
                continue

            cost = usage.get("costUSD")
            payload = {
                "tokens": tokens,
                "provider": "cloud",
                "model": model_id,
                "message_count": 1,
            }
            if cost is not None:
                payload["cost"] = cost

            try:
                response = await client.post(url, json=payload)
                if response.status_code < 300:
                    logger.info(
                        "Reported usage: model=%s tokens=%d cost=%s",
                        model_id,
                        tokens,
                        cost,
                    )
                else:
                    logger.warning(
                        "Usage report failed (%d): %s",
                        response.status_code,
                        response.text[:200],
                    )
            except Exception:
                logger.warning("Failed to report usage for %s", model_id, exc_info=True)

    async def _report_timeline_event(self, event: dict) -> None:
        """Report a single timeline event to the Volundr API.

        Fires as a background task — must not raise or block the WebSocket.
        The event dict must contain at minimum: t, type, label.
        """
        if not self.volundr_api_url:
            return

        client = await self._get_http_client()
        url = f"{FORGE_CHRONICLES_PATH}/{self.session_id}/timeline"

        try:
            response = await client.post(url, json=event)
            if response.status_code < 300:
                logger.debug(
                    "Timeline event reported: type=%s, t=%d",
                    event.get("type"),
                    event.get("t", 0),
                )
            else:
                logger.debug(
                    "Timeline event report failed (%d): %s",
                    response.status_code,
                    response.text[:200],
                )
        except Exception:
            logger.debug(
                "Failed to report timeline event: type=%s",
                event.get("type"),
                exc_info=True,
            )

    @staticmethod
    def _classify_pipeline_event(tool_ev: dict) -> str:
        """Map a timeline tool event dict to a SessionEventType value."""
        ev_type = tool_ev.get("type", "")
        action = tool_ev.get("action", "")
        if ev_type == "file":
            if action == "created":
                return "file_created"
            if action == "deleted":
                return "file_deleted"
            return "file_modified"
        if ev_type == "git":
            return "git_commit"
        if ev_type == "terminal":
            return "terminal_command"
        return "tool_use"

    async def _report_session_start(self) -> None:
        """Report the session start timeline event (once)."""
        if self._session_start_reported:
            return
        self._session_start_reported = True
        await self._report_timeline_event(
            {
                "t": 0,
                "type": "session",
                "label": "Session started",
            }
        )
        # Emit session_start to event pipeline
        await self._emit_pipeline_event(
            "session_start",
            {
                "model": self.model,
                "session_name": self._settings.session.name,
            },
            model=self.model,
        )

    async def _report_activity_state(self, state: str) -> None:
        """Report activity state change to Volundr.

        States: active, idle, tool_executing.
        Debounces rapid transitions — only reports when state actually changes.
        """
        if state == self._activity_state:
            return

        self._activity_state = state
        now = time.monotonic()
        self._last_activity_report = now

        if not self.volundr_api_url:
            return

        metadata = {
            "turn_count": self._artifacts.turn_count,
            "duration_seconds": self._artifacts.duration_seconds,
        }

        try:
            client = await self._get_http_client()
            resp = await client.post(
                f"{FORGE_SESSIONS_PATH}/{self.session_id}/activity",
                json={"state": state, "metadata": metadata},
            )
            logger.info(
                "Activity report: state=%s status=%d url=%s",
                state,
                resp.status_code,
                resp.url,
            )
        except Exception:
            logger.warning(
                "Failed to report activity state %s",
                state,
                exc_info=True,
            )

    async def _generate_summary(self) -> dict:
        """Ask the CLI to generate a session summary.

        Returns a dict with ``summary``, ``key_changes``, and
        ``unfinished_work`` keys.  Falls back to artifacts data
        when the CLI is unavailable or times out.
        """
        if not self._transport or not self._transport.is_alive:
            logger.info("CLI not alive, skipping AI summary generation")
            return {
                "summary": None,
                "key_changes": self._artifacts.files_changed,
                "unfinished_work": None,
            }

        try:
            await self._transport.send_message(CHRONICLE_SUMMARY_PROMPT)

            # Wait for the result event (set by _handle_cli_message)
            deadline = time.monotonic() + SUMMARY_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                last = self._transport.last_result
                if last is not None:
                    break
                await asyncio.sleep(0.25)

            last = self._transport.last_result
            if last is None:
                logger.warning("Summary generation timed out after %ds", SUMMARY_TIMEOUT_SECONDS)
                return {
                    "summary": None,
                    "key_changes": self._artifacts.files_changed,
                    "unfinished_work": None,
                }

            # Extract text from result
            result_text = last.get("result", "")
            if not result_text:
                # Try to extract from content blocks
                for block in last.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        result_text = block.get("text", "")
                        break

            # Strip markdown fencing if present
            result_text = result_text.strip()
            if result_text.startswith("```"):
                lines = result_text.split("\n")
                lines = lines[1:]  # drop opening fence
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]  # drop closing fence
                result_text = "\n".join(lines).strip()

            parsed = json.loads(result_text)
            logger.info("AI summary generated successfully")
            return {
                "summary": parsed.get("summary"),
                "key_changes": parsed.get("key_changes", self._artifacts.files_changed),
                "unfinished_work": parsed.get("unfinished_work"),
            }
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to parse summary response: %s", e)
        except Exception:
            logger.warning("Summary generation failed", exc_info=True)

        return {
            "summary": None,
            "key_changes": self._artifacts.files_changed,
            "unfinished_work": None,
        }

    def _build_transcript(self) -> str:
        """Concatenate all assistant turns to form the session transcript."""
        return "\n\n".join(
            turn.content
            for turn in self._conversation_turns
            if turn.role == "assistant" and turn.content
        )

    def _extract_and_store_outcome(self) -> None:
        """Extract outcome block from the session transcript and store in artifacts.

        No-ops silently when no outcome block is present or parsing fails.
        """
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

    async def _emit_session_ended_event(self) -> None:
        """Emit ravn.session.ended via Sleipnir with structured outcome and saga/raid context.

        Always emits the event — even when outcome extraction failed — so that
        downstream Tyr pipeline executors receive session completion signals.
        """
        outcome_str = "SUCCESS" if self._artifacts.outcome_valid else "PARTIAL"
        source = f"ravn:{self.session_id}"
        persona = self._settings.session.name

        try:
            event = ravn_session_ended(
                session_id=self.session_id,
                persona=persona,
                outcome=outcome_str,
                token_count=self._artifacts.total_tokens,
                duration_s=self._artifacts.duration_seconds,
                source=source,
                correlation_id=self.session_id,
            )
            if self._artifacts.structured_outcome is not None:
                event.payload["structured_outcome"] = self._artifacts.structured_outcome
                event.payload["outcome_valid"] = self._artifacts.outcome_valid
            if self._artifacts.raid_id:
                event.payload["raid_id"] = self._artifacts.raid_id
            if self._artifacts.saga_id:
                event.payload["saga_id"] = self._artifacts.saga_id
            await self._sleipnir_publisher.publish(event)
            logger.info(
                "Session ended event emitted: session=%s outcome=%s saga=%s raid=%s",
                self.session_id,
                outcome_str,
                self._artifacts.saga_id,
                self._artifacts.raid_id,
            )
        except Exception:
            logger.warning("Failed to emit session ended event", exc_info=True)

    async def _on_result_publish_mesh(self) -> None:
        """Called when a CLI result event arrives (turn finished).

        Extracts outcome from transcript and publishes ``code.changed``
        on the mesh so flock peers (reviewer) can react immediately.
        """
        self._extract_and_store_outcome()
        await self._publish_mesh_outcome()

    async def _git_diff_summary(self) -> str:
        """Return a truncated git diff from the workspace.

        Best-effort: returns an empty string on any failure so mesh
        publishing is never blocked by git issues.
        """
        max_bytes = self._settings.mesh.diff_max_bytes
        timeout = self._settings.mesh.diff_timeout_s

        # Try committed changes first (HEAD~1..HEAD) since the coder
        # session typically commits before finishing.  Fall back to
        # uncommitted working-tree changes (diff HEAD).
        for diff_args in (["git", "diff", "HEAD~1..HEAD"], ["git", "diff", "HEAD"]):
            try:
                proc = await asyncio.create_subprocess_exec(
                    *diff_args,
                    cwd=self.workspace_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except Exception:
                return ""
            raw = stdout.decode(errors="replace")
            if raw.strip():
                if len(raw) > max_bytes:
                    return raw[:max_bytes] + "\n... (truncated)"
                return raw

        return ""

    async def _publish_mesh_outcome(self) -> None:
        """Publish a ``code.changed`` event on the mesh so flock peers react.

        Called after the session completes.  The reviewer ravn subscribes
        to ``code.changed`` and will trigger a review when it receives this.
        """
        if self._mesh_adapter is None:
            return

        from ravn.domain.events import RavnEvent, RavnEventType

        diff_summary = await self._git_diff_summary()

        outcome_payload: dict = {
            "event_type": "code.changed",
            "session_id": self.session_id,
            "persona": self._settings.mesh.persona,
            "summary": (
                f"Session completed"
                f" ({self._artifacts.turn_count} turns,"
                f" {len(self._artifacts.files_changed)} files)"
            ),
            "workspace_path": self.workspace_dir,
        }

        initial_prompt = self._settings.session.initial_prompt
        if initial_prompt:
            outcome_payload["task_description"] = initial_prompt

        if diff_summary:
            outcome_payload["diff_summary"] = diff_summary

        if self._artifacts.structured_outcome is not None:
            outcome_payload["outcome"] = self._artifacts.structured_outcome
        if self._artifacts.files_changed:
            outcome_payload["files_changed"] = list(self._artifacts.files_changed)

        event = RavnEvent(
            type=RavnEventType.OUTCOME,
            source=f"skuld:{self._mesh_adapter.peer_id}",
            payload=outcome_payload,
            timestamp=datetime.now(UTC),
            urgency=0.8,
            correlation_id=self.session_id,
            session_id=self.session_id,
        )

        try:
            await self._mesh_adapter._mesh.publish(event, "code.changed")
            logger.info(
                "Mesh: published code.changed (peer=%s, files=%d)",
                self._mesh_adapter.peer_id,
                len(self._artifacts.files_changed),
            )
            # RoomMeshBridge subscribes to the same NNG bus and will
            # pick this up via loopback (subscriber dials own pub address
            # from cluster.yaml).  No separate broadcast needed.

        except Exception:
            logger.warning("Mesh: failed to publish code.changed", exc_info=True)

    async def _report_chronicle(self) -> None:
        """Report chronicle summary data to the Volundr API on shutdown.

        Mirrors ``_report_usage`` — fires once during shutdown, best-effort,
        never raises.

        Also extracts the outcome block from the session transcript and emits
        the ``ravn.session.ended`` Sleipnir event so Tyr can track raid completion.
        """
        self._extract_and_store_outcome()
        await self._emit_session_ended_event()
        await self._publish_mesh_outcome()

        if not self.volundr_api_url:
            return

        if self._artifacts.turn_count == 0:
            logger.info("No turns recorded, skipping chronicle report")
            return

        logger.info(
            "Generating chronicle report (turns=%d, files=%d, duration=%ds)",
            self._artifacts.turn_count,
            len(self._artifacts.files_changed),
            self._artifacts.duration_seconds,
        )

        try:
            summary_data = await self._generate_summary()

            client = await self._get_http_client()
            url = f"{FORGE_SESSIONS_PATH}/{self.session_id}/chronicle"

            payload: dict = {
                "duration_seconds": self._artifacts.duration_seconds,
            }
            if summary_data.get("summary"):
                payload["summary"] = summary_data["summary"]
            if summary_data.get("key_changes"):
                payload["key_changes"] = summary_data["key_changes"]
            if summary_data.get("unfinished_work"):
                payload["unfinished_work"] = summary_data["unfinished_work"]

            response = await client.post(url, json=payload)
            if response.status_code < 300:
                logger.info("Chronicle report submitted successfully")
            else:
                logger.warning(
                    "Chronicle report failed (%d): %s",
                    response.status_code,
                    response.text[:200],
                )

            # Emit session_stop to event pipeline
            await self._emit_pipeline_event(
                "session_stop",
                {
                    "reason": "shutdown",
                    "total_tokens": 0,
                    "duration_seconds": self._artifacts.duration_seconds,
                    "turn_count": self._artifacts.turn_count,
                    "files_changed": len(self._artifacts.files_changed),
                },
            )
        except Exception:
            logger.warning("Failed to report chronicle", exc_info=True)

    async def _write_workspace_archive(self) -> None:
        """Write a workspace-backed archive snapshot for stopped-session reads."""
        try:
            transcript_payload = {
                "turns": [asdict(turn) for turn in self._conversation_turns],
                "is_active": False,
                "last_activity": "",
            }
            aggregated_logs = aggregate_workspace_logs(
                self.workspace_dir,
                lines=5000,
                level="DEBUG",
            )
            workspace_slug = self.workspace_dir.replace("/", "-")
            event_source_dir = Path.home() / ".claude" / "projects" / workspace_slug
            self._archive_store.write_archive(
                session_id=self.session_id,
                workspace_dir=self.workspace_dir,
                transcript_payload=transcript_payload,
                aggregated_logs=aggregated_logs,
                event_source_dir=event_source_dir,
            )
            logger.info("Workspace archive written for session %s", self.session_id)
        except Exception:
            logger.warning("Failed to write workspace archive", exc_info=True)

    async def _init_telegram_channel(self) -> None:
        """Initialize and register a Telegram channel if configured."""
        tg_config = self._settings.telegram
        if not tg_config.enabled:
            return

        if not tg_config.bot_token or not tg_config.chat_id:
            logger.warning("Telegram enabled but bot_token or chat_id missing, skipping")
            return

        try:
            channel = TelegramChannel(
                bot_token=tg_config.bot_token,
                chat_id=tg_config.chat_id,
                notify_only=tg_config.notify_only,
                topic_mode=tg_config.topic_mode,
                message_thread_id=tg_config.message_thread_id,
                topic_name=self._build_telegram_topic_name(),
                on_message=self._dispatch_browser_message,
            )
            await channel.start()
            self._channels.add(channel)
            logger.info("Telegram channel initialized for chat %s", tg_config.chat_id)
        except RuntimeError:
            logger.warning("python-telegram-bot not installed, Telegram channel disabled")
        except Exception:
            logger.warning("Failed to initialize Telegram channel", exc_info=True)

    def _build_telegram_topic_name(self) -> str:
        """Build a readable Telegram topic name for the active session."""
        session_name = (self._settings.session.name or "").strip()
        session_id = (self._settings.session.id or "").strip()

        if not session_name or session_name == "unknown":
            session_name = "Volundr session"

        pieces = [session_name]
        if session_id and session_id not in session_name:
            pieces.append(session_id[:8])

        topic_name = " · ".join(piece for piece in pieces if piece).strip()
        topic_name = " ".join(topic_name.split())
        return topic_name[:128] or "Volundr session"

    def _update_jwt_from_websocket(self, websocket: WebSocket) -> None:
        """Extract and store JWT from an incoming WebSocket connection.

        Prefers the Authorization header (set by Envoy or reverse proxy),
        then falls back to the access_token query parameter (browser).
        Updates the stored JWT on each connection so token refreshes
        propagate automatically.
        """
        try:
            token = _extract_token_from_websocket(websocket)
        except Exception:
            logger.debug("Failed to extract JWT from WebSocket", exc_info=True)
            return
        if not token:
            if self._user_jwt is None:
                logger.warning("No JWT found on WebSocket connection")
            return

        self._user_jwt = token
        self._user_claims = _decode_jwt_claims(token)

        user_id = self._user_claims.get("sub", "unknown")
        logger.info("JWT updated from WebSocket connection (sub=%s)", _sanitize_log(user_id))

        # Propagate new auth headers to the chronicle watcher
        if self._chronicle_watcher is not None:
            self._chronicle_watcher.update_headers(self._build_auth_headers())

    async def handle_websocket(self, websocket: WebSocket) -> None:
        """Handle a browser WebSocket connection at /session."""
        # Extract JWT before accepting — headers are available pre-accept
        self._update_jwt_from_websocket(websocket)

        await websocket.accept()
        channel = WebSocketChannel(websocket)
        self._channels.add(channel)
        conn_count = self._channels.count
        logger.info("WebSocket connected, total channels: %d", conn_count)

        try:
            if not self._transport:
                logger.error("handle_websocket: transport not initialized")
                await websocket.send_json({"type": "error", "content": "Transport not initialized"})
                return

            # Lazy-start transport on first browser connection
            if not self._transport.is_alive:
                if self._is_room_only_workflow_session():
                    logger.info(
                        "handle_websocket: workflow room session detected; "
                        "skipping transport lazy-start"
                    )
                else:
                    logger.info("handle_websocket: transport not alive, starting...")
                    try:
                        await self._transport.start()
                        logger.info("handle_websocket: transport started successfully")
                    except Exception as e:
                        logger.error(
                            "handle_websocket: transport.start() failed: %r",
                            e,
                            exc_info=True,
                        )
                        await websocket.send_json(
                            {
                                "type": "error",
                                "content": f"Transport start failed: {e}",
                            }
                        )
                        return
            else:
                logger.debug("handle_websocket: transport already alive")

            # Report session start to timeline (once, on first connection)
            asyncio.create_task(self._report_session_start())

            # Send welcome message
            await websocket.send_json(
                {"type": "system", "content": f"Connected to session {self.session_id}"}
            )
            logger.debug("handle_websocket: welcome message sent")

            # Send transport capabilities so the frontend knows which
            # controls to render.
            if self._transport:
                caps = {"type": "capabilities", **asdict(self._transport.capabilities)}
                await websocket.send_json(caps)
                logger.debug("handle_websocket: capabilities sent")

            # Replay conversation history so late-joining browsers see
            # earlier messages (including the initial prompt)
            if self._conversation_turns:
                logger.info(
                    "Replaying %d conversation turns to new browser",
                    len(self._conversation_turns),
                )
                await websocket.send_json(
                    {
                        "type": "conversation_history",
                        "turns": [asdict(t) for t in self._conversation_turns],
                    }
                )

            # Send current room state to late-joining browsers when room mode active
            if self._room_bridge is not None:
                await websocket.send_json(self._room_bridge.get_room_state_event())

            # Handle messages from browser
            while True:
                data = await websocket.receive_json()
                logger.debug(
                    "handle_websocket: browser msg: %s",
                    _sanitize_log(json.dumps(data)[:500]),
                )
                try:
                    await self._dispatch_browser_message(data, sender_ws=websocket)
                except Exception as e:
                    logger.exception("Error processing browser message: %s", _sanitize_log(data))
                    await websocket.send_json({"type": "error", "content": str(e)})

        except WebSocketDisconnect:
            logger.info("WebSocket disconnected")
        except Exception as e:
            logger.exception("WebSocket error")
            try:
                await websocket.send_json({"type": "error", "content": str(e)})
            except Exception:
                logger.debug("Failed to send error response to WebSocket", exc_info=True)
        finally:
            self._channels.remove(channel)
            remaining = self._channels.count
            logger.info("Connection closed, remaining channels: %d", remaining)

    async def handle_cli_websocket(self, websocket: WebSocket, session_id: str) -> None:
        """Handle the CLI WebSocket connection at /ws/cli/{session_id}.

        Only used by the SdkWebSocketTransport. The CLI process connects
        back to this endpoint after being spawned with --sdk-url.
        """
        logger.info(
            "handle_cli_websocket: incoming CLI connection for session=%s (transport=%s)",
            _sanitize_log(session_id),
            type(self._transport).__name__ if self._transport else None,
        )

        if not self._transport or not self._transport.capabilities.cli_websocket:
            logger.warning(
                "CLI WebSocket received but transport %s does not support SDK WebSocket protocol",
                type(self._transport).__name__ if self._transport else "None",
            )
            await websocket.close(code=1008, reason="SDK transport not active")
            return

        if session_id != self.session_id:
            logger.warning(
                "CLI WebSocket session mismatch: expected %s, got %s",
                _sanitize_log(self.session_id),
                _sanitize_log(session_id),
            )
            await websocket.close(code=1008, reason="Session ID mismatch")
            return

        logger.info("handle_cli_websocket: attaching CLI websocket to transport")
        await self._transport.attach_cli_websocket(websocket)

        # Block until the receive loop finishes (CLI disconnects)
        logger.info("handle_cli_websocket: waiting for CLI disconnect")
        await self._transport.wait_for_cli_disconnect()
        logger.info("handle_cli_websocket: CLI disconnected, handler returning")

    async def handle_ravn_websocket(self, websocket: WebSocket, peer_id: str) -> None:
        """Handle a Ravn WebSocket connection at /ws/ravn/{peer_id}.

        Accepts NDJSON frames from Ravn daemons and forwards them to the
        RoomBridge for translation and broadcast. Only active when room mode
        is enabled.
        """
        if self._room_bridge is None:
            logger.warning(
                "handle_ravn_websocket: room mode disabled, rejecting peer_id=%s",
                _sanitize_log(peer_id),
            )
            await websocket.close(code=1008, reason="Room mode is not enabled")
            return

        await websocket.accept()
        logger.info("handle_ravn_websocket: Ravn connected peer_id=%s", _sanitize_log(peer_id))

        # Register with peer_id as initial persona; enriched on first frame
        await self._room_bridge.register(
            peer_id=peer_id,
            persona=peer_id,
            websocket=websocket,
        )
        _registered_with_metadata = False

        try:
            while True:
                raw = await websocket.receive_text()
                for line in raw.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        frame = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning(
                            "handle_ravn_websocket: invalid JSON from peer_id=%s",
                            _sanitize_log(peer_id),
                        )
                        continue

                    # Enrich participant on first frame with persona metadata
                    if not _registered_with_metadata and (
                        frame.get("persona") or frame.get("subscribes_to")
                    ):
                        _registered_with_metadata = True
                        await self._room_bridge.register(
                            peer_id=peer_id,
                            persona=frame.get("persona", peer_id),
                            websocket=websocket,
                            display_name=frame.get("display_name", ""),
                            subscribes_to=frame.get("subscribes_to"),
                            emits=frame.get("emits"),
                            tools=frame.get("tools"),
                        )

                    await self._room_bridge.handle_ravn_frame(peer_id, frame)

        except WebSocketDisconnect:
            logger.info(
                "handle_ravn_websocket: Ravn disconnected peer_id=%s",
                _sanitize_log(peer_id),
            )
        except Exception:
            logger.exception(
                "handle_ravn_websocket: error from peer_id=%s",
                _sanitize_log(peer_id),
            )
        finally:
            await self._room_bridge.unregister(peer_id)


# Global broker instance
broker = Broker()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Attach JWT redaction filter after uvicorn has configured its loggers
    _redact_filter = _TokenRedactFilter()
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logging.getLogger(name).addFilter(_redact_filter)

    await broker.startup()
    yield
    await broker.shutdown()


app = FastAPI(
    title="Skuld Broker",
    description="WebSocket broker for Claude Code CLI",
    version="0.3.0",
    lifespan=lifespan,
)

# Add CORS middleware — browser UI (hlidskjalf) is served from a different
# origin than the per-session Skuld pod, so cross-origin requests to /api/*
# need explicit CORS headers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    return {"status": "healthy", "session_id": broker.session_id}


@app.get("/ready")
async def ready() -> dict:
    """Readiness check endpoint."""
    is_ready = broker._transport is not None
    return {"ready": is_ready, "session_id": broker.session_id}


@app.websocket("/session")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for browser chat."""
    await broker.handle_websocket(websocket)


@app.websocket("/ws/cli/{session_id}")
async def cli_websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    """WebSocket endpoint for Claude Code CLI (via --sdk-url)."""
    await broker.handle_cli_websocket(websocket, session_id)


@app.websocket("/ws/ravn/{peer_id}")
async def ravn_websocket_endpoint(websocket: WebSocket, peer_id: str) -> None:
    """WebSocket endpoint for Ravn daemon connections (room mode)."""
    await broker.handle_ravn_websocket(websocket, peer_id)


# --- Broker Log API ---


@app.get("/api/logs")
async def get_broker_logs(
    lines: int = Query(default=100, ge=1, le=2000),
    level: str = Query(default="DEBUG"),
) -> dict:
    """Return recent broker log entries from the in-memory ring buffer."""
    min_level = getattr(logging, level.upper(), logging.DEBUG)
    filtered = [entry for entry in _log_buffer if logging.getLevelName(entry["level"]) >= min_level]
    tail = list(filtered)[-lines:]
    return {
        "session_id": broker.session_id,
        "total": len(_log_buffer),
        "returned": len(tail),
        "lines": tail,
    }


@app.get("/api/logs/aggregate")
async def get_aggregate_logs(
    lines: int = Query(default=100, ge=1, le=2000),
    level: str = Query(default="DEBUG"),
    participants: str | None = Query(default=None),
    query: str = Query(default=""),
) -> dict:
    """Return interleaved broker, flock, and service logs from the session workspace."""
    requested_participants = (
        {item.strip() for item in participants.split(",") if item.strip()} if participants else None
    )
    payload = aggregate_workspace_logs(
        broker.workspace_dir,
        lines=lines,
        level=level,
        participants=requested_participants,
        query=query,
    )
    payload["session_id"] = broker.session_id
    return payload


# --- Conversation History API ---


@app.get("/api/conversation/history")
async def get_conversation_history() -> dict:
    """Return the full conversation history with activity state."""
    is_active = (
        broker._transport is not None
        and broker._transport.is_alive
        and bool(broker._pending_assistant_content or broker._pending_reasoning_text)
    )
    # Build a short activity description for the UI
    last_activity = ""
    if is_active:
        if broker._pending_assistant_content:
            # Last ~100 chars of what the assistant is writing
            last_activity = broker._pending_assistant_content[-100:]
        elif broker._pending_reasoning_text:
            last_activity = "Thinking..."

    def _serialize_turn(turn: ConversationTurn) -> dict:
        d = asdict(turn)
        # Omit optional participant fields when absent to keep JSON backward-compatible
        if d["participant_id"] is None:
            del d["participant_id"]
        if d["participant_meta"] is None:
            del d["participant_meta"]
        if d["thread_id"] is None:
            del d["thread_id"]
        # Always include visibility so clients can rely on it being present
        return d

    return {
        "turns": [_serialize_turn(t) for t in broker._conversation_turns],
        "is_active": is_active,
        "last_activity": last_activity,
    }


# --- Capabilities API ---


@app.get("/api/capabilities")
async def get_capabilities() -> dict:
    """Return transport capabilities so the frontend knows which controls to render."""
    if not broker._transport:
        raise HTTPException(status_code=503, detail="Transport not initialized")
    return asdict(broker._transport.capabilities)


# --- Service Management API ---


@app.post("/api/services", response_model=ServiceStatus)
async def create_service(request: ServiceCreateRequest) -> ServiceStatus:
    """Start a new local service."""
    if not broker.service_manager:
        raise HTTPException(status_code=503, detail="Service manager not initialized")
    return await broker.service_manager.add_service(request)


@app.get("/api/services", response_model=list[ServiceStatus])
async def list_services() -> list[ServiceStatus]:
    """List all local services."""
    if not broker.service_manager:
        raise HTTPException(status_code=503, detail="Service manager not initialized")
    return await broker.service_manager.list_services()


@app.get("/api/services/{name}", response_model=ServiceStatus)
async def get_service(name: str) -> ServiceStatus:
    """Get status of a specific service."""
    if not broker.service_manager:
        raise HTTPException(status_code=503, detail="Service manager not initialized")

    result = await broker.service_manager.get_service(name)
    if not result:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    return result


@app.delete("/api/services/{name}")
async def delete_service(name: str) -> dict:
    """Stop and remove a local service."""
    if not broker.service_manager:
        raise HTTPException(status_code=503, detail="Service manager not initialized")

    removed = await broker.service_manager.remove_service(name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    return {"status": "removed", "name": name}


@app.get("/api/services/{name}/logs")
async def get_service_logs(name: str, lines: int = 100) -> dict:
    """Get logs for a service."""
    if not broker.service_manager:
        raise HTTPException(status_code=503, detail="Service manager not initialized")

    logs = await broker.service_manager.get_logs(name, lines=lines)
    if logs is None:
        raise HTTPException(status_code=404, detail=f"No logs found for service '{name}'")
    return {"name": name, "lines": lines, "logs": logs}


@app.post("/api/services/{name}/restart", response_model=ServiceStatus)
async def restart_service(name: str) -> ServiceStatus:
    """Restart a local service."""
    if not broker.service_manager:
        raise HTTPException(status_code=503, detail="Service manager not initialized")

    result = await broker.service_manager.restart_service(name)
    if not result:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    return result


# --- Workspace File Listing API ---

# Directories that add noise and should be hidden from the file browser
_SKIP_NAMES = frozenset(
    {
        "node_modules",
        "__pycache__",
        ".git",
        "venv",
        ".venv",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
    }
)

# Dotfiles/dotdirs that *should* be shown despite the general hidden-file rule
_SHOW_HIDDEN = frozenset({".github", ".claude", ".vscode"})


def _resolve_root(root: str) -> Path:
    """Return the resolved base directory for the given root name."""
    if root == "home":
        return Path(broker._settings.home_path).resolve()
    return Path(broker.workspace_dir).resolve()


def _sanitize_relative(relative_path: str) -> str:
    """Sanitize user-supplied path: reject NUL, normalize, reject traversal/absolute."""
    if "\0" in relative_path:
        raise HTTPException(400, "Invalid path")
    normalised = os.path.normpath(relative_path)
    # Disallow absolute paths
    if os.path.isabs(normalised):
        raise HTTPException(400, "Path traversal not allowed")
    # Disallow parent-directory references that could escape the base directory
    # (for example "foo/../../etc/passwd").
    parts = [p for p in normalised.split(os.sep) if p not in (".", "")]
    if any(part == os.pardir for part in parts):
        raise HTTPException(400, "Path traversal not allowed")
    return normalised


def _check_within_base(base: str | Path, target: str | Path) -> None:
    """Raise if *target* is not under *base*.

    Uses ``os.path.realpath`` + ``str.startswith`` so that CodeQL
    recognises the guard as a path-injection sanitiser.
    """
    base_real = os.path.realpath(str(base))
    target_real = os.path.realpath(str(target))
    if target_real != base_real and not target_real.startswith(base_real + os.sep):
        raise HTTPException(400, "Path traversal not allowed")


def _validate_root(root: str) -> None:
    """Validate root parameter is exactly 'workspace' or 'home'."""
    if root not in ("workspace", "home"):
        raise HTTPException(400, "root must be 'workspace' or 'home'")


@app.get("/api/files")
async def list_files(path: str = "", root: str = "workspace") -> dict:
    """List files and directories in a session root (workspace or home)."""
    _validate_root(root)
    base = _resolve_root(root)
    sanitized = _sanitize_relative(path)
    base_real = os.path.realpath(str(base))
    target_real = os.path.realpath(os.path.join(base_real, sanitized))
    _check_within_base(base_real, target_real)
    target = Path(target_real)

    if not target.is_dir():
        raise HTTPException(404, "Directory not found")

    try:
        items = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        raise HTTPException(403, "Permission denied")

    entries: list[dict] = []
    for item in items:
        if item.name.startswith(".") and item.name not in _SHOW_HIDDEN:
            continue
        if item.name in _SKIP_NAMES:
            continue
        stat = item.stat(follow_symlinks=False)
        entries.append(
            {
                "name": item.name,
                "path": str(item.relative_to(base)),
                "type": "directory" if item.is_dir() else "file",
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
            }
        )

    return {"entries": entries}


@app.get("/api/files/download")
async def download_file(path: str, root: str = "workspace") -> FileResponse:
    """Download a single file from the session."""
    _validate_root(root)
    base = _resolve_root(root)
    sanitized = _sanitize_relative(path)
    base_real = os.path.realpath(str(base))
    target_real = os.path.realpath(os.path.join(base_real, sanitized))
    _check_within_base(base_real, target_real)

    if not os.path.isfile(target_real):
        raise HTTPException(404, "File not found")

    return FileResponse(
        path=target_real,
        filename=os.path.basename(target_real),
        media_type="application/octet-stream",
    )


@app.post("/api/files/upload")
async def upload_files(
    files: list[UploadFile],
    path: str = "",
    root: str = "workspace",
) -> dict:
    """Upload files to a target directory in the session."""
    _validate_root(root)
    base = _resolve_root(root)
    sanitized = _sanitize_relative(path)
    base_real = os.path.realpath(str(base))
    dir_real = os.path.realpath(os.path.join(base_real, sanitized))
    _check_within_base(base_real, dir_real)

    if not os.path.isdir(dir_real):
        raise HTTPException(404, "Target directory not found")

    max_size = broker._settings.max_upload_size_bytes
    uploaded: list[dict] = []
    for upload in files:
        if upload.filename is None:
            continue
        # Prevent path traversal in filenames
        safe_name = os.path.basename(upload.filename)
        dest_real = os.path.realpath(os.path.join(dir_real, safe_name))
        _check_within_base(base_real, dest_real)

        content = await upload.read()
        if len(content) > max_size:
            raise HTTPException(
                413,
                f"File {safe_name} exceeds maximum upload size ({max_size} bytes)",
            )
        with open(dest_real, "wb") as f:
            f.write(content)
        stat = os.stat(dest_real)
        uploaded.append(
            {
                "name": safe_name,
                "path": os.path.relpath(dest_real, base_real),
                "type": "file",
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
            }
        )

    return {"entries": uploaded}


class MkdirRequest(BaseModel):
    path: str
    root: str = "workspace"


@app.post("/api/files/mkdir")
async def mkdir(body: MkdirRequest) -> dict:
    """Create a directory."""
    _validate_root(body.root)
    base = _resolve_root(body.root)
    sanitized = _sanitize_relative(body.path)
    base_real = os.path.realpath(str(base))
    target_real = os.path.realpath(os.path.join(base_real, sanitized))
    _check_within_base(base_real, target_real)

    if os.path.exists(target_real):
        raise HTTPException(409, "Path already exists")

    try:
        os.makedirs(target_real, exist_ok=False)
    except PermissionError:
        raise HTTPException(403, "Permission denied")

    stat = os.stat(target_real)
    return {
        "name": os.path.basename(target_real),
        "path": os.path.relpath(target_real, base_real),
        "type": "directory",
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
    }


@app.delete("/api/files")
async def delete_file(path: str, root: str = "workspace") -> dict:
    """Delete a file or directory."""
    _validate_root(root)
    if not path:
        raise HTTPException(400, "Cannot delete root directory")
    base = _resolve_root(root)
    sanitized = _sanitize_relative(path)
    base_real = os.path.realpath(str(base))
    target_real = os.path.realpath(os.path.join(base_real, sanitized))
    _check_within_base(base_real, target_real)

    if not os.path.exists(target_real):
        raise HTTPException(404, "Path not found")

    try:
        if os.path.isdir(target_real):
            shutil.rmtree(target_real)
            return {"deleted": os.path.relpath(target_real, base_real)}
        os.unlink(target_real)
    except PermissionError:
        raise HTTPException(403, "Permission denied")

    return {"deleted": os.path.relpath(target_real, base_real)}


def _parse_diff_output(raw: str, file_path: str) -> dict:
    """Parse unified diff output into structured hunks."""
    hunks: list[dict] = []
    current_hunk: dict | None = None

    for line in raw.splitlines():
        # Hunk header: @@ -oldStart,oldCount +newStart,newCount @@
        if line.startswith("@@"):
            parts = line.split("@@")
            if len(parts) < 2:
                continue
            header = parts[1].strip()
            tokens = header.split()
            old_start, old_count = 0, 0
            new_start, new_count = 0, 0
            for token in tokens:
                if token.startswith("-"):
                    nums = token[1:].split(",")
                    old_start = int(nums[0])
                    old_count = int(nums[1]) if len(nums) > 1 else 1
                elif token.startswith("+"):
                    nums = token[1:].split(",")
                    new_start = int(nums[0])
                    new_count = int(nums[1]) if len(nums) > 1 else 1
            current_hunk = {
                "oldStart": old_start,
                "oldCount": old_count,
                "newStart": new_start,
                "newCount": new_count,
                "lines": [],
            }
            hunks.append(current_hunk)
            continue

        if current_hunk is None:
            continue

        if line.startswith("+"):
            current_hunk["lines"].append(
                {
                    "type": "add",
                    "content": line[1:],
                    "newLine": new_start,
                }
            )
            new_start += 1
        elif line.startswith("-"):
            current_hunk["lines"].append(
                {
                    "type": "remove",
                    "content": line[1:],
                    "oldLine": old_start,
                }
            )
            old_start += 1
        elif line.startswith(" "):
            current_hunk["lines"].append(
                {
                    "type": "context",
                    "content": line[1:],
                    "oldLine": old_start,
                    "newLine": new_start,
                }
            )
            old_start += 1
            new_start += 1

    return {"filePath": file_path, "hunks": hunks}


@app.get("/api/diff")
async def get_diff(
    file: str = Query(..., description="File path relative to workspace"),
    base: str = Query(
        default="last-commit",
        description="Diff base: last-commit or default-branch",
    ),
) -> dict:
    """Return parsed git diff for a single file."""
    workspace = _resolve_git_workspace_root(broker.workspace_dir)
    target = (workspace / file).resolve()

    if not str(target).startswith(str(workspace)):
        raise HTTPException(400, "Path traversal not allowed")

    if base == "last-commit":
        cmd = ["git", "diff", "HEAD", "--", file]
    elif base == "default-branch":
        cmd = ["git", "diff", "main...HEAD", "--", file]
    else:
        raise HTTPException(400, f"Invalid base: {base}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except TimeoutError:
        raise HTTPException(504, "git diff timed out")

    if proc.returncode not in (0, 1):
        detail = stderr.decode(errors="replace").strip()
        logger.warning("git diff failed for %s: %s", _sanitize_log(file), _sanitize_log(detail))
        raise HTTPException(502, f"git diff failed: {detail}")

    raw = stdout.decode(errors="replace")
    return _parse_diff_output(raw, file)


@app.get("/api/diff/files")
async def get_diff_files(
    base: str = Query(
        default="last-commit",
        description="Diff base: last-commit or default-branch",
    ),
) -> dict:
    """Return list of changed files with insertion/deletion counts."""
    workspace = _resolve_git_workspace_root(broker.workspace_dir)

    if base == "last-commit":
        cmd = ["git", "diff", "HEAD", "--numstat"]
    elif base == "default-branch":
        cmd = ["git", "diff", "main...HEAD", "--numstat"]
    else:
        raise HTTPException(400, f"Invalid base: {base}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except TimeoutError:
        raise HTTPException(504, "git diff timed out")

    if proc.returncode not in (0, 1):
        detail = stderr.decode(errors="replace").strip()
        logger.warning("git diff --numstat failed: %s", detail)
        raise HTTPException(502, f"git diff failed: {detail}")

    files = []
    for line in stdout.decode(errors="replace").strip().splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        ins_str, del_str, path = parts
        ins = int(ins_str) if ins_str != "-" else 0
        del_ = int(del_str) if del_str != "-" else 0
        files.append({"path": path, "status": "mod", "ins": ins, "del": del_})

    return {"files": files}


class _SendMessageRequest(BaseModel):
    """Request body for session-to-session messaging."""

    session_id: str
    content: str


class _RoomMessageRequest(BaseModel):
    """Request body for a human message entering a room session."""

    content: str
    source: str = "external"
    metadata: dict[str, Any] = Field(default_factory=dict)


class _DirectedRoomMessageRequest(BaseModel):
    """Request body for a directed human message entering a room session."""

    target_peer_id: str
    content: str
    source: str = "external"
    metadata: dict[str, Any] = Field(default_factory=dict)


@app.post("/api/message")
async def send_message_to_session(body: _SendMessageRequest) -> dict:
    """Send a message to another session via Volundr's WS proxy.

    The broker injects its own auth token so the calling session
    never sees credentials.
    """
    if not broker.volundr_api_url:
        raise HTTPException(503, "Volundr API URL not configured")

    client = await broker._get_http_client()
    url = f"{FORGE_SESSIONS_PATH}/{body.session_id}/messages"
    try:
        resp = await client.post(url, json={"content": body.content})
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, f"Volundr error: {e.response.text[:500]}")
    except httpx.RequestError as e:
        raise HTTPException(502, f"Failed to reach Volundr: {e}")

    return {"status": "sent", "target_session_id": body.session_id}


@app.post("/api/room/message")
async def send_room_message(body: _RoomMessageRequest) -> dict:
    """Inject a human-originated message into the active room session."""
    try:
        message_id = await broker.handle_human_room_message(
            body.content,
            source=body.source,
            metadata=body.metadata,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    return {"status": "sent", "message_id": message_id}


@app.post("/api/room/direct")
async def send_directed_room_message(body: _DirectedRoomMessageRequest) -> dict:
    """Inject a human-originated directed room message."""
    try:
        message_id = await broker.handle_directed_room_message(
            body.target_peer_id,
            body.content,
            source=body.source,
            metadata=body.metadata,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))

    return {"status": "sent", "message_id": message_id}


@app.get("/api/room/participants")
async def get_room_participants() -> dict:
    """Return the current participants in the active room session."""
    return {"participants": broker.get_room_participants()}


@app.get("/api/communication/routes")
async def get_communication_routes() -> dict:
    """Return active external communication routes for the live session."""
    return {"routes": broker.get_communication_routes()}


class _TokenRedactFilter(logging.Filter):
    """Redact access_token values from log messages to prevent JWT leaks."""

    _pattern = re.compile(r"access_token=[^\s\"&]+")

    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, "msg") and isinstance(record.msg, str):
            record.msg = self._pattern.sub("access_token=[REDACTED]", record.msg)
        return True


def main() -> None:
    """Run the broker server."""
    import uvicorn

    settings = SkuldSettings()
    logger.info("Starting Skuld broker on %s:%d", settings.host, settings.port)
    uvicorn.run(app, host=settings.host, port=settings.port, access_log=False)


if __name__ == "__main__":
    main()
