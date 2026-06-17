"""GrokACPTransport — xAI Grok Build via Agent Client Protocol (ACP) over stdio.

Uses the recommended ACP engine path (`grok agent stdio`) for rich integration:
persistent agent process, streaming thoughts, tool visibility, plans, and
structured session control. Events are normalized to the same Claude-style
format used by other Skuld transports so the broker, Ravn, Volundr UI,
artifact tracking, and usage reporting continue to work unchanged.

Authentication: relies on the grok CLI (XAI_API_KEY or cached auth.json).
Install via the official xAI CLI installer so `grok` is on PATH inside the
Skuld container/pod (or mount the binary).

Model example: "grok-build" (default). Pass via Skuld session.model.
"""

import asyncio
import json
import logging
import os
import shutil
import signal
from typing import Any

from skuld.transports import (
    CLITransport,
    TransportCapabilities,
    _drain_stream,
    _filter_event,
    _stop_process,
)

logger = logging.getLogger("skuld.transport")

# Grok ACP surfaces no token counts, so usage is estimated from streamed text at
# this rough ratio (~4 chars/token) — enough for message_count/usage to advance.
_CHARS_PER_TOKEN = 4

# ---------------------------------------------------------------------------
# Tool name mapping — Grok internal IDs -> normalized names (for UI parity)
# ---------------------------------------------------------------------------

_GROK_TOOL_MAP: dict[str, str] = {
    "run_terminal_command": "Bash",
    "search_replace": "Edit",
    "read_file": "Read",
    "list_dir": "LS",
    "grep": "Grep",
    "web_search": "WebSearch",
    "web_fetch": "WebFetch",
    "todo_write": "Todo",
    "spawn_subagent": "Subagent",
    "memory_search": "Memory",
}


def _map_grok_tool(name: str) -> str:
    """Map a Grok tool name (from ACP) to a normalized display name."""
    return _GROK_TOOL_MAP.get(name, name)


