"""DshJsonRpcTransport — DeepSeek Harness (dsh) via the SDK JSON-RPC stdio protocol.

Spawns the dsh SDK runtime (``dsh-jsonrpc-agent``) as one long-lived process per
Skuld session and speaks its newline-delimited JSON-RPC 2.0 protocol directly:

- client→server requests: ``initialize``, ``session/prompt``, ``shutdown``
- server→client notifications: ``session.event`` (full durable session-log
  envelopes: assistant chunks, tool calls, tool results, turn boundaries) and
  ``session.status`` (whole-agent running/idle transitions)

Events are normalized to the same Claude-style format used by other Skuld
transports so the broker, browser rendering, artifact tracking, Ravn
observation, and usage reporting work unchanged.

Runtime distribution: the ``deepseek-harness-runtime-bin`` Python package
bundles a single-file runtime executable plus the default agent composition
(bash + filesystem tools, JSONL session persistence, token meter, compaction).
``dsh_runtime_bin`` / ``dsh_cordis_config`` override both for custom builds.

Model routing: the runtime's ``deepseek-official`` adapter accepts any
OpenAI-compatible chat-completions endpoint via ``DEEPSEEK_BASE_URL``
(``{base}/chat/completions`` is the request path), so sessions can run against
DeepSeek's API, Bifrost, or a local vLLM.

Interrupt semantics (deliberate, verified by spike): the SDK protocol has no
cancel method, and the SDK server cannot resume a persisted session in a fresh
process (it only ever creates agents; the harness-core ``agents.resume`` path
is not exposed over this protocol as of 0.1.0-rc6). The live runtime process
therefore IS the session: ``interrupt()`` raises instead of killing the
process, because a kill would silently discard the session's conversational
state. Revisit when upstream adds protocol-level cancel/resume.
"""

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

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

logger = logging.getLogger("skuld.transport")

_STDOUT_CHUNK_BYTES = 65536
_INITIALIZE_TIMEOUT_S = 60.0

# ---------------------------------------------------------------------------
# Tool name mapping — dsh tool names -> normalized names (for UI parity)
# ---------------------------------------------------------------------------

_DSH_TOOL_MAP: dict[str, str] = {
    "bash": "Bash",
    "read_file": "Read",
    "write_file": "Write",
    "edit_file": "Edit",
    "list_dir": "LS",
    "glob": "Glob",
    "grep": "Grep",
    "todo": "Todo",
    "subagent": "Subagent",
    "job_output": "JobOutput",
    "job_kill": "JobKill",
    "skill": "Skill",
}

_STOP_REASON_MAP: dict[str, str] = {
    "completed": "end_turn",
    "max-tokens": "max_tokens",
    "interrupted": "interrupted",
    "error": "error",
}


def _map_dsh_tool(name: str) -> str:
    """Map a dsh tool name (from the session log) to a normalized display name."""
    return _DSH_TOOL_MAP.get(name, name)


def resolve_dsh_launch_args(runtime_bin: str) -> list[str]:
    """Resolve the argv that launches the dsh SDK runtime.

    An explicit ``runtime_bin`` wins. Otherwise the bundled executable from the
    ``deepseek-harness-runtime-bin`` package is used; its absence is fatal.
    """
    if runtime_bin:
        return [runtime_bin]

    try:
        from deepseek_harness_runtime import resolve_bundled_launch_args
    except ImportError as exc:
        raise RuntimeError(
            "DshJsonRpcTransport needs a dsh runtime: install the "
            "'deepseek-harness-runtime-bin' package in the Skuld image, or set "
            "dsh.runtime_bin to a dsh-jsonrpc-agent executable path"
        ) from exc

    return list(resolve_bundled_launch_args())


def resolve_dsh_cordis_config(cordis_config: str) -> str:
    """Resolve the Cordis composition file the runtime must load.

    An explicit path wins; otherwise the default composition shipped with
    ``deepseek-harness-runtime-bin`` is used. No config is fatal — the runtime
    has no built-in fallback composition.
    """
    if cordis_config:
        path = Path(cordis_config)
        if not path.is_file():
            raise RuntimeError(
                f"dsh.cordis_config points at {cordis_config}, which does not exist; "
                "fix the path or clear the setting to use the bundled default composition"
            )
        return str(path)

    try:
        from deepseek_harness_runtime import bundled_default_config_path
    except ImportError as exc:
        raise RuntimeError(
            "DshJsonRpcTransport needs a Cordis composition: install the "
            "'deepseek-harness-runtime-bin' package, or set dsh.cordis_config"
        ) from exc

    return str(bundled_default_config_path())


