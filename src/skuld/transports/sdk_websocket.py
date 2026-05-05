"""SdkWebSocketTransport — new SDK path (long-lived process, WS bridge)."""

import asyncio
import json
import logging
import os
import uuid

from fastapi import WebSocket

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
from skuld.transports.mcp_config import build_claude_mcp_config

logger = logging.getLogger("skuld.transport")


class SdkWebSocketTransport(CLITransport):
    """Long-lived CLI process that connects back via --sdk-url WebSocket.

    The CLI connects to ws://localhost:{port}/ws/cli/{session_id} and
    communicates using NDJSON messages over that WebSocket.
    """

    def __init__(
        self,
        workspace_dir: str,
        sdk_port: int,
        session_id: str,
        *,
        model: str = "",
        skip_permissions: bool = True,
        agent_teams: bool = False,
        system_prompt: str = "",
        initial_prompt: str = "",
        mcp_servers: list[dict] | None = None,
    ) -> None:
        super().__init__()
        self.workspace_dir = workspace_dir
        self._sdk_port = sdk_port
        self._broker_session_id = session_id
        self._model = model
        self._skip_permissions = skip_permissions
        self._agent_teams = agent_teams
        self._system_prompt = system_prompt
        self._initial_prompt = initial_prompt
        self._mcp_config = build_claude_mcp_config(mcp_servers or [])
        self._process: asyncio.subprocess.Process | None = None
        self._cli_ws: WebSocket | None = None
        self._cli_connected = asyncio.Event()
        self._cli_session_id: str | None = None
        self._last_result: dict | None = None
        self._pending_messages: list[dict] = []
        self._keepalive_task: asyncio.Task | None = None
        self._receive_task: asyncio.Task | None = None
        self._alive = False
        self._spawning = False
        self._slash_commands: list[str] = []
        self._skills: list[str] = []

    @property
    def sdk_url(self) -> str:
        return f"ws://localhost:{self._sdk_port}/ws/cli/{self._broker_session_id}"

    async def start(self) -> None:
        if self._spawning:
            logger.info("start() called but already spawning, skipping")
            return
        await self._spawn()

    async def _spawn(self, resume_session_id: str | None = None) -> None:
        """Spawn the CLI process with --sdk-url."""
        self._spawning = True
        resume_id = resume_session_id or self._cli_session_id

        cmd = [
            "claude",
            "--sdk-url",
            self.sdk_url,
            "--output-format",
            "stream-json",
            "--input-format",
            "stream-json",
            "--verbose",
        ]
        if self._model:
            cmd.extend(["--model", self._model])
        if self._skip_permissions:
            cmd.extend(["--permission-mode", "bypassPermissions"])
        if self._system_prompt:
            cmd.extend(["--append-system-prompt", self._system_prompt])
        if self._mcp_config:
            cmd.extend(["--mcp-config", self._mcp_config])
        if self._initial_prompt and not resume_id:
            self._pending_messages.append(
                {
                    "type": "user",
                    "message": {"role": "user", "content": self._initial_prompt},
                    "parent_tool_use_id": None,
                    "session_id": "",
                }
            )
        if resume_id:
            cmd.extend(["--resume", resume_id])

        logger.info(
            "Spawning Claude CLI: %s",
            " ".join(cmd[:10]) + ("..." if len(cmd) > 10 else ""),
        )
        logger.info(
            "CLI args: system_prompt=%d chars, initial_prompt=%d chars, model=%s, resume=%s",
            len(self._system_prompt),
            len(self._initial_prompt),
            self._model,
            resume_id,
        )

        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        if self._agent_teams:
            env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] = "1"

        if "ANTHROPIC_API_KEY" not in env:
            logger.warning(
                "ANTHROPIC_API_KEY not found in environment — "
                "CLI may fail to authenticate with the API"
            )

        logger.debug("CLI spawn command: %s", " ".join(cmd))
        logger.debug("CLI spawn env: CLAUDECODE unset, AGENT_TEAMS=%s", self._agent_teams)

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.workspace_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        logger.info("CLI process spawned with PID %s", self._process.pid)
        asyncio.create_task(_drain_stream(self._process.stdout, "stdout"))
        asyncio.create_task(_drain_stream(self._process.stderr, "stderr"))

    async def stop(self) -> None:
        logger.info(
            "SdkWebSocketTransport.stop() called (alive=%s, process=%s, cli_ws=%s)",
            self._alive,
            self._process.pid if self._process else None,
            self._cli_ws is not None,
        )
        self._alive = False

        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()

        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()

        if self._cli_ws:
            try:
                await self._cli_ws.close()
            except Exception as e:
                logger.debug("Error closing CLI WS during stop: %r", e)
            self._cli_ws = None

        if self._process:
            await _stop_process(self._process)
            self._process = None

        self._cli_connected.clear()
        self._spawning = False
        logger.info("SdkWebSocketTransport stopped")

    async def attach_cli_websocket(self, ws: WebSocket) -> None:
        """Called when the CLI connects back to /ws/cli/{session_id}."""
        logger.info(
            "CLI WebSocket attaching (pending_messages=%d, had_previous_ws=%s)",
            len(self._pending_messages),
            self._cli_ws is not None,
        )
        await ws.accept()
        self._cli_ws = ws
        self._alive = True
        self._spawning = False
        self._cli_connected.set()
        logger.info("CLI WebSocket accepted and marked alive")

        if self._pending_messages:
            logger.info("Flushing %d pending messages to CLI", len(self._pending_messages))
            for msg in self._pending_messages:
                logger.debug("Flushing pending message type=%s", msg.get("type"))
                await self._send_to_cli(msg)
                # Emit to broker so the message is broadcast to all channels
                # (browser WebSockets, Telegram, etc.) and recorded in history
                await self._emit(msg)
            self._pending_messages.clear()

        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        self._receive_task = asyncio.create_task(self._receive_loop())
        logger.info("CLI WebSocket receive and keepalive loops started")

    async def _receive_loop(self) -> None:
        """Read messages from the CLI WebSocket and dispatch events."""
        logger.info("_receive_loop started")
        msg_count = 0
        try:
            while self._cli_ws:
                message = await self._cli_ws.receive()
                msg_type = message.get("type", "")

                if msg_type == "websocket.disconnect":
                    code = message.get("code", 1000)
                    logger.info("CLI WS disconnect frame: code=%s", code)
                    break

                raw = message.get("text")
                if raw is None:
                    raw_bytes = message.get("bytes")
                    if raw_bytes:
                        raw = raw_bytes.decode("utf-8", errors="replace")
                        logger.info(
                            "CLI WS binary frame (%d bytes)",
                            len(raw_bytes),
                        )
                    else:
                        logger.warning("CLI WS empty frame: %s", message)
                        continue

                if msg_count == 0:
                    logger.info(
                        "CLI WS first frame (%d bytes): %.500s",
                        len(raw),
                        raw,
                    )

                for line in raw.split("\n"):
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError as e:
                        logger.warning(
                            "Non-JSON from CLI WS: %s (%s)",
                            line[:200],
                            e,
                        )
                        continue

                    msg_count += 1
                    logger.info(
                        "CLI WS msg #%d type=%s subtype=%s",
                        msg_count,
                        data.get("type", "?"),
                        data.get("subtype", "?"),
                    )
                    await self._handle_cli_message(data)
        except Exception as e:
            logger.warning(
                "CLI WS receive loop exception: %r",
                e,
                exc_info=True,
            )
        finally:
            self._alive = False
            self._cli_ws = None
            self._cli_connected.clear()
            logger.info(
                "CLI WS receive loop ended after %d messages",
                msg_count,
            )

    async def _handle_cli_message(self, data: dict) -> None:
        """Process a single parsed message from the CLI."""
        msg_type = data.get("type", "unknown")
        logger.debug("_handle_cli_message: type=%s", msg_type)

        if msg_type == "system" and data.get("subtype") == "init":
            if data.get("session_id"):
                self._cli_session_id = data["session_id"]
            self._slash_commands = data.get("slash_commands", [])
            self._skills = data.get("skills", [])
            logger.info(
                "CLI init: session=%s model=%s tools=%s slash_commands=%d skills=%d",
                data.get("session_id"),
                data.get("model"),
                len(data.get("tools", [])),
                len(self._slash_commands),
                len(self._skills),
            )

        if data.get("session_id") and msg_type != "system":
            self._cli_session_id = data["session_id"]

        if msg_type == "result":
            self._last_result = data
            logger.info("CLI result event received (session=%s)", self._cli_session_id)

        filtered = _filter_event(data)
        if not filtered:
            logger.debug("_handle_cli_message: event type=%s filtered out", msg_type)
            return
        logger.debug("_handle_cli_message: emitting event type=%s to broker", msg_type)
        await self._emit(filtered)

    async def send_message(self, content: str) -> None:
        logger.info(
            "send_message: alive=%s, spawning=%s, pid=%s, cli_ws=%s, connected=%s, len=%d",
            self._alive,
            self._spawning,
            self._process.pid if self._process else None,
            self._cli_ws is not None,
            self._cli_connected.is_set(),
            len(content),
        )

        if not self._alive and not self._spawning and self._process is not None:
            logger.info("CLI process dead, re-spawning with --resume")
            await self._spawn()

        msg = {
            "type": "user",
            "message": {"role": "user", "content": content},
            "parent_tool_use_id": None,
            "session_id": self._cli_session_id or "",
        }

        if not self._cli_connected.is_set():
            logger.info(
                "CLI not connected yet, queuing message (pending=%d)",
                len(self._pending_messages) + 1,
            )
            self._pending_messages.append(msg)
            return

        self._last_result = None
        logger.debug("Sending message to CLI WebSocket")
        await self._send_to_cli(msg)

    async def send_control_response(self, request_id: str, response: dict) -> None:
        """Respond to a CLI-initiated control_request (e.g. can_use_tool)."""
        msg = {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": request_id,
                "response": response,
            },
        }
        logger.debug("Sending control_response for request %s", request_id)
        await self._send_to_cli(msg)

    async def send_control(self, subtype: str, **kwargs: object) -> None:
        """Send a server-initiated control message to the CLI."""
        msg = {
            "type": "control_response",
            "response": {
                "subtype": subtype,
                "request_id": str(uuid.uuid4()),
                **kwargs,
            },
        }
        logger.debug("Sending control %s to CLI", subtype)
        await self._send_to_cli(msg)

    async def _send_to_cli(self, msg: dict) -> None:
        """Send a JSON message to the CLI WebSocket as NDJSON (newline-delimited)."""
        if not self._cli_ws:
            logger.warning(
                "Cannot send to CLI: WS not connected (type=%s)",
                msg.get("type"),
            )
            return

        payload = json.dumps(msg) + "\n"
        try:
            await self._cli_ws.send_text(payload)
        except Exception as e:
            logger.error(
                "Failed to send to CLI WS: %r (type=%s)",
                e,
                msg.get("type"),
                exc_info=True,
            )
            self._alive = False

    async def _keepalive_loop(self) -> None:
        """Send keep_alive messages every 10 seconds."""
        try:
            while self._alive:
                await asyncio.sleep(10)
                if self._cli_ws:
                    await self._send_to_cli({"type": "keep_alive"})
        except asyncio.CancelledError:
            pass  # Expected: keepalive loop cancelled during shutdown

    async def wait_for_cli_disconnect(self) -> None:
        """Block until the CLI WebSocket disconnects."""
        logger.debug(
            "wait_for_cli_disconnect: task=%s",
            self._receive_task is not None,
        )
        if self._receive_task:
            try:
                await self._receive_task
            except asyncio.CancelledError:
                logger.debug("wait_for_cli_disconnect: receive task cancelled")
        logger.debug("wait_for_cli_disconnect: done")

    @property
    def session_id(self) -> str | None:
        return self._cli_session_id

    @property
    def last_result(self) -> dict | None:
        return self._last_result

    @property
    def is_alive(self) -> bool:
        return self._alive or self._spawning

    @property
    def slash_commands(self) -> list[str]:
        return self._slash_commands

    @property
    def skills(self) -> list[str]:
        return self._skills

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(
            cli_websocket=True,
            session_resume=True,
            interrupt=True,
            set_model=True,
            set_thinking_tokens=True,
            set_permission_mode=True,
            rewind_files=True,
            mcp_set_servers=True,
            permission_requests=True,
            slash_commands=True,
            skills=True,
        )
