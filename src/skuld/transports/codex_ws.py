"""CodexWebSocketTransport — Codex app-server over WebSocket (JSON-RPC 2.0).

Spawns ``codex app-server --listen ws://127.0.0.1:PORT`` and connects to it as a
WebSocket client over loopback. Communication uses JSON-RPC 2.0
(requests, responses, notifications) rather than the NDJSON protocol used by
Claude's ``--sdk-url``.

The direction is reversed compared to SdkWebSocketTransport: here Skuld is the
*client* connecting to the Codex app-server, whereas with Claude the CLI
connects back to Skuld.
"""

import asyncio
import json
import logging
import os
import shlex
from itertools import count
from pathlib import Path

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as ws_connect

from niuu.adapters.cli.runtime import (
    drain_process_stream as _drain_stream,
)
from niuu.adapters.cli.runtime import (
    filter_cli_event as _filter_event,
)
from niuu.adapters.cli.runtime import (
    stop_subprocess as _stop_process,
)
from niuu.ports.cli import CLITransport, TransportCapabilities
from skuld.codex_auth import CodexAuthProviderError, CodexAuthProviderPort, HostCodexAuthProvider
from skuld.transports.codex import (
    CodexSubprocessTransport,
    _map_codex_tool,
    resolve_codex_cli,
)
from skuld.transports.mcp_config import build_codex_mcp_overrides
from skuld.transports.tool_shims import ensure_codex_tool_shims

logger = logging.getLogger("skuld.transport")

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
        "description": "Show or set the current Codex thread goal.",
        "source": "codex-app-server",
        "method": "thread/goal",
        "capability": "thread.goal",
    },
    {
        "name": "/title",
        "description": "Rename the current Codex thread.",
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
]
_CODEX_APP_SERVER_SLASH_BY_NAME = {
    str(command["name"]): command for command in _CODEX_APP_SERVER_SLASH_COMMANDS
}

# Monotonic request-ID generator for JSON-RPC calls.
_next_id = count(1)

# Codex models whose app-server build accepts the `ultra` reasoning effort.
# Sol supports it, but Völundr deliberately defaults every model to `high`.
_ULTRA_EFFORT_MODELS = ("gpt-5.6-sol",)


def _model_supports_ultra(model: str) -> bool:
    """True when the model's Codex build accepts the `ultra` reasoning effort."""
    return (model or "").strip().lower() in _ULTRA_EFFORT_MODELS


def _codex_effort_for_model(model: str) -> str:
    """Default reasoning effort to push a new Codex session to, by model.

    Völundr defaults all Codex models, including GPT-5.6 Sol, to ``high``.
    """
    return "high"


