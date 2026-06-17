"""SubprocessTransport — legacy path (one process per message)."""

from __future__ import annotations

import asyncio
import json
import logging
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
from skuld.slash_commands import build_slash_command_catalog, compose_slash_command_text
from skuld.transports.claude_env import claude_spawn_env
from skuld.transports.mcp_config import build_claude_mcp_config
from skuld.transports.tool_shims import ensure_codex_tool_shims

logger = logging.getLogger("skuld.transport")

_DEFAULT_PERMISSION_MODE = "bypassPermissions"
_MAX_RETRIES = 5
_RETRY_BASE_DELAY_MS = 1000
_TRANSPORT_ERROR_DELAY_MS = 2000
# asyncio.StreamReader defaults to a 64 KB line buffer; a single Claude JSON
# event (especially tool results or file reads) routinely exceeds that, which
# raises LimitOverrunError on readline() and kills the transport. 10 MB covers
# realistic worst-case payloads without unbounded memory.
_STDOUT_LINE_LIMIT_BYTES = 10 * 1024 * 1024
_RETRYABLE_ERROR_MARKERS = (
    "processtransport",
    "not ready for writing",
    "transport closed",
    "connection reset",
    "econnreset",
    "broken pipe",
)


class _RetryableClaudeError(RuntimeError):
    """Transient Claude CLI failure that should be retried."""


