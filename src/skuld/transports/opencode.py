"""OpenCodeHttpTransport — OpenCode (opencode.ai) via HTTP REST + SSE.

Spawns ``opencode serve --port {port}`` and communicates using:
- **HTTP REST** for commands (create session, send prompt, grant permission)
- **SSE** (``GET /event``) for streaming events (message deltas, permissions, status)

The SSE stream delivers ``message.part.delta`` events with real-time text
deltas, plus ``question.asked`` for permission requests and ``session.idle``
for turn completion.
"""

import asyncio
import json
import logging
import os

import httpx

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
from skuld.transports.codex import _map_codex_tool

logger = logging.getLogger("skuld.transport")


class OpenCodeHttpTransport(CLITransport):
    """HTTP REST + SSE transport for OpenCode (opencode.ai).

    Lifecycle:
        1. ``start()`` spawns ``opencode serve --port {port}``
        2. Skuld connects via HTTP, creates a session
        3. User messages sent via ``POST /session/{id}/prompt_async``
        4. Streaming events arrive via SSE on ``GET /event``

    OpenCode supports multiple LLM providers (Anthropic, OpenAI, Gemini,
    Ollama) — the model/provider is configured via its own config or
    passed per-prompt.
    """

    def __init__(
        self,
        workspace_dir: str,
        *,
        model: str = "",
        skip_permissions: bool = True,
        system_prompt: str = "",
        initial_prompt: str = "",
        opencode_port: int = 0,
        **_kwargs: object,
    ) -> None:
        super().__init__()
        self.workspace_dir = workspace_dir
        self._model = model
        self._skip_permissions = skip_permissions
        self._system_prompt = system_prompt
        self._initial_prompt = initial_prompt
        self._opencode_port = opencode_port or _pick_free_port()

        self._process: asyncio.subprocess.Process | None = None
        self._client: httpx.AsyncClient | None = None
        self._sse_task: asyncio.Task | None = None
        self._session_id: str | None = None
        self._last_result: dict | None = None
        self._last_usage: dict | None = None
        self._alive = False
        self._block_index: int = 0
        self._pending_permissions: dict[str, dict] = {}
        self._user_message_ids: set[str] = set()
        # Track partIDs that are reasoning/thinking so we route their
        # deltas correctly even when the field name is ambiguous.
        self._reasoning_part_ids: set[str] = set()

    @property
    def _base_url(self) -> str:
        return f"http://127.0.0.1:{self._opencode_port}"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        await self._spawn_server()
        await self._connect()
        await self._create_session()

        if self._initial_prompt:
            await self.send_message(self._initial_prompt)

    async def stop(self) -> None:
        self._alive = False

        if self._sse_task and not self._sse_task.done():
            self._sse_task.cancel()

        if self._client:
            await self._client.aclose()
            self._client = None

        if self._process:
            await _stop_process(self._process)
            self._process = None

        logger.info("OpenCodeHttpTransport stopped")

    # ------------------------------------------------------------------
    # Spawn & connect
    # ------------------------------------------------------------------

    async def _spawn_server(self) -> None:
        cmd = [
            "opencode",
            "serve",
            "--port",
            str(self._opencode_port),
            "--hostname",
            "127.0.0.1",
        ]

        env = dict(os.environ)
        if self._skip_permissions:
            env["OPENCODE_AUTO_APPROVE"] = "1"

        logger.info("Spawning OpenCode server on port %d", self._opencode_port)
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.workspace_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        logger.info("OpenCode server PID %s", self._process.pid)

        asyncio.create_task(_drain_stream(self._process.stdout, "opencode-stdout"))
        asyncio.create_task(_drain_stream(self._process.stderr, "opencode-stderr"))

    async def _connect(self) -> None:
        """Wait for the OpenCode server to be ready and create HTTP client."""
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=60.0)
        max_attempts = 30
        for attempt in range(1, max_attempts + 1):
            if self._process and self._process.returncode is not None:
                raise RuntimeError(f"OpenCode server exited with code {self._process.returncode}")
            try:
                resp = await self._client.get("/global/health")
                if resp.status_code == 200:
                    logger.info("OpenCode server ready (attempt %d)", attempt)
                    self._alive = True
                    self._sse_task = asyncio.create_task(self._sse_loop())
                    return
            except httpx.ConnectError:
                pass
            await asyncio.sleep(0.5)

        raise RuntimeError(
            f"Could not connect to OpenCode server at {self._base_url} "
            f"after {max_attempts} attempts"
        )

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    async def _create_session(self) -> None:
        """Create a new OpenCode session."""
        resp = await self._client.post("/session", json={})
        resp.raise_for_status()
        data = resp.json()
        self._session_id = data.get("id") or data.get("sessionID")
        logger.info("OpenCode session created: %s", self._session_id)

        await self._emit(
            {
                "type": "system",
                "subtype": "init",
                "session_id": self._session_id,
                "model": self._model,
                "tools": [],
            }
        )

    # ------------------------------------------------------------------
    # SSE event loop
    # ------------------------------------------------------------------

    async def _sse_loop(self) -> None:
        """Read SSE events from the OpenCode server."""
        logger.info("OpenCode SSE loop started")
        url = f"{self._base_url}/event"
        msg_count = 0

        try:
            async with httpx.AsyncClient(timeout=None) as sse_client:
                async with sse_client.stream("GET", url) as response:
                    buffer = ""
                    async for chunk in response.aiter_text():
                        buffer += chunk
                        while "\n\n" in buffer:
                            frame, buffer = buffer.split("\n\n", 1)
                            for line in frame.split("\n"):
                                if line.startswith("data: "):
                                    raw = line[6:]
                                    try:
                                        event = json.loads(raw)
                                    except json.JSONDecodeError:
                                        continue
                                    msg_count += 1
                                    await self._handle_sse_event(event)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("OpenCode SSE loop error: %r", exc, exc_info=True)
        finally:
            self._alive = False
            logger.info("OpenCode SSE loop ended after %d events", msg_count)

    # ------------------------------------------------------------------
    # SSE event dispatch
    # ------------------------------------------------------------------

    async def _handle_sse_event(self, event: dict) -> None:
        """Dispatch an SSE event from OpenCode."""
        event_type = event.get("type", "")
        props = event.get("properties", {})

        # --- Message updated (full message state) ---
        # Process this FIRST to track user vs assistant messages.
        if event_type == "message.updated":
            info = props.get("info", {})
            msg_id = info.get("id", "")
            role = info.get("role", "")
            if role == "user" and msg_id:
                self._user_message_ids.add(msg_id)
            model = info.get("model", "")
            if model and not self._model:
                self._model = model
            return

        # --- Streaming text delta ---
        if event_type == "message.part.delta":
            # Skip deltas for user messages (echoed prompt)
            if props.get("messageID", "") in self._user_message_ids:
                return
            part_id = props.get("partID", "")
            field = props.get("field", "")
            delta = props.get("delta", "")
            if not delta:
                return
            # Route as thinking if the field says so OR if this partID
            # was registered as a reasoning part.
            is_thinking = field == "thinking" or part_id in self._reasoning_part_ids
            if is_thinking:
                await self._emit(
                    {
                        "type": "content_block_delta",
                        "delta": {"type": "thinking_delta", "thinking": delta},
                    }
                )
            else:
                await self._emit_text_delta(delta)
            return

        # --- Message part updated (tool calls, reasoning blocks) ---
        if event_type == "message.part.updated":
            # Skip parts for user messages
            if props.get("messageID", "") in self._user_message_ids:
                return
            part = props.get("part", props)
            await self._handle_part_updated(part, props)
            return

        # --- Permission request ---
        if event_type == "question.asked":
            request_id = props.get("id", "")
            tool = props.get("tool", "")
            description = props.get("question", props.get("description", ""))
            await self._emit(
                {
                    "type": "control_request",
                    "subtype": "can_use_tool",
                    "request_id": request_id,
                    "tool": _map_codex_tool(tool) if tool else "Bash",
                    "input": {"command": description},
                }
            )
            self._pending_permissions[request_id] = props
            return

        # --- Session idle (turn complete) ---
        if event_type == "session.idle":
            self._last_result = {
                "type": "result",
                "stop_reason": "end_turn",
                "modelUsage": self._last_usage or {},
            }
            await self._emit(self._last_result)
            return

        # --- Session error ---
        if event_type == "session.error":
            message = props.get("error", props.get("message", str(props)))
            logger.warning("OpenCode error: %s", message)
            await self._emit({"type": "error", "error": message})
            return

        # --- Session status (analyzing, executing) ---
        if event_type == "session.status":
            status = props.get("status", "")
            if status in ("analyzing", "executing"):
                # Emit assistant event to signal streaming started
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

        # --- Heartbeat / connected ---
        if event_type in ("server.connected", "server.heartbeat"):
            return

        logger.debug("OpenCode: unhandled event %s", event_type)

    # ------------------------------------------------------------------
    # Part handling
    # ------------------------------------------------------------------

    async def _handle_part_updated(self, part: dict, props: dict) -> None:
        """Handle a message.part.updated event."""
        part_type = part.get("type", "")
        part_id = part.get("id", props.get("partID", ""))

        if part_type == "tool-invocation":
            tool_name = part.get("toolName", part.get("name", ""))
            tool_input = part.get("args", part.get("input", {}))
            if isinstance(tool_input, str):
                try:
                    tool_input = json.loads(tool_input)
                except json.JSONDecodeError:
                    tool_input = {"command": tool_input}
            normalized = _map_codex_tool(tool_name)

            # Broker-facing: assistant with message.content
            await self._emit(
                {
                    "type": "assistant",
                    "message": {
                        "model": self._model,
                        "content": [
                            {
                                "type": "tool_use",
                                "id": part_id,
                                "name": normalized,
                                "input": tool_input if isinstance(tool_input, dict) else {},
                            }
                        ],
                    },
                }
            )
            # Browser-facing: content_block lifecycle
            idx = self._next_block_index()
            await self._emit(
                {
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {"type": "tool_use", "id": part_id, "name": normalized},
                }
            )
            input_json = json.dumps(tool_input if isinstance(tool_input, dict) else {})
            await self._emit(
                {
                    "type": "content_block_delta",
                    "delta": {"type": "input_json_delta", "partial_json": input_json},
                }
            )
            return

        if part_type == "tool-result":
            content = part.get("result", part.get("content", ""))
            is_error = part.get("isError", False)
            # Close previous tool_use block and show result as text
            await self._emit({"type": "content_block_stop"})
            if content:
                idx = self._next_block_index()
                await self._emit(
                    {
                        "type": "content_block_start",
                        "index": idx,
                        "content_block": {"type": "text"},
                    }
                )
                prefix = "[error] " if is_error else ""
                await self._emit_text_delta(prefix + str(content))
                await self._emit({"type": "content_block_stop"})
            return

        if part_type == "text":
            # Open a text block — actual content arrives via message.part.delta.
            # Do NOT emit the text field here to avoid duplication.
            idx = self._next_block_index()
            await self._emit(
                {
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {"type": "text"},
                }
            )
            return

        if part_type == "reasoning":
            self._reasoning_part_ids.add(part_id)
            idx = self._next_block_index()
            await self._emit(
                {
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {"type": "thinking"},
                }
            )
            return

        if part_type == "finish":
            model_id = self._model or "unknown"
            self._last_usage = {
                model_id: {
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "cacheReadInputTokens": 0,
                    "cacheCreationInputTokens": 0,
                }
            }
            return

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _next_block_index(self) -> int:
        idx = self._block_index
        self._block_index += 1
        return idx

    async def _emit_text_delta(self, text: str) -> None:
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
        if not self._session_id:
            raise RuntimeError("No active session — call start() first")

        self._last_result = None
        self._last_usage = None
        self._block_index = 0
        self._user_message_ids.clear()
        self._reasoning_part_ids.clear()

        body: dict = {
            "parts": [{"type": "text", "text": content}],
        }
        if self._system_prompt:
            body["system"] = self._system_prompt
        if self._model:
            # OpenCode model format: "provider/model" or just "model"
            body["model"] = {"modelID": self._model}

        logger.info("Sending prompt to OpenCode session %s", self._session_id)
        resp = await self._client.post(
            f"/session/{self._session_id}/prompt_async",
            json=body,
        )
        if resp.status_code not in (200, 204):
            error = resp.text
            logger.warning("OpenCode prompt failed: %s %s", resp.status_code, error)
            await self._emit({"type": "error", "error": f"Prompt failed: {error}"})

    async def send_control_response(self, request_id: str, response: dict) -> None:
        """Respond to an OpenCode permission request."""
        self._pending_permissions.pop(request_id, None)
        behavior = response.get("behavior", "allow")
        reply = "allow" if behavior in ("allow", "allowForever") else "deny"

        try:
            resp = await self._client.post(
                f"/permission/{request_id}/reply",
                json={"reply": reply},
            )
            if resp.status_code not in (200, 204):
                logger.warning("Permission reply failed: %s", resp.text)
        except Exception as exc:
            logger.warning("Permission reply error: %r", exc)

    async def send_control(self, subtype: str, **kwargs: object) -> None:
        if subtype == "set_model":
            model = kwargs.get("model")
            if model and isinstance(model, str):
                self._model = model
            return

        if subtype == "interrupt":
            if self._session_id:
                try:
                    await self._client.post(
                        f"/session/{self._session_id}/abort",
                    )
                except Exception as exc:
                    logger.debug("Interrupt failed (may not be running): %r", exc)
            return

        logger.debug("OpenCode: unhandled control subtype=%s", subtype)

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def last_result(self) -> dict | None:
        return self._last_result

    @property
    def is_alive(self) -> bool:
        return self._alive

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(
            cli_websocket=False,
            session_resume=True,
            interrupt=True,
            set_model=True,
            set_thinking_tokens=False,
            set_permission_mode=False,
            rewind_files=False,
            mcp_set_servers=False,
            permission_requests=True,
        )

    # ------------------------------------------------------------------
    # Session resume
    # ------------------------------------------------------------------

    async def resume(self, session_id: str) -> None:
        """Resume an existing OpenCode session."""
        self._session_id = session_id
        logger.info("OpenCode session resumed: %s", self._session_id)

        await self._emit(
            {
                "type": "system",
                "subtype": "init",
                "session_id": self._session_id,
                "model": self._model,
                "tools": [],
            }
        )


def _pick_free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