class DshJsonRpcTransport(CLITransport):
    """Drives DeepSeek Harness through its SDK JSON-RPC stdio protocol.

    One runtime process per Skuld session; ``session/prompt`` enqueues each
    user message as the next FIFO turn and the transport waits for the
    whole-agent idle transition before returning from ``send_message``.
    """

    def __init__(
        self,
        workspace_dir: str,
        model: str = "deepseek-v4-flash",
        session_id: str = "",
        system_prompt: str = "",
        dsh_runtime_bin: str = "",
        dsh_cordis_config: str = "",
        dsh_base_url: str = "",
        dsh_api_key: str = "",
        dsh_provider: str = "deepseek-official",
        dsh_prompt_timeout_s: float = 600.0,
    ) -> None:
        super().__init__()
        self.workspace_dir = workspace_dir
        self._model = model
        self._system_prompt = system_prompt
        self._runtime_bin = dsh_runtime_bin
        self._cordis_config = dsh_cordis_config
        self._base_url = dsh_base_url
        self._api_key = dsh_api_key
        self._provider = dsh_provider
        self._prompt_timeout_s = dsh_prompt_timeout_s

        # The dsh session id is derived from the Skuld session id so a broker
        # restart within a live runtime process addresses the same agent.
        seed = session_id or uuid.uuid4().hex
        self._dsh_session_id = f"session-{uuid.uuid5(uuid.NAMESPACE_URL, seed).hex}"

        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_request_id = 0
        self._idle_event = asyncio.Event()
        self._turn_active = False
        self._last_result: dict | None = None
        self._turn_usage: dict[str, int] | None = None
        self._block_index = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        launch_args = resolve_dsh_launch_args(self._runtime_bin)
        cordis_config = resolve_dsh_cordis_config(self._cordis_config)

        workspace = Path(self.workspace_dir)
        workspace.mkdir(parents=True, exist_ok=True)
        session_root = workspace / ".dsh-sessions"
        session_root.mkdir(parents=True, exist_ok=True)

        env = dict(os.environ)
        env["DSH_CORDIS_CONFIG"] = cordis_config
        env["DSH_CWD"] = str(workspace)
        env["DSH_SESSION_ROOT"] = str(session_root)
        if self._system_prompt:
            env["DSH_SYSTEM_PROMPT"] = self._system_prompt
        if self._base_url:
            env["DEEPSEEK_BASE_URL"] = self._base_url
        if self._api_key:
            env["DEEPSEEK_API_KEY"] = self._api_key

        logger.info(
            "Starting dsh runtime %s (model: %s, provider: %s)",
            launch_args[0],
            self._model,
            self._provider,
        )
        self._process = await asyncio.create_subprocess_exec(
            *launch_args,
            cwd=str(workspace),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._stderr_task = asyncio.create_task(_drain_stream(self._process.stderr, "dsh-stderr"))
        self._reader_task = asyncio.create_task(self._read_stdout())

        init = await self._request(
            "initialize",
            {
                "cwd": str(workspace),
                "provider": self._provider,
                "model": self._model,
            },
            timeout=_INITIALIZE_TIMEOUT_S,
        )
        server = init.get("serverInfo", {}) if isinstance(init, dict) else {}
        logger.info(
            "dsh runtime initialized (server: %s %s, session: %s)",
            server.get("name", "?"),
            server.get("version", "?"),
            self._dsh_session_id,
        )

    async def stop(self) -> None:
        if self._process is None:
            return
        process = self._process
        try:
            # Graceful protocol shutdown: the server flushes the response,
            # disposes the root context, and exits 0.
            await self._request("shutdown", {}, timeout=10.0)
            await asyncio.wait_for(process.wait(), timeout=10.0)
        except Exception:
            logger.warning("dsh graceful shutdown failed; terminating", exc_info=True)
            await _stop_process(process)
        finally:
            self._process = None
            for task in (self._reader_task, self._stderr_task):
                if task is not None and not task.done():
                    task.cancel()
            self._reader_task = None
            self._stderr_task = None
            self._fail_pending(RuntimeError("dsh runtime stopped"))

    async def interrupt(self) -> None:
        raise RuntimeError(
            "The dsh SDK protocol (0.1.0-rc6) has no cancel method, and killing the "
            "runtime would discard the session (the SDK server cannot resume a "
            "persisted session). Wait for the turn to finish, or stop the session."
        )

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    async def send_message(
        self, content: str, *, msg_id: str | None = None, request_id: str | None = None
    ) -> None:
        if self._process is None or self._process.returncode is not None:
            raise RuntimeError(
                "dsh runtime is not running; the session cannot continue because the "
                "SDK protocol cannot resume a persisted session in a new process. "
                "Restart the Skuld session."
            )

        self._last_result = None
        self._turn_usage = None
        self._idle_event.clear()
        self._turn_active = True

        try:
            await self._request(
                "session/prompt",
                {
                    "sessionId": self._dsh_session_id,
                    "contentBlocks": [{"type": "text", "text": content}],
                },
                timeout=30.0,
            )
            # The prompt response is a durable enqueue receipt; the turn's
            # events stream as notifications until the whole agent goes idle.
            await asyncio.wait_for(self._idle_event.wait(), timeout=self._prompt_timeout_s)
        except TimeoutError:
            logger.warning("dsh turn did not reach idle within %.0fs", self._prompt_timeout_s)
        finally:
            self._turn_active = False

        if self._last_result is None:
            # The runtime went idle without a turn/end (e.g. admission was
            # discarded). Emit a result so the broker's turn accounting fires.
            self._last_result = self._make_result("end_turn")
            await self._emit(self._last_result)

    # ------------------------------------------------------------------
    # JSON-RPC plumbing
    # ------------------------------------------------------------------

    async def _request(self, method: str, params: dict, *, timeout: float) -> Any:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("dsh runtime is not running")
        self._next_request_id += 1
        req_id = self._next_request_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        frame = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        self._process.stdin.write((json.dumps(frame) + "\n").encode())
        await self._process.stdin.drain()
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(req_id, None)

    def _fail_pending(self, error: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    async def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        buffer = b""
        try:
            while True:
                chunk = await process.stdout.read(_STDOUT_CHUNK_BYTES)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    text = line.decode().strip()
                    if text:
                        await self._handle_frame(text)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("dsh stdout reader failed", exc_info=True)
        finally:
            self._fail_pending(RuntimeError("dsh runtime stdout closed"))
            # A runtime that dies mid-turn must not leave send_message hanging.
            self._idle_event.set()

    async def _handle_frame(self, text: str) -> None:
        try:
            frame = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("dsh emitted a non-JSON stdout line: %.200s", text)
            return

        if "id" in frame and "method" not in frame:
            future = self._pending.get(frame["id"])
            if future is None or future.done():
                return
            if "error" in frame:
                err = frame["error"]
                future.set_exception(RuntimeError(f"dsh request failed: {err.get('message', err)}"))
                return
            future.set_result(frame.get("result"))
            return

        method = frame.get("method", "")
        params = frame.get("params", {})
        if method == "session.status":
            await self._handle_status(params)
            return
        if method == "session.event":
            await self._handle_session_event(params)
            return
        logger.debug("dsh notification %s (ignored)", method)

    # ------------------------------------------------------------------
    # Event mapping (dsh session log -> broker-expected Claude-style events)
    # ------------------------------------------------------------------

    async def _handle_status(self, params: dict) -> None:
        if params.get("sessionId") != self._dsh_session_id:
            return
        if params.get("status") == "idle":
            self._idle_event.set()

    async def _handle_session_event(self, params: dict) -> None:
        if params.get("sessionId") != self._dsh_session_id:
            # Descendant (subagent) sessions stream here too; keep the root
            # transcript clean and let the child's work surface via tool frames.
            return
        event = params.get("event", {})
        if not isinstance(event, dict):
            return
        event_type = event.get("type", "")
        data = event.get("data", {})

        if event_type == "assistant/chunk":
            await self._handle_chunk(data.get("chunk", {}))
            return

        if event_type == "assistant/message":
            await self._handle_assistant_message(data.get("message", {}))
            return

        if event_type == "tool/call":
            name = _map_dsh_tool(data.get("name", "tool"))
            raw_args = data.get("arguments", "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                args = {"raw": raw_args}
            await self._emit(
                {
                    "type": "assistant",
                    "message": {
                        "model": self._model,
                        "content": [
                            {
                                "type": "tool_use",
                                "id": data.get("callId", ""),
                                "name": name,
                                "input": args if isinstance(args, dict) else {},
                            }
                        ],
                    },
                }
            )
            return

        if event_type == "tool/result":
            await self._handle_tool_result(data)
            return

        if event_type == "turn/end":
            reason = data.get("reason", {})
            kind = reason.get("kind", "completed")
            if kind == "error":
                message = reason.get("error", {}).get("message", "unknown dsh error")
                await self._emit({"type": "error", "content": message})
            self._last_result = self._make_result(_STOP_REASON_MAP.get(kind, kind))
            await self._emit(self._last_result)
            return

        logger.debug("dsh session event %s (not mapped)", event_type)

    async def _handle_chunk(self, chunk: dict) -> None:
        chunk_type = chunk.get("type", "")
        if chunk_type == "text-delta":
            event = {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": chunk.get("text", "")},
            }
        elif chunk_type == "reasoning-delta":
            event = {
                "type": "content_block_delta",
                "delta": {"type": "thinking_delta", "thinking": chunk.get("text", "")},
            }
        elif chunk_type == "usage":
            usage = chunk.get("usage", {})
            self._turn_usage = {
                "inputTokens": int(usage.get("inputTokens", 0)),
                "outputTokens": int(usage.get("outputTokens", 0)),
                "cacheReadInputTokens": int(usage.get("cacheReadTokens", 0) or 0),
                "cacheCreationInputTokens": int(usage.get("cacheWriteTokens", 0) or 0),
            }
            return
        else:
            # block-start/block-end/tool-call-delta/finish carry no
            # broker-facing content; committed shapes arrive via
            # assistant/message, tool/call, and turn/end.
            return

        filtered = _filter_event(event)
        if filtered:
            await self._emit(filtered)

    async def _handle_assistant_message(self, message: dict) -> None:
        texts = [
            block.get("text", "")
            for block in message.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "".join(texts)
        usage = message.get("usage")
        if isinstance(usage, dict):
            self._turn_usage = {
                "inputTokens": int(usage.get("inputTokens", 0)),
                "outputTokens": int(usage.get("outputTokens", 0)),
                "cacheReadInputTokens": int(usage.get("cacheReadTokens", 0) or 0),
                "cacheCreationInputTokens": int(usage.get("cacheWriteTokens", 0) or 0),
            }
        if text:
            await self._emit(
                {
                    "type": "assistant",
                    "message": {"content": text},
                    "content": text,
                }
            )

    async def _handle_tool_result(self, data: dict) -> None:
        message = data.get("message", {})
        for block in message.get("content", []):
            if not isinstance(block, dict) or block.get("type") != "tool-result":
                continue
            parts = [
                part.get("text", "")
                for part in block.get("content", [])
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            content = "".join(parts)
            await self._emit(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": block.get("toolCallId", ""),
                                "content": content,
                                "is_error": bool(block.get("isError", False)),
                            }
                        ]
                    },
                }
            )

    def _make_result(self, stop_reason: str) -> dict:
        usage = self._turn_usage or {
            "inputTokens": 0,
            "outputTokens": 0,
            "cacheReadInputTokens": 0,
            "cacheCreationInputTokens": 0,
        }
        return {
            "type": "result",
            "stop_reason": stop_reason,
            "modelUsage": {self._model: usage},
            "sessionId": self._dsh_session_id,
        }

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def session_id(self) -> str | None:
        return self._dsh_session_id

    @property
    def last_result(self) -> dict | None:
        return self._last_result

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def is_turn_active(self) -> bool:
        return self._turn_active

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(
            send_message=True,
            cli_websocket=False,
            session_resume=False,
            interrupt=False,
        )
