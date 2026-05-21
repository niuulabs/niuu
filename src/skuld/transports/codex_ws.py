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
import json
import logging
import os
import shutil
import tempfile
from itertools import count

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
from niuu.ports.cli import CLITransport, TransportCapabilities
from skuld.transports.codex import (
    CodexSubprocessTransport,
    _map_codex_tool,
    resolve_codex_cli,
)
from skuld.transports.mcp_config import build_codex_mcp_overrides

logger = logging.getLogger("skuld.transport")

# Monotonic request-ID generator for JSON-RPC calls.
_next_id = count(1)


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


class CodexWebSocketTransport(CLITransport):
    """Long-lived Codex app-server process controlled via WebSocket JSON-RPC.

    Lifecycle:
        1. ``start()`` spawns ``codex app-server --listen unix://PATH``
        2. Skuld connects to the server as a WebSocket client
        3. JSON-RPC ``initialize`` handshake, then ``thread/start``
        4. User messages are sent via ``turn/start``
        5. Streaming events arrive as JSON-RPC notifications

    Authentication: set ``OPENAI_API_KEY`` in the environment.
    """

    def __init__(
        self,
        workspace_dir: str,
        *,
        model: str = "o4-mini",
        skip_permissions: bool = True,
        system_prompt: str = "",
        initial_prompt: str = "",
        codex_port: int = 0,
        mcp_servers: list[dict] | None = None,
        **_kwargs: object,
    ) -> None:
        super().__init__()
        self.workspace_dir = workspace_dir
        self._model = model
        self._skip_permissions = skip_permissions
        self._system_prompt = system_prompt
        self._initial_prompt = initial_prompt
        self._codex_port = codex_port or _pick_free_port()
        self._mcp_servers = list(mcp_servers or [])
        self._mcp_overrides = build_codex_mcp_overrides(self._mcp_servers)

        self._process: asyncio.subprocess.Process | None = None
        self._ws: ClientConnection | None = None
        self._receive_task: asyncio.Task | None = None
        self._codex_socket_dir: str | None = None
        self._codex_socket_path: str | None = None
        self._fallback_transport: CodexSubprocessTransport | None = None
        self._thread_id: str | None = None
        self._current_turn_id: str | None = None
        self._last_result: dict | None = None
        self._last_usage: dict | None = None
        self._alive = False
        self._block_index: int = 0
        self._pending_redirects: list[str] = []
        self._redirect_interrupt_requested = False

        # Pending RPC response futures keyed by request id.
        self._pending: dict[int, asyncio.Future] = {}
        # Pending approval RPC ids keyed by string request_id.
        self._pending_approvals: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        try:
            await self._spawn_app_server()
            await self._connect_ws()
            await self._handshake()
        except Exception as exc:
            await self._start_fallback_transport(exc)
            return

        if self._initial_prompt:
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
        if self._codex_socket_dir:
            shutil.rmtree(self._codex_socket_dir, ignore_errors=True)
        self._codex_socket_dir = tempfile.mkdtemp(prefix="skuld-codex-")
        self._codex_socket_path = os.path.join(self._codex_socket_dir, "app-server.sock")
        listen_url = f"unix://{self._codex_socket_path}"
        codex_cli = resolve_codex_cli()

        cmd = [
            codex_cli,
            "app-server",
            "--listen",
            listen_url,
        ]
        for key, value in self._mcp_overrides:
            cmd.extend(["-c", f"{key}={value}"])

        env = dict(os.environ)
        if "OPENAI_API_KEY" not in env:
            logger.info(
                "OPENAI_API_KEY not found — relying on Codex CLI auth state for app-server access"
            )

        logger.info("Spawning Codex app-server on %s", listen_url)
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
        if not self._codex_socket_path:
            raise RuntimeError("Codex socket path missing before connect")

        socket_path = self._codex_socket_path
        uri = "ws://localhost/"
        max_attempts = 30
        for attempt in range(1, max_attempts + 1):
            if self._process and self._process.returncode is not None:
                raise RuntimeError(f"Codex app-server exited with code {self._process.returncode}")
            try:
                self._ws = await unix_connect(path=socket_path, uri=uri, compression=None)
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

        await self._ws.send(json.dumps(msg))
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
        await self._ws.send(json.dumps(_rpc_notification(method)))

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

        thread_params: dict = {
            "experimentalRawEvents": False,
            "persistExtendedHistory": True,
            "cwd": self.workspace_dir,
        }
        if self._model:
            thread_params["model"] = self._model
        if self._skip_permissions:
            thread_params["approvalPolicy"] = "never"
            thread_params["sandbox"] = "danger-full-access"
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

                logger.debug("Codex WS unknown frame: %.200s", raw)

        except websockets.exceptions.ConnectionClosed as exc:
            logger.info("Codex WS closed: %s", exc)
        except asyncio.CancelledError:
            pass
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
            return

        if method == "turn/completed":
            self._current_turn_id = None
            # Merge saved usage into result event.
            usage = self._last_usage or {}
            self._last_result = {
                "type": "result",
                "stop_reason": "end_turn",
                "modelUsage": usage,
            }
            await self._emit(self._last_result)
            next_prompt = self._consume_pending_redirects()
            if next_prompt is not None:
                logger.info("Codex redirect: starting replacement turn after interrupt")
                asyncio.create_task(
                    self.send_message(next_prompt),
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

        # --- Command / file output deltas (show in chat as text) ---
        if method == "item/commandExecution/outputDelta":
            delta = params.get("delta", "")
            if delta:
                await self._emit_text_delta(delta)
            return

        if method == "item/fileChange/outputDelta":
            delta = params.get("delta", "")
            if delta:
                await self._emit_text_delta(delta)
            return

        # --- Errors ---
        if method == "error":
            error = params.get("error", {})
            message = error.get("message", str(params))
            logger.warning("Codex error notification: %s", message)
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

    # ------------------------------------------------------------------
    # Server requests (approval callbacks)
    # ------------------------------------------------------------------

    async def _handle_server_request(self, data: dict) -> None:
        """Handle a server-initiated request that needs a response."""
        method = data.get("method", "")
        rid = data["id"]
        params = data.get("params", {})

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
            self._pending_approvals[request_id] = rid
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
            self._pending_approvals[request_id] = rid
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

        if item_type == "agentMessage":
            # Start a text content block — deltas will follow via agentMessage/delta.
            await self._emit_content_block_start({"type": "text"})
            return

        if item_type == "reasoning":
            await self._emit_content_block_start({"type": "thinking"})
            return

        if item_type == "webSearch":
            await self._emit_tool_use(item_id, "WebSearch", {"query": item.get("query", "")})
            return

    async def _handle_item_completed(self, item: dict) -> None:
        """Emit content_block_stop and any final content when an item completes."""
        item_type = item.get("type", "")
        item_id = item.get("id", "")

        if item_type == "commandExecution":
            # Close the tool_use block, then emit the output as a tool_result
            # paired by id so the UI groups it under the call.
            await self._emit_content_block_stop()
            output = item.get("aggregatedOutput") or ""
            if not isinstance(output, str):
                output = str(output)
            exit_code = item.get("exitCode", 0)
            if output or exit_code != 0:
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
            if result_text:
                is_error = bool(item.get("isError") or item.get("is_error"))
                await self._emit_tool_result(item_id, result_text, is_error=is_error)
            return

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

    # ------------------------------------------------------------------
    # CLITransport interface
    # ------------------------------------------------------------------

    async def send_message(self, content: str) -> None:
        if self._fallback_transport is not None:
            await self._fallback_transport.send_message(content)
            return

        if not self._thread_id:
            raise RuntimeError("No active thread — call start() first")

        self._last_result = None
        self._last_usage = None
        self._block_index = 0
        params: dict = {
            "threadId": self._thread_id,
            "input": [{"type": "text", "text": content, "textElements": []}],
        }

        logger.info("Sending turn/start to Codex (thread=%s)", self._thread_id)
        await self._send_rpc("turn/start", params)

    async def send_control_response(self, request_id: str, response: dict) -> None:
        if self._fallback_transport is not None:
            await self._fallback_transport.send_control_response(request_id, response)
            return

        """Respond to a Codex approval request."""
        rid = self._pending_approvals.pop(request_id, None)
        if rid is None:
            logger.warning("No pending approval for request_id=%s", request_id)
            return

        # Map broker permission response to Codex approval decision.
        # Codex uses camelCase enum variants: accept, acceptForSession, decline, cancel.
        behavior = response.get("behavior", "allow")
        if behavior in ("allow", "allowForever"):
            decision = "accept"
        else:
            decision = "decline"
        await self._send_rpc_response(rid, {"decision": decision})

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
            content = str(kwargs.get("content") or "").strip()
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
            content = str(kwargs.get("content") or "").strip()
            if not content:
                return

            if not (self._thread_id and self._current_turn_id):
                logger.info(
                    "Codex redirect without active turn; sending replacement prompt immediately"
                )
                await self.send_message(content)
                return

            self._pending_redirects.append(content)
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

        logger.debug("Codex WS: unhandled control subtype=%s", subtype)

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
        )

    def _consume_pending_redirects(self) -> str | None:
        """Drain queued redirect messages into the next replacement prompt."""
        self._redirect_interrupt_requested = False
        if not self._pending_redirects:
            return None

        if len(self._pending_redirects) == 1:
            return self._pending_redirects.pop(0)

        pending = list(self._pending_redirects)
        self._pending_redirects.clear()
        lines = [
            "The user redirected the work while the previous turn was still running.",
            "Ignore the interrupted approach and follow all of these updates in order:",
        ]
        lines.extend(f"- {item}" for item in pending)
        return "\n".join(lines)

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
        if self._skip_permissions:
            params["approvalPolicy"] = "never"
            params["sandbox"] = "danger-full-access"

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
