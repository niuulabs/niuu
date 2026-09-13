"""CodexWebSocketTransport — Codex app-server over WebSocket (JSON-RPC 2.0).

Spawns ``codex app-server --listen unix://PATH`` and connects to it as a
WebSocket client over the Unix socket. Communication uses JSON-RPC 2.0
(requests, responses, notifications) rather than the NDJSON protocol used by
Claude's ``--sdk-url``.

The direction is reversed compared to SdkWebSocketTransport: here Skuld is the
*client* connecting to the Codex app-server, whereas with Claude the CLI
connects back to Skuld.
"""

import asyncio
import hashlib
import json
import logging
import os
import shlex
import shutil
import sys
import tempfile
import uuid
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection, unix_connect

from niuu.adapters.cli.runtime import (
    drain_process_stream as _drain_stream,
)
from niuu.adapters.cli.runtime import (
    filter_cli_event as _filter_event,
)
from niuu.adapters.cli.runtime import (
    stop_subprocess as _stop_process,
)
from niuu.domain.reasoning import MODEL_EFFORTS, validate_effort
from niuu.domain.transcript_reducer import TOOL_ENDED_AT
from niuu.ports.cli import CLITransport, TransportCapabilities
from skuld.codex_auth import CodexAuthProviderError, CodexAuthProviderPort, HostCodexAuthProvider
from skuld.transports.codex import _map_codex_tool, resolve_codex_cli
from skuld.transports.mcp_config import build_codex_mcp_overrides
from skuld.transports.owned_codex_process import OwnedCodexProcess, OwnedProcessError
from skuld.transports.tool_shims import ensure_codex_tool_shims

logger = logging.getLogger("skuld.transport")


@dataclass(frozen=True)
class _CodexApproval:
    """An opaque native request and the exact permissions/action under review."""

    rpc_id: int | str
    method: str
    params: dict = field(default_factory=dict)


@dataclass
class _CodexTextItem:
    """One public native message, independent of other messages in its turn."""

    id: str
    index: int
    id_source: str
    thread_id: str | None = None
    turn_id: str | None = None
    phase: str | None = None
    private: bool = False
    started: bool = False
    completion_fingerprint: str | None = None
    streamed_hash: Any = field(default_factory=hashlib.sha256)
    streamed_bytes: int = 0


_MAX_WS_FRAME_BYTES = 1024 * 1024
_WS_FRAME_HEADROOM_BYTES = 8 * 1024
_DEFAULT_MAX_WS_MESSAGE_BYTES = 8 * _MAX_WS_FRAME_BYTES
_MIN_MAX_WS_MESSAGE_BYTES = _MAX_WS_FRAME_BYTES
_CODEX_APP_SERVER_SLASH_COMMANDS = [
    {
        "name": "/compact",
        "description": "Compact the Codex thread context.",
        "source": "codex-app-server",
        "method": "thread/compact/start",
        "capability": "thread.compact",
    },
    {
        "name": "/review",
        "description": "Review the current workspace changes, or review with custom instructions.",
        "source": "codex-app-server",
        "method": "review/start",
        "capability": "review.start",
    },
    {
        "name": "/goal",
        "description": "Show, set, edit, pause, resume, or clear the current Codex goal.",
        "argument_hint": "[objective | set/edit <objective> | pause | resume | clear]",
        "source": "codex-app-server",
        "method": "thread/goal",
        "capability": "thread.goal",
    },
    {
        "name": "/title",
        "description": "Legacy Forge alias for /rename, not the Codex TUI title-settings menu.",
        "source": "codex-app-server",
        "method": "thread/name/set",
        "capability": "thread.name.set",
    },
    {
        "name": "/fork",
        "description": "Fork the current Codex thread.",
        "source": "codex-app-server",
        "method": "thread/fork",
        "capability": "thread.fork",
    },
    {
        "name": "/rename",
        "description": "Rename the current Codex thread.",
        "argument_hint": "<name>",
        "source": "codex-app-server",
        "method": "thread/name/set",
        "capability": "thread.name.set",
    },
    {
        "name": "/status",
        "description": "Read native thread status and Forge's requested next-turn settings.",
        "source": "codex-app-server",
        "method": "thread/read",
        "capability": "thread.read",
    },
    {
        "name": "/skills",
        "description": "List Codex skills for this workspace (not Claude filesystem commands).",
        "source": "codex-app-server",
        "method": "skills/list",
        "capability": "skills.list",
    },
    {
        "name": "/mcp",
        "description": "List this Codex thread's MCP servers and tool counts.",
        "source": "codex-app-server",
        "method": "mcpServerStatus/list",
        "capability": "mcp.status",
    },
]
_CODEX_APP_SERVER_SLASH_BY_NAME = {
    str(command["name"]): command for command in _CODEX_APP_SERVER_SLASH_COMMANDS
}

# Monotonic request-ID generator for JSON-RPC calls.
_next_id = count(1)

# Codex models whose app-server build accepts the `ultra` reasoning effort.
# GPT-5.6 Sol introduced Ultra (subagent-parallel reasoning); GPT-6 Astra keeps
# it (its bundled Codex metadata lists low/medium/high/xhigh/max/ultra, where
# ultra = maximum reasoning with automatic task delegation). Every earlier
# Codex model tops out at `high`, so `ultra` must be clamped for them.
_ULTRA_EFFORT_MODELS = ("gpt-5.6-sol", "gpt-6-astra")


def _model_supports_ultra(model: str) -> bool:
    """True when the model's Codex build accepts the `ultra` reasoning effort."""
    return (model or "").strip().lower() in _ULTRA_EFFORT_MODELS


def _codex_effort_for_model(model: str) -> str:
    """Default reasoning effort to push a new Codex session to, by model.

    GPT-6 Astra and GPT-5.6 Sol default to the ``ultra`` effort; every other
    Codex model keeps the ``high`` default (their app-server build has no
    ultra tier).
    """
    if _model_supports_ultra(model):
        return "ultra"
    return "high"


def _normalize_codex_effort(effort: str, model: str) -> str:
    """Map launch aliases to an effort accepted by the current app-server."""
    return validate_effort(effort, MODEL_EFFORTS.get(model, ("low", "medium", "high")))


def _rpc_request(method: str, params: dict | None = None) -> tuple[int, dict]:
    """Build a JSON-RPC 2.0 request and return (id, message)."""
    rid = next(_next_id)
    msg: dict = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        msg["params"] = params
    return rid, msg


def _rpc_notification(method: str) -> dict:
    """Build a JSON-RPC 2.0 notification (no id, no response expected)."""
    return {"jsonrpc": "2.0", "method": method}