class SubprocessTransport(CLITransport):
    """Spawn Claude per turn, resuming the logical session between invocations."""

    def __init__(
        self,
        workspace_dir: str,
        model: str = "",
        skip_permissions: bool = False,
        agent_teams: bool = False,
        system_prompt: str = "",
        initial_prompt: str = "",
        mcp_servers: list[dict] | None = None,
    ) -> None:
        super().__init__()
        self.workspace_dir = workspace_dir
        self._model = model
        self._skip_permissions = skip_permissions
        self._agent_teams = agent_teams
        self._system_prompt = system_prompt
        self._initial_prompt = initial_prompt
        self._raw_mcp_servers = list(mcp_servers or [])
        self._mcp_config = build_claude_mcp_config(mcp_servers or [])
        self._initial_prompt_sent = False
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._session_id: str | None = None
        self._last_result: dict | None = None
        # Slash commands + skills advertised in the CLI's system/init event.
        self._slash_commands: list[str] = []
        self._skills: list[str] = []
        self._slash_command_catalog: list[dict] | None = None

    async def start(self) -> None:
        logger.info(
            "SubprocessTransport configured for %s (model=%s, resume=%s)",
            self.workspace_dir,
            self._model or "<default>",
            self._session_id,
        )
        if not self._initial_prompt or self._initial_prompt_sent:
            return
        self._initial_prompt_sent = True
        try:
            await self.send_message(self._initial_prompt)
        except Exception:
            self._initial_prompt_sent = False
            raise

    async def stop(self) -> None:
        async with self._lock:
            if self._process is None:
                return
            await _stop_process(self._process)
            self._process = None

    async def send_message(self, content: str) -> None:
        async with self._lock:
            self._last_result = None
            await self._send_message_with_retries(content)

    async def _send_message_with_retries(self, content: str) -> None:
        last_error: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            if attempt > 0:
                delay = _RETRY_BASE_DELAY_MS * (1 << (attempt - 1))
                if last_error is not None and _is_retryable_error(str(last_error)):
                    delay += _TRANSPORT_ERROR_DELAY_MS
                logger.info(
                    "Retrying Claude CLI request (attempt %d/%d, delay=%dms)",
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay / 1000)

            try:
                await self._send_message_once(content)
                return
            except _RetryableClaudeError as exc:
                last_error = exc
                if attempt + 1 >= _MAX_RETRIES:
                    break
                logger.warning("Transient Claude CLI error: %s", exc)
            except Exception:
                raise

        if last_error is not None:
            raise RuntimeError(f"Claude Code CLI failed after {_MAX_RETRIES} retries: {last_error}")
        raise RuntimeError("Claude Code CLI failed without a retryable error")

    async def _send_message_once(self, content: str) -> None:
        cmd = [
            "claude",
            "-p",
            "--output-format",
            "stream-json",
            "--input-format",
            "stream-json",
            "--verbose",
        ]
        if self._model:
            cmd.extend(["--model", self._model])
        if self._skip_permissions:
            cmd.extend(["--permission-mode", _DEFAULT_PERMISSION_MODE])
        if self._session_id:
            cmd.extend(["--resume", self._session_id])
        elif self._system_prompt:
            cmd.extend(["--append-system-prompt", self._system_prompt])
        if self._mcp_config:
            cmd.extend(["--mcp-config", self._mcp_config])

        logger.info("Running Claude CLI (session=%s)", self._session_id)
        logger.debug("Claude CLI command: %s", " ".join(cmd))

        env = claude_spawn_env()  # subscription auth by default (SKULD__CLAUDE_AUTH)
        if self._agent_teams:
            env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] = "1"
        _, shim_env = ensure_codex_tool_shims(
            self.workspace_dir,
            mcp_servers=self._raw_mcp_servers,
        )
        if shim_env:
            env.update(shim_env)

        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.workspace_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
            env=env,
            limit=_STDOUT_LINE_LIMIT_BYTES,
        )
        self._process = process

        stderr_task = asyncio.create_task(_drain_stream(process.stderr, "claude-stderr"))

        try:
            if process.stdout is None:
                raise RuntimeError("Claude Code CLI stdout not available")
            if process.stdin is None:
                raise RuntimeError("Claude Code CLI stdin not available")

            await self._write_user_message(process.stdin, content)
            saw_result = False
            saw_meaningful_output = False

            while True:
                line = await process.stdout.readline()
                if not line:
                    break

                raw = line.decode().strip()
                if not raw:
                    continue

                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as exc:
                    logger.warning("Skipping non-JSON line: %s (%s)", raw[:200], exc)
                    continue

                session_id = data.get("session_id")
                if isinstance(session_id, str) and session_id:
                    self._session_id = session_id

                if data.get("type") == "system" and data.get("subtype") == "init":
                    self._capture_init_commands(data)

                event_type = data.get("type", "unknown")
                if event_type == "result":
                    self._last_result = data
                    saw_result = True

                filtered = _filter_event(data)
                if filtered:
                    if _counts_as_output(filtered):
                        saw_meaningful_output = True
                    await self._emit(filtered)

                if event_type == "error":
                    message = _extract_error_message(data)
                    if not saw_meaningful_output and _is_retryable_error(message):
                        raise _RetryableClaudeError(message)

                if event_type == "result":
                    break

            exit_code = await process.wait()
            if exit_code != 0:
                raise RuntimeError(f"Claude Code CLI exited with code {exit_code}")
            if not saw_result:
                raise RuntimeError("Claude Code CLI completed without a result event")
        except _RetryableClaudeError:
            await _stop_process(process)
            raise
        finally:
            if not stderr_task.done():
                stderr_task.cancel()
            self._process = None

    async def _write_user_message(
        self,
        stdin: Any,
        content: str,
    ) -> None:
        payload = {
            "type": "user",
            "message": {
                "role": "user",
                "content": content,
            },
        }

        try:
            stdin.write(json.dumps(payload).encode("utf-8"))
            stdin.write(b"\n")
            await stdin.drain()
            if hasattr(stdin, "close"):
                stdin.close()
                if hasattr(stdin, "wait_closed"):
                    await stdin.wait_closed()
        except Exception as exc:
            raise RuntimeError(f"Failed to write Claude CLI stdin: {exc}") from exc

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(
            session_resume=True,
            slash_commands=True,
            skills=True,
        )

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def last_result(self) -> dict | None:
        return self._last_result

    @property
    def is_alive(self) -> bool:
        return self._process is not None

    def _capture_init_commands(self, data: dict) -> None:
        """Record the slash_commands + skills the CLI advertised at startup."""
        slash = data.get("slash_commands")
        skills = data.get("skills")
        if isinstance(slash, list):
            self._slash_commands = [c for c in slash if isinstance(c, str)]
        if isinstance(skills, list):
            self._skills = [s for s in skills if isinstance(s, str)]
        self._slash_command_catalog = None

    async def discover_slash_commands(self, *, refresh: bool = False) -> list[dict]:
        """Return the rich slash-command catalog (CLI-reported names enriched
        with filesystem descriptions). Cached until init resets it or refresh."""
        if refresh or self._slash_command_catalog is None:
            self._slash_command_catalog = build_slash_command_catalog(
                self._slash_commands,
                self._skills,
                self.workspace_dir,
            )
        return list(self._slash_command_catalog)

    async def send_control(self, subtype: str, **kwargs: object) -> None:
        """Send a slash command by composing ``/cmd args`` and running it as a
        user turn (a fresh per-turn spawn picks up the prior session via
        ``--resume``)."""
        if subtype != "slash_command":
            return
        command = str(kwargs.get("command") or "")
        arguments = str(kwargs.get("arguments") or kwargs.get("args") or "")
        text = compose_slash_command_text(command, arguments)
        if not text:
            return
        logger.info("SubprocessTransport: sending slash command %s", text)
        asyncio.create_task(  # noqa: RUF006 — fire-and-forget; turn streams back
            self._safe_send_message(text),
            name="claude-slash-command",
        )

    async def _safe_send_message(self, content: str) -> None:
        try:
            await self.send_message(content)
        except Exception:
            logger.warning("Background slash-command send failed", exc_info=True)


def _extract_error_message(data: dict) -> str:
    message = data.get("message")
    if isinstance(message, str) and message:
        return message

    error = data.get("error")
    if isinstance(error, dict):
        nested = error.get("message")
        if isinstance(nested, str) and nested:
            return nested
    elif isinstance(error, str) and error:
        return error

    return json.dumps(data, sort_keys=True)


def _is_retryable_error(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in _RETRYABLE_ERROR_MARKERS)


def _counts_as_output(data: dict) -> bool:
    event_type = data.get("type")
    if event_type == "content_block_delta":
        return True
    if event_type == "assistant":
        return True
    if event_type == "result":
        return True
    return False
