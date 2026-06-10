"""PersistentSubprocessTransport — long-lived Claude with stream-json IO.

Spawns one ``claude -p --input-format stream-json --output-format stream-json``
process per session and keeps stdin held open across turns. Each call to
``send_message`` writes one user-message JSON line to stdin and awaits the
``result`` event that closes the corresponding turn. Subsequent calls reuse
the same process — no respawn, no ``--resume`` reload between turns.

Compared to the original ``SubprocessTransport``:

- No per-turn spawn / cold start.
- No reload of conversation history from disk between turns (Claude
  keeps it in-memory).
- Same Claude CLI flags, no SDK or WebSocket dependency — works around
  the Anthropic endpoint allowlist that breaks ``SdkWebSocketTransport``
  in self-hosted setups.

Limitations:

- Still one turn at a time. Claude reads the next stdin line only after
  the current turn ends, so this does not give true mid-turn injection;
  it gives zero-spawn queueing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import suppress
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
from skuld.transports.mcp_config import build_claude_mcp_config
from skuld.transports.tool_shims import ensure_codex_tool_shims

logger = logging.getLogger("skuld.transport")

_DEFAULT_PERMISSION_MODE = "bypassPermissions"
# StreamReader buffer for Claude stdout — single JSON events (esp. tool
# results or large file reads) routinely exceed asyncio's default 64 KB
# line limit and cause LimitOverrunError. 10 MB covers realistic worst
# cases without unbounded memory.
_STDOUT_LINE_LIMIT_BYTES = 10 * 1024 * 1024


class PersistentSubprocessTransport(CLITransport):
    """Long-lived Claude subprocess driven via stream-json stdin/stdout."""

    def __init__(
        self,
        workspace_dir: str,
        model: str = "",
        skip_permissions: bool = False,
        agent_teams: bool = False,
        system_prompt: str = "",
        initial_prompt: str = "",
        mcp_servers: list[dict] | None = None,
        resume_session_id: str = "",
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
        self._send_lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        # Set when the current turn's ``result`` event arrives. ``None``
        # when no turn is awaiting completion.
        self._turn_done: asyncio.Event | None = None
        # Seeding the session id makes the FIRST spawn pass ``--resume``,
        # which reattaches to an imported/external Claude session.
        self._session_id: str | None = resume_session_id or None
        self._last_result: dict | None = None

    # ------------------------------------------------------------------
    # CLITransport interface
    # ------------------------------------------------------------------

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(session_resume=True)

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def last_result(self) -> dict | None:
        return self._last_result

    @property
    def is_alive(self) -> bool:
        proc = self._process
        return proc is not None and proc.returncode is None

    async def start(self) -> None:
        """Spawn Claude (if not already running) and send the initial prompt."""
        if not self.is_alive:
            await self._spawn()
        if not self._initial_prompt or self._initial_prompt_sent:
            return
        self._initial_prompt_sent = True
        try:
            await self.send_message(self._initial_prompt)
        except Exception:
            self._initial_prompt_sent = False
            raise

    async def stop(self) -> None:
        """Close stdin and wait for Claude to exit gracefully."""
        proc = self._process
        if proc is None:
            return
        # Closing stdin signals "no more input" — Claude finishes any
        # in-flight turn (if any) and exits cleanly.
        try:
            if proc.stdin is not None and not proc.stdin.is_closing():
                proc.stdin.close()
        except Exception:
            logger.debug("Failed to close stdin during stop", exc_info=True)
        try:
            await _stop_process(proc)
        except Exception:
            logger.debug("Error stopping Claude subprocess", exc_info=True)
        if self._reader_task is not None:
            self._reader_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._reader_task
        self._process = None
        self._reader_task = None
        # Wake any waiter so it doesn't hang.
        if self._turn_done is not None:
            self._turn_done.set()

    async def send_message(self, content: str) -> None:
        """Write a user message and block until the turn's ``result`` arrives.

        Calls serialize on a per-instance lock so concurrent ``send_message``
        invocations queue cleanly. Each call:

        1. Acquires the lock.
        2. Re-spawns Claude if it died (``--resume`` picks up history).
        3. Writes one user-message JSON line to stdin.
        4. Awaits the turn-complete event raised by the stdout reader.
        """
        async with self._send_lock:
            if not self.is_alive:
                await self._spawn()
            self._turn_done = asyncio.Event()
            try:
                await self._write_user_message(content)
                await self._turn_done.wait()
            finally:
                self._turn_done = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_command(self) -> list[str]:
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
        # ``--resume`` applies when re-spawning after a crash or when a
        # resume id was seeded for an imported session. Otherwise the first
        # spawn has no session yet — Claude assigns one in its first
        # ``system`` event, which we capture in the stdout reader.
        if self._session_id:
            cmd.extend(["--resume", self._session_id])
        elif self._system_prompt:
            cmd.extend(["--append-system-prompt", self._system_prompt])
        if self._mcp_config:
            cmd.extend(["--mcp-config", self._mcp_config])
        return cmd

    async def _spawn(self) -> None:
        cmd = self._build_command()
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        if self._agent_teams:
            env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] = "1"
        _, shim_env = ensure_codex_tool_shims(
            self.workspace_dir,
            mcp_servers=self._raw_mcp_servers,
        )
        if shim_env:
            env.update(shim_env)

        logger.info(
            "PersistentSubprocessTransport: spawning Claude (resume=%s)",
            self._session_id,
        )
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
        self._stderr_task = asyncio.create_task(
            _drain_stream(process.stderr, "claude-stderr"),
            name="claude-stderr-drain",
        )
        self._reader_task = asyncio.create_task(
            self._read_stdout_loop(),
            name="claude-stdout-reader",
        )

    async def _write_user_message(self, content: str) -> None:
        proc = self._process
        if proc is None or proc.stdin is None or proc.stdin.is_closing():
            raise RuntimeError("Claude stdin not available")
        payload: dict[str, Any] = {
            "type": "user",
            "message": {"role": "user", "content": content},
        }
        try:
            proc.stdin.write(json.dumps(payload).encode("utf-8") + b"\n")
            await proc.stdin.drain()
        except Exception as exc:
            raise RuntimeError(f"Failed to write to Claude stdin: {exc}") from exc

    async def _read_stdout_loop(self) -> None:
        """Demultiplex Claude's stdout JSON stream.

        Three things happen per line:
          - ``system``/``init`` events carry ``session_id``; capture for
            future ``--resume`` if the process needs to re-spawn.
          - All non-trivial events get fanned out to the registered
            ``on_event`` callback (same shape the existing transport
            emits — broker code path is unchanged).
          - ``result`` events mark the end of a turn — wake the
            ``send_message`` caller blocked on ``self._turn_done``.
        """
        proc = self._process
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                text = raw.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    logger.warning(
                        "PersistentSubprocessTransport: skipping non-JSON line: %s",
                        text[:200],
                    )
                    continue
                if data.get("type") == "system":
                    sid = data.get("session_id")
                    if isinstance(sid, str) and sid:
                        self._session_id = sid
                event = _filter_event(data)
                if event is not None:
                    try:
                        await self._emit(event)
                    except Exception:
                        logger.warning(
                            "on_event handler raised for type=%s",
                            event.get("type"),
                            exc_info=True,
                        )
                if data.get("type") == "result":
                    self._last_result = data
                    if self._turn_done is not None:
                        self._turn_done.set()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "PersistentSubprocessTransport reader loop crashed",
                exc_info=True,
            )
        finally:
            # Process exited unexpectedly — unblock any send_message
            # waiter so it returns instead of hanging forever.
            if self._turn_done is not None:
                self._turn_done.set()