def _normalize_text_content(content: object) -> str:
    """Return a safe plain-text representation for transport control content."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text_parts: list[str] = []
        attachment_labels: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip()
            if item_type == "text":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    text_parts.append(text.strip())
                continue
            if item_type == "image":
                attachment_labels.append("image attachment")
            elif item_type:
                attachment_labels.append(f"{item_type} attachment")
        lines: list[str] = []
        if text_parts:
            lines.append("\n\n".join(text_parts))
        if attachment_labels:
            counts: dict[str, int] = {}
            for label in attachment_labels:
                counts[label] = counts.get(label, 0) + 1
            summary = ", ".join(
                f"{count} {label}" if count != 1 else f"1 {label}"
                for label, count in sorted(counts.items())
            )
            lines.append(f"[User attached {summary}. This transport forwards text only.]")
        return "\n\n".join(lines).strip()
    return str(content or "").strip()


def _encode_rpc_message(msg: dict, method: str) -> str:
    """Serialize an outbound RPC message and fail early if it exceeds WS limits."""
    encoded = json.dumps(msg)
    encoded_size = len(encoded.encode("utf-8"))
    limit = _MAX_WS_FRAME_BYTES - _WS_FRAME_HEADROOM_BYTES
    if encoded_size > limit:
        raise RuntimeError(
            f"Codex WebSocket payload too large for {method}: "
            f"{encoded_size} bytes exceeds safe limit of {limit} bytes"
        )
    return encoded


class CodexWebSocketTransport(CLITransport):
    """Long-lived Codex app-server process controlled via WebSocket JSON-RPC.

    Lifecycle:
        1. ``start()`` spawns ``codex app-server --listen unix://PATH``
        2. Skuld connects to the server as a WebSocket client
        3. JSON-RPC ``initialize`` handshake, then ``thread/start``
        4. User messages are sent via ``turn/start``
        5. Streaming events arrive as JSON-RPC notifications

    Authentication is selected by the configured ``CodexAuthProviderPort``.
    Local sessions retain Codex's host login; Kubernetes sessions use externally
    managed access-only ChatGPT tokens supplied by Völundr.
    """

    def __init__(
        self,
        workspace_dir: str,
        *,
        model: str = "o4-mini",
        skip_permissions: bool = False,
        approval_policy: str = "",
        sandbox: str = "",
        system_prompt: str = "",
        initial_prompt: str = "",
        codex_port: int = 0,
        codex_receive_max_bytes: int = _DEFAULT_MAX_WS_MESSAGE_BYTES,
        live_frame_max_bytes: int = 900 * 1024,
        mcp_servers: list[dict] | None = None,
        resume_session_id: str = "",
        reasoning_effort: str = "",
        max_ws_message_bytes: int | None = None,
        codex_auth_provider: CodexAuthProviderPort | None = None,
        session_id: str = "",
        **_kwargs: object,
    ) -> None:
        super().__init__()
        self.workspace_dir = workspace_dir
        self._model = model
        # Default reasoning effort by model when none is specified — GPT-6 Astra
        # and GPT-5.6 Sol launch at the `ultra` tier, every other Codex model at
        # `high`.
        self._reasoning_effort = reasoning_effort or _codex_effort_for_model(model)
        self._skip_permissions = skip_permissions
        self._approval_policy = approval_policy.strip()
        self._sandbox = sandbox.strip()
        self._system_prompt = system_prompt
        self._initial_prompt = initial_prompt
        self._codex_port = codex_port or _pick_free_port()
        # Preserve the earlier constructor keyword while routing both names to
        # the actual native socket receive bound.
        receive_limit = (
            max(_MIN_MAX_WS_MESSAGE_BYTES, int(max_ws_message_bytes))
            if max_ws_message_bytes is not None
            else codex_receive_max_bytes
        )
        if receive_limit <= 0:
            raise ValueError("codex_receive_max_bytes must be positive")
        self._receive_max_bytes = receive_limit
        if live_frame_max_bytes <= 0:
            raise ValueError("live_frame_max_bytes must be positive")
        self._live_frame_max_bytes = live_frame_max_bytes
        self._mcp_servers = list(mcp_servers or [])
        self._mcp_overrides = build_codex_mcp_overrides(self._mcp_servers)
        self._resume_session_id = (resume_session_id or "").strip() or None
        self._env = dict(os.environ)
        self._codex_auth_provider = codex_auth_provider or HostCodexAuthProvider()
        self._process_owner = (
            OwnedCodexProcess(
                session_id,
                workspace_dir,
                Path(self._env.get("SKULD__PERSISTENCE_MOUNT_PATH") or Path.home() / ".niuu"),
            )
            if sys.platform == "linux" and session_id
            else None
        )

        self._process: asyncio.subprocess.Process | None = None
        self._ws: ClientConnection | None = None
        self._receive_task: asyncio.Task | None = None
        self._compaction_task: asyncio.Task | None = None
        self._codex_socket_dir: str | None = None
        self._codex_socket_path: str | None = None
        self._thread_id: str | None = None
        self._current_turn_id: str | None = None
        self._last_result: dict | None = None
        self._turn_error: str | None = None
        self._last_usage: dict | None = None
        self._alive = False
        self._start_lock = asyncio.Lock()
        self._block_index: int = 0
        self._text_items: dict[str, _CodexTextItem] = {}
        self._active_text_item_id: str | None = None
        self._pending_redirects: list[str] = []
        # Steering correlation, mirroring the tmux transport. (msg_id, request_id) is recorded when
        # we fire a turn/start for a user message and popped on the matching turn/started to emit
        # a `user_consumed` event (which the broker turns into the pending→active flip). One
        # turn/start ⇒ one turn/started ⇒ one pop, so a single FIFO stays aligned.
        self._pending_prompt_correlations: list[tuple[str | None, str | None]] = []
        self._current_turn_request_id: str | None = None
        self._completed_turn_ids: set[str] = set()
        # Parallel to _pending_redirects. INVARIANT: every append/drain of _pending_redirects
        # mirrors _redirect_correlations in the SAME branch, so when queued mid-turn redirects are
        # coalesced into one replacement turn we still flip the right N bubbles.
        self._redirect_correlations: list[tuple[str | None, str | None]] = []
        self._redirect_interrupt_requested = False
        self._buffered_item_output: dict[str, list[str]] = {}
        self._active_user_prompt: str | None = None
        self._pending_context_retry_prompt: str | None = None
        self._context_compaction_active = False
        self._context_compaction_starting = False
        self._context_compaction_turn_id: str | None = None
        self._context_retry_attempts: dict[str, int] = {}

        # Pending RPC response futures keyed by request id.
        self._pending: dict[int, asyncio.Future] = {}
        self._pending_approvals: dict[str, _CodexApproval] = {}
        self._pending_user_inputs: dict[str, tuple[object, list[dict]]] = {}
        self._answer_lock = asyncio.Lock()
        self._agent_states: dict[str, str] = {}

    async def _emit(self, data: dict) -> None:
        """Carry native identity through normalization so clients can reject stale frames."""
        if data.get("type") in {
            "assistant",
            "user",
            "result",
            "content_block_start",
            "content_block_delta",
            "content_block_stop",
            "message_delta",
            "plan",
            "user_consumed",
            "agent_status",
        }:
            data = dict(data)
            if self._thread_id:
                data.setdefault("thread_id", self._thread_id)
            if self._current_turn_id:
                data.setdefault("turn_id", self._current_turn_id)
            if self._current_turn_request_id:
                data.setdefault("request_id", self._current_turn_request_id)
        await super()._emit(data)

    def _permission_thread_params(self) -> dict[str, str]:
        """Return Codex thread permission params for start/resume."""
        if self._skip_permissions:
            return {
                "approvalPolicy": "never",
                "sandbox": "danger-full-access",
            }

        params: dict[str, str] = {}
        if self._approval_policy:
            params["approvalPolicy"] = self._approval_policy
        if self._sandbox:
            params["sandbox"] = self._sandbox
        if not params:
            logger.info("Codex thread permissions are delegated to the Codex config file")
        return params

    @staticmethod
    def _ensure_codex_home(env: dict[str, str]) -> None:
        """Make the user's Codex config path explicit for spawned app-server."""
        if env.get("CODEX_HOME"):
            return

        home = env.get("HOME")
        if home:
            env["CODEX_HOME"] = str(Path(home).expanduser() / ".codex")
            return

        env["CODEX_HOME"] = str(Path.home() / ".codex")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        async with self._start_lock:
            if self.is_alive:
                return
            # A dropped socket does not end the conversation. Reuse the thread
            # selected by the previous successful handshake, even when the
            # transport was originally created without a resume_session_id.
            if self._thread_id:
                self._resume_session_id = self._thread_id
            if self._process or self._ws or self._receive_task:
                await self.stop()
            await self._start()

    async def _start(self) -> None:
        try:
            await self._spawn_app_server()
            await self._connect_ws()
            await self._handshake()
        except CodexAuthProviderError as exc:
            await self._emit({"type": "error", "error": str(exc)})
            await self.stop()
            raise
        except OwnedProcessError:
            await self.stop()
            # A competing or unverifiable writer must never trigger a fresh-thread fallback.
            raise
        except Exception as exc:
            # A different adapter cannot preserve app-server permissions,
            # capabilities or conversation identity, even on a fresh launch.
            await self.stop()
            if self._resume_session_id or self._thread_id:
                raise RuntimeError("Could not resume the existing Codex conversation") from exc
            raise RuntimeError(
                "Codex app-server startup failed; fix the configured runtime before retrying"
            ) from exc

        # On resume the prior thread's history is reloaded, so don't replay
        # the initial prompt (it was already part of that conversation).
        if self._initial_prompt and not self._resume_session_id:
            await self.send_message(self._initial_prompt)

    async def stop(self) -> None:
        self._alive = False

        await self._finalize_stranded_turn("cancelled", "Codex transport stopped")

        if self._compaction_task and not self._compaction_task.done():
            self._compaction_task.cancel()
            await asyncio.gather(self._compaction_task, return_exceptions=True)
        self._compaction_task = None

        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            await asyncio.gather(self._receive_task, return_exceptions=True)
        self._receive_task = None

        if self._ws:
            try:
                await self._ws.close()
            except Exception as exc:
                logger.debug("Error closing Codex WS: %r", exc)
            self._ws = None

        try:
            if self._process:
                with suppress(ProcessLookupError):
                    await _stop_process(self._process)
                self._process = None
        finally:
            if self._process_owner:
                self._process_owner.release()

        if self._codex_socket_dir:
            shutil.rmtree(self._codex_socket_dir, ignore_errors=True)
            self._codex_socket_dir = None
            self._codex_socket_path = None

        # Cancel any awaiting RPC futures.
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

        logger.info("CodexWebSocketTransport stopped")

    # ------------------------------------------------------------------
    # Spawn & connect
    # ------------------------------------------------------------------

    async def _spawn_app_server(self) -> None:
        if self._process_owner:
            await self._process_owner.acquire()
        if self._codex_socket_dir:
            shutil.rmtree(self._codex_socket_dir, ignore_errors=True)
        self._codex_socket_dir = tempfile.mkdtemp(prefix="skuld-codex-")
        self._codex_socket_path = os.path.join(self._codex_socket_dir, "app-server.sock")
        listen_url = f"unix://{self._codex_socket_path}"
        codex_cli = resolve_codex_cli()

        _, shim_env = ensure_codex_tool_shims(
            self.workspace_dir,
            mcp_servers=self._mcp_servers,
        )
        if shim_env:
            self._env.update(shim_env)

        cmd = [
            codex_cli,
            "app-server",
            "--listen",
            listen_url,
        ]
        for key, value in self._mcp_overrides:
            cmd.extend(["-c", f"{key}={value}"])

        env = dict(self._env)
        self._ensure_codex_home(env)
        if "OPENAI_API_KEY" not in env:
            logger.info(
                "OPENAI_API_KEY not found — relying on Codex CLI auth state for app-server access"
            )

        logger.info(
            "Spawning Codex app-server on %s with CODEX_HOME=%s",
            listen_url,
            env.get("CODEX_HOME", ""),
        )
        spawn_cmd = self._process_owner.spawn_command(cmd, env) if self._process_owner else cmd
        self._process = await asyncio.create_subprocess_exec(
            *spawn_cmd,
            cwd=self.workspace_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        if self._process_owner:
            self._process_owner.record_child(self._process.pid, cmd, spawn_cmd)
        logger.info("Codex app-server PID %s", self._process.pid)

        asyncio.create_task(_drain_stream(self._process.stdout, "codex-app-stdout"))
        asyncio.create_task(_drain_stream(self._process.stderr, "codex-app-stderr"))

    async def _connect_ws(self) -> None:
        """Connect to the Codex app-server with retries."""
        socket_path = self._codex_socket_path
        if not socket_path:
            raise RuntimeError("Codex socket path missing before connect")
        uri = "ws://localhost/"
        max_attempts = 30
        for attempt in range(1, max_attempts + 1):
            if self._process and self._process.returncode is not None:
                raise RuntimeError(f"Codex app-server exited with code {self._process.returncode}")
            try:
                self._ws = await unix_connect(
                    path=socket_path,
                    uri=uri,
                    compression=None,
                    max_size=self._receive_max_bytes,
                )
                logger.info("Connected to Codex app-server (attempt %d)", attempt)
                self._alive = True
                self._receive_task = asyncio.create_task(self._receive_loop())
                return
            except (OSError, websockets.exceptions.InvalidHandshake):
                if attempt == max_attempts:
                    raise RuntimeError(
                        f"Could not connect to Codex app-server at {socket_path} "
                        f"after {max_attempts} attempts"
                    )
                await asyncio.sleep(0.5)

    # ------------------------------------------------------------------
    # JSON-RPC helpers
    # ------------------------------------------------------------------

    async def _send_rpc(self, method: str, params: dict | None = None) -> dict:
        """Send a JSON-RPC request and wait for the response."""
        if not self._ws:
            raise RuntimeError("WebSocket not connected")

        rid, msg = _rpc_request(method, params)
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[dict] = loop.create_future()
        self._pending[rid] = fut

        try:
            payload = _encode_rpc_message(msg, method)
            await self._ws.send(payload)
            logger.debug("RPC → %s id=%d", method, rid)
            return await asyncio.wait_for(fut, timeout=60.0)
        except TimeoutError:
            raise RuntimeError(f"RPC timeout for {method} (id={rid})")
        finally:
            self._pending.pop(rid, None)
            if not fut.done():
                fut.cancel()

    async def _send_notification(self, method: str) -> None:
        """Send a JSON-RPC notification (fire-and-forget)."""
        if not self._ws:
            return
        payload = _encode_rpc_message(_rpc_notification(method), method)
        await self._ws.send(payload)

    # ------------------------------------------------------------------
    # Handshake
    # ------------------------------------------------------------------

    async def _handshake(self) -> None:
        """Perform initialize + initialized + thread/start."""
        result = await self._send_rpc(
            "initialize",
            {
                "clientInfo": {"name": "skuld", "version": "1.0.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        logger.info("Codex initialize response: %s", result)

        await self._send_notification("initialized")
        await self._authenticate_codex()

        if self._resume_session_id:
            # Imported/external session — reattach to the existing thread
            # instead of starting a fresh one.
            resume_params: dict = {
                "threadId": self._resume_session_id,
            }
            if self._model:
                resume_params["model"] = self._model
            if self._reasoning_effort:
                resume_params["config"] = {
                    "model_reasoning_effort": _normalize_codex_effort(
                        self._reasoning_effort, self._model
                    )
                }
            resume_params.update(self._permission_thread_params())

            result = await self._send_rpc("thread/resume", resume_params)
            self._thread_id = self._thread_response_id(result, expected=self._resume_session_id)
            logger.info("Codex thread resumed: %s", self._thread_id)
        else:
            thread_params: dict = {
                # Native Codex tools such as ``exec`` are Responses API items
                # that do not appear in the normal ThreadItem history.  Keep
                # raw events enabled so the transport can normalize those
                # calls into the same generic tool lifecycle as CLI/MCP tools.
                "experimentalRawEvents": True,
                # 0.154 uses legacy history by default. The removed
                # persistExtendedHistory flag no longer affects persistence;
                # paginated history creation/resume is not supported upstream.
                "cwd": self.workspace_dir,
            }
            if self._model:
                thread_params["model"] = self._model
            # Reasoning effort -> Codex config override. Codex accepts
            # minimal/low/medium/high, plus `ultra` on GPT-5.6 Sol. Map
            # extra-high/xhigh/max to the highest classic tier so an unknown
            # alias can never break the session.
            if self._reasoning_effort:
                thread_params["config"] = {
                    "model_reasoning_effort": _normalize_codex_effort(
                        self._reasoning_effort, self._model
                    )
                }
            thread_params.update(self._permission_thread_params())
            if self._system_prompt:
                # baseInstructions = role/persona ("you are a service developer…")
                # developerInstructions = per-session task instructions
                # Skuld provides a single system_prompt that combines both,
                # so we set it as baseInstructions (persistent identity).
                thread_params["baseInstructions"] = self._system_prompt

            result = await self._send_rpc("thread/start", thread_params)
            self._thread_id = self._thread_response_id(result)
            logger.info("Codex thread started: %s", self._thread_id)

        # Emit a synthetic init event so the broker knows we're ready.
        await self._emit(
            {
                "type": "system",
                "subtype": "init",
                "session_id": self._thread_id,
                "model": self._model,
                "tools": [],
            }
        )

    @staticmethod
    def _thread_response_id(result: dict, *, expected: str | None = None) -> str:
        """Bind only an identified native conversation, never an invented one."""
        thread = result.get("thread")
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        thread_id = thread_id or result.get("threadId")
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise RuntimeError("Codex thread response did not identify a conversation")
        if expected is not None and thread_id != expected:
            raise RuntimeError("Codex resumed a different conversation than requested")
        return thread_id

    async def _authenticate_codex(self) -> None:
        """Select host-managed or externally managed auth through the configured port."""
        tokens = await self._codex_auth_provider.get_tokens()
        if tokens is None:
            return
        await self._send_rpc(
            "account/login/start",
            {"type": "chatgptAuthTokens", **tokens.app_server_payload()},
        )

    # ------------------------------------------------------------------
    # Receive loop
    # ------------------------------------------------------------------

    async def _receive_loop(self) -> None:
        """Read JSON-RPC messages from the Codex WebSocket."""
        logger.info("Codex WS receive loop started")
        msg_count = 0
        failure_detail = "Codex WebSocket closed"
        last_method = "unknown frame"
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")

                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("Non-JSON from Codex WS: %.200s", raw)
                    continue
                if not isinstance(data, dict):
                    logger.warning("Ignoring non-object Codex WS frame")
                    continue

                msg_count += 1
                last_method = str(data.get("method") or data.get("type") or "RPC response")

                # JSON-RPC response (has "id" + "result" or "error")
                if "id" in data and ("result" in data or "error" in data):
                    self._resolve_pending(data)
                    continue

                # JSON-RPC notification or server request (has "method")
                if "method" in data:
                    await self._handle_server_message(data)
                    continue

                if data.get("type") == "response_item":
                    payload = data.get("payload")
                    payload_type = payload.get("type") if isinstance(payload, dict) else ""
                    logger.info("Codex response_item frame: %s", payload_type)
                    await self._handle_response_item_frame(payload)
                    continue

                logger.info(
                    "Codex WS unknown frame shape: type=%s keys=%s",
                    data.get("type"),
                    sorted(str(key) for key in data.keys()),
                )

        except websockets.exceptions.ConnectionClosed as exc:
            logger.info("Codex WS closed: %s", exc)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            failure_detail = (
                f"Codex event reader closed for {last_method} ({type(exc).__name__}): {exc}"
            )
            logger.warning("Codex WS receive error: %r", exc, exc_info=True)
        finally:
            self._alive = False
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(RuntimeError(failure_detail))
            self._pending.clear()
            await self._finalize_stranded_turn("error", failure_detail)
            logger.info("Codex WS receive loop ended after %d messages", msg_count)

    async def _finalize_stranded_turn(
        self, reason: str, detail: str, *, force: bool = False
    ) -> None:
        """Close a lost turn before any await so EOF and stop cannot double-close it."""
        active = bool(force or self._current_turn_id or self._active_user_prompt)
        turn_id = self._current_turn_id
        turn_request_id = self._current_turn_request_id
        if turn_id:
            self._completed_turn_ids.add(turn_id)
        self._current_turn_id = None
        self._active_user_prompt = None
        self._pending_context_retry_prompt = None
        self._context_compaction_active = False
        self._context_compaction_starting = False
        self._context_compaction_turn_id = None
        self._pending_prompt_correlations.clear()
        self._pending_redirects.clear()
        self._redirect_correlations.clear()
        self._redirect_interrupt_requested = False
        pending_approvals = list(self._pending_approvals)
        self._pending_approvals.clear()
        pending_questions = list(self._pending_user_inputs)
        self._pending_user_inputs.clear()
        running_agents = [
            agent_id
            for agent_id, state in self._agent_states.items()
            if state in {"running", "started", "pendingInit"}
        ]
        self._agent_states.clear()
        self._buffered_item_output.clear()
        for request_id in pending_approvals:
            await self._emit(
                {
                    "type": "permission_resolved",
                    "request_id": request_id,
                    "behavior": "resolved",
                    "metadata": {"source": "codex_app_server", "reason": "transport_closed"},
                }
            )
        for request_id in pending_questions:
            await self._emit(
                {
                    "type": "ask_user_resolved",
                    "request_id": request_id,
                    "decision": "transport_closed",
                    "accepted": False,
                }
            )
        for agent_id in running_agents:
            await self._emit(
                {
                    "type": "agent_update",
                    "action": "stopped",
                    "agent": {"id": agent_id, "kind": "subagent", "status": "failed"},
                    "metadata": {"source": "codex_app_server", "reason": detail},
                }
            )
        if not active:
            return
        self._last_result = {
            "type": "result",
            "result": "",
            "stop_reason": reason,
            "is_error": reason == "error",
            "error": detail,
            "modelUsage": self._last_usage or {},
            **({"turn_id": turn_id} if turn_id else {}),
            **({"request_id": turn_request_id} if turn_request_id else {}),
        }
        await self._emit(self._last_result)
        self._current_turn_request_id = None

    def _resolve_pending(self, data: dict) -> None:
        """Match a JSON-RPC response to its pending future."""
        rid = data.get("id")
        fut = self._pending.pop(rid, None)
        if not fut or fut.done():
            return

        if "error" in data:
            err = data["error"]
            fut.set_exception(RuntimeError(f"RPC error {err.get('code')}: {err.get('message')}"))
            return

        fut.set_result(data.get("result", {}))

    # ------------------------------------------------------------------
    # Server message dispatch
    # ------------------------------------------------------------------

    async def _handle_server_message(self, data: dict) -> None:
        """Dispatch a JSON-RPC notification or server-request from Codex."""
        method = data.get("method", "")
        params = data.get("params", {})
        logger.debug("Codex notification: %s", method)

        if method == "serverRequest/resolved" and "id" not in data:
            # RPC IDs are unique on this socket, including worker requests.
            # Resolve before filtering worker transcript notifications.
            await self._resolve_server_request(params.get("requestId"))
            return

        event_thread = params.get("threadId")
        if method == "thread/started":
            # Unlike other native notifications, thread/started nests its ID.
            # Worker starts must not replace the conversation receiving input.
            event_thread = params.get("thread", {}).get("id")
        bound_thread = self._thread_id or self._resume_session_id
        if "id" not in data and bound_thread and event_thread and event_thread != bound_thread:
            # app-server multiplexes worker threads onto the same socket. Their
            # text and turn/completed notifications must never finalize the parent.
            await self._emit(
                {
                    "type": "agent_event",
                    "agent_id": event_thread,
                    "method": method,
                    "payload": params,
                    "metadata": {"source": "codex_app_server"},
                }
            )
            return

        event_turn = params.get("turnId")
        if not event_turn and isinstance(params.get("turn"), dict):
            event_turn = params["turn"].get("id")
        if (
            "id" not in data
            and isinstance(event_turn, str)
            and method != "turn/started"
            and (
                event_turn in self._completed_turn_ids
                or (self._current_turn_id and event_turn != self._current_turn_id)
            )
        ):
            await self._emit(
                {
                    "type": "agent_event",
                    "method": method,
                    "thread_id": event_thread,
                    "turn_id": event_turn,
                    "metadata": {"reason": "stale_turn_event"},
                }
            )
            return

        # --- Server requests (need a response) ---
        if "id" in data:
            await self._handle_server_request(data)
            return

        # --- Streaming text ---
        if method in ("item/agentMessage/delta", "item/plan/delta"):
            await self._emit_text_delta(
                params.get("delta", ""),
                item_id=params.get("itemId"),
                phase=params.get("phase") or params.get("channel"),
                context={"thread_id": event_thread, "turn_id": event_turn},
            )
            return

        # --- Reasoning / thinking ---
        if method in ("item/reasoning/textDelta", "item/reasoning/summaryTextDelta"):
            delta = params.get("delta", "")
            if delta:
                await self._emit(
                    {
                        "type": "content_block_delta",
                        "delta": {"type": "thinking_delta", "thinking": delta},
                    }
                )
            return

        # --- Turn lifecycle ---
        if method == "turn/started":
            turn = params.get("turn", {})
            if turn.get("id") != self._current_turn_id:
                self._text_items.clear()
                self._active_text_item_id = None
                self._block_index = 0
            self._current_turn_id = turn.get("id")
            if self._is_context_compaction_turn(turn):
                self._context_compaction_active = True
                self._context_compaction_starting = False
                self._context_compaction_turn_id = self._current_turn_id
                logger.info("Codex context compaction turn started: %s", self._current_turn_id)
                return
            correlation = (
                self._pending_prompt_correlations.pop(0)
                if self._pending_prompt_correlations
                else (None, None)
            )
            self._current_turn_request_id = correlation[1]
            # Emit an assistant event to signal a new streaming message.
            # The browser uses this to create a new message with status 'running'.
            await self._emit(
                {
                    "type": "assistant",
                    "message": {
                        "model": self._model,
                        "content": [],
                    },
                }
            )
            # Codex just took a user prompt into its flow — the consumption signal. Pop the steer
            # that requested this turn and tell the broker (which flips it pending→active and
            # broadcasts user_active). A non-compaction turn/started always pairs with one
            # send_message → one correlation push, so a single pop stays aligned.
            await self._emit_user_consumed(*correlation)
            return

        if method == "turn/completed":
            turn = params.get("turn", {})
            completed_turn_id = turn.get("id") or self._current_turn_id
            self._current_turn_id = None

            if self._is_context_compaction_turn(turn):
                await self._complete_context_compaction()
                return

            if self._turn_has_context_window_error(turn):
                message = self._turn_error_message(turn)
                recovered = await self._recover_from_context_window_exceeded(message)
                if recovered:
                    return
                self._current_turn_id = completed_turn_id
                await self._emit({"type": "error", "error": message})
                await self._finalize_stranded_turn("error", message, force=True)
                return

            self._active_user_prompt = None
            if self._turn_error:
                self._last_result = {
                    "type": "result",
                    "stop_reason": "error",
                    "result": self._turn_error,
                    "is_error": True,
                    "modelUsage": self._last_usage or {},
                }
                await self._emit(self._last_result)
                return
            # Merge saved usage into result event.
            usage = self._last_usage or {}
            self._last_result = {
                "type": "result",
                "result": "",
                "stop_reason": {
                    "failed": "error",
                    "interrupted": "cancelled",
                }.get(turn.get("status"), "end_turn"),
                "is_error": turn.get("status") == "failed",
                "modelUsage": usage,
                **({"turn_id": completed_turn_id} if completed_turn_id else {}),
            }
            if turn.get("error"):
                self._last_result["error"] = self._turn_error_message(turn)
            await self._emit(self._last_result)
            if completed_turn_id:
                self._completed_turn_ids.add(completed_turn_id)
            self._current_turn_request_id = None
            next_prompt, redirect_correlations = self._consume_pending_redirects()
            if next_prompt is not None:
                logger.info("Codex redirect: starting replacement turn after interrupt")
                # The replacement turn carries the FIRST drained correlation, so its turn/started
                # emits user_consumed for it. The rest were coalesced into the SAME replacement turn
                # (all consumed at once), so flip them now. N queued steers ⇒ N flips.
                first_msg_id, first_request_id = (
                    redirect_correlations[0] if redirect_correlations else (None, None)
                )
                for extra_msg_id, extra_request_id in redirect_correlations[1:]:
                    await self._emit_user_consumed(extra_msg_id, extra_request_id)
                asyncio.create_task(
                    self.send_message(
                        next_prompt, msg_id=first_msg_id, request_id=first_request_id
                    ),
                    name=f"codex-redirect-{self._thread_id or 'thread'}",
                )
            return

        # --- Token usage (arrives before turn/completed) ---
        if method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage", {})
            total = usage.get("total", {})
            last = usage.get("last", {})
            model_id = self._model
            self._last_usage = {
                model_id: {
                    "inputTokens": last.get("inputTokens", 0) or total.get("inputTokens", 0),
                    "outputTokens": last.get("outputTokens", 0) or total.get("outputTokens", 0),
                    "cacheReadInputTokens": last.get("cachedInputTokens", 0)
                    or total.get("cachedInputTokens", 0),
                    "cacheCreationInputTokens": 0,
                }
            }
            # Emit message_delta so the browser can update token counters live.
            output_tokens = last.get("outputTokens", 0) or total.get("outputTokens", 0)
            if output_tokens:
                await self._emit(
                    {
                        "type": "message_delta",
                        "usage": {"output_tokens": output_tokens},
                    }
                )
            return

        if method == "turn/plan/updated":
            tasks = [
                {
                    "content": step["step"],
                    "status": "in_progress" if step["status"] == "inProgress" else step["status"],
                }
                for step in params.get("plan", [])
                if isinstance(step, dict)
                and isinstance(step.get("step"), str)
                and step.get("status")
            ]
            await self._emit(
                {
                    "type": "plan",
                    "event_type": "codex.plan",
                    "tasks": tasks,
                    "counts": {
                        "total": len(tasks),
                        **{
                            status: sum(t["status"] == status for t in tasks)
                            for status in ("pending", "in_progress", "completed")
                        },
                    },
                    "metadata": {"source": "codex_app_server"},
                }
            )
            return

        # --- Item lifecycle (tool calls, agent text blocks) ---
        if method == "item/started":
            item = params.get("item", {})
            await self._handle_item_started(
                item, text_context={"thread_id": event_thread, "turn_id": event_turn}
            )
            return

        if method == "item/completed":
            item = params.get("item", {})
            await self._handle_item_completed(
                item, text_context={"thread_id": event_thread, "turn_id": event_turn}
            )
            return

        if method == "rawResponseItem/completed":
            item = params.get("item", {})
            await self._observe_raw_response_item(item)
            return

        # --- Command / file output deltas ---
        if method == "item/commandExecution/outputDelta":
            self._buffer_item_output(params.get("itemId"), params.get("delta", ""))
            return

        if method == "item/fileChange/outputDelta":
            self._buffer_item_output(params.get("itemId"), params.get("delta", ""))
            return

        # --- Errors ---
        if method == "error":
            error = params.get("error", {})
            message = error.get("message", str(params))
            if self._is_context_window_error(error, message):
                recovered = await self._recover_from_context_window_exceeded(message)
                if recovered:
                    return
            logger.warning("Codex error notification: %s", message)
            self._turn_error = message
            await self._emit({"type": "error", "error": message})
            return

        # --- Thread lifecycle ---
        if method == "thread/started":
            thread = params.get("thread", {})
            tid = thread.get("id")
            if tid:
                self._thread_id = tid
            return

        if method in ("thread/status/changed", "thread/name/updated"):
            return  # Informational

        if method == "thread/closed":
            self._alive = False
            await self._finalize_stranded_turn("error", "Codex thread closed")
            return

        logger.debug("Codex: unhandled notification %s", method)

    async def _handle_response_item_frame(self, payload: object) -> None:
        """Handle rollout-style response_item frames emitted by app-server."""
        if not isinstance(payload, dict):
            return
        if payload.get("type") in {
            "function_call",
            "custom_tool_call",
            "custom_tool_call_output",
            "function_call_output",
        }:
            await self._observe_raw_response_item(payload)
            return

    async def _observe_raw_response_item(self, item: object) -> None:
        """Normalize completed Responses API tool items without executing them.

        Raw notifications are observational.  Codex already owns execution of
        these calls; the transport only exposes their lifecycle to Ravn so
        episodes, resident turns, and traces truthfully report tool use.
        """
        if not isinstance(item, dict):
            return

        item_type = str(item.get("type") or "")
        call_id = str(item.get("call_id") or item.get("id") or "")
        if not call_id:
            return

        if item_type in {"custom_tool_call", "function_call"}:
            name = _map_codex_tool(str(item.get("name") or ""))
            if not name:
                return
            raw_input = item.get("input")
            if item_type == "function_call":
                raw_input = item.get("arguments")
                tool_input = self._decode_function_arguments(raw_input)
            elif isinstance(raw_input, dict):
                tool_input = raw_input
            else:
                try:
                    decoded = json.loads(raw_input) if isinstance(raw_input, str) else raw_input
                except json.JSONDecodeError:
                    decoded = raw_input
                tool_input = decoded if isinstance(decoded, dict) else {"input": decoded}
            await self._emit_tool_use(call_id, name, tool_input)
            return

        if item_type not in {"custom_tool_call_output", "function_call_output"}:
            return
        output = item.get("output", "")
        if isinstance(output, str):
            output_text = output
        else:
            output_text = self._dynamic_tool_content_text(output)
            if not output_text:
                try:
                    output_text = json.dumps(output)
                except (TypeError, ValueError):
                    output_text = str(output)
        await self._emit_tool_result(call_id, output_text)

    # ------------------------------------------------------------------
    # Server requests (approval callbacks)
    # ------------------------------------------------------------------

    async def _handle_server_request(self, data: dict) -> None:
        """Handle a server-initiated request that needs a response."""
        method = data.get("method", "")
        rid = data["id"]
        params = data.get("params", {})

        if method == "account/chatgptAuthTokens/refresh":
            try:
                tokens = await self._codex_auth_provider.get_tokens(force_refresh=True)
                if tokens is None:
                    raise CodexAuthProviderError(
                        "Codex requested external token refresh while host auth is configured"
                    )
            except CodexAuthProviderError as exc:
                self._turn_error = str(exc)
                await self._emit({"type": "error", "error": str(exc)})
                await self._send_rpc_error(rid, -32001, str(exc))
                return
            await self._send_rpc_response(rid, tokens.app_server_payload())
            return

        if method == "item/tool/requestUserInput":
            request_id = f"codex-question-{rid}-{uuid.uuid4().hex}"
            questions = [
                {**question, "options": question.get("options") or []}
                for question in params.get("questions", [])
            ]
            self._pending_user_inputs[request_id] = (rid, questions)
            await self._emit(
                {
                    "type": "ask_user_question",
                    "request_id": request_id,
                    "questions": questions,
                    "metadata": {"source": "codex_app_server"},
                }
            )
            return

        approval_tools = {
            "item/commandExecution/requestApproval": "Bash",
            "execCommandApproval": "Bash",
            "item/fileChange/requestApproval": "Edit",
            "applyPatchApproval": "Edit",
            "item/permissions/requestApproval": "Permissions",
            "mcpServer/elicitation/request": "MCP",
        }
        if method in approval_tools:
            if method == "item/permissions/requestApproval" and not isinstance(
                params.get("permissions"), dict
            ):
                await self._send_rpc_error(rid, -32602, "Missing requested permissions profile")
                return
            request_id = f"codex-approval-{rid}-{uuid.uuid4().hex}"
            self._pending_approvals[request_id] = _CodexApproval(rid, method, deepcopy(params))
            input_payload = deepcopy(params)
            if method == "execCommandApproval":
                input_payload["command"] = self._display_command(params.get("command"))
            await self._emit(
                {
                    "type": "control_request",
                    "subtype": "can_use_tool",
                    "request_id": request_id,
                    "tool": approval_tools[method],
                    "input": input_payload,
                    # Elicitation gathers user input/consent, not permission to
                    # execute a tool. Sandbox approval policy cannot answer it.
                    **(
                        {"auto_approval_allowed": False}
                        if method == "mcpServer/elicitation/request"
                        else {}
                    ),
                }
            )
            return

        if method == "item/tool/call":
            task = asyncio.create_task(
                self._handle_dynamic_tool_call_request(rid, params),
                name=f"codex-dynamic-tool-{rid}",
            )
            task.add_done_callback(self._log_dynamic_tool_task_result)
            return

        # New methods have their own response/authorization semantics. Never
        # fabricate an approval for a request this adapter does not implement.
        logger.warning("Unsupported Codex server request: %s", method)
        await self._send_rpc_error(rid, -32601, f"Unsupported Codex server request: {method}")

    async def _resolve_server_request(self, rid: object) -> None:
        """Retire native controls cleared elsewhere, without inventing a decision."""
        if type(rid) not in (int, str):
            return
        # Do not acquire the answer lock in the socket reader: a raw async
        # question answer can hold it while awaiting an RPC on this very reader.
        # Retire all matching handles synchronously, before emitting callbacks.
        approvals = [key for key, value in self._pending_approvals.items() if value.rpc_id == rid]
        questions = [
            key for key, (native_id, _) in self._pending_user_inputs.items() if native_id == rid
        ]
        for request_id in approvals:
            self._pending_approvals.pop(request_id)
        for request_id in questions:
            self._pending_user_inputs.pop(request_id)
        for request_id in approvals:
            await self._emit(
                {
                    "type": "permission_resolved",
                    "request_id": request_id,
                    "behavior": "resolved",
                    "metadata": {"source": "codex_app_server"},
                }
            )
        for request_id in questions:
            await self._emit(
                {
                    "type": "ask_user_resolved",
                    "request_id": request_id,
                    "decision": "resolved_by_runtime",
                    "metadata": {"source": "codex_app_server"},
                }
            )

    async def _send_rpc_response(self, rid: int | str, result: dict) -> None:
        """Send a JSON-RPC response for a server-initiated request."""
        if not self._ws:
            raise RuntimeError("Codex WebSocket is not connected; response was not sent")
        msg = {"jsonrpc": "2.0", "id": rid, "result": result}
        await self._ws.send(json.dumps(msg))

    async def _send_rpc_error(self, rid: int | str, code: int, message: str) -> None:
        """Reject a server request without logging credential material."""
        if not self._ws:
            raise RuntimeError("Codex WebSocket is not connected; error response was not sent")
        msg = {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}
        await self._ws.send(json.dumps(msg))

    @staticmethod
    def _display_command(command: object) -> str:
        if isinstance(command, str):
            return command
        if isinstance(command, list):
            try:
                return shlex.join([str(part) for part in command])
            except Exception:
                return " ".join(str(part) for part in command)
        return str(command or "")

    def _log_dynamic_tool_task_result(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            logger.warning("Codex dynamic tool task failed", exc_info=True)

    async def _handle_dynamic_tool_call_request(self, rid: int, params: dict) -> None:
        try:
            result = await self._execute_dynamic_tool_call(params)
        except Exception as exc:
            logger.warning("Dynamic tool call failed", exc_info=True)
            result = {
                "contentItems": [
                    {
                        "type": "inputText",
                        "text": f"Tool execution error: {exc}",
                    }
                ],
                "success": False,
            }
        await self._send_rpc_response(rid, result)

    async def _execute_dynamic_tool_call(self, params: dict) -> dict:
        tool = str(params.get("tool") or "").strip()
        args = params.get("arguments")
        if not isinstance(args, dict):
            args = {}

        if tool == "shell_command":
            return await self._execute_shell_command_tool(args)

        logger.warning("Unsupported Codex dynamic tool call: %s", tool)
        return {
            "contentItems": [
                {
                    "type": "inputText",
                    "text": f"Unsupported dynamic tool: {tool or '<unknown>'}",
                }
            ],
            "success": False,
        }

    async def _execute_shell_command_tool(self, args: dict) -> dict:
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return {
                "contentItems": [{"type": "inputText", "text": "No command provided."}],
                "success": False,
            }

        cwd = args.get("workdir") or args.get("cwd") or self.workspace_dir
        if not isinstance(cwd, str) or not cwd.strip():
            cwd = self.workspace_dir

        exec_params: dict = {
            "command": ["/bin/zsh", "-lc", command],
            "cwd": cwd,
        }

        timeout_ms = self._coerce_timeout_ms(args.get("timeout_ms"))
        if timeout_ms is not None:
            exec_params["timeoutMs"] = timeout_ms

        env = args.get("env")
        if isinstance(env, dict):
            exec_params["env"] = {
                str(key): (str(value) if value is not None else None) for key, value in env.items()
            }

        result = await self._send_rpc("command/exec", exec_params)
        exit_code = result.get("exitCode", 1)
        try:
            exit_code_int = int(exit_code)
        except (TypeError, ValueError):
            exit_code_int = 1

        output = self._format_command_exec_result(exit_code_int, result)
        return {
            "contentItems": [{"type": "inputText", "text": output}],
            "success": exit_code_int == 0,
        }

    @staticmethod
    def _decode_function_arguments(raw: object) -> dict:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError:
                return {}
            if isinstance(decoded, dict):
                return decoded
        return {}

    @staticmethod
    def _coerce_timeout_ms(value: object) -> int | None:
        if value is None:
            return None
        try:
            timeout_ms = int(value)
        except (TypeError, ValueError):
            return None
        if timeout_ms <= 0:
            return None
        return timeout_ms

    @staticmethod
    def _format_command_exec_result(exit_code: int, result: dict) -> str:
        stdout = result.get("stdout")
        stderr = result.get("stderr")
        parts = [f"Exit code: {exit_code}"]
        if isinstance(stdout, str) and stdout:
            parts.append(f"stdout:\n{stdout}")
        if isinstance(stderr, str) and stderr:
            parts.append(f"stderr:\n{stderr}")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Item handling (tool calls, agent text, reasoning)
    # ------------------------------------------------------------------

    def _next_block_index(self) -> int:
        """Return and increment the content block index for this turn."""
        idx = self._block_index
        self._block_index += 1
        return idx

    async def _emit_content_block_start(self, block: dict) -> None:
        """Emit a content_block_start event with the given block descriptor."""
        idx = self._next_block_index()
        await self._emit(
            {
                "type": "content_block_start",
                "index": idx,
                "content_block": block,
            }
        )

    async def _emit_content_block_stop(self) -> None:
        """Emit a content_block_stop event."""
        await self._emit({"type": "content_block_stop"})

    def _text_item(
        self, identifier: object, *, phase: object = None, context: dict | None = None
    ) -> _CodexTextItem:
        native_id = identifier if isinstance(identifier, str) and identifier else None
        context = context or {}
        thread_id = context.get("thread_id") or self._thread_id
        turn_id = context.get("turn_id") or self._current_turn_id
        thread_id = thread_id if isinstance(thread_id, str) else None
        turn_id = turn_id if isinstance(turn_id, str) else None
        key = native_id or self._active_text_item_id
        state = self._text_items.get(key) if key else None
        if state is not None and (
            (thread_id and state.thread_id and thread_id != state.thread_id)
            or (turn_id and state.turn_id and turn_id != state.turn_id)
        ):
            state = None
        if state is None:
            state = _CodexTextItem(
                id=native_id or f"codex-text-{uuid.uuid4()}",
                index=self._next_block_index(),
                id_source="native" if native_id else "synthetic",
                thread_id=thread_id,
                turn_id=turn_id,
            )
            self._text_items[state.id] = state
        else:
            state.thread_id = state.thread_id or thread_id
            state.turn_id = state.turn_id or turn_id
        if isinstance(phase, str) and phase in {"commentary", "final_answer"}:
            state.phase = phase
        elif phase == "analysis":
            state.private = True
        return state

    def _text_context(self, state: _CodexTextItem) -> dict:
        context = {"item_id": state.id, "index": state.index}
        if state.thread_id:
            context["thread_id"] = state.thread_id
        if state.turn_id:
            context["turn_id"] = state.turn_id
        if state.phase:
            context["phase"] = state.phase
        if state.id_source == "synthetic":
            context["metadata"] = {"text_identity_source": "synthetic"}
        return context

    def _text_block(self, state: _CodexTextItem, text: str, *, complete: bool) -> dict:
        block = {
            "type": "text",
            "id": state.id,
            "text": text,
            "index": state.index,
            "id_source": state.id_source,
            "complete": complete,
        }
        if state.phase:
            block["phase"] = state.phase
        if state.thread_id:
            block["thread_id"] = state.thread_id
        if state.turn_id:
            block["turn_id"] = state.turn_id
        return block

    async def _start_text_item(self, state: _CodexTextItem) -> None:
        if state.private or state.started or state.completion_fingerprint is not None:
            return
        await self._emit(
            {
                "type": "content_block_start",
                **self._text_context(state),
                "content_block": self._text_block(state, "", complete=False),
            }
        )
        state.started = True
        self._active_text_item_id = state.id

    async def _complete_text_item(self, item: dict, *, context: dict | None = None) -> bool:
        """Reconcile authoritative text at its original anchor, once per completion.

        A full native completion can repair missed or partial deltas. Its text
        is an identity-based replacement, never another append to streamed text.
        The boolean also prevents a repeated item from reopening an async question.
        """
        state = self._text_item(
            item.get("id"), phase=item.get("phase") or item.get("channel"), context=context
        )
        text = item.get("text")
        fingerprint = hashlib.sha256(
            json.dumps([text if isinstance(text, str) else None, state.phase]).encode()
        ).hexdigest()
        if state.private or state.completion_fingerprint == fingerprint:
            return False
        first_completion = state.completion_fingerprint is None
        text_proof = {}
        if isinstance(text, str):
            await self._start_text_item(state)
            completion = {
                "type": "assistant",
                **self._text_context(state),
                "message": {
                    "model": self._model,
                    "content": [self._text_block(state, text, complete=True)],
                },
            }
            encoded_text = text.encode("utf-8")
            text_proof = {
                "text_bytes": len(encoded_text),
                "text_sha256": hashlib.sha256(encoded_text).hexdigest(),
            }
            streamed_matches = (
                len(encoded_text) == state.streamed_bytes
                and hashlib.sha256(encoded_text).digest() == state.streamed_hash.digest()
            )
            # The live channel accepts 900 KiB by default, while Codex can send
            # a 16 MiB native item. A redundant whole-message copy need not turn
            # a successfully streamed answer into a REST recovery notice. Reserve
            # room for broker envelope fields using the exact channel serializer.
            oversized = len(json.dumps(completion, ensure_ascii=False).encode("utf-8")) > max(
                1, self._live_frame_max_bytes - _WS_FRAME_HEADROOM_BYTES
            )
            if not (streamed_matches and oversized):
                # Missing/partial/mismatching text still enters the durable log
                # whole. The existing channel projection provides explicit REST
                # recovery if it cannot fit; it never truncates authoritative text.
                await self._emit(completion)
        await self._emit(
            {
                "type": "content_block_stop",
                **self._text_context(state),
                "complete": True,
                **text_proof,
            }
        )
        state.completion_fingerprint = fingerprint
        if self._active_text_item_id == state.id:
            self._active_text_item_id = None
        return first_completion

    async def _emit_tool_result(
        self, tool_use_id: str, content: str, *, is_error: bool = False
    ) -> None:
        """Emit a tool_result content block paired to a previous tool_use by id.

        The browser groups tool_use + tool_result with the same id into a
        single collapsible block (see ``groupContentBlocks`` in the UI).
        Emitting the result as a tool_result — rather than a fresh text
        block — keeps the output attached to the call instead of leaking
        into the surrounding chat stream.
        """
        if not tool_use_id:
            return
        block: dict = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
        }
        if is_error:
            block["is_error"] = True
        # D1 per-tool timing — the end stamp a hierarchical row reads for "ran in 3m 12s".
        block[TOOL_ENDED_AT] = datetime.now(UTC).isoformat()

        # DURABLE FIRST, browser second.
        #
        # This used to emit ONLY the content_block lifecycle below, which is a
        # browser-facing stream: neither the transcript reducer nor the broker's live
        # turn assembly consumes `content_block_start`. So a Codex session persisted
        # its tool CALLS with no results at all — verified on a real session, which
        # stored 2 tool_use parts, zero tool_result, and no per-tool end stamp. In
        # hierarchical mode that is a tool row whose output never arrives.
        #
        # A tool_result belongs in a **user** frame carrying the `message` envelope:
        # that is the Anthropic convention (model calls, harness answers), it is the
        # only shape the reducer harvests results from, and the broker recognises such
        # a frame via `_is_tool_result_only_user_event` so it never shows as a user
        # turn. Same fault, same fix as the Grok transport.
        await self._emit({"type": "user", "message": {"content": [block]}, "content": [block]})

        await self._emit_content_block_start(block)
        await self._emit_content_block_stop()

    async def _emit_tool_use(self, item_id: str, name: str, tool_input: dict) -> None:
        """Emit an assistant event (for broker tracking) + content_block lifecycle (for browser).

        The broker reads ``assistant.message.content`` to track artifacts.
        The browser renders via ``content_block_start/delta/stop``.
        Both are needed.
        """
        # Broker-facing: assistant event with message.content
        await self._emit(
            {
                "type": "assistant",
                "message": {
                    "model": self._model,
                    "content": [
                        {
                            "type": "tool_use",
                            "id": item_id,
                            "name": name,
                            "input": tool_input,
                        }
                    ],
                },
            }
        )
        # Browser-facing: content_block lifecycle
        await self._emit_content_block_start({"type": "tool_use", "id": item_id, "name": name})
        input_json = json.dumps(tool_input)
        await self._emit(
            {
                "type": "content_block_delta",
                "delta": {"type": "input_json_delta", "partial_json": input_json},
            }
        )

    async def _handle_item_started(self, item: dict, *, text_context: dict | None = None) -> None:
        """Emit proper content_block lifecycle events when an item starts."""
        item_type = item.get("type", "")
        item_id = item.get("id", "")

        if item_type == "collabAgentToolCall":
            name = "Agent" if item.get("tool") == "spawnAgent" else f"Agent:{item.get('tool')}"
            await self._emit_tool_use(
                item_id,
                name,
                {
                    "prompt": item.get("prompt"),
                    "receiverThreadIds": item.get("receiverThreadIds", []),
                    "senderThreadId": item.get("senderThreadId"),
                    "model": item.get("model"),
                },
            )
            return

        if item_type == "subAgentActivity":
            # The installed Codex build exposes native collaboration as an
            # instantaneous activity item, not a collabAgentToolCall pair.
            return

        if item_type == "commandExecution":
            await self._emit_tool_use(item_id, "Bash", {"command": item.get("command", "")})
            return

        if item_type == "fileChange":
            await self._emit_tool_use(item_id, "Edit", {"changes": item.get("changes", [])})
            return

        if item_type == "mcpToolCall":
            tool = item.get("tool", "")
            args = item.get("arguments", {})
            normalized = _map_codex_tool(tool)
            await self._emit_tool_use(item_id, normalized, args if isinstance(args, dict) else {})
            return

        if item_type == "dynamicToolCall":
            tool = item.get("tool", "")
            args = item.get("arguments", {})
            normalized = "Bash" if tool == "shell_command" else _map_codex_tool(tool)
            tool_input = args if isinstance(args, dict) else {"arguments": args}
            await self._emit_tool_use(item_id, normalized, tool_input)
            return

        if item_type in ("agentMessage", "plan"):
            state = self._text_item(
                item_id, phase=item.get("phase") or item.get("channel"), context=text_context
            )
            await self._start_text_item(state)
            return

        if item_type == "reasoning":
            await self._emit_content_block_start({"type": "thinking"})
            return

        if item_type == "webSearch":
            await self._emit_tool_use(item_id, "WebSearch", self._web_search_input(item))
            return

    @staticmethod
    def _web_search_input(item: dict) -> dict:
        """Keep native search/open/find arguments, including fields added by Codex."""
        action = item.get("action")
        query = item.get("query") or ""
        if not query and isinstance(action, dict):
            query = action.get("query") or action.get("url") or ""
        result = {"query": query}
        if isinstance(action, dict):
            result["action"] = dict(action)
        return result

    async def _handle_item_completed(self, item: dict, *, text_context: dict | None = None) -> None:
        """Emit content_block_stop and any final content when an item completes."""
        item_type = item.get("type", "")
        item_id = item.get("id", "")

        if item_type == "collabAgentToolCall":
            await self._emit_content_block_stop()
            await self._emit_tool_result(
                item_id,
                json.dumps(
                    {
                        "receiverThreadIds": item.get("receiverThreadIds", []),
                        "agentsStates": item.get("agentsStates", {}),
                        "status": item.get("status"),
                    }
                ),
                is_error=item.get("status") in {"failed", "interrupted"},
            )
            await self._surface_collab_agents(item)
            return

        if item_type == "subAgentActivity":
            await self._surface_subagent_activity(item)
            return

        if item_type == "commandExecution":
            # Close the tool_use block, then emit the output as a tool_result
            # paired by id so the UI groups it under the call.
            await self._emit_content_block_stop()
            output = item.get("aggregatedOutput")
            if not isinstance(output, str) or not output:
                output = self._consume_buffered_item_output(item_id)
            if not isinstance(output, str):
                output = str(output)
            exit_code = item.get("exitCode", 0)
            # ALWAYS emit a result for a finished call, even a silent success.
            # Gating on `output or exit_code != 0` meant a command that succeeded with
            # no stdout produced no tool_result at all — so the call never paired and
            # never got its D1 end stamp, and a hierarchical row for a perfectly good
            # `touch`/`mkdir` renders as a tool that never finished.
            prefix = "" if exit_code == 0 else f"[exit code {exit_code}] "
            await self._emit_tool_result(item_id, prefix + output, is_error=exit_code != 0)
            return

        if item_type in ("agentMessage", "plan"):
            if not await self._complete_text_item(item, context=text_context):
                return
            if item.get("delivery") == "async" and item.get("questions"):
                questions = [
                    {
                        "id": f"{item_id}:{index}",
                        "header": "Question",
                        "question": question["title"],
                        "options": [
                            {"label": option, "description": ""}
                            for option in question.get("options") or []
                        ],
                    }
                    for index, question in enumerate(item["questions"])
                ]
                request_id = f"codex-question-{item_id}-{uuid.uuid4().hex}"
                self._pending_user_inputs[request_id] = (None, questions)
                await self._emit(
                    {
                        "type": "ask_user_question",
                        "request_id": request_id,
                        "questions": questions,
                        "metadata": {"source": "codex_app_server", "delivery": "async"},
                    }
                )
            return

        if item_type == "reasoning":
            await self._emit_content_block_stop()
            return

        if item_type == "webSearch":
            await self._emit_content_block_stop()
            # Started notifications may be placeholders with an empty query.
            # Refresh the existing call's input by ID, without starting another
            # streaming block. Both live and durable folds upsert this tool ID.
            await self._emit(
                {
                    "type": "assistant",
                    "message": {
                        "model": self._model,
                        "content": [
                            {
                                "type": "tool_use",
                                "id": item_id,
                                "name": "WebSearch",
                                "input": self._web_search_input(item),
                            }
                        ],
                    },
                }
            )
            if item.get("results") is not None:
                # The app-server schema intentionally leaves these result objects
                # opaque: retain search hits, open-page results, and future fields.
                output = json.dumps(
                    {key: item[key] for key in ("query", "action", "results") if key in item},
                    ensure_ascii=False,
                )
            else:
                output = self._extract_item_result_text(item)
                if not output:
                    output = self._consume_buffered_item_output(item_id)
                if not output and (activity := self._web_search_activity(item)):
                    output = json.dumps(activity, ensure_ascii=False)
            await self._emit_tool_result(
                item_id, output, is_error=bool(item.get("isError") or item.get("is_error"))
            )
            return

        if item_type in ("fileChange", "mcpToolCall"):
            await self._emit_content_block_stop()
            result_text = self._extract_item_result_text(item)
            if not result_text:
                result_text = self._consume_buffered_item_output(item_id)
            is_error = bool(item.get("isError") or item.get("is_error"))
            await self._emit_tool_result(item_id, result_text, is_error=is_error)
            return

        if item_type == "dynamicToolCall":
            await self._emit_content_block_stop()
            result_text = self._dynamic_tool_content_text(item.get("contentItems"))
            await self._emit_tool_result(
                item_id,
                result_text,
                is_error=item.get("success") is False,
            )
            return

        self._buffered_item_output.pop(item_id, None)

    async def _surface_subagent_activity(self, item: dict) -> None:
        agent_id = item.get("agentThreadId")
        kind = item.get("kind")
        if not agent_id or kind not in {"started", "completed"}:
            return
        if self._agent_states.get(agent_id) == kind:
            return
        self._agent_states[agent_id] = kind
        if kind == "started":
            await self._emit_tool_use(
                item["id"], "Agent", {"agent_id": agent_id, "agent_path": item.get("agentPath")}
            )
            await self._emit_content_block_stop()
            await self._emit_tool_result(
                item["id"], json.dumps({"agent_id": agent_id, "status": "started"})
            )
        await self._emit(
            {
                "type": "agent_update",
                "event_type": "codex.agent",
                "action": "started" if kind == "started" else "stopped",
                "agent": {
                    "id": agent_id,
                    "kind": "subagent",
                    "name": item.get("agentPath") or agent_id,
                    "status": "running" if kind == "started" else "done",
                    "parent_id": self._thread_id,
                },
                "metadata": {"source": "codex_app_server"},
            }
        )

    async def _surface_collab_agents(self, item: dict) -> None:
        for agent_id in item.get("receiverThreadIds", []):
            state = item.get("agentsStates", {}).get(agent_id, {})
            status = state.get("status")
            if (
                not status
                and item.get("tool") == "spawnAgent"
                and item.get("status") == "completed"
            ):
                status = "running"
            if not status or self._agent_states.get(agent_id) == status:
                continue
            terminal = status in {"completed", "errored", "shutdown", "notFound", "interrupted"}
            failed = status in {"errored", "notFound", "interrupted"}
            self._agent_states[agent_id] = status
            await self._emit(
                {
                    "type": "agent_update",
                    "event_type": "codex.agent",
                    "action": "stopped" if terminal else "started",
                    "agent": {
                        "id": agent_id,
                        "kind": "subagent",
                        "name": agent_id,
                        "status": "failed" if failed else "done" if terminal else "running",
                        "description": item.get("prompt") or "",
                        "parent_id": item.get("senderThreadId"),
                        "result": state.get("message"),
                    },
                    "metadata": {"source": "codex_app_server"},
                }
            )

    async def _answer_user_input(self, request_id: str, answers: object) -> None:
        async with self._answer_lock:
            await self._answer_user_input_locked(request_id, answers)

    async def _answer_user_input_locked(self, request_id: str, answers: object) -> None:
        pending = self._pending_user_inputs.get(request_id)
        if pending is None:
            raise ValueError("Unknown or already answered Codex question")
        rid, questions = pending
        if not isinstance(answers, list) or not answers:
            raise ValueError("Codex question requires explicit answers")
        mapped = {}
        for question in questions:
            match = next(
                (
                    answer
                    for answer in answers
                    if isinstance(answer, dict)
                    and (
                        answer.get("question_id") == question["id"]
                        or answer.get("question") in {question["id"], question["question"]}
                    )
                ),
                None,
            )
            if match is None:
                raise ValueError(f"Missing answer for Codex question {question['id']}")
            value = match.get("answer")
            values = value if isinstance(value, list) else [value]
            if not values or not all(isinstance(v, str) and v.strip() for v in values):
                raise ValueError("Codex question answer must contain non-empty strings")
            mapped[question["id"]] = {"answers": values}
        if rid is None:
            content = "\n".join(
                f"{question['question']}: " + ", ".join(mapped[question["id"]]["answers"])
                for question in questions
            )
            if self._thread_id and self._current_turn_id:
                await self.send_control("steer", content=content)
            else:
                await self.send_message(content)
        else:
            await self._send_rpc_response(rid, {"answers": mapped})
        self._pending_user_inputs.pop(request_id, None)
        await self._emit(
            {
                "type": "ask_user_resolved",
                "request_id": request_id,
                "decision": "answered",
                "accepted": True,
                "metadata": {"source": "codex_app_server"},
            }
        )

    @staticmethod
    def _dynamic_tool_content_text(content_items: object) -> str:
        if not isinstance(content_items, list):
            return ""
        parts: list[str] = []
        for item in content_items:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        return "\n".join(parts)

    @staticmethod
    def _extract_item_result_text(item: dict) -> str:
        """Best-effort textual extraction of a completed item's result payload."""
        for key in ("result", "output", "response", "text", "summary"):
            val = item.get(key)
            if val is None:
                continue
            if isinstance(val, str):
                if val:
                    return val
                continue
            try:
                return json.dumps(val)
            except (TypeError, ValueError):
                return str(val)
        return ""

    @staticmethod
    def _web_search_activity(item: dict) -> dict:
        """Preserve the app-server's truthful search/open/find descriptor."""
        activity: dict = {}
        query = item.get("query")
        if isinstance(query, str) and query:
            activity["query"] = query
        action = item.get("action")
        if isinstance(action, dict):
            activity["action"] = action
        return activity

    def _buffer_item_output(self, item_id: object, delta: object) -> None:
        """Accumulate incremental tool output until the item completes."""
        if not isinstance(item_id, str) or not item_id:
            return
        if not isinstance(delta, str) or not delta:
            return
        self._buffered_item_output.setdefault(item_id, []).append(delta)

    def _consume_buffered_item_output(self, item_id: object) -> str:
        """Return and clear buffered incremental output for a completed item."""
        if not isinstance(item_id, str) or not item_id:
            return ""
        chunks = self._buffered_item_output.pop(item_id, None) or []
        return "".join(chunks)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _emit_text_delta(
        self,
        text: str,
        *,
        item_id: object = None,
        phase: object = None,
        context: dict | None = None,
    ) -> None:
        """Keep arbitrary chunks on their own text item, without inserting bytes."""
        if not isinstance(text, str) or not text:
            return
        state = self._text_item(item_id, phase=phase, context=context)
        if state.private or state.completion_fingerprint is not None:
            return
        await self._start_text_item(state)
        event = {
            "type": "content_block_delta",
            **self._text_context(state),
            "delta": {"type": "text_delta", "text": text},
        }
        filtered = _filter_event(event)
        if filtered:
            await self._emit(filtered)
            encoded = text.encode("utf-8")
            state.streamed_hash.update(encoded)
            state.streamed_bytes += len(encoded)

    @staticmethod
    def _is_context_window_error(error: object, message: object = "") -> bool:
        """Return true for Codex context-window exhaustion in old and new shapes."""
        if isinstance(error, dict):
            info = error.get("codexErrorInfo")
            if info == "contextWindowExceeded":
                return True
            if isinstance(info, dict) and "contextWindowExceeded" in info:
                return True
            nested = error.get("error")
            if nested is not error and CodexWebSocketTransport._is_context_window_error(nested):
                return True
            message = error.get("message", message)

        text = str(message or "").lower()
        return "context window" in text or "ran out of room" in text or "out of context" in text

    @classmethod
    def _turn_has_context_window_error(cls, turn: object) -> bool:
        if not isinstance(turn, dict):
            return False
        if turn.get("status") != "failed":
            return False
        return cls._is_context_window_error(turn.get("error"))

    @staticmethod
    def _turn_error_message(turn: object) -> str:
        if isinstance(turn, dict):
            error = turn.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                if message:
                    return str(message)
            if isinstance(error, str) and error:
                return error
        return "Codex ran out of room in the model context window."

    def _is_context_compaction_turn(self, turn: object) -> bool:
        if not isinstance(turn, dict):
            return False

        turn_id = turn.get("id")
        if self._context_compaction_turn_id and turn_id == self._context_compaction_turn_id:
            return True

        items = turn.get("items")
        if isinstance(items, list) and any(
            isinstance(item, dict) and item.get("type") == "contextCompaction" for item in items
        ):
            return True

        # Immediately after `thread/compact/start`, older app-server builds may
        # send turn/started before the compact item is present. A failed turn is
        # the original overflowed turn, not the compact turn.
        return self._context_compaction_starting and turn.get("status") != "failed"

    async def _recover_from_context_window_exceeded(self, message: str) -> bool:
        """Run native Codex compaction and retry the active prompt once."""
        prompt = self._active_user_prompt
        if not prompt or not self._thread_id:
            return False

        if self._pending_context_retry_prompt == prompt and self._context_compaction_active:
            logger.info("Codex context compaction already in progress; suppressing duplicate error")
            return True

        attempts = self._context_retry_attempts.get(prompt, 0)
        if attempts >= 1:
            logger.warning("Codex context compaction retry already attempted; surfacing error")
            self._active_user_prompt = None
            return False

        self._context_retry_attempts[prompt] = attempts + 1
        self._pending_context_retry_prompt = prompt
        self._context_compaction_starting = True
        self._context_compaction_active = True
        self._context_compaction_turn_id = None
        self._current_turn_id = None

        logger.info("Codex context window exceeded; starting native compaction: %s", message)
        await self._emit(
            {
                "type": "system",
                "subtype": "context_compaction",
                "status": "started",
                "content": "Codex context window was full; compacting and retrying the turn.",
            }
        )

        # The caller can be the sole WS reader. Waiting for an RPC here prevents
        # that same reader from consuming its response, forcing a timeout.
        self._compaction_task = asyncio.create_task(self._start_context_compaction(message))
        self._compaction_task.add_done_callback(self._log_dynamic_tool_task_result)
        return True

    async def _start_context_compaction(self, message: str) -> None:
        try:
            result = await self._send_rpc(
                "thread/compact/start",
                {"threadId": self._thread_id},
            )
        except Exception as exc:
            self._context_compaction_starting = False
            self._context_compaction_active = False
            self._pending_context_retry_prompt = None
            logger.warning("Codex context compaction failed to start", exc_info=True)
            await self._emit(
                {
                    "type": "error",
                    "error": f"{message}\n\nAutomatic context compaction failed: {exc}",
                }
            )
            await self._finalize_stranded_turn("error", "Automatic context compaction failed")
            return

        turn = result.get("turn") if isinstance(result, dict) else None
        if isinstance(turn, dict) and turn.get("id"):
            self._context_compaction_turn_id = turn["id"]

    async def _complete_context_compaction(self) -> None:
        prompt = self._pending_context_retry_prompt
        self._pending_context_retry_prompt = None
        self._context_compaction_active = False
        self._context_compaction_starting = False
        self._context_compaction_turn_id = None
        self._active_user_prompt = None

        await self._emit(
            {
                "type": "system",
                "subtype": "context_compaction",
                "status": "completed",
                "content": "Codex compacted the thread context; retrying the turn.",
            }
        )

        if prompt:
            logger.info("Codex context compaction completed; retrying user turn")
            asyncio.create_task(
                # Re-send of the SAME message — don't record a fresh correlation; the retry's
                # turn/started reuses the original steer's still-queued correlation (or pops nothing
                # if it already flipped), so the right bubble flips and no orphan is left.
                self.send_message(prompt, record_correlation=False),
                name=f"codex-context-retry-{self._thread_id or 'thread'}",
            )

    # ------------------------------------------------------------------
    # CLITransport interface
    # ------------------------------------------------------------------

    async def send_message(
        self,
        content: str,
        *,
        msg_id: str | None = None,
        request_id: str | None = None,
        record_correlation: bool = True,
    ) -> None:
        if not self._thread_id:
            raise RuntimeError("No active thread — call start() first")

        self._last_result = None
        self._turn_error = None
        self._last_usage = None
        self._active_user_prompt = content
        # Correlate this turn/start back to the originating steer; popped on the matching
        # turn/started to emit user_consumed. Appended BEFORE the RPC so the correlation is ready
        # when turn/started arrives (it can race the RPC response).
        # record_correlation=False for the context-compaction RETRY: it re-sends the SAME message,
        # so its turn/started should pop the ORIGINAL correlation still queued (if the error hit
        # before the first turn/started) or nothing (if it already fired) — NOT push a (None, None)
        # that would leave an orphan and slip every later steer by one.
        if record_correlation:
            self._pending_prompt_correlations.append((msg_id, request_id))
        params: dict = {
            "threadId": self._thread_id,
            "input": [{"type": "text", "text": content, "textElements": []}],
        }
        if self._model:
            params["model"] = self._model
        if self._reasoning_effort:
            params["effort"] = _normalize_codex_effort(self._reasoning_effort, self._model)

        logger.info("Sending turn/start to Codex (thread=%s)", self._thread_id)
        try:
            await self._send_rpc("turn/start", params)
        except Exception:
            # turn/start failed → no turn/started will arrive for it. Drop the orphan correlation so
            # a LATER turn can't pop this stale entry and mis-attribute the flip (off-by-one).
            try:
                self._pending_prompt_correlations.remove((msg_id, request_id))
            except ValueError:
                pass
            raise

    async def send_control_response(self, request_id: str, response: dict) -> None:
        async with self._answer_lock:
            await self._send_approval_response(request_id, response)

    async def _send_approval_response(self, request_id: str, response: dict) -> None:
        """Retain a failed approval send; never resolve an unknown native RPC."""
        pending = self._pending_approvals.get(request_id)
        if pending is None:
            raise ValueError("Unknown or already answered Codex approval")
        result = self._approval_result(pending, response)
        await self._send_rpc_response(pending.rpc_id, result)
        self._pending_approvals.pop(request_id, None)

    @staticmethod
    def _approval_result(pending: _CodexApproval, response: dict) -> dict:
        """Translate an explicit generic choice to this native method's schema."""
        behavior = response.get("behavior")
        if behavior not in ("allow", "allowOnce", "allowForever", "deny", "cancel"):
            raise ValueError("Codex approval requires an explicit, supported behavior")
        if response.get("updatedInput") or response.get("updatedPermissions"):
            raise ValueError(
                "Codex approvals cannot edit the pending tool input or permission rules"
            )
        allowed = behavior in ("allow", "allowOnce", "allowForever")
        if pending.method == "item/permissions/requestApproval":
            # An allow grants only the displayed request, never a client-supplied
            # wider profile. Partial grants need a distinct, validated UI contract.
            return {
                "permissions": deepcopy(pending.params["permissions"]) if allowed else {},
                "scope": "session" if behavior == "allowForever" else "turn",
            }
        if pending.method == "mcpServer/elicitation/request":
            if not allowed:
                return {"action": "cancel" if behavior == "cancel" else "decline", "content": None}
            if pending.params.get("mode") == "url":
                return {"action": "accept"}
            if "content" not in response or response["content"] is None:
                raise ValueError("Accepted Codex MCP form requires explicit user content")
            # Preserve structured input; the native MCP server validates its own
            # requested schema. Never synthesize an empty/default form submission.
            return {"action": "accept", "content": response["content"]}
        if pending.method in ("execCommandApproval", "applyPatchApproval"):
            decision: str | dict = {
                "allow": "approved",
                "allowOnce": "approved",
                "allowForever": "approved_for_session",
                "deny": {"denied": {"rejection": "User declined"}},
                "cancel": "abort",
            }[behavior]
            return {"decision": decision}
        decision = {
            "allow": "accept",
            "allowOnce": "accept",
            "allowForever": "acceptForSession",
            "deny": "decline",
            "cancel": "cancel",
        }[behavior]
        available = pending.params.get("availableDecisions")
        if isinstance(available, list) and decision not in available:
            raise ValueError("Selected Codex approval decision is not available for this request")
        return {"decision": decision}

    async def send_control(self, subtype: str, **kwargs: object) -> None:
        """Handle control messages (interrupt, set_model, etc.)."""
        if subtype == "set_effort":
            self._reasoning_effort = validate_effort(
                str(kwargs.get("effort") or ""), (await self.get_effort())["levels"]
            )
            return

        if subtype == "ask_user_answer":
            await self._answer_user_input(str(kwargs.get("request_id", "")), kwargs.get("answers"))
            return

        if subtype == "interrupt":
            if self._thread_id and self._current_turn_id:
                logger.info(
                    "Codex interrupt requested (thread=%s turn=%s)",
                    self._thread_id,
                    self._current_turn_id,
                )
                await self._send_rpc(
                    "turn/interrupt",
                    {
                        "threadId": self._thread_id,
                        "turnId": self._current_turn_id,
                    },
                )
            else:
                logger.info(
                    "Codex interrupt ignored without active turn (thread=%s turn=%s)",
                    self._thread_id,
                    self._current_turn_id,
                )
            return

        if subtype == "steer":
            content = _normalize_text_content(kwargs.get("content"))
            if not content:
                return
            raw_msg_id = kwargs.get("msg_id")
            msg_id = raw_msg_id if isinstance(raw_msg_id, str) and raw_msg_id else None
            raw_request_id = kwargs.get("request_id")
            request_id = (
                raw_request_id if isinstance(raw_request_id, str) and raw_request_id else None
            )
            thread_id, turn_id = self._thread_id, self._current_turn_id
            if not (thread_id and turn_id):
                # The turn may finish between broker routing and this call. No input
                # has crossed the provider boundary yet, so the ordinary start path
                # is safe and retains the same durable message/request identity.
                await self.send_message(content, msg_id=msg_id, request_id=request_id)
                return
            result = await self._send_rpc(
                "turn/steer",
                {
                    "threadId": thread_id,
                    "expectedTurnId": turn_id,
                    "input": [{"type": "text", "text": content, "textElements": []}],
                },
            )
            # turn/steer emits no turn/started. Correlate the exact successful RPC
            # response instead of leaving the bubble pending or contaminating the
            # FIFO of future turn/start prompts. This means appended to the active
            # turn, NOT that the model completed the user's objective.
            if msg_id:
                if not isinstance(result, dict) or result.get("turnId") != turn_id:
                    raise RuntimeError("Codex live steering acceptance was not confirmed")
                await self._emit_user_consumed(
                    msg_id, request_id, event_type="codex.turn.steer.accepted"
                )
            return

        if subtype == "redirect":
            content = _normalize_text_content(kwargs.get("content"))
            if not content:
                return
            raw_msg_id = kwargs.get("msg_id")
            msg_id = raw_msg_id if isinstance(raw_msg_id, str) and raw_msg_id else None
            raw_request_id = kwargs.get("request_id")
            request_id = (
                raw_request_id if isinstance(raw_request_id, str) and raw_request_id else None
            )

            if not (self._thread_id and self._current_turn_id):
                logger.info(
                    "Codex redirect without active turn; sending replacement prompt immediately"
                )
                await self.send_message(content, msg_id=msg_id, request_id=request_id)
                return

            # INVARIANT: append to BOTH queues in lockstep so the drained correlations line up
            # with the coalesced replacement prompt (see _consume_pending_redirects).
            self._pending_redirects.append(content)
            self._redirect_correlations.append((msg_id, request_id))
            if self._redirect_interrupt_requested:
                logger.info("Codex redirect queued while interrupt already pending")
                return

            self._redirect_interrupt_requested = True
            logger.info(
                "Codex redirect requested (thread=%s turn=%s)",
                self._thread_id,
                self._current_turn_id,
            )
            await self._send_rpc(
                "turn/interrupt",
                {
                    "threadId": self._thread_id,
                    "turnId": self._current_turn_id,
                },
            )
            return

        if subtype == "set_model":
            model = kwargs.get("model")
            if model and isinstance(model, str):
                self._model = model
            return

        if subtype == "slash_command":
            raw_command = str(kwargs.get("command") or "").strip()
            arguments = str(kwargs.get("arguments") or kwargs.get("args") or "").strip()
            command_parts = raw_command.split(maxsplit=1)
            command = command_parts[0] if command_parts else ""
            if not arguments and len(command_parts) > 1:
                arguments = command_parts[1]
            if not command:
                raise ValueError("Command is required")
            if not command.startswith("/"):
                command = f"/{command}"
            # Let the broker's correlated control-error / HTTP error path report
            # rejection. A notice followed by a successful return is a false ACK.
            await self._dispatch_slash_action(command, arguments)
            return

        logger.debug("Codex WS: unhandled control subtype=%s", subtype)

    async def _dispatch_slash_action(self, command: str, arguments: str = "") -> None:
        """Dispatch a UI slash command to its backing Codex app-server action."""
        if command not in _CODEX_APP_SERVER_SLASH_BY_NAME:
            raise ValueError(f"{command} is not supported by the Codex app-server adapter")
        if not self._thread_id:
            raise RuntimeError("No active Codex thread")

        if arguments and command in {"/compact", "/fork", "/status", "/skills", "/mcp"}:
            raise ValueError(f"{command} does not accept arguments in Forge")

        if command == "/compact":
            await self._send_rpc("thread/compact/start", {"threadId": self._thread_id})
            return

        if command == "/review":
            target: dict[str, object]
            if arguments:
                target = {"type": "custom", "instructions": arguments}
            else:
                target = {"type": "uncommittedChanges"}
            await self._send_rpc(
                "review/start",
                {
                    "threadId": self._thread_id,
                    "target": target,
                    "delivery": "inline",
                },
            )
            return

        if command == "/goal":
            parts = arguments.split(maxsplit=1)
            action = parts[0] if parts else ""
            objective = parts[1].strip() if len(parts) > 1 else ""
            if action in {"pause", "resume", "clear"}:
                if objective:
                    raise ValueError(f"Usage: /goal {action}")
                if action == "clear":
                    await self._send_rpc("thread/goal/clear", {"threadId": self._thread_id})
                else:
                    await self._send_rpc(
                        "thread/goal/set",
                        {
                            "threadId": self._thread_id,
                            "status": "paused" if action == "pause" else "active",
                        },
                    )
                outcome = {"pause": "paused", "resume": "resumed", "clear": "cleared"}[action]
                await self._emit_system_notice(f"Goal {outcome}.")
                return
            if action in {"set", "edit"} and not objective:
                raise ValueError(f"Usage: /goal {action} <objective>")
            if arguments:
                objective = objective if action in {"set", "edit"} else arguments
                goal_params = {"threadId": self._thread_id, "objective": objective}
                if action != "edit":
                    goal_params["status"] = "active"
                await self._send_rpc("thread/goal/set", goal_params)
                await self._emit_system_notice(f"Goal set: {objective}")
                return
            result = await self._send_rpc("thread/goal/get", {"threadId": self._thread_id})
            goal = result.get("goal") if isinstance(result, dict) else None
            if isinstance(goal, dict):
                objective = str(goal.get("objective") or "").strip()
                status = str(goal.get("status") or "").strip()
                if objective:
                    suffix = f" ({status})" if status else ""
                    await self._emit_system_notice(f"Current goal: {objective}{suffix}")
                    return
            await self._emit_system_notice("No Codex goal is set.")
            return

        if command in {"/title", "/rename"}:
            if not arguments:
                raise ValueError("Usage: /rename <new thread title>")
            await self._send_rpc(
                "thread/name/set",
                {"threadId": self._thread_id, "name": arguments},
            )
            await self._emit_system_notice(f"Thread renamed: {arguments}")
            return

        if command == "/status":
            result = await self._send_rpc(
                "thread/read", {"threadId": self._thread_id, "includeTurns": False}
            )
            thread = result["thread"]
            await self._emit_system_notice(
                f"Codex thread: {thread['id']}\nNative status: {json.dumps(thread.get('status'))}\n"
                f"Requested next-turn model: {self._model}\n"
                f"Requested effort: {self._reasoning_effort}"
            )
            return

        if command == "/skills":
            result = await self._send_rpc(
                "skills/list", {"cwds": [self.workspace_dir], "forceReload": True}
            )
            lines = []
            for entry in result["data"]:
                lines.extend(f"Skill discovery error: {error}" for error in entry.get("errors", []))
                for skill in entry.get("skills", []):
                    state = "enabled" if skill.get("enabled", True) else "disabled"
                    lines.append(f"${skill['name']} ({state}): {skill.get('description', '')}")
            await self._emit_system_notice(
                "\n".join(lines) or "No Codex skills reported for this workspace."
            )
            return

        if command == "/mcp":
            lines = []
            cursor = None
            seen = set()
            while True:
                params = {"threadId": self._thread_id}
                if cursor is not None:
                    params["cursor"] = cursor
                result = await self._send_rpc("mcpServerStatus/list", params)
                for server in result["data"]:
                    lines.append(
                        f"{server['name']}: {len(server.get('tools', {}))} tools; "
                        f"auth {server.get('authStatus', 'unknown')}"
                    )
                cursor = result.get("nextCursor")
                if cursor is None:
                    break
                if not isinstance(cursor, str) or cursor in seen:
                    raise RuntimeError("Codex returned an invalid or repeated MCP cursor")
                seen.add(cursor)
            await self._emit_system_notice(
                "\n".join(lines) or "No MCP servers reported for this thread."
            )
            return

        if command == "/fork":
            result = await self._send_rpc("thread/fork", {"threadId": self._thread_id})
            forked_thread = result.get("thread") if isinstance(result, dict) else None
            forked_id = ""
            if isinstance(forked_thread, dict):
                forked_id = str(forked_thread.get("id") or "")
            await self._emit_system_notice(
                f"Forked Codex thread: {forked_id}" if forked_id else "Forked Codex thread."
            )
            return

    async def _emit_system_notice(self, content: str) -> None:
        await self._emit({"type": "system", "subtype": "notice", "content": content})

    async def get_effort(self) -> dict:
        return {
            "current": self._reasoning_effort,
            "levels": list(MODEL_EFFORTS.get(self._model, ())),
            "mutable": True,
            "applies_to": "next_turn",
        }

    async def discover_slash_commands(self, *, refresh: bool = False) -> list[dict]:
        if not self._thread_id:
            return []
        return [dict(command) for command in _CODEX_APP_SERVER_SLASH_COMMANDS]

    @property
    def session_id(self) -> str | None:
        return self._thread_id

    @property
    def last_result(self) -> dict | None:
        return self._last_result

    @property
    def is_alive(self) -> bool:
        return self._alive

    @property
    def is_turn_active(self) -> bool:
        return bool(self._thread_id and self._current_turn_id)

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(
            cli_websocket=False,  # We don't expose a /ws/cli endpoint
            session_resume=True,
            interrupt=True,
            set_effort=True,
            steer=True,
            steering_mode="live",
            set_model=True,
            set_thinking_tokens=False,
            set_permission_mode=False,
            rewind_files=False,
            mcp_set_servers=False,
            permission_requests=True,
            slash_commands=True,
        )

    async def _emit_user_consumed(
        self,
        msg_id: str | None,
        request_id: str | None,
        *,
        event_type: str = "codex.turn.started",
    ) -> None:
        """Correlate started input or accepted active-turn input, not objective completion.

        No-op without a message identity (seed/retry resends carry no correlation).
        """
        if not msg_id:
            return
        event: dict = {
            "type": "user_consumed",
            "event_type": event_type,
            "msg_id": msg_id,
        }
        if request_id:
            event["request_id"] = request_id
        await self._emit(event)

    def _consume_pending_redirects(
        self,
    ) -> tuple[str | None, list[tuple[str | None, str | None]]]:
        """Drain queued redirect messages into the next replacement prompt, returning that prompt
        plus the drained steering correlations (FIFO-aligned with _pending_redirects) so the caller
        can flip every coalesced steer pending→active."""
        self._redirect_interrupt_requested = False
        if not self._pending_redirects:
            return None, []

        correlations = list(self._redirect_correlations)
        self._redirect_correlations.clear()

        if len(self._pending_redirects) == 1:
            return self._pending_redirects.pop(0), correlations

        pending = list(self._pending_redirects)
        self._pending_redirects.clear()
        lines = [
            "The user redirected the work while the previous turn was still running.",
            "Ignore the interrupted approach and follow all of these updates in order:",
        ]
        lines.extend(f"- {item}" for item in pending)
        return "\n".join(lines), correlations

    # ------------------------------------------------------------------
    # Session resume
    # ------------------------------------------------------------------

    async def resume(self, thread_id: str) -> None:
        """Resume a previous Codex thread."""
        params: dict = {
            "threadId": thread_id,
        }
        if self._model:
            params["model"] = self._model
        params.update(self._permission_thread_params())

        result = await self._send_rpc("thread/resume", params)
        self._thread_id = self._thread_response_id(result, expected=thread_id)
        logger.info("Codex thread resumed: %s", self._thread_id)

        await self._emit(
            {
                "type": "system",
                "subtype": "init",
                "session_id": self._thread_id,
                "model": self._model,
                "tools": [],
            }
        )


def _pick_free_port() -> int:
    """Pick an available TCP port."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
