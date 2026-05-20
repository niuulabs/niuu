"""CodexSubprocessTransport — OpenAI Codex CLI (one process per message)."""

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path

from niuu.adapters.cli.runtime import (
    drain_process_stream as _drain_stream,
)
from niuu.adapters.cli.runtime import (
    filter_cli_event as _filter_event,
)
from niuu.adapters.cli.runtime import (
    stop_subprocess as _stop_process,
)
from niuu.ports.cli import CLITransport
from skuld.transports.mcp_config import build_codex_mcp_overrides
from skuld.transports.tool_shims import ensure_codex_tool_shims

logger = logging.getLogger("skuld.transport")

_CODEX_ENV_VARS = ("CODEX_CLI_PATH", "SKULD_CODEX_CLI_PATH")
_CODEX_BUNDLE_CANDIDATES = (
    "/Applications/Codex.app/Contents/Resources/codex",
    "/Applications/Codex.app/Contents/MacOS/Codex",
)

# ---------------------------------------------------------------------------
# Tool name mapping — Codex -> normalized names (matching Claude's tool names)
# ---------------------------------------------------------------------------

_CODEX_TOOL_MAP: dict[str, str] = {
    "shell": "Bash",
    "container.exec": "Bash",
    "str_replace_editor": "Edit",
    "str_replace_based_edit_tool": "Edit",
    "write_file": "Write",
    "create_file": "Write",
    "read_file": "Read",
    "list_directory": "LS",
    "search_files": "Grep",
}


def _map_codex_tool(codex_name: str) -> str:
    """Map a Codex CLI tool name to its normalized equivalent."""
    return _CODEX_TOOL_MAP.get(codex_name, codex_name)


def resolve_codex_cli() -> str:
    """Resolve the Codex CLI executable path.

    Preference order:
    1. explicit env override
    2. PATH lookup
    3. common macOS app-bundle locations
    4. literal ``codex`` as a final fallback
    """
    for env_var in _CODEX_ENV_VARS:
        configured = os.environ.get(env_var, "").strip()
        if configured:
            return configured

    on_path = shutil.which("codex")
    if on_path:
        return on_path

    for candidate in _CODEX_BUNDLE_CANDIDATES:
        if Path(candidate).exists():
            return candidate

    return "codex"