class GrokACPTransport(CLITransport):
    """Persistent ACP client over `grok agent stdio` (Scaldy / Grok Build pipeline).

    - Spawns one long-lived `grok agent stdio --always-approve -m <model>` process.
    - Performs ACP initialize + session/new on start().
    - send_message() issues session/prompt requests and streams mapped updates.
    - Rich events (message chunks, thoughts, tool calls, plans) are emitted as
      they arrive via session/update notifications.
    - Final prompt result produces a normalized "result" event with synthetic
      modelUsage (Grok ACP currently surfaces stopReason/sessionId; token counts
      can be added later via extension methods if exposed).
    - Matches Codex yolo style and Claude SDK event shapes for full platform
      parity (Ravn episodes, timeline, usage, web UI, broker controls).
    """

    def __init__(
        self,
        workspace_dir: str,
        model: str = "grok-build",
        session_id: str | None = None,
        grok_bin: str | None = None,
        skip_permissions: bool = True,
        agent_teams: bool = False,
        system_prompt: str = "",
        initial_prompt: str = "",
        acp_prompt_timeout_s: float = 300.0,
        acp_auth_preflight_timeout_s: float = 60.0,
        **_: Any,
    ) -> None:
        super().__init__()
        self.workspace_dir = workspace_dir
        self._model = model
        self._requested_session_id = session_id
        self._grok_bin_override = grok_bin
        self._skip_permissions = skip_permissions  # yolo via --always-approve; accepted for parity
        self._agent_teams = agent_teams
        self._system_prompt = system_prompt
        self._initial_prompt = initial_prompt
        self._prompt_timeout = acp_prompt_timeout_s
        self._auth_preflight_timeout = acp_auth_preflight_timeout_s

        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._initial_dispatch_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()  # serialize prompt turns

        self._session_id: str | None = None
        self._last_result: dict | None = None

        # JSON-RPC bookkeeping
        self._next_id: int = 1
        self._pending: dict[int, asyncio.Future] = {}
        # Responses read before their awaiter registered (fast peer / handshake
        # race): buffered here so _acp_send resolves from them instead of hanging.
        self._early_results: dict[int, dict] = {}
        self._current_prompt_id: int | None = None

        self._pending_text_chunks: list[str] = []
        # Per-turn character counters for usage estimation (reset each turn).
        self._turn_out_chars: int = 0
        self._turn_reason_chars: int = 0
        self._turn_in_chars: int = 0
        self._stdout_reader: asyncio.StreamReader | None = None

    async def _emit(self, data: dict) -> None:
        """Emit an event, tolerating both sync and async registered callbacks."""
        if not self._event_callback:
            logger.debug(
                "_emit: no callback registered, dropping type=%s",
                data.get("type"),
            )
            return
        cb = self._event_callback
        result = cb(data)
        if asyncio.iscoroutine(result):
            await result

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _preflight_auth(self, grok_bin: str) -> None:
        """Hydrate grok auth before starting the persistent ACP agent.

        A cold ``grok agent stdio`` can die with ``Auth(AuthorizationRequired)``
        and a closed transport channel. A short headless ``grok -p`` run first
        refreshes the cached auth/session state so the agent authenticates.
        Best-effort: failures are logged, never fatal (the agent may still work,
        or fail with a clearer error of its own).
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                grok_bin,
                "-p",
                "ok",
                "--output-format",
                "json",
                "--yolo",
                "--no-auto-update",
                cwd=self.workspace_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ},
            )
            await asyncio.wait_for(proc.communicate(), timeout=self._auth_preflight_timeout)
            logger.info("Grok auth preflight complete (rc=%s)", proc.returncode)
        except Exception as exc:
            logger.warning("Grok auth preflight skipped (%r); continuing to ACP agent", exc)

    async def start(self) -> None:
        logger.info(
            "GrokACPTransport configured for %s "
            "(model: %s, system_prompt=%d chars, initial_prompt=%d chars)",
            self.workspace_dir,
            self._model,
            len(self._system_prompt or ""),
            len(self._initial_prompt or ""),
        )

        if self._process is not None:
            return

        grok_bin = (
            self._grok_bin_override or os.environ.get("GROK_BIN") or shutil.which("grok") or "grok"
        )

        # Hydrate auth first — a cold `grok agent stdio` otherwise fails with
        # Auth(AuthorizationRequired). (Mirrors the working manual launch flow.)
        await self._preflight_auth(grok_bin)

        # IMPORTANT: agent-level options (--always-approve, -m) MUST come before
        # the "stdio" subcommand
        cmd = [
            grok_bin,
            "agent",
            # yolo equivalent; non-interactive for Skuld (matches Codex --full-auto)
            "--always-approve",
            "-m",
            self._model,
            "stdio",
        ]

        logger.info("Spawning Grok ACP agent: %s", " ".join(cmd))

        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.workspace_dir,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ},  # inherits XAI_API_KEY / auth.json etc.
        )
        self._process = process

        if process.stdout is None or process.stdin is None:
            raise RuntimeError("Grok ACP stdio pipes not available")

        self._stdout_reader = process.stdout

        # Drain stderr (non-blocking)
        asyncio.create_task(_drain_stream(process.stderr, "grok-stderr"))

        # Start the always-on reader that handles notifications + responses
        self._reader_task = asyncio.create_task(self._reader_loop())

        # ACP handshake
        await self._acp_initialize()
        await self._acp_new_session()

        logger.info("Grok ACP session ready: %s (sessionId=%s)", self._model, self._session_id)

        # Forge auto-start seeds the task via initial_prompt and relies on the
        # transport to dispatch it (ACP has no separate "initial prompt" slot).
        # Fire-and-forget so start() returns promptly; the turn streams via the
        # reader. Interactive sessions leave initial_prompt empty and skip this.
        if self._initial_prompt:
            logger.info(
                "Grok ACP: dispatching seeded initial prompt (%d chars)",
                len(self._initial_prompt),
            )
            self._initial_dispatch_task = asyncio.create_task(
                self.send_message(self._initial_prompt)
            )

    async def stop(self) -> None:
        async with self._lock:
            if self._reader_task and not self._reader_task.done():
                self._reader_task.cancel()
            if self._process:
                await _stop_process(self._process)
            self._process = None
            self._reader_task = None
            self._stdout_reader = None
            self._pending.clear()
            self._early_results.clear()
            self._current_prompt_id = None

    # ------------------------------------------------------------------
    # ACP JSON-RPC over stdio
    # ------------------------------------------------------------------

    async def _reader_loop(self) -> None:
        """Continuously read lines from the agent.

        Dispatches notifications and resolves request futures.
        """
        assert self._stdout_reader is not None
        try:
            while True:
                line = await self._stdout_reader.readline()
                if not line:
                    break
                raw = line.decode().strip()
                if not raw:
                    continue

                try:
                    data: dict[str, Any] = json.loads(raw)
                except json.JSONDecodeError:
                    logger.debug("Grok ACP non-JSON line (ignored for protocol): %s", raw[:120])
                    continue

                # Notification from agent (the important streaming path)
                if data.get("method") == "session/update":
                    params = data.get("params") or {}
                    update = params.get("update") or params
                    mapped = self._map_acp_update(update)
                    if mapped:
                        filtered = _filter_event(mapped)
                        if filtered:
                            await self._emit(filtered)
                    continue

                # Response to a request we sent
                if "id" in data:
                    req_id = data["id"]
                    fut = self._pending.pop(req_id, None)
                    if fut is not None and not fut.done():
                        if "error" in data:
                            fut.set_exception(RuntimeError(data["error"]))
                        else:
                            fut.set_result(data.get("result") or {})
                    elif fut is None:
                        # Awaiter not registered yet (fast peer / handshake race):
                        # buffer so the matching _acp_send picks it up.
                        self._early_results[req_id] = data
                    # If this was the response to our current prompt, record completion
                    if req_id == self._current_prompt_id:
                        result = data.get("result") or {}
                        self._last_result = self._make_result_from_acp(result)
                        await self._emit(self._last_result)
                        self._current_prompt_id = None
                    continue

                # Unknown / pass-through (helps debugging in logs)
                logger.debug("Grok ACP unhandled message: %s", str(data)[:200])

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.exception("Grok ACP reader loop error: %r", exc)
        finally:
            logger.info("Grok ACP reader loop exited")

    async def _acp_send(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON-RPC request and await the result (via reader)."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("Grok ACP process not started")

        req_id = self._next_id
        self._next_id += 1

        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        line = json.dumps(msg) + "\n"

        fut: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut

        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

        # The reader may have answered (and buffered) before we got here.
        early = self._early_results.pop(req_id, None)
        if early is not None:
            self._pending.pop(req_id, None)
            if "error" in early:
                raise RuntimeError(early["error"])
            return early.get("result") or {}

        # Bounded wait so a silent/unresponsive agent can never hang the handshake.
        try:
            return await asyncio.wait_for(fut, timeout=self._prompt_timeout)
        finally:
            self._pending.pop(req_id, None)

    async def _acp_initialize(self) -> None:
        result = await self._acp_send(
            "initialize",
            {
                "protocolVersion": "1",
                "clientCapabilities": {
                    "fs": {"readTextFile": True, "writeTextFile": True},
                    "terminal": True,
                },
            },
        )
        logger.debug("Grok ACP initialize result keys: %s", list(result.keys()) if result else None)

    async def _acp_new_session(self) -> None:
        params: dict[str, Any] = {
            "cwd": self.workspace_dir,
            "mcpServers": [],
        }
        if self._system_prompt:
            # Pass system prompt if ACP supports the key (ignored or error will surface in practice)
            params["systemPrompt"] = self._system_prompt
        if self._requested_session_id:
            # ACP has no direct "resume by id" in the basic new; pass meta or rely
            # on grok session mgmt. For now we create fresh; resume can be explored
            # via x.ai/ extensions later.
            params["_meta"] = {"resumeHint": self._requested_session_id}

        result = await self._acp_send("session/new", params)
        self._session_id = result.get("sessionId") or result.get("session_id")
        logger.info("Grok ACP new session established: %s", self._session_id)

    async def send_message(self, content: str) -> None:
        """Send a user turn. The ACP reader will stream mapped events and emit a final result."""
        async with self._lock:
            self._last_result = None
            self._pending_text_chunks = []
            self._turn_out_chars = 0
            self._turn_reason_chars = 0
            self._turn_in_chars = len(content or "")

            if not self._process or not self._process.stdin:
                await self.start()

            prompt_blocks = [{"type": "text", "text": content}]

            req_id = self._next_id
            self._next_id += 1
            self._current_prompt_id = req_id

            # Register the future BEFORE writing so the reader can resolve it, and so
            # current_prompt_id stays set until the reader emits the final result.
            fut: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
            self._pending[req_id] = fut

            msg = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "session/prompt",
                "params": {
                    "sessionId": self._session_id,
                    "prompt": prompt_blocks,
                },
            }

            self._process.stdin.write((json.dumps(msg) + "\n").encode())
            await self._process.stdin.drain()

            # The reader streams mapped chunks as they arrive and, on the matching
            # response, sets last_result and emits the final result event. Wait here
            # (bounded) for that response to preserve turn ordering.
            try:
                if self._early_results.pop(req_id, None) is None:
                    await asyncio.wait_for(asyncio.shield(fut), timeout=self._prompt_timeout)
            except TimeoutError:
                logger.warning(
                    "Grok ACP prompt did not complete within timeout (%.1fs)", self._prompt_timeout
                )
            finally:
                self._pending.pop(req_id, None)
                self._current_prompt_id = None

    # ------------------------------------------------------------------
    # Event mapping (ACP -> broker-expected Claude-style events)
    # ------------------------------------------------------------------

    def _map_acp_update(self, update: dict) -> dict | None:
        """Convert ACP session/update payloads into the event shapes other transports emit."""
        if not isinstance(update, dict):
            return None

        su = update.get("sessionUpdate") or update.get("session_update") or update.get("type")

        # Text response streaming
        if su in ("agent_message_chunk", "agentMessageChunk", "message_chunk"):
            content = update.get("content") or {}
            text = content.get("text") or update.get("text") or update.get("delta") or ""
            if text:
                self._turn_out_chars += len(text)
                return {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": text},
                }
            return None

        # Thoughts / reasoning — emit a thinking_delta so the broker routes it to the
        # separate reasoning block (matches Codex/Claude). It must NOT be a text_delta:
        # that inlines reasoning into the answer stream. (Previously this emitted a
        # "[thinking] " text_delta, which polluted the main message token-by-token.)
        if su in ("agent_thought_chunk", "agentThoughtChunk", "thought", "thinking"):
            content = update.get("content") or {}
            text = content.get("text") or update.get("text") or ""
            if text:
                self._turn_reason_chars += len(text)
                return {
                    "type": "content_block_delta",
                    "delta": {"type": "thinking_delta", "thinking": text},
                }
            return None

        # Tool call started
        if su in ("tool_call", "toolCall", "tool_use"):
            tool_name = update.get("tool") or update.get("name") or update.get("title") or "tool"
            args = update.get("arguments") or update.get("input") or update.get("args") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {"raw": args}
            normalized = _map_grok_tool(tool_name)
            return {
                "type": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": normalized,
                        "input": args,
                    }
                ],
            }

        # Tool progress / result update (optional, forward a lightweight marker)
        if su in ("tool_call_update", "toolCallUpdate", "tool_result"):
            tool_name = update.get("tool") or update.get("name") or update.get("title") or "tool"
            status = update.get("status") or update.get("kind") or "update"
            normalized = _map_grok_tool(tool_name)
            extra = update.get("result") or update.get("output") or {}
            return {
                "type": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": normalized,
                        "input": {
                            "status": status,
                            **(extra if isinstance(extra, dict) else {"raw": extra}),
                        },
                    }
                ],
            }

        # Plan (forward for observability; UI may render or ignore)
        if su in ("plan", "plan_update"):
            entries = update.get("entries") or update.get("plan") or []
            return {
                "type": "system",
                "content": [{"type": "plan", "entries": entries}],
            }

        # Pass other structured updates through (debug / future)
        if su:
            return {"type": "system", "content": [{"type": su, **update}]}

        # Unknown shape — let it through for the UI/logs
        return update

    def _make_result_from_acp(self, acp_result: dict) -> dict:
        """Produce a normalized result event for the broker's usage + timeline paths."""
        stop_reason = (
            acp_result.get("stopReason") or acp_result.get("stop_reason") or "end_turn"
        ).lower()
        model_id = self._model

        # Grok ACP exposes no token counts, so estimate from streamed characters
        # (~_CHARS_PER_TOKEN chars/token); reasoning counts as output (billed as
        # output). Must be > 0 so the broker advances message_count + usage and the
        # session reads as active in clients. Replace with real counts if a future
        # x.ai/ ACP extension surfaces them.
        in_tokens = max(1, self._turn_in_chars // _CHARS_PER_TOKEN)
        out_tokens = max(1, (self._turn_out_chars + self._turn_reason_chars) // _CHARS_PER_TOKEN)
        self._turn_out_chars = 0
        self._turn_reason_chars = 0
        self._turn_in_chars = 0
        return {
            "type": "result",
            "stop_reason": stop_reason,
            "modelUsage": {
                model_id: {
                    "inputTokens": in_tokens,
                    "outputTokens": out_tokens,
                    "cacheReadInputTokens": 0,
                    "cacheCreationInputTokens": 0,
                }
            },
            "sessionId": self._session_id,
            "text": acp_result.get("text", ""),
        }

    # ------------------------------------------------------------------
    # Properties required by CLITransport + broker
    # ------------------------------------------------------------------

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def last_result(self) -> dict | None:
        return self._last_result

    @property
    def is_alive(self) -> bool:
        return bool(
            self._process
            and self._process.returncode is None
            and (self._reader_task is None or not self._reader_task.done())
        )

    @property
    def capabilities(self) -> TransportCapabilities:
        # ACP gives us a persistent session (higher quality than per-turn Codex) with rich
        # observability via notifications. Matches Codex yolo + Claude event shapes for
        # full Ravn / UI / broker parity. Interrupt wired via SIGINT + future cancel.
        # Skills surface via the Grok tool catalog (todo, implement loops, etc. appear as tool_use).
        return TransportCapabilities(
            send_message=True,
            cli_websocket=False,  # we use stdio ACP, not the --sdk-url WS
            session_resume=True,
            interrupt=True,
            # set_model / rewind / mcp / permission_requests etc. are no-op or future ACP extensions
            skills=True,  # Grok Build surfaces skills and subagents as tools
        )

    # ------------------------------------------------------------------
    # Control support for broker parity (interrupt, etc.)
    # ------------------------------------------------------------------

    async def send_control(self, subtype: str, **kwargs: object) -> None:
        """Handle server-initiated controls. Supports interrupt for parity with other transports."""
        if subtype == "interrupt":
            logger.info("GrokACPTransport: received interrupt control")
            if self._process and self._process.returncode is None:
                try:
                    self._process.send_signal(signal.SIGINT)
                    logger.info("GrokACPTransport: sent SIGINT to interrupt current turn")
                except Exception as exc:
                    logger.warning("GrokACPTransport: SIGINT failed: %r", exc)
            # Unblock any in-flight prompt future so caller can proceed
            if self._current_prompt_id:
                fut = self._pending.pop(self._current_prompt_id, None)
                if fut and not fut.done():
                    fut.set_exception(RuntimeError("interrupted by control"))
                self._current_prompt_id = None
            return

        # Other controls (set_model, rewind, mcp_set_servers, etc.) not directly supported
        # by basic ACP stdio in yolo mode; log for observability.
        logger.debug(
            "GrokACPTransport.send_control(%s, %s) — no-op "
            "(ACP stdio yolo; use always-approve path)",
            subtype,
            kwargs,
        )

    async def send_control_response(self, request_id: str, response: dict) -> None:
        """ACP skips the Claude-SDK control_request/response handshake (always-approve)."""
        logger.debug(
            "GrokACPTransport control response ignored (always-approve path; request_id=%s)",
            request_id,
        )

    # Convenience for external inspection / tests
    @property
    def model(self) -> str:
        return self._model


# Re-export for convenience (mirrors codex style)
__all__ = ["GrokACPTransport", "_map_grok_tool", "_GROK_TOOL_MAP"]