def _normalize_codex_effort(effort: str, model: str) -> str:
    """Map launch aliases to an effort accepted by the current app-server."""
    mapped = {
        "minimal": "minimal",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "extra-high": "high",
        "extra_high": "high",
        "xhigh": "high",
        "max": "high",
        "ultra": "ultra",
    }.get(effort.strip().lower(), "high")
    if mapped == "ultra" and not _model_supports_ultra(model):
        return "high"
    return mapped


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
        1. ``start()`` spawns ``codex app-server --listen ws://127.0.0.1:PORT``
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
        mcp_servers: list[dict] | None = None,
        resume_session_id: str = "",
        reasoning_effort: str = "",
        max_ws_message_bytes: int = _DEFAULT_MAX_WS_MESSAGE_BYTES,
        codex_auth_provider: CodexAuthProviderPort | None = None,
        **_kwargs: object,
    ) -> None:
        super().__init__()
        self.workspace_dir = workspace_dir
        self._model = model
        # Default every Codex model to high unless launch configuration overrides it.
        self._reasoning_effort = reasoning_effort or _codex_effort_for_model(model)
        self._skip_permissions = skip_permissions
        self._approval_policy = approval_policy.strip()
        self._sandbox = sandbox.strip()
        self._system_prompt = system_prompt
        self._initial_prompt = initial_prompt
        self._codex_port = codex_port or _pick_free_port()
        self._mcp_servers = list(mcp_servers or [])
        self._mcp_overrides = build_codex_mcp_overrides(self._mcp_servers)
        self._resume_session_id = (resume_session_id or "").strip() or None
        self._max_ws_message_bytes = max(_MIN_MAX_WS_MESSAGE_BYTES, int(max_ws_message_bytes))
        self._env = dict(os.environ)
        self._codex_auth_provider = codex_auth_provider or HostCodexAuthProvider()

        self._process: asyncio.subprocess.Process | None = None
        self._ws: ClientConnection | None = None
        self._receive_task: asyncio.Task | None = None
        self._fallback_transport: CodexSubprocessTransport | None = None
        self._thread_id: str | None = None
        self._current_turn_id: str | None = None
        self._last_result: dict | None = None
        self._turn_error: str | None = None
        self._last_usage: dict | None = None
        self._alive = False
        self._block_index: int = 0
        self._pending_redirects: list[str] = []
        # Steering correlation, mirroring the tmux transport. (msg_id, request_id) is recorded when
        # we fire a turn/start for a user message and popped on the matching turn/started to emit
        # a `user_consumed` event (which the broker turns into the pending→active flip). One
        # turn/start ⇒ one turn/started ⇒ one pop, so a single FIFO stays aligned.
        self._pending_prompt_correlations: list[tuple[str | None, str | None]] = []
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
        # Pending approval RPC ids keyed by string request_id. The second value
        # identifies which app-server approval response shape the request needs.
        self._pending_approvals: dict[str, tuple[int, str] | int] = {}

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
        try:
            await self._spawn_app_server()
            await self._connect_ws()
            await self._handshake()
        except CodexAuthProviderError as exc:
            await self._emit({"type": "error", "error": str(exc)})
            await self.stop()
            raise
        except Exception as exc:
            await self._start_fallback_transport(exc)
            return

        # On resume the prior thread's history is reloaded, so don't replay
        # the initial prompt (it was already part of that conversation).
        if self._initial_prompt and not self._resume_session_id:
            await self.send_message(self._initial_prompt)

    async def stop(self) -> None:
        if self._fallback_transport is not None:
            await self._fallback_transport.stop()
            self._fallback_transport = None

        self._alive = False

        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()

        if self._ws:
            try:
                await self._ws.close()
            except Exception as exc:
                logger.debug("Error closing Codex WS: %r", exc)
            self._ws = None

        if self._process:
            await _stop_process(self._process)
            self._process = None

        # Cancel any awaiting RPC futures.
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

        logger.info("CodexWebSocketTransport stopped")

    def on_event(self, callback) -> None:  # type: ignore[override]
        super().on_event(callback)
        if self._fallback_transport is not None:
            self._fallback_transport.on_event(callback)

    async def _start_fallback_transport(self, cause: Exception) -> None:
        logger.warning(
            "Codex app-server transport unavailable; falling back to subprocess transport: %s",
            cause,
            exc_info=True,
        )
        await self.stop()
        fallback = CodexSubprocessTransport(
            workspace_dir=self.workspace_dir,
            model=self._model,
            mcp_servers=self._mcp_servers,
        )
        fallback.on_event(self.event_callback)
        self._fallback_transport = fallback
        await fallback.start()
        if self._initial_prompt:
            await fallback.send_message(self._initial_prompt)

    # ------------------------------------------------------------------
    # Spawn & connect
    # ------------------------------------------------------------------

    async def _spawn_app_server(self) -> None:
        listen_url = f"ws://127.0.0.1:{self._codex_port}"
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
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.workspace_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        logger.info("Codex app-server PID %s", self._process.pid)

        asyncio.create_task(_drain_stream(self._process.stdout, "codex-app-stdout"))
        asyncio.create_task(_drain_stream(self._process.stderr, "codex-app-stderr"))

    async def _connect_ws(self) -> None:
        """Connect to the Codex app-server with retries."""
        uri = f"ws://127.0.0.1:{self._codex_port}"
        max_attempts = 30
        for attempt in range(1, max_attempts + 1):
            if self._process and self._process.returncode is not None:
                raise RuntimeError(f"Codex app-server exited with code {self._process.returncode}")
            try:
                self._ws = await ws_connect(
                    uri,
                    compression=None,
                    max_size=self._max_ws_message_bytes,
                    proxy=None,
                )
                logger.info("Connected to Codex app-server (attempt %d)", attempt)
                self._alive = True
                self._receive_task = asyncio.create_task(self._receive_loop())
                return
            except (OSError, websockets.exceptions.InvalidHandshake):
                if attempt == max_attempts:
                    raise RuntimeError(
                        f"Could not connect to Codex app-server at {uri} "
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
        except Exception:
            self._pending.pop(rid, None)
            raise

        await self._ws.send(payload)
        logger.debug("RPC → %s id=%d", method, rid)

        try:
            return await asyncio.wait_for(fut, timeout=60.0)
        except TimeoutError:
            self._pending.pop(rid, None)
            raise RuntimeError(f"RPC timeout for {method} (id={rid})")

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
                "persistExtendedHistory": True,
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
            thread = result.get("thread", {})
            self._thread_id = thread.get("id") or self._resume_session_id
            logger.info("Codex thread resumed: %s", self._thread_id)
        else:
            thread_params: dict = {
                # Native Codex tools such as ``exec`` are Responses API items
                # that do not appear in the normal ThreadItem history.  Keep
                # raw events enabled so the transport can normalize those
                # calls into the same generic tool lifecycle as CLI/MCP tools.
                "experimentalRawEvents": True,
                "persistExtendedHistory": True,
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
            # The response triggers a thread/started notification with the thread info.
            # But the RPC response itself may contain the thread_id.
            thread = result.get("thread", {})
            self._thread_id = thread.get("id") or result.get("threadId")
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
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")

                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("Non-JSON from Codex WS: %.200s", raw)
                    continue

                msg_count += 1

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
            logger.warning("Codex WS receive error: %r", exc, exc_info=True)
        finally:
            self._alive = False
            logger.info("Codex WS receive loop ended after %d messages", msg_count)

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

        # --- Server requests (need a response) ---
        if "id" in data:
            await self._handle_server_request(data)
            return

        # --- Streaming text ---
        if method == "item/agentMessage/delta":
            await self._emit_text_delta(params.get("delta", ""))
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
            self._current_turn_id = turn.get("id")
            self._block_index = 0
            if self._is_context_compaction_turn(turn):
                self._context_compaction_active = True
                self._context_compaction_starting = False
                self._context_compaction_turn_id = self._current_turn_id
                logger.info("Codex context compaction turn started: %s", self._current_turn_id)
                return
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
            if self._pending_prompt_correlations:
                msg_id, request_id = self._pending_prompt_correlations.pop(0)
                await self._emit_user_consumed(msg_id, request_id)
            return

        if method == "turn/completed":
            turn = params.get("turn", {})
            self._current_turn_id = None

            if self._is_context_compaction_turn(turn):
                await self._complete_context_compaction()
                return

            if self._turn_has_context_window_error(turn):
                message = self._turn_error_message(turn)
                recovered = await self._recover_from_context_window_exceeded(message)
                if recovered:
                    return
                await self._emit({"type": "error", "error": message})
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
                "stop_reason": "end_turn",
                "modelUsage": usage,
            }
            await self._emit(self._last_result)
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

        # --- Item lifecycle (tool calls, agent text blocks) ---
        if method == "item/started":
            item = params.get("item", {})
            await self._handle_item_started(item)
            return

        if method == "item/completed":
            item = params.get("item", {})
            await self._handle_item_completed(item)
            return

        if method == "rawResponseItem/completed":
            item = params.get("item", {})
            if (
                isinstance(item, dict)
                and item.get("type") == "function_call"
                and item.get("name") == "shell_command"
            ):
                task = asyncio.create_task(
                    self._handle_raw_function_call_item(item),
                    name=f"codex-raw-function-{item.get('call_id') or 'shell_command'}",
                )
                task.add_done_callback(self._log_dynamic_tool_task_result)
                return
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
            return

        logger.debug("Codex: unhandled notification %s", method)

    async def _handle_response_item_frame(self, payload: object) -> None:
        """Handle rollout-style response_item frames emitted by app-server."""
        if not isinstance(payload, dict):
            return
        if payload.get("type") in {
            "custom_tool_call",
            "custom_tool_call_output",
            "function_call_output",
        }:
            await self._observe_raw_response_item(payload)
            return
        if payload.get("type") == "function_call":
            logger.info(
                "Codex response_item function_call: name=%s call_id=%s",
                payload.get("name"),
                payload.get("call_id"),
            )
            task_name = (
                f"codex-response-function-{payload.get('call_id') or payload.get('name') or 'call'}"
            )
            task = asyncio.create_task(
                self._handle_raw_function_call_item(payload),
                name=task_name,
            )
            task.add_done_callback(self._log_dynamic_tool_task_result)
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

        if method == "item/commandExecution/requestApproval":
            request_id = str(rid)
            command = params.get("command", "")
            await self._emit(
                {
                    "type": "control_request",
                    "subtype": "can_use_tool",
                    "request_id": request_id,
                    "tool": "Bash",
                    "input": {"command": command},
                }
            )
            self._pending_approvals[request_id] = (rid, "command_execution")
            return

        if method == "execCommandApproval":
            request_id = str(rid)
            command = self._display_command(params.get("command"))
            await self._emit(
                {
                    "type": "control_request",
                    "subtype": "can_use_tool",
                    "request_id": request_id,
                    "tool": "Bash",
                    "input": {
                        "command": command,
                        "cwd": params.get("cwd"),
                        "reason": params.get("reason"),
                    },
                }
            )
            self._pending_approvals[request_id] = (rid, "exec_command")
            return

        if method in (
            "item/fileChange/requestApproval",
            "item/permissions/requestApproval",
            "applyPatchApproval",
        ):
            request_id = str(rid)
            await self._emit(
                {
                    "type": "control_request",
                    "subtype": "can_use_tool",
                    "request_id": request_id,
                    "tool": "Edit",
                    "input": params,
                }
            )
            self._pending_approvals[request_id] = (rid, "command_execution")
            return

        if method == "item/tool/call":
            task = asyncio.create_task(
                self._handle_dynamic_tool_call_request(rid, params),
                name=f"codex-dynamic-tool-{rid}",
            )
            task.add_done_callback(self._log_dynamic_tool_task_result)
            return

        if method == "mcpServer/elicitation/request":
            if self._skip_permissions or self._approval_policy == "never":
                await self._send_rpc_response(rid, {"action": "accept", "content": {}})
                return
            request_id = str(rid)
            await self._emit(
                {
                    "type": "control_request",
                    "subtype": "can_use_tool",
                    "request_id": request_id,
                    "tool": "MCP",
                    "input": params,
                }
            )
            self._pending_approvals[request_id] = (rid, "mcp_elicitation")
            return

        # Default: auto-approve unknown requests
        logger.debug("Auto-approving Codex server request: %s", method)
        await self._send_rpc_response(rid, {"decision": "accept"})

    async def _send_rpc_response(self, rid: int, result: dict) -> None:
        """Send a JSON-RPC response for a server-initiated request."""
        if not self._ws:
            return
        msg = {"jsonrpc": "2.0", "id": rid, "result": result}
        await self._ws.send(json.dumps(msg))

    async def _send_rpc_error(self, rid: int, code: int, message: str) -> None:
        """Reject a server request without logging credential material."""
        if not self._ws:
            return
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

    async def _handle_raw_function_call_item(self, item: dict) -> None:
        """Execute Responses function_call items surfaced by newer app-server builds."""
        call_id = str(item.get("call_id") or "")
        name = str(item.get("name") or "").strip()
        args = self._decode_function_arguments(item.get("arguments"))
        ui_tool_id = call_id or f"function-{next(self._ids)}"
        logger.info("Handling Codex raw function_call name=%s call_id=%s", name, call_id)

        if name == "shell_command":
            tool_input = args if isinstance(args, dict) else {}
            await self._emit_tool_use(ui_tool_id, "Bash", tool_input)
            result = await self._execute_shell_command_tool(tool_input)
        else:
            logger.warning("Unsupported Codex function call: %s", name or "<unknown>")
            result = {
                "contentItems": [
                    {
                        "type": "inputText",
                        "text": f"Unsupported function call: {name or '<unknown>'}",
                    }
                ],
                "success": False,
            }

        output_text = self._dynamic_tool_content_text(result.get("contentItems"))
        if not output_text:
            output_text = ""

        if call_id and self._thread_id:
            await self._send_rpc(
                "thread/inject_items",
                {
                    "threadId": self._thread_id,
                    "items": [
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": output_text,
                        }
                    ],
                },
            )
        elif not self._thread_id:
            logger.warning("Cannot inject Codex function_call_output without thread id")

        await self._emit_content_block_stop()
        await self._emit_tool_result(
            ui_tool_id,
            output_text,
            is_error=result.get("success") is False,
        )

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

    async def _handle_item_started(self, item: dict) -> None:
        """Emit proper content_block lifecycle events when an item starts."""
        item_type = item.get("type", "")
        item_id = item.get("id", "")

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

        if item_type == "agentMessage":
            # Start a text content block — deltas will follow via agentMessage/delta.
            block = {"type": "text", "id": item_id}
            phase = item.get("phase")
            if isinstance(phase, str) and phase:
                block["phase"] = phase
            await self._emit_content_block_start(block)
            return

        if item_type == "reasoning":
            await self._emit_content_block_start({"type": "thinking"})
            return

        if item_type == "webSearch":
            await self._emit_tool_use(
                item_id,
                "WebSearch",
                self._web_search_activity(item),
            )
            return

    async def _handle_item_completed(self, item: dict) -> None:
        """Emit content_block_stop and any final content when an item completes."""
        item_type = item.get("type", "")
        item_id = item.get("id", "")

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
            prefix = "" if exit_code == 0 else f"[exit code {exit_code}] "
            await self._emit_tool_result(item_id, prefix + output, is_error=exit_code != 0)
            return

        if item_type == "agentMessage":
            # The full text was already streamed via item/agentMessage/delta
            # notifications, so just close the block without re-emitting.
            await self._emit_content_block_stop()
            return

        if item_type == "reasoning":
            await self._emit_content_block_stop()
            return

        if item_type in ("fileChange", "mcpToolCall", "webSearch"):
            await self._emit_content_block_stop()
            result_text = self._extract_item_result_text(item)
            if not result_text:
                result_text = self._consume_buffered_item_output(item_id)
            if item_type == "webSearch" and not result_text:
                activity = self._web_search_activity(item)
                if activity:
                    result_text = json.dumps(activity)
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

    async def _emit_text_delta(self, text: str) -> None:
        """Emit a text delta event, filtering empties."""
        if not text:
            return
        event = {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": text},
        }
        filtered = _filter_event(event)
        if filtered:
            await self._emit(filtered)

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
            return True

        turn = result.get("turn") if isinstance(result, dict) else None
        if isinstance(turn, dict) and turn.get("id"):
            self._context_compaction_turn_id = turn["id"]
        return True

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
        if self._fallback_transport is not None:
            await self._fallback_transport.send_message(
                content, msg_id=msg_id, request_id=request_id
            )
            return

        if not self._thread_id:
            raise RuntimeError("No active thread — call start() first")

        self._last_result = None
        self._turn_error = None
        self._last_usage = None
        self._block_index = 0
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
        if self._fallback_transport is not None:
            await self._fallback_transport.send_control_response(request_id, response)
            return

        """Respond to a Codex approval request."""
        pending = self._pending_approvals.pop(request_id, None)
        if pending is None:
            logger.warning("No pending approval for request_id=%s", request_id)
            return
        if isinstance(pending, tuple):
            rid, approval_kind = pending
        else:
            rid, approval_kind = pending, "command_execution"

        # Map broker permission response to Codex approval decision.
        # New app-server exec approvals use review decisions; older item-level
        # approvals use camelCase enum variants.
        behavior = response.get("behavior", "allow")
        if approval_kind == "exec_command":
            decision = "approved_for_session" if behavior == "allowForever" else "approved"
            if behavior not in ("allow", "allowForever"):
                decision = "denied"
            result = {"decision": decision}
        elif approval_kind == "mcp_elicitation":
            result = {"action": "accept" if behavior in ("allow", "allowForever") else "decline"}
            if result["action"] == "accept":
                content = response.get("content")
                result["content"] = content if isinstance(content, dict) else {}
        else:
            decision = "accept" if behavior in ("allow", "allowForever") else "decline"
            result = {"decision": decision}
        await self._send_rpc_response(rid, result)

    async def send_control(self, subtype: str, **kwargs: object) -> None:
        if self._fallback_transport is not None:
            await self._fallback_transport.send_control(subtype, **kwargs)
            return

        """Handle control messages (interrupt, set_model, etc.)."""
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
            if content and self._thread_id and self._current_turn_id:
                logger.info(
                    "Codex steer requested (thread=%s turn=%s)",
                    self._thread_id,
                    self._current_turn_id,
                )
                result = await self._send_rpc(
                    "turn/steer",
                    {
                        "threadId": self._thread_id,
                        "expectedTurnId": self._current_turn_id,
                        "input": [{"type": "text", "text": content, "textElements": []}],
                    },
                )
                logger.info("Codex steer response: %s", result)
            elif content:
                logger.info(
                    "Codex steer ignored without active turn (thread=%s turn=%s)",
                    self._thread_id,
                    self._current_turn_id,
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
                return
            if not command.startswith("/"):
                command = f"/{command}"
            try:
                await self._dispatch_slash_action(command, arguments)
            except Exception as exc:
                logger.warning("Codex slash command failed: %s", command, exc_info=True)
                await self._emit_system_notice(f"{command} failed: {exc}")
            return

        logger.debug("Codex WS: unhandled control subtype=%s", subtype)

    async def _dispatch_slash_action(self, command: str, arguments: str = "") -> None:
        """Dispatch a UI slash command to its backing Codex app-server action."""
        if command not in _CODEX_APP_SERVER_SLASH_BY_NAME:
            logger.debug("Codex WS: unknown slash command=%s", command)
            return
        if not self._thread_id:
            logger.info("Codex %s ignored without an active thread", command)
            return

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
            if arguments:
                await self._send_rpc(
                    "thread/goal/set",
                    {
                        "threadId": self._thread_id,
                        "objective": arguments,
                        "status": "active",
                    },
                )
                await self._emit_system_notice(f"Goal set: {arguments}")
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

        if command == "/title":
            if not arguments:
                await self._emit_system_notice("Usage: /title <new thread title>")
                return
            await self._send_rpc(
                "thread/name/set",
                {"threadId": self._thread_id, "name": arguments},
            )
            await self._emit_system_notice(f"Thread renamed: {arguments}")
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

    async def discover_slash_commands(self, *, refresh: bool = False) -> list[dict]:
        if self._fallback_transport is not None:
            return await self._fallback_transport.discover_slash_commands(refresh=refresh)
        if not self._thread_id:
            return []
        return [dict(command) for command in _CODEX_APP_SERVER_SLASH_COMMANDS]

    @property
    def session_id(self) -> str | None:
        if self._fallback_transport is not None:
            return self._fallback_transport.session_id
        return self._thread_id

    @property
    def last_result(self) -> dict | None:
        if self._fallback_transport is not None:
            return self._fallback_transport.last_result
        return self._last_result

    @property
    def is_alive(self) -> bool:
        if self._fallback_transport is not None:
            return self._fallback_transport.is_alive
        return self._alive

    @property
    def is_turn_active(self) -> bool:
        if self._fallback_transport is not None:
            return self._fallback_transport.is_turn_active
        return bool(self._thread_id and self._current_turn_id)

    @property
    def capabilities(self) -> TransportCapabilities:
        if self._fallback_transport is not None:
            return self._fallback_transport.capabilities
        return TransportCapabilities(
            cli_websocket=False,  # We don't expose a /ws/cli endpoint
            session_resume=True,
            interrupt=True,
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

    async def _emit_user_consumed(self, msg_id: str | None, request_id: str | None) -> None:
        """Tell the broker a steered user message was consumed (its turn/started), so it flips that
        message pending→active and broadcasts user_active. No-ops without a msg_id (seed/retry
        resends carry no correlation)."""
        if not msg_id:
            return
        event: dict = {
            "type": "user_consumed",
            "event_type": "codex.turn.started",
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
            "persistExtendedHistory": True,
        }
        if self._model:
            params["model"] = self._model
        params.update(self._permission_thread_params())

        result = await self._send_rpc("thread/resume", params)
        thread = result.get("thread", {})
        self._thread_id = thread.get("id") or thread_id
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