class CodexSubprocessTransport(CLITransport):
    """Spawns the OpenAI Codex CLI as a subprocess per message.

    Codex CLI reference: https://github.com/openai/codex

    Authentication: set OPENAI_API_KEY in the environment.

    Codex does not implement the Claude SDK WebSocket protocol, so
    capabilities.cli_websocket is False and the /ws/cli endpoint is not used.

    Events emitted by Codex are normalized to the same format the broker
    expects so the rest of the pipeline (browser rendering, artifact tracking,
    usage reporting) works without change.  Where Codex does not provide
    structured usage data a synthetic ``modelUsage`` block with zero counts
    is emitted so the broker's result-handling path still fires.
    """

    def __init__(
        self,
        workspace_dir: str,
        model: str = "o4-mini",
        mcp_servers: list[dict] | None = None,
    ) -> None:
        super().__init__()
        self.workspace_dir = workspace_dir
        self._model = model
        self._mcp_overrides = build_codex_mcp_overrides(mcp_servers or [])
        self._mcp_servers = list(mcp_servers or [])
        self._process: asyncio.subprocess.Process | None = None
        self._last_result: dict | None = None
        self._pending_text: list[str] = []
        self._env = dict(os.environ)

    async def start(self) -> None:
        _, shim_env = ensure_codex_tool_shims(
            self.workspace_dir,
            mcp_servers=self._mcp_servers,
        )
        if shim_env:
            self._env.update(shim_env)
        logger.info(
            "CodexSubprocessTransport configured for %s (model: %s)",
            self.workspace_dir,
            self._model,
        )

    async def stop(self) -> None:
        if self._process is None:
            return
        await _stop_process(self._process)
        self._process = None

    async def send_message(self, content: str) -> None:
        self._last_result = None
        self._pending_text = []
        codex_cli = resolve_codex_cli()

        cmd = [
            codex_cli,
            "exec",
            "--model",
            self._model,
            "--sandbox",
            "workspace-write",
            "--json",
        ]
        for key, value in self._mcp_overrides:
            cmd.extend(["-c", f"{key}={value}"])
        cmd.append(content)

        logger.info("Running Codex CLI (model: %s)", self._model)
        logger.debug("Codex CLI command: %s", " ".join(cmd))

        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.workspace_dir,
            env=self._env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._process = process

        stderr_task = asyncio.create_task(_drain_stream(process.stderr, "codex-stderr"))

        try:
            if process.stdout is None:
                raise RuntimeError("Codex CLI stdout not available")

            while True:
                line = await process.stdout.readline()
                if not line:
                    break

                raw = line.decode().strip()
                if not raw:
                    continue

                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    # Plain text output — emit as streaming text delta
                    event = {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": raw + "\n"},
                    }
                    self._pending_text.append(raw)
                    filtered = _filter_event(event)
                    if filtered:
                        await self._emit(filtered)
                    continue

                await self._handle_codex_event(data)

            exit_code = await process.wait()

            # Synthesize a result event if Codex didn't emit one
            if self._last_result is None:
                self._last_result = self._make_synthetic_result(exit_code)
                await self._emit(self._last_result)

        finally:
            if not stderr_task.done():
                stderr_task.cancel()
            self._process = None

    async def _handle_codex_event(self, data: dict) -> None:
        """Normalize a Codex CLI JSON event to the broker's common format."""
        event_type = data.get("type", "")
        logger.debug("Codex event: type=%s", event_type)

        # --- Current Codex CLI agent message item ---
        if event_type == "item.completed":
            item = data.get("item", {})
            if not isinstance(item, dict):
                return
            if item.get("type") == "agent_message":
                text = item.get("text", "")
                if isinstance(text, str) and text:
                    await self._emit(
                        {
                            "type": "assistant",
                            "message": {"content": text},
                            "content": text,
                        }
                    )
                return

        # --- Streaming text output ---
        if event_type in ("response.output_text.delta", "text_delta"):
            delta_text = data.get("delta", "") or data.get("text", "")
            event = {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": delta_text},
            }
            filtered = _filter_event(event)
            if filtered:
                await self._emit(filtered)
            return

        # --- Tool / function call ---
        if event_type in ("response.output_item.added", "function_call", "tool_call"):
            item = data.get("item", data)
            fn_name = item.get("name") or item.get("function", {}).get("name", "")
            args_raw = item.get("arguments") or item.get("function", {}).get("arguments", "{}")
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                args = {"command": args_raw}

            normalized_name = _map_codex_tool(fn_name)
            await self._emit(
                {
                    "type": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": normalized_name,
                            "input": args,
                        }
                    ],
                }
            )
            return

        # --- Turn complete ---
        if event_type in ("response.completed", "response.done", "turn.completed", "done"):
            usage = data.get("usage", {})
            model_id = data.get("model", self._model)
            self._last_result = {
                "type": "result",
                "stop_reason": "end_turn",
                "modelUsage": {
                    model_id: {
                        "inputTokens": usage.get("input_tokens", 0),
                        "outputTokens": usage.get("output_tokens", 0),
                        "cacheReadInputTokens": 0,
                        "cacheCreationInputTokens": 0,
                    }
                },
            }
            await self._emit(self._last_result)
            return

        # --- Error ---
        if event_type == "error":
            message = data.get("message", str(data))
            logger.warning("Codex CLI error event: %s", message)
            await self._emit({"type": "error", "content": message})
            return

        # --- Pass unknown events through (forward to browser for inspection) ---
        logger.debug("Codex: unknown event type=%s, forwarding as-is", event_type)
        await self._emit(data)

    def _make_synthetic_result(self, exit_code: int) -> dict:
        """Build a synthetic result event when Codex exits without emitting one."""
        stop_reason = "end_turn" if exit_code == 0 else "error"
        return {
            "type": "result",
            "stop_reason": stop_reason,
            "modelUsage": {
                self._model: {
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "cacheReadInputTokens": 0,
                    "cacheCreationInputTokens": 0,
                }
            },
        }

    @property
    def session_id(self) -> str | None:
        # Codex CLI does not expose a resumable session ID
        return None

    @property
    def last_result(self) -> dict | None:
        return self._last_result

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.returncode is None
