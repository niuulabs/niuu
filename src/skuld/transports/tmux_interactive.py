"""TmuxInteractiveTransport — interactive Claude Code through a real TTY.

This adapter is intentionally separate from the SDK/print transports. It runs
the interactive ``claude`` REPL in tmux so built-in slash commands, terminal
history navigation, permission prompts, and tmux-backed agent-team panes stay
available. Tmux output is UI bytes, not stream-json, so the transport emits raw
terminal events and a conservative chat-shaped synthetic turn for existing
Skuld clients.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from niuu.build_info import build_info
from niuu.ports.cli import CLITransport, TransportCapabilities
from skuld.transports.claude_env import claude_spawn_env
from skuld.transports.mcp_config import build_claude_mcp_config
from skuld.transports.subprocess import _DEFAULT_PERMISSION_MODE
from skuld.transports.tool_shims import ensure_codex_tool_shims

logger = logging.getLogger("skuld.transport")

_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1B\\))")

_BUILT_IN_SLASH_COMMANDS = [
    {"name": "/agents", "description": "Manage agent teams and subagents"},
    {"name": "/compact", "description": "Compact the current conversation"},
    {"name": "/context", "description": "Inspect context usage"},
    {"name": "/cost", "description": "Show session cost"},
    {"name": "/doctor", "description": "Check Claude Code health"},
    {"name": "/exit", "description": "Exit Claude Code"},
    {"name": "/export", "description": "Export the conversation"},
    {"name": "/help", "description": "Show help"},
    {"name": "/hooks", "description": "Configure hooks"},
    {"name": "/init", "description": "Initialize project context"},
    {"name": "/login", "description": "Authenticate Claude Code"},
    {"name": "/logout", "description": "Sign out of Claude Code"},
    {"name": "/mcp", "description": "Manage MCP servers"},
    {"name": "/memory", "description": "Manage memory"},
    {"name": "/model", "description": "Switch model"},
    {"name": "/permissions", "description": "Manage permissions"},
    {"name": "/resume", "description": "Resume a prior session"},
    {"name": "/review", "description": "Review code changes"},
    {"name": "/status", "description": "Show Claude Code status"},
    {"name": "/terminal-setup", "description": "Configure terminal integration"},
    {"name": "/vim", "description": "Toggle Vim mode"},
]

_KEY_ALIASES = {
    "ctrl-c": "C-c",
    "ctrl+d": "C-d",
    "ctrl-d": "C-d",
    "ctrl+r": "C-r",
    "ctrl-r": "C-r",
    "enter": "Enter",
    "escape": "Escape",
    "esc": "Escape",
    "tab": "Tab",
    "backspace": "BSpace",
    "delete": "Delete",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
}

_CLAUDE_HOOK_EVENTS = [
    "SessionStart",
    "UserPromptSubmit",
    "UserPromptExpansion",
    "PreToolUse",
    "PermissionRequest",
    "PermissionDenied",
    "PostToolUse",
    "PostToolUseFailure",
    "PostToolBatch",
    "Notification",
    "SubagentStart",
    "SubagentStop",
    "TaskCreated",
    "TaskCompleted",
    "TeammateIdle",
    "PreCompact",
    "PostCompact",
    "Stop",
    "StopFailure",
    "SessionEnd",
    "Elicitation",
    "ElicitationResult",
    "InstructionsLoaded",
    "CwdChanged",
]

_OPTIONAL_HIGH_VOLUME_HOOK_EVENTS = [
    "MessageDisplay",
]

_SLASH_COMMAND_ROW_RE = re.compile(r"^(/\S+)\s{2,}(.+?)\s*$")
# A numbered selection row in an interactive Claude menu, e.g. " 1. Allow" or
# "❯ 2. Allow & don't ask again". The single source of truth for menu parsing —
# the tmux test harness (tests/support/forge/tmux_page.py) imports this so it
# parses menus exactly the way the shipped transport does.
_MENU_ROW_RE = re.compile(r"^\s*[❯>\s]*([1-9])[.)]\s+(.+?)\s*$")


@dataclass
class _TmuxResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class _PaneState:
    pane_id: str
    pane_index: str
    window_name: str
    active: bool
    current_command: str
    width: int
    height: int
    cursor_x: int
    cursor_y: int
    log_path: Path


class TmuxCommandError(RuntimeError):
    """Raised when a required tmux command fails."""


class TmuxInteractiveTransport(CLITransport):
    """Drive an interactive Claude Code process through tmux."""

    def __init__(
        self,
        workspace_dir: str,
        model: str = "",
        session_id: str = "",
        skip_permissions: bool = False,
        agent_teams: bool = False,
        system_prompt: str = "",
        initial_prompt: str = "",
        mcp_servers: list[dict] | None = None,
        sdk_port: int | None = None,
        turn_idle_timeout_s: float | None = None,
        turn_no_output_timeout_s: float | None = None,
        turn_max_seconds: float | None = None,
        pane_poll_interval_s: float | None = None,
        frame_interval_s: float | None = None,
    ) -> None:
        super().__init__()
        self.workspace_dir = workspace_dir
        self._model = model
        self._forge_session_id = session_id or "skuld-interactive"
        self._skip_permissions = skip_permissions
        self._agent_teams = agent_teams
        self._system_prompt = system_prompt
        self._initial_prompt = initial_prompt
        self._raw_mcp_servers = list(mcp_servers or [])
        self._mcp_config = build_claude_mcp_config(mcp_servers or [])
        self._sdk_port = sdk_port

        self._session_name = self._safe_name(f"skuld-{self._forge_session_id}")[:80]
        base_socket_dir = Path(
            os.environ.get("SKULD__TMUX_SOCKET_DIR", f"/tmp/skuld-tmux-{os.getuid()}")
        )
        self._socket_path = base_socket_dir / f"{self._session_name}.sock"
        self._runtime_dir = (
            Path(self.workspace_dir) / ".skuld" / "tmux-interactive" / self._session_name
        )
        self._pane_log_dir = self._runtime_dir / "panes"
        self._hook_settings_path = self._runtime_dir / "claude-hooks.settings.json"

        self._turn_idle_timeout_s = self._float_env(
            "SKULD__TMUX_TURN_IDLE_TIMEOUT_SECONDS", turn_idle_timeout_s, 3.0
        )
        self._turn_no_output_timeout_s = self._float_env(
            "SKULD__TMUX_TURN_NO_OUTPUT_TIMEOUT_SECONDS", turn_no_output_timeout_s, 30.0
        )
        self._turn_max_seconds = self._float_env(
            "SKULD__TMUX_TURN_MAX_SECONDS", turn_max_seconds, 3600.0
        )
        self._pane_poll_interval_s = self._float_env(
            "SKULD__TMUX_PANE_POLL_INTERVAL_SECONDS", pane_poll_interval_s, 1.0
        )
        # E3 race fix: a structured answer can arrive BEFORE the on-screen menu has
        # rendered. Bound-poll the live menu for up to this many seconds (in
        # _pane_poll_interval_s steps, or a short fallback step) before pressing a
        # key, so the chosen row's digit lands instead of the default first row.
        self._menu_render_wait_s = self._float_env(
            "SKULD__TMUX_MENU_RENDER_WAIT_SECONDS", None, 1.5
        )
        # Poll cadence while waiting for the menu to render (capped by the pane poll
        # interval). Config-driven so there is no bare literal in the wait loop.
        self._menu_poll_step_s = self._float_env("SKULD__TMUX_MENU_POLL_STEP_SECONDS", None, 0.1)
        # BUG-3: hard ceiling on a single steering delivery (lock wait + paste). A prior
        # delivery that wedged the send lock, or a hung tmux subprocess, must surface as a
        # loud error the client can see — never a silent swallow of the user's message.
        self._deliver_timeout_s = self._float_env("SKULD__TMUX_DELIVER_TIMEOUT_SECONDS", None, 15.0)
        self._frame_interval_s = self._float_env(
            "SKULD__TMUX_FRAME_INTERVAL_SECONDS", frame_interval_s, 0.12
        )
        self._emit_raw_terminal_output = self._bool_env("SKULD__TMUX_EMIT_RAW_OUTPUT", False)
        self._hook_events_enabled = self._bool_env(
            "SKULD__TMUX_HOOK_EVENTS_ENABLED",
            bool(self._sdk_port),
        )
        self._message_display_hook_enabled = self._bool_env(
            "SKULD__TMUX_MESSAGE_DISPLAY_HOOK_ENABLED",
            False,
        )
        # Remote Control (default ON): ALSO expose the live CLI on the host's claude.ai
        # login so the same session can be driven / observed from the Anthropic apps
        # (claude.ai/code + phone) IN PARALLEL with the Volundr/Lexi API — a stable second
        # control plane while the native tmux path is hardened. Requires subscription auth:
        # the API-key path can't register a session to the account (it blocks on the
        # interactive "use this API key?" chooser), so force it off under
        # SKULD__CLAUDE_AUTH=api_key. Toggle with SKULD__TMUX_REMOTE_CONTROL=0/1.
        self._claude_auth_mode = (
            os.environ.get("SKULD__CLAUDE_AUTH", "subscription").strip().lower()
        )
        self._remote_control = (
            self._bool_env("SKULD__TMUX_REMOTE_CONTROL", True)
            and self._claude_auth_mode != "api_key"
        )

        self._alive = False
        self._initial_prompt_sent = False
        self._lifecycle_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        # Agents in flight, keyed by a resolved id (tool_use_id for Task-tool
        # subagents, subagent_id for SubagentStart, etc.). Lets the matching
        # PostToolUse / SubagentStop emit the "stopped" agent_update, and lets the
        # Task and Subagent* signals for the same agent merge instead of duplicate.
        self._hook_agents: dict[str, dict[str, Any]] = {}
        self._panes: dict[str, _PaneState] = {}
        self._tail_tasks: dict[str, asyncio.Task[None]] = {}
        self._frame_tasks: dict[str, asyncio.Task[None]] = {}
        self._last_frame_signature: dict[str, str] = {}
        self._pane_sequences: dict[str, int] = {}
        self._pane_watcher_task: asyncio.Task[None] | None = None
        # CLI-mode questions bridge (2026-06-21): the interactive Claude CLI renders permission
        # gates + AskUserQuestion menus in the TTY (not the SDK's structured ask_user_question).
        # This map mirrors the SDK's `_pending_questions` (request_id -> pending prompt) so a remote
        # client can answer structurally; we translate the choice back into pane keystrokes. Popped
        # on answer; cleared when the turn finishes.
        self._pending_tty_prompts: dict[str, dict[str, Any]] = {}
        self._tty_question_seq = 0
        self._last_result: dict | None = None
        self._slash_commands_cache = self._normalize_slash_command_items(
            _BUILT_IN_SLASH_COMMANDS,
            source="static",
        )

        self._turn_active = False
        self._turn_started_at = 0.0
        self._turn_last_output_at: float | None = None
        self._turn_stream_started = False
        self._turn_buffer: list[str] = []
        self._turn_last_clean_text = ""
        # BUG-2: the prompt text delivered this turn, for a best-effort input-token estimate
        # in the synthesized result frame (so completed tmux turns advance message_count).
        self._turn_prompt_text = ""
        self._turn_done: asyncio.Event | None = None
        self._turn_watchdog_task: asyncio.Task[None] | None = None

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(
            interrupt=True,
            slash_commands=True,
            steer=True,
            # The interactive CLI inserts queued input at the right moment, so a
            # new message is delivered by typing it into the live pane — never by
            # stopping and restarting the turn. The broker routes EVERY message
            # (mid-turn or idle) through the steer path for native transports.
            steering_mode="native",
            terminal_output=True,
            terminal_input=True,
            terminal_keys=True,
            terminal_resize=True,
            terminal_panes=True,
        )

    @property
    def session_id(self) -> str | None:
        return self._session_name

    @property
    def last_result(self) -> dict | None:
        return self._last_result

    @property
    def is_alive(self) -> bool:
        return self._alive

    @property
    def is_turn_active(self) -> bool:
        return self._turn_active

    async def start(self) -> None:
        await self._ensure_started()

        if self._initial_prompt and not self._initial_prompt_sent:
            self._initial_prompt_sent = True
            try:
                await self.send_message(self._initial_prompt)
            except Exception:
                self._initial_prompt_sent = False
                raise

    async def _ensure_started(self) -> None:
        async with self._lifecycle_lock:
            if not self._tmux_binary_exists():
                raise RuntimeError("tmux is required for TmuxInteractiveTransport")
            self._runtime_dir.mkdir(parents=True, exist_ok=True)
            self._pane_log_dir.mkdir(parents=True, exist_ok=True)
            self._socket_path.parent.mkdir(parents=True, exist_ok=True)

            if not await self._has_session():
                await self._create_session()
                await self._apply_tmux_options()
            self._alive = True
            await self._refresh_panes(emit_events=True)
            if self._pane_watcher_task is None or self._pane_watcher_task.done():
                self._pane_watcher_task = asyncio.create_task(
                    self._watch_panes(),
                    name=f"tmux-pane-watch-{self._session_name}",
                )
            await self._emit_system_init()

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            self._alive = False
            if self._pane_watcher_task is not None:
                self._pane_watcher_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await self._pane_watcher_task
                self._pane_watcher_task = None
            if self._turn_watchdog_task is not None:
                self._turn_watchdog_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await self._turn_watchdog_task
                self._turn_watchdog_task = None
            for task in list(self._tail_tasks.values()):
                task.cancel()
            for task in list(self._frame_tasks.values()):
                task.cancel()
            for task in list(self._tail_tasks.values()):
                with suppress(asyncio.CancelledError, Exception):
                    _ = await task
            for task in list(self._frame_tasks.values()):
                with suppress(asyncio.CancelledError, Exception):
                    _ = await task
            self._tail_tasks.clear()
            self._frame_tasks.clear()
            self._panes.clear()
            if self._turn_done is not None:
                self._turn_done.set()
            await self._run_tmux("kill-session", "-t", self._session_name, check=False)

    async def send_message(self, content: str) -> None:
        """Deliver a user message by typing it into the live interactive CLI.

        This is real steering: we never stop/restart the turn. The CLI queues
        the keystrokes and inserts them at the right moment (mid-turn it steers
        the running turn; idle it starts a fresh one). We do NOT block waiting
        for the turn to finish — the response streams back asynchronously via
        the pane tail / Claude hooks, and completion is detected by the
        watchdog or the Stop hook.
        """
        await self._deliver_user_text(content)

    async def _deliver_user_text(self, content: str) -> None:
        """Paste content into the pane and ensure a turn is being tracked.

        The send lock is held only for the paste (to keep concurrent messages
        ordered), NOT for the whole turn — so a follow-up message is never
        blocked behind an in-flight turn the way a turn-scoped lock would do.

        BUG-3: the lock acquisition AND the paste are bounded by
        ``_deliver_timeout_s``. After a WS crash/reconnect a wedged prior delivery
        (or a hung tmux subprocess) used to hold the lock forever, so every later
        steering message blocked silently — the user typed and "nothing happened".
        Now a stuck channel raises a clear error the broker turns into a
        ``user_delivery_failed`` the client can render, instead of a silent drop.
        """
        preview = content.strip()[:80].replace("\n", "⏎")
        logger.info(
            "tmux deliver: %d chars (turn_active=%s, alive=%s, lock_held=%s): %r",
            len(content),
            self._turn_active,
            self._alive,
            self._send_lock.locked(),
            preview,
        )
        try:
            await asyncio.wait_for(self._send_lock.acquire(), timeout=self._deliver_timeout_s)
        except TimeoutError as exc:
            logger.error(
                "tmux deliver: send lock held >%.0fs — a prior delivery is wedged; "
                "message NOT delivered: %r",
                self._deliver_timeout_s,
                preview,
            )
            raise RuntimeError(
                "steering message not delivered: the session input channel has been "
                f"busy for >{self._deliver_timeout_s:.0f}s (prior delivery stuck)"
            ) from exc
        try:
            if not self.is_alive:
                await self._ensure_started()
            if self._turn_active:
                # Mid-turn steer: keep the SAME turn alive. Refresh the idle
                # clock so the completion watchdog doesn't fire in the gap
                # before the CLI echoes the steered input.
                self._turn_last_output_at = time.monotonic()
            else:
                self._begin_turn()
            # BUG-2: remember the prompt text for the result frame's input-token estimate
            # (append across a mid-turn steer; _begin_turn reset it for a fresh turn).
            self._turn_prompt_text += ("\n" if self._turn_prompt_text else "") + content
            await asyncio.wait_for(
                self._paste_text(content, enter=True), timeout=self._deliver_timeout_s
            )
        except TimeoutError as exc:
            logger.error(
                "tmux deliver: paste into pane timed out after %.0fs: %r",
                self._deliver_timeout_s,
                preview,
            )
            raise RuntimeError(
                f"steering message not delivered: tmux paste timed out after "
                f"{self._deliver_timeout_s:.0f}s"
            ) from exc
        finally:
            self._send_lock.release()
        logger.info("tmux deliver: pasted %d chars into pane OK", len(content))

    def _begin_turn(self) -> None:
        """Start tracking a new turn so its output streams and completion fires.

        Used by both a fresh message and the first message of an idle session.
        Does NOT finish any prior turn — delivery is non-disruptive.
        """
        self._turn_done = asyncio.Event()
        self._turn_active = True
        self._turn_started_at = time.monotonic()
        self._turn_last_output_at = None
        self._turn_stream_started = False
        self._turn_buffer = []
        self._turn_last_clean_text = ""
        self._turn_prompt_text = ""
        # The watchdog captures the `_turn_done` Event it was started with. A fresh
        # turn always gets a freshly-created Event (above), so we MUST bind a live
        # watchdog to it — otherwise the new turn never closes. Cancel any prior
        # watchdog: if it already finished (or signalled the previous turn but is
        # still mid-sleep), reusing it would leave the new Event with no watcher.
        prior = self._turn_watchdog_task
        if prior is not None and not prior.done():
            prior.cancel()
        self._turn_watchdog_task = asyncio.create_task(
            self._watch_turn_completion(self._turn_done),
            name=f"tmux-turn-watch-{self._session_name}",
        )

    async def interrupt(self) -> None:
        await self.send_control("interrupt")

    async def send_control(self, subtype: str, **kwargs: object) -> None:
        if not self.is_alive:
            await self.start()

        if subtype == "interrupt":
            await self._send_key("C-c", pane_id=self._coerce_str(kwargs.get("pane_id")))
            if self._turn_active:
                await self._finish_synthetic_turn(reason="interrupted", is_error=True)
            return

        if subtype == "ask_user_answer":
            # CLI-mode questions bridge: a remote client answered a TTY permission/question we
            # surfaced as a structured `ask_user_question`. Translate the choice back into the pane
            # (digit for the matched menu row; Escape to deny). See `_answer_tty_prompt`.
            await self._answer_tty_prompt(
                self._coerce_str(kwargs.get("request_id")),
                kwargs.get("answers"),
                pane_id=self._coerce_str(kwargs.get("pane_id")),
            )
            return

        if subtype in {"terminal_input", "input"}:
            text = self._coerce_str(kwargs.get("data")) or self._coerce_str(kwargs.get("text"))
            if text:
                await self._paste_text(
                    text,
                    enter=bool(kwargs.get("enter", False)),
                    pane_id=self._coerce_str(kwargs.get("pane_id")),
                )
            return

        if subtype in {"terminal_key", "key"}:
            raw_keys = kwargs.get("keys")
            if isinstance(raw_keys, list):
                keys = [self._normalize_key(str(key)) for key in raw_keys if str(key)]
            else:
                key = self._coerce_str(kwargs.get("key")) or self._coerce_str(raw_keys)
                keys = [self._normalize_key(key)] if key else []
            for key in keys:
                await self._send_key(key, pane_id=self._coerce_str(kwargs.get("pane_id")))
            return

        if subtype == "terminal_resize":
            cols = int(kwargs.get("cols") or kwargs.get("columns") or 0)
            rows = int(kwargs.get("rows") or 0)
            if cols > 0 and rows > 0:
                await self._resize_pane(
                    cols=cols,
                    rows=rows,
                    pane_id=self._coerce_str(kwargs.get("pane_id")),
                )
            return

        if subtype in {"slash_command", "terminal_slash_command"}:
            command = self._coerce_str(kwargs.get("command"))
            if command:
                await self._send_slash_command(
                    command,
                    arguments=(
                        self._coerce_str(kwargs.get("arguments"))
                        or self._coerce_str(kwargs.get("args"))
                    ),
                    pane_id=self._coerce_str(kwargs.get("pane_id")),
                )
            return

        if subtype in {"redirect", "steer"}:
            content = self._coerce_str(kwargs.get("content"))
            if content:
                # Same native delivery as a regular message: type it into the
                # live CLI and ensure the turn is tracked so the reply streams.
                await self._deliver_user_text(content)

    async def discover_slash_commands(self, *, refresh: bool = False) -> list[dict]:
        """Discover slash commands from Claude Code's live `/` autocomplete menu."""
        if not refresh and self._slash_commands_cache:
            return list(self._slash_commands_cache)
        if not self.is_alive:
            await self.start()
        if self._turn_active:
            return list(self._slash_commands_cache)

        async with self._send_lock:
            if self._turn_active:
                return list(self._slash_commands_cache)
            commands = await self._discover_slash_commands_from_terminal()
            if commands:
                self._slash_commands_cache = commands
                await self._emit(
                    {
                        "type": "slash_commands",
                        "event_type": "slash.commands",
                        "commands": commands,
                        "source": "tmux_autocomplete",
                    }
                )
            return list(self._slash_commands_cache)

    async def send_control_response(self, request_id: str, response: dict) -> None:
        await self._emit(
            {
                "type": "terminal_control_response_ignored",
                "request_id": request_id,
                "response": response,
                "reason": "interactive terminal permission prompts are handled in the TTY",
            }
        )

    async def handle_claude_hook(self, payload: dict[str, Any]) -> bool:
        """Convert Claude Code hook callbacks into Skuld events.

        The terminal pane remains the interactive display channel. Hooks provide
        the semantic channel: prompt state, tool calls/results, permissions, and
        the final assistant message for a turn.
        """
        event_name = self._hook_event_name(payload)
        await self._emit(
            {
                "type": "claude_hook",
                "event_type": "claude.hook",
                "hook_event_name": event_name,
                "payload": payload,
                "metadata": {"source": "claude_hook"},
            }
        )

        if event_name == "UserPromptSubmit":
            self._mark_semantic_turn_started()
            prompt = payload.get("prompt")
            await self._emit(
                {
                    "type": "terminal_prompt_submitted",
                    "event_type": "claude.prompt.submitted",
                    "prompt": prompt if isinstance(prompt, str) else "",
                    "claude_session_id": payload.get("session_id"),
                    "transcript_path": payload.get("transcript_path"),
                    "metadata": {"source": "claude_hook"},
                }
            )
            return True

        if event_name == "PreToolUse":
            self._mark_semantic_turn_started()
            await self._emit_tool_use_from_hook(payload)
            return True

        if event_name in {"PostToolUse", "PostToolUseFailure"}:
            await self._emit_tool_result_from_hook(
                payload,
                is_error=event_name == "PostToolUseFailure",
            )
            return True

        if event_name == "PermissionRequest":
            await self._emit_permission_request_from_hook(payload)
            return True

        if event_name == "SubagentStart":
            await self._surface_subagent_start(payload)
            return True

        if event_name == "SubagentStop":
            await self._surface_subagent_stop(payload)
            return True

        if event_name == "Stop":
            await self._finish_hook_turn(
                content=self._coerce_str(payload.get("last_assistant_message")),
                reason="stop",
            )
            return True

        if event_name == "StopFailure":
            await self._finish_hook_turn(
                content=(
                    self._coerce_str(payload.get("last_assistant_message"))
                    or self._coerce_str(payload.get("error"))
                ),
                reason="stop_failure",
                is_error=True,
            )
            return True

        return True

    @staticmethod
    def _hook_event_name(payload: dict[str, Any]) -> str:
        event_name = (
            payload.get("hook_event_name")
            or payload.get("hookEventName")
            or payload.get("event")
            or payload.get("hook_event")
            or "unknown"
        )
        return str(event_name)

    def _mark_semantic_turn_started(self) -> None:
        if self._turn_active:
            return
        self._turn_active = True
        self._turn_started_at = time.monotonic()
        self._turn_last_output_at = self._turn_started_at
        self._turn_stream_started = False
        self._turn_buffer = []
        self._turn_last_clean_text = ""

    async def _emit_tool_use_from_hook(self, payload: dict[str, Any]) -> None:
        tool_name = self._coerce_str(payload.get("tool_name"))
        if not tool_name:
            return
        tool_input = payload.get("tool_input")
        # CLI-mode questions bridge: the AskUserQuestion tool renders its menu in the TTY. Surface
        # it as a structured `ask_user_question` (its tool_input already carries the questions iOS
        # parses) so a remote client can answer; the tool_use is still emitted below for history.
        if tool_name == "AskUserQuestion" and isinstance(tool_input, dict):
            await self._surface_tty_ask_user_question(tool_input)
        tool_use_id = self._coerce_str(payload.get("tool_use_id")) or (f"hook-{uuid.uuid4().hex}")
        # Plan surfacing: TodoWrite carries Claude's full task list each call.
        if tool_name == "TodoWrite" and isinstance(tool_input, dict):
            await self._surface_plan_from_todowrite(tool_input)
        # Agents surfacing: the Task tool spawns a subagent. Register it by
        # tool_use_id so the matching PostToolUse can mark it stopped.
        if tool_name == "Task" and isinstance(tool_input, dict):
            await self._surface_agent_started_from_task(tool_use_id, tool_input)
        await self._emit(
            {
                "type": "assistant",
                "message": {
                    "model": self._model or "interactive",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": tool_use_id,
                            "name": tool_name,
                            "input": tool_input if isinstance(tool_input, dict) else {},
                        }
                    ],
                },
                "metadata": {
                    "source": "claude_hook",
                    "hook_event_name": "PreToolUse",
                    "claude_session_id": payload.get("session_id"),
                    "transcript_path": payload.get("transcript_path"),
                },
            }
        )

    async def _emit_tool_result_from_hook(
        self,
        payload: dict[str, Any],
        *,
        is_error: bool,
    ) -> None:
        tool_use_id = self._coerce_str(payload.get("tool_use_id"))
        if not tool_use_id:
            return
        # Agents surfacing: a finished Task tool means its subagent stopped.
        if tool_use_id in self._hook_agents:
            await self._finish_agent(tool_use_id, is_error=is_error)
        result = payload.get("tool_response")
        if result is None:
            result = payload.get("error", "")
        await self._emit(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": self._stringify_hook_value(result),
                            "is_error": is_error,
                        }
                    ]
                },
                "metadata": {
                    "source": "claude_hook",
                    "hook_event_name": ("PostToolUseFailure" if is_error else "PostToolUse"),
                    "claude_session_id": payload.get("session_id"),
                    "transcript_path": payload.get("transcript_path"),
                },
            }
        )

    async def _emit_permission_request_from_hook(self, payload: dict[str, Any]) -> None:
        tool_input = payload.get("tool_input")
        await self._emit(
            {
                "type": "claude_permission_request",
                "event_type": "claude.permission.request",
                "tool_name": payload.get("tool_name"),
                "input": tool_input if isinstance(tool_input, dict) else {},
                "permission_suggestions": payload.get("permission_suggestions", []),
                "claude_session_id": payload.get("session_id"),
                "transcript_path": payload.get("transcript_path"),
                "metadata": {"source": "claude_hook"},
            }
        )
        # CLI-mode questions bridge: ALSO surface the gate as a structured `ask_user_question` so a
        # remote client renders its answer card + the session flips to awaiting_input. The keystroke
        # translation on answer happens in `_answer_tty_prompt`.
        await self._surface_tty_permission(payload)

    # ──────────────────────────── Plan + running-agents surfacing ────────────────────────────

    @staticmethod
    def _normalize_status(status: object) -> str:
        text = str(status or "").strip().lower()
        if text in {"pending", "in_progress", "completed"}:
            return text
        if text in {"done", "complete", "finished"}:
            return "completed"
        if text in {"running", "active", "doing"}:
            return "in_progress"
        return text or "pending"

    async def _surface_plan_from_todowrite(self, tool_input: dict[str, Any]) -> None:
        """Emit Claude's full task list (the TodoWrite plan) as a structured `plan`."""
        raw = tool_input.get("todos")
        if not isinstance(raw, list):
            return
        tasks: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            content = self._coerce_str(item.get("content"))
            if not content:
                continue
            task = {"content": content, "status": self._normalize_status(item.get("status"))}
            active_form = self._coerce_str(item.get("activeForm"))
            if active_form:
                task["activeForm"] = active_form
            tasks.append(task)
        counts = {"total": len(tasks)}
        for status in ("pending", "in_progress", "completed"):
            counts[status] = sum(1 for t in tasks if t["status"] == status)
        await self._emit(
            {
                "type": "plan",
                "event_type": "claude.plan",
                "tasks": tasks,
                "counts": counts,
                "metadata": {"source": "claude_hook"},
            }
        )

    @staticmethod
    def _resolve_agent_id(payload: dict[str, Any]) -> str:
        """Best-available stable id for an agent across signals. Preferring
        tool_use_id lets a SubagentStart that carries the Task's tool_use_id merge
        with the Task entry instead of double-counting the same subagent."""
        for key in ("tool_use_id", "subagent_id", "agent_id", "id"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    async def _start_agent(
        self,
        agent_id: str,
        *,
        kind: str,
        name: str,
        description: str = "",
        model: str = "",
    ) -> None:
        """Upsert an in-flight agent and emit a `started` agent_update. Re-emitting
        for a known id merges new fields (e.g. SubagentStart enriching a Task entry)
        — the broker registry is keyed by id, so this never duplicates."""
        if not agent_id:
            return
        agent = dict(self._hook_agents.get(agent_id, {}))
        agent.update({"id": agent_id, "kind": kind, "name": name, "status": "running"})
        if description:
            agent["description"] = description
        if model:
            agent["model"] = model
        agent.setdefault("started_at", datetime.now(UTC).isoformat())
        self._hook_agents[agent_id] = agent
        await self._emit_agent_update(agent, action="started")

    async def _finish_agent(self, agent_id: str, *, is_error: bool) -> None:
        agent = self._hook_agents.pop(agent_id, None)
        if agent is None:
            return
        agent = {**agent, "status": "failed" if is_error else "done"}
        await self._emit_agent_update(agent, action="stopped")

    async def _surface_agent_started_from_task(
        self, tool_use_id: str, tool_input: dict[str, Any]
    ) -> None:
        """A Task-tool call spawned a subagent."""
        name = (
            self._coerce_str(tool_input.get("subagent_type"))
            or self._coerce_str(tool_input.get("description"))
            or "subagent"
        )
        await self._start_agent(
            tool_use_id,
            kind="subagent",
            name=name,
            description=self._coerce_str(tool_input.get("description")),
        )

    async def _surface_subagent_start(self, payload: dict[str, Any]) -> None:
        """A SubagentStart hook — Claude's purpose-built subagent-lifecycle signal.

        Real Claude (validated live) sends {agent_id, agent_type, cwd, session_id,
        transcript_path}; the id is agent_id and the name is agent_type. Other field
        spellings are accepted defensively for forward/backward compatibility.
        """
        agent_id = self._resolve_agent_id(payload)
        name = (
            self._coerce_str(payload.get("agent_type"))
            or self._coerce_str(payload.get("subagent_name"))
            or self._coerce_str(payload.get("subagent_type"))
            or self._coerce_str(payload.get("name"))
            or "subagent"
        )
        await self._start_agent(
            agent_id,
            kind="subagent",
            name=name,
            description=self._coerce_str(payload.get("task")),
            model=self._coerce_str(payload.get("model")),
        )

    async def _surface_subagent_stop(self, payload: dict[str, Any]) -> None:
        reason = self._coerce_str(payload.get("reason")).lower()
        is_error = reason in {"failed", "error", "interrupted"} or bool(payload.get("error"))
        await self._finish_agent(self._resolve_agent_id(payload), is_error=is_error)

    async def _emit_agent_update(self, agent: dict[str, Any], *, action: str) -> None:
        await self._emit(
            {
                "type": "agent_update",
                "event_type": "claude.agent",
                "action": action,
                "agent": agent,
                "metadata": {"source": "claude_hook"},
            }
        )

    # ──────────────────────────── CLI-mode questions bridge ────────────────────────────
    #
    # The interactive Claude CLI handles permission gates + AskUserQuestion IN THE TTY (a numbered
    # selection menu — see `_is_terminal_chrome_row`: "Do you want to proceed?" + "1. ./2. ./3. .").
    # The SDK transport emits a structured, blocking `ask_user_question` (request_id + questions)
    # the broker turns into `awaiting_input` and iOS answers via `ask_user_answer`. We give the tmux
    # transport PARITY with no client change: surface the TTY prompt as the SAME structured frame,
    # then on the structured answer translate the chosen option back into pane keystrokes.

    def _next_tty_request_id(self) -> str:
        self._tty_question_seq += 1
        return f"tty-{self._tty_question_seq}-{uuid.uuid4().hex[:8]}"

    async def _surface_tty_permission(self, payload: dict[str, Any]) -> None:
        """Surface a Claude-CLI permission gate as a structured `ask_user_question`.

        The on-screen rows aren't reliably available yet at hook time (they render a beat later), so
        we offer Claude's stable 3-option shape (Allow / Allow & don't ask again / Deny) and resolve
        the actual keystroke against the LIVE menu at answer time (`_answer_tty_prompt`) — race-free
        (the menu is on screen while the human deliberates) and tolerant of 2-row menus.
        """
        tool_name = self._coerce_str(payload.get("tool_name")) or "this action"
        tool_input = payload.get("tool_input")
        detail = self._permission_detail(tool_name, tool_input)
        request_id = self._next_tty_request_id()
        question = {
            "header": tool_name,
            "question": detail or f"Allow {tool_name}?",
            "options": [
                {"label": "Allow"},
                {"label": "Allow & don't ask again"},
                {"label": "Deny"},
            ],
            "multiSelect": False,
        }
        self._pending_tty_prompts[request_id] = {"kind": "permission", "tool_name": tool_name}
        await self._emit_ask_user_question(request_id, [question])

    async def _surface_tty_ask_user_question(self, tool_input: dict[str, Any]) -> None:
        """Surface the AskUserQuestion tool's TTY menu as a structured `ask_user_question`.

        `tool_input.questions` is already in the shape iOS parses (header/question/options), so this
        is a pass-through; the on-screen menu mirrors those options, which `_answer_tty_prompt`
        matches by label at answer time.
        """
        questions = tool_input.get("questions")
        if not isinstance(questions, list) or not questions:
            return
        request_id = self._next_tty_request_id()
        self._pending_tty_prompts[request_id] = {"kind": "question", "questions": questions}
        await self._emit_ask_user_question(request_id, questions)

    async def _emit_ask_user_question(self, request_id: str, questions: list) -> None:
        # `event_type` makes the broker flip to awaiting_input (its event_type=="ask_user_question"
        # branch); `type` is what the iOS client switches on (its frame arm). Carry both.
        await self._emit(
            {
                "type": "ask_user_question",
                "event_type": "ask_user_question",
                "request_id": request_id,
                "tool_use_id": request_id,
                "questions": questions,
                "metadata": {"source": "tmux_tty_bridge"},
            }
        )

    @staticmethod
    def _permission_detail(tool_name: str, tool_input: object) -> str:
        """A short human prompt for a gate, e.g. 'Run `npm test`?' / 'Allow Edit on src/app.py?'."""
        if not isinstance(tool_input, dict):
            return f"Allow {tool_name}?"
        if tool_name == "Bash":
            cmd = str(tool_input.get("command") or "").strip()
            return f"Run `{cmd}`?" if cmd else "Run this command?"
        for key in ("file_path", "path", "url", "pattern"):
            val = str(tool_input.get(key) or "").strip()
            if val:
                return f"Allow {tool_name} on {val}?"
        return f"Allow {tool_name}?"

    async def _answer_tty_prompt(
        self, request_id: str | None, answers: object, *, pane_id: str | None = None
    ) -> None:
        """Translate a structured `ask_user_answer` into the keystroke that drives the TTY menu.

        Robust by design: DENY is always `Escape` (cancels any menu shape); ALLOW/option selection
        reads the LIVE numbered menu and presses the matched row's digit (race-free, and tolerant of
        2- vs 3-row menus). For the AskUserQuestion tool we confirm the digit with Enter (its
        select-list needs it); a permission gate acts on the digit alone.
        """
        pending = self._pending_tty_prompts.pop(request_id, None) if request_id else None
        if pending is None:
            # Stale/unknown id (e.g. answered in-terminal). If exactly one prompt is pending, answer
            # that; otherwise no-op rather than guess.
            if len(self._pending_tty_prompts) == 1:
                request_id, pending = self._pending_tty_prompts.popitem()
            else:
                return

        chosen = self._first_answer_text(answers)
        kind = pending.get("kind")

        if kind == "permission" and self._is_deny_answer(chosen):
            await self._send_key("Escape", pane_id=pane_id)
            await self._emit_ask_user_resolved(request_id, "deny")
            return

        rows = await self._capture_menu_rows_wait(pane_id=pane_id)
        digit = self._match_menu_digit(chosen, rows)
        if digit is None:
            # No matching numbered row. The affirmative row is the highlighted default, so Enter
            # accepts it; for a question fall back to the first option then confirm.
            if kind == "permission":
                await self._send_key("Enter", pane_id=pane_id)
            else:
                await self._send_key("1", pane_id=pane_id)
                await self._send_key("Enter", pane_id=pane_id)
        else:
            await self._send_key(str(digit), pane_id=pane_id)
            if kind == "question":
                await self._send_key("Enter", pane_id=pane_id)
        await self._emit_ask_user_resolved(request_id, chosen or "answered")

    async def _capture_menu_rows_wait(self, *, pane_id: str | None = None) -> list[tuple[int, str]]:
        """Capture the numbered menu, bound-polling until it renders (E3 race fix).

        A structured answer can arrive before the on-screen menu has drawn. Poll the
        live pane for up to ``_menu_render_wait_s`` (in short steps) and return as soon
        as rows appear, so the chosen row's digit lands instead of the default first
        row. Falls back to whatever is on screen (possibly empty) if it never renders.
        """
        deadline = time.monotonic() + max(self._menu_render_wait_s, 0.0)
        step = (
            min(self._pane_poll_interval_s, self._menu_poll_step_s)
            if self._pane_poll_interval_s > 0
            else self._menu_poll_step_s
        )
        while True:
            rows = await self._capture_menu_rows(pane_id=pane_id)
            if rows:
                return rows
            if time.monotonic() >= deadline:
                return rows
            await asyncio.sleep(step)

    async def _capture_menu_rows(self, *, pane_id: str | None = None) -> list[tuple[int, str]]:
        """Numbered option rows currently on screen, e.g. [(1,'Yes'), (2,'…ask'), (3,'No')]."""
        target = self._target_pane(pane_id)
        try:
            result = await self._run_tmux("capture-pane", "-t", target, "-p", "-S", "-50")
        except Exception:  # pragma: no cover - capture is best-effort
            return []
        out: list[tuple[int, str]] = []
        seen: set[int] = set()
        for line in result.stdout.splitlines():
            match = _MENU_ROW_RE.match(line)
            if match:
                digit = int(match.group(1))
                if digit not in seen:
                    seen.add(digit)
                    out.append((digit, match.group(2).strip()))
        return sorted(out)

    @staticmethod
    def _match_menu_digit(chosen: str, rows: list[tuple[int, str]]) -> int | None:
        """Map the chosen option label to its on-screen menu digit, or None if no clear match."""
        low = chosen.strip().lower()
        if not low or not rows:
            return None
        # 1a) EXACT label match over ALL rows first — a verbatim choice must win
        # over any shorter substring row. Without this exact-first pass, choosing
        # "Allow & don't ask again" would be captured by row "Allow" via the
        # substring test below and silently downgraded to a one-time allow.
        for digit, label in rows:
            if low == label.lower():
                return digit
        # 1b) substring match (one label contains the other), in row order.
        for digit, label in rows:
            ll = label.lower()
            if low in ll or ll in low:
                return digit
        # 2) "allow & don't ask again" / "always" → the persistent-allow row.
        if "don't ask" in low or "always" in low:
            for digit, label in rows:
                if "don't ask" in label.lower() or "always" in label.lower():
                    return digit
        # 3) plain "allow"/"yes" → the first (affirmative) row.
        if low.startswith(("allow", "yes")):
            return rows[0][0]
        # 4) "deny"/"no" → a row that reads as the negative.
        if low.startswith(("deny", "no")):
            for digit, label in rows:
                if label.lower().startswith("no"):
                    return digit
        return None

    @staticmethod
    def _is_deny_answer(chosen: str) -> bool:
        low = chosen.strip().lower()
        return low.startswith(("deny", "no"))

    @staticmethod
    def _first_answer_text(answers: object) -> str:
        if isinstance(answers, list) and answers:
            first = answers[0]
            if isinstance(first, dict):
                ans = first.get("answer")
                if isinstance(ans, list):
                    return str(ans[0]) if ans else ""
                return str(ans) if ans is not None else ""
            return str(first)
        if isinstance(answers, str):
            return answers
        return ""

    async def _emit_ask_user_resolved(self, request_id: str | None, decision: str) -> None:
        await self._emit(
            {
                "type": "ask_user_resolved",
                "event_type": "ask_user.resolved",
                "request_id": request_id or "",
                "decision": decision,
                "metadata": {"source": "tmux_tty_bridge"},
            }
        )

    async def _clear_pending_tty_prompts(self, reason: str) -> None:
        """Resolve + drop any unanswered TTY prompts (turn ended / answered in-terminal) so a remote
        client dismisses its stale card instead of stranding it."""
        if not self._pending_tty_prompts:
            return
        stale = list(self._pending_tty_prompts.keys())
        self._pending_tty_prompts.clear()
        for request_id in stale:
            await self._emit_ask_user_resolved(request_id, reason)

    async def _finish_hook_turn(
        self,
        *,
        content: str,
        reason: str,
        is_error: bool = False,
    ) -> None:
        content = content.strip()
        if content:
            await self._emit(
                {
                    "type": "assistant",
                    "message": {
                        "model": self._model or "interactive",
                        "content": [{"type": "text", "text": content}],
                    },
                    "metadata": {"source": "claude_hook"},
                }
            )
        result = {
            "type": "result",
            "result": content,
            "is_error": is_error,
            "stop_reason": reason,
            # BUG-2: estimate usage for a completed hook turn so message_count advances;
            # empty content keeps {} (no phantom +1).
            "modelUsage": (
                self._estimate_model_usage(
                    in_chars=len(self._turn_prompt_text),
                    out_chars=len(content),
                )
                if content
                else {}
            ),
            "metadata": {"source": "claude_hook"},
        }
        self._last_result = result
        await self._emit(result)
        # The turn ended — resolve any TTY prompt still pending (answered in-terminal or moot) so a
        # remote client dismisses its card rather than stranding it.
        await self._clear_pending_tty_prompts("turn_ended")
        self._turn_active = False
        if self._turn_done is not None:
            self._turn_done.set()

    @staticmethod
    def _stringify_hook_value(value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except TypeError:
            return str(value)

    async def _create_session(self) -> None:
        env = self._spawn_env()
        self._write_hook_settings()
        command = self._interactive_argv()
        logger.info("TmuxInteractiveTransport: starting %s", self._session_name)
        await self._run_tmux(
            "new-session",
            "-d",
            "-s",
            self._session_name,
            "-c",
            self.workspace_dir,
            "-n",
            "main",
            "-x",
            "200",
            "-y",
            "50",
            "--",
            *command,
            env=env,
        )

    async def _apply_tmux_options(self) -> None:
        for args in (
            ("set-option", "-t", self._session_name, "-g", "status", "off"),
            ("set-option", "-t", self._session_name, "-g", "history-limit", "50000"),
            ("set-option", "-t", self._session_name, "-g", "mouse", "on"),
            ("set-option", "-t", self._session_name, "-g", "remain-on-exit", "on"),
        ):
            await self._run_tmux(*args, check=False)

    def _interactive_argv(self) -> list[str]:
        cmd = ["claude"]
        if self._model:
            cmd.extend(["--model", self._model])
        if self._skip_permissions:
            cmd.extend(["--permission-mode", _DEFAULT_PERMISSION_MODE])
        if self._remote_control:
            rc_name = self._remote_control_name()
            cmd.extend(["--remote-control", rc_name])
            logger.info(
                "tmux: Remote Control enabled (name=%s) — session is also drivable from "
                "claude.ai/code + phone in parallel with the Volundr API",
                rc_name,
            )
        if self._hook_events_enabled and self._sdk_port:
            cmd.extend(["--settings", str(self._hook_settings_path)])
        if self._system_prompt:
            cmd.extend(["--append-system-prompt", self._system_prompt])
        if self._mcp_config:
            cmd.extend(["--mcp-config", self._mcp_config])
        if self._agent_teams:
            cmd.extend(["--teammate-mode", "tmux"])
        return cmd

    def _remote_control_name(self) -> str:
        """Label shown in the claude.ai/code + phone session list. Prefer the friendly
        Forge session name the broker exports; fall back to the session id."""
        raw = os.environ.get("SKULD__SESSION__NAME", "").strip() or self._forge_session_id
        return self._safe_name(raw)[:60]

    def _write_hook_settings(self) -> None:
        if not self._hook_events_enabled or not self._sdk_port:
            return

        events = list(_CLAUDE_HOOK_EVENTS)
        if self._message_display_hook_enabled:
            events.extend(_OPTIONAL_HIGH_VOLUME_HOOK_EVENTS)
        hook = {
            "type": "http",
            "url": f"http://127.0.0.1:{self._sdk_port}/api/claude/hooks",
            "timeout": 5,
        }
        settings = {"hooks": {event: [{"matcher": "", "hooks": [hook]}] for event in events}}
        self._hook_settings_path.parent.mkdir(parents=True, exist_ok=True)
        self._hook_settings_path.write_text(
            json.dumps(settings, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _spawn_env(self) -> dict[str, str]:
        env = claude_spawn_env()
        env["TERM"] = env.get("TERM") or "xterm-256color"
        if self._agent_teams:
            env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] = "1"
        _, shim_env = ensure_codex_tool_shims(
            self.workspace_dir,
            mcp_servers=self._raw_mcp_servers,
        )
        env.update(shim_env)
        return env

    async def _emit_system_init(self) -> None:
        await self._emit(
            {
                "type": "system",
                "subtype": "init",
                "session_id": self._session_name,
                "message": {"model": self._model or "interactive"},
                "build": build_info(),
                "slash_commands": list(_BUILT_IN_SLASH_COMMANDS),
                "terminal": {
                    "transport": "tmux_interactive",
                    "session_name": self._session_name,
                    "socket_path": str(self._socket_path),
                    "hook_endpoint": (
                        f"http://127.0.0.1:{self._sdk_port}/api/claude/hooks"
                        if self._sdk_port
                        else None
                    ),
                },
            }
        )

    async def _watch_panes(self) -> None:
        try:
            while self._alive:
                await self._refresh_panes(emit_events=True)
                await asyncio.sleep(self._pane_poll_interval_s)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Tmux pane watcher failed", exc_info=True)

    async def _refresh_panes(self, *, emit_events: bool) -> None:
        result = await self._run_tmux(
            "list-panes",
            "-t",
            self._session_name,
            "-F",
            (
                "#{pane_id}\t#{pane_index}\t#{window_name}\t#{pane_active}\t"
                "#{pane_current_command}\t#{pane_width}\t#{pane_height}\t"
                "#{cursor_x}\t#{cursor_y}"
            ),
            check=False,
        )
        if result.returncode != 0:
            self._alive = False
            return

        seen: set[str] = set()
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 5:
                continue
            pane_id, pane_index, window_name, pane_active, current_command = parts[:5]
            width = self._coerce_int(parts[5] if len(parts) > 5 else None, 200)
            height = self._coerce_int(parts[6] if len(parts) > 6 else None, 50)
            cursor_x = self._coerce_int(parts[7] if len(parts) > 7 else None, 0)
            cursor_y = self._coerce_int(parts[8] if len(parts) > 8 else None, 0)
            seen.add(pane_id)
            pane = _PaneState(
                pane_id=pane_id,
                pane_index=pane_index,
                window_name=window_name,
                active=pane_active == "1",
                current_command=current_command,
                width=width,
                height=height,
                cursor_x=cursor_x,
                cursor_y=cursor_y,
                log_path=self._pane_log_path(pane_id),
            )
            is_new = pane_id not in self._panes
            self._panes[pane_id] = pane
            if is_new:
                await self._start_pipe_for_pane(pane)
                if emit_events:
                    await self._emit_pane_opened(pane)

        for pane_id in set(self._panes) - seen:
            pane = self._panes.pop(pane_id)
            task = self._tail_tasks.pop(pane_id, None)
            if task is not None:
                task.cancel()
            frame_task = self._frame_tasks.pop(pane_id, None)
            if frame_task is not None:
                frame_task.cancel()
            self._last_frame_signature.pop(pane_id, None)
            self._pane_sequences.pop(pane_id, None)
            if emit_events:
                await self._emit(
                    {
                        "type": "terminal_pane_closed",
                        "pane_id": pane_id,
                        "window_name": pane.window_name,
                    }
                )

    async def _start_pipe_for_pane(self, pane: _PaneState) -> None:
        pane.log_path.parent.mkdir(parents=True, exist_ok=True)
        pane.log_path.touch(exist_ok=True)
        await self._run_tmux("pipe-pane", "-t", pane.pane_id, check=False)
        await self._run_tmux(
            "pipe-pane",
            "-t",
            pane.pane_id,
            f"cat >> {shlex.quote(str(pane.log_path))}",
            check=False,
        )
        await self._emit_pane_frame(pane, event_type="terminal_snapshot", force=True)
        task = asyncio.create_task(
            self._tail_pane_log(pane),
            name=f"tmux-pane-tail-{self._session_name}-{pane.pane_id}",
        )
        self._tail_tasks[pane.pane_id] = task

    async def _tail_pane_log(self, pane: _PaneState) -> None:
        try:
            with pane.log_path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                while self._alive and pane.pane_id in self._panes:
                    chunk = handle.read(8192)
                    if not chunk:
                        await asyncio.sleep(0.1)
                        continue
                    await self._handle_pane_output(pane, chunk)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Pane tail failed for %s", pane.pane_id, exc_info=True)

    async def _handle_pane_output(self, pane: _PaneState, chunk: bytes | str) -> None:
        text = chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else chunk
        if not text:
            return
        if self._turn_active:
            self._turn_last_output_at = time.monotonic()
        if self._emit_raw_terminal_output:
            await self._emit(
                {
                    "type": "terminal_output",
                    "event_type": "terminal.output.raw",
                    "pane_id": pane.pane_id,
                    "pane_index": pane.pane_index,
                    "window_name": pane.window_name,
                    "data": text,
                    "encoding": "utf-8",
                    "stream": "stdout",
                    "raw": True,
                    "ts": time.time(),
                }
            )
        self._schedule_pane_frame(pane)

    def _schedule_pane_frame(self, pane: _PaneState) -> None:
        if pane.pane_id in self._frame_tasks and not self._frame_tasks[pane.pane_id].done():
            return

        async def _delayed_emit() -> None:
            try:
                await asyncio.sleep(self._frame_interval_s)
                latest = self._panes.get(pane.pane_id, pane)
                await self._emit_pane_frame(latest, event_type="terminal_frame")
            finally:
                self._frame_tasks.pop(pane.pane_id, None)

        self._frame_tasks[pane.pane_id] = asyncio.create_task(
            _delayed_emit(),
            name=f"tmux-pane-frame-{self._session_name}-{pane.pane_id}",
        )

    async def _emit_pane_frame(
        self,
        pane: _PaneState,
        *,
        event_type: str,
        force: bool = False,
    ) -> dict | None:
        frame = await self._capture_pane_frame(pane)
        if frame is None:
            return None
        signature = frame["text"]
        if not force and self._last_frame_signature.get(pane.pane_id) == signature:
            return frame
        self._last_frame_signature[pane.pane_id] = signature
        await self._emit(frame | {"type": event_type})
        if self._turn_active and pane.active and not self._hook_events_enabled:
            await self._maybe_emit_clean_turn_delta(frame["rows"])
        return frame

    async def _capture_pane_frame(self, pane: _PaneState) -> dict | None:
        start = f"-{max(pane.height - 1, 0)}"
        snapshot = await self._run_tmux(
            "capture-pane",
            "-p",
            "-S",
            start,
            "-t",
            pane.pane_id,
            check=False,
        )
        if snapshot.returncode != 0:
            return None
        rows = self._normalize_terminal_rows(snapshot.stdout)
        seq = self._pane_sequences.get(pane.pane_id, 0) + 1
        self._pane_sequences[pane.pane_id] = seq
        return {
            "event_type": "terminal.frame",
            "pane_id": pane.pane_id,
            "pane_index": pane.pane_index,
            "window_name": pane.window_name,
            "active": pane.active,
            "current_command": pane.current_command,
            "cols": pane.width,
            "rows_count": pane.height,
            "cursor": {"x": pane.cursor_x, "y": pane.cursor_y},
            "rows": rows,
            "text": "\n".join(rows).rstrip(),
            "seq": seq,
            "encoding": "utf-8",
            "raw": False,
            "ts": time.time(),
        }

    async def _emit_synthetic_delta(self, text: str) -> None:
        self._turn_last_output_at = time.monotonic()
        if not self._turn_stream_started:
            self._turn_stream_started = True
            await self._emit(
                {
                    "type": "assistant",
                    "message": {
                        "model": self._model or "interactive",
                        "content": [],
                    },
                    "metadata": {"source": "tmux_interactive"},
                }
            )
            await self._emit(
                {
                    "type": "content_block_start",
                    "content_block": {"type": "text"},
                    "metadata": {"source": "tmux_interactive"},
                }
            )
        self._turn_buffer.append(text)
        await self._emit(
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": text},
                "metadata": {"source": "tmux_interactive"},
            }
        )

    async def _maybe_emit_clean_turn_delta(self, rows: list[str]) -> None:
        clean = self._extract_assistant_response(rows)
        if not clean:
            return
        previous = self._turn_last_clean_text
        self._turn_last_clean_text = clean
        if clean == previous:
            return
        if not previous:
            delta = clean
        elif clean.startswith(previous):
            delta = clean[len(previous) :]
        else:
            prefix_len = self._common_prefix_len(previous, clean)
            # Terminal reflow can rewrite visible rows. Only stream the suffix
            # when the new frame mostly preserves what the browser already saw;
            # the final result event still replaces the message with clean text.
            if prefix_len < max(12, int(len(previous) * 0.8)):
                return
            delta = clean[prefix_len:]
        if delta:
            await self._emit_synthetic_delta(delta)

    async def _watch_turn_completion(self, done: asyncio.Event) -> None:
        try:
            while not done.is_set():
                now = time.monotonic()
                if self._hook_events_enabled:
                    if now - self._turn_started_at >= self._turn_max_seconds:
                        await self._finish_hook_turn(
                            content="",
                            reason="timeout",
                            is_error=True,
                        )
                        return
                    await asyncio.sleep(0.2)
                    continue
                if self._turn_last_output_at is not None:
                    if now - self._turn_last_output_at >= self._turn_idle_timeout_s:
                        await self._finish_synthetic_turn(reason="terminal_idle")
                        return
                elif now - self._turn_started_at >= self._turn_no_output_timeout_s:
                    await self._finish_synthetic_turn(reason="no_terminal_output")
                    return
                if now - self._turn_started_at >= self._turn_max_seconds:
                    await self._finish_synthetic_turn(reason="timeout", is_error=True)
                    return
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            raise

    def _estimate_model_usage(self, *, in_chars: int, out_chars: int) -> dict:
        """BUG-2: best-effort token estimate so a COMPLETED tmux turn advances
        ``message_count`` via the broker's ``/usage`` path (which early-returns on an empty
        ``modelUsage``). ``outputTokens`` is the load-bearing ``> 0`` value; ``inputTokens``
        is best-effort and never gates the ``+1``. Mirrors the Grok transport's estimate."""
        chars_per_token = 4
        model_id = self._model or "interactive"
        return {
            model_id: {
                "inputTokens": max(1, in_chars // chars_per_token),
                "outputTokens": max(1, out_chars // chars_per_token),
                "cacheReadInputTokens": 0,
                "cacheCreationInputTokens": 0,
            }
        }

    async def _finish_synthetic_turn(self, *, reason: str, is_error: bool = False) -> None:
        if not self._turn_active:
            return
        content = (await self._capture_turn_text()) or self._turn_last_clean_text
        if not content:
            content = "".join(self._turn_buffer).strip()
        if self._turn_stream_started:
            await self._emit(
                {
                    "type": "content_block_stop",
                    "metadata": {"source": "tmux_interactive"},
                }
            )
        result = {
            "type": "result",
            "result": content,
            "is_error": is_error,
            "stop_reason": reason,
            # BUG-2: empty content -> {} keeps _report_usage's early-return (no phantom +1);
            # real content -> estimated usage so a completed tmux turn advances message_count.
            "modelUsage": (
                self._estimate_model_usage(
                    in_chars=len(self._turn_prompt_text),
                    out_chars=len(content),
                )
                if content
                else {}
            ),
            "metadata": {"source": "tmux_interactive"},
        }
        self._last_result = result
        await self._emit(result)
        # The turn ended — resolve any TTY prompt still pending (answered in-terminal or moot) so a
        # remote client dismisses its card rather than stranding it.
        await self._clear_pending_tty_prompts("turn_ended")
        self._turn_active = False
        if self._turn_done is not None:
            self._turn_done.set()

    async def _capture_turn_text(self) -> str:
        pane = None
        for candidate in self._panes.values():
            if candidate.active:
                pane = candidate
                break
        if pane is None and self._panes:
            pane = next(iter(self._panes.values()))
        if pane is None:
            return ""
        frame = await self._emit_pane_frame(pane, event_type="terminal_frame", force=True)
        if not frame:
            return ""
        return self._extract_assistant_response(frame["rows"])

    async def _paste_text(
        self,
        text: str,
        *,
        enter: bool,
        pane_id: str | None = None,
    ) -> None:
        text = text.replace("\r", "")
        target = self._target_pane(pane_id)
        buffer_name = f"skuld-{uuid.uuid4().hex}"
        input_path = self._runtime_dir / f"input-{uuid.uuid4().hex}.txt"
        input_path.write_text(text, encoding="utf-8")
        try:
            await self._run_tmux("load-buffer", "-b", buffer_name, str(input_path))
            await self._run_tmux("paste-buffer", "-p", "-t", target, "-b", buffer_name)
        finally:
            await self._run_tmux("delete-buffer", "-b", buffer_name, check=False)
            with suppress(OSError):
                input_path.unlink()
        await self._emit(
            {
                "type": "terminal_input_sent",
                "event_type": "terminal.input",
                "pane_id": target,
                "bytes": len(text.encode("utf-8")),
                "enter": enter,
            }
        )
        if enter:
            await self._send_key("Enter", pane_id=target)

    async def _send_key(self, key: str, *, pane_id: str | None = None) -> None:
        target = self._target_pane(pane_id)
        await self._run_tmux("send-keys", "-t", target, key)
        await self._emit(
            {
                "type": "terminal_key_sent",
                "event_type": "terminal.key",
                "pane_id": target,
                "key": key,
            }
        )

    async def _send_slash_command(
        self,
        command: str,
        *,
        arguments: str = "",
        pane_id: str | None = None,
    ) -> None:
        text = command.strip()
        if not text:
            return
        if not text.startswith("/"):
            text = f"/{text}"
        arguments = arguments.strip()
        if arguments:
            text = f"{text} {arguments}"
        await self._paste_text(text, enter=True, pane_id=pane_id)
        await self._emit(
            {
                "type": "slash_command_sent",
                "event_type": "slash.command.sent",
                "command": text.split(maxsplit=1)[0],
                "input": text,
                "pane_id": self._target_pane(pane_id),
            }
        )

    async def _discover_slash_commands_from_terminal(self) -> list[dict]:
        target = self._target_pane(None)
        captures: list[str] = []
        try:
            await self._send_key_raw("Escape", pane_id=target)
            await asyncio.sleep(0.05)
            await self._send_key_raw("C-u", pane_id=target)
            await asyncio.sleep(0.05)
            await self._send_key_raw("/", pane_id=target)
            await asyncio.sleep(0.25)
            for _ in range(12):
                result = await self._run_tmux(
                    "capture-pane",
                    "-t",
                    target,
                    "-p",
                    "-S",
                    "-200",
                )
                captures.append(result.stdout)
                for _ in range(10):
                    await self._send_key_raw("Down", pane_id=target)
                await asyncio.sleep(0.05)
        finally:
            with suppress(Exception):
                await self._send_key_raw("Escape", pane_id=target)
                await self._send_key_raw("C-u", pane_id=target)

        commands: list[dict] = []
        seen: set[str] = set()
        for capture in captures:
            for item in self._parse_slash_command_menu(capture):
                if item["name"] in seen:
                    continue
                seen.add(item["name"])
                commands.append(item)
        return commands

    async def _send_key_raw(self, key: str, *, pane_id: str | None = None) -> None:
        await self._run_tmux("send-keys", "-t", self._target_pane(pane_id), key)

    async def _resize_pane(self, *, cols: int, rows: int, pane_id: str | None = None) -> None:
        target = self._target_pane(pane_id)
        await self._run_tmux("resize-pane", "-t", target, "-x", str(cols), "-y", str(rows))
        await self._emit(
            {
                "type": "terminal_resized",
                "event_type": "terminal.resize",
                "pane_id": target,
                "cols": cols,
                "rows": rows,
            }
        )

    async def _emit_pane_opened(self, pane: _PaneState) -> None:
        await self._emit(
            {
                "type": "terminal_pane_opened",
                "event_type": "terminal.pane.opened",
                "pane_id": pane.pane_id,
                "pane_index": pane.pane_index,
                "window_name": pane.window_name,
                "active": pane.active,
                "current_command": pane.current_command,
                "log_path": str(pane.log_path),
            }
        )

    async def _has_session(self) -> bool:
        result = await self._run_tmux("has-session", "-t", self._session_name, check=False)
        return result.returncode == 0

    async def _run_tmux(
        self,
        *args: str,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> _TmuxResult:
        cmd = [self._tmux_bin(), "-S", str(self._socket_path), *args]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout_b, stderr_b = await process.communicate()
        result = _TmuxResult(
            returncode=process.returncode or 0,
            stdout=stdout_b.decode("utf-8", errors="replace"),
            stderr=stderr_b.decode("utf-8", errors="replace"),
        )
        if check and result.returncode != 0:
            raise TmuxCommandError(
                f"tmux {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
            )
        return result

    def _target_pane(self, pane_id: str | None = None) -> str:
        if pane_id:
            return pane_id
        for pane in self._panes.values():
            if pane.active:
                return pane.pane_id
        if self._panes:
            return next(iter(self._panes.values())).pane_id
        return f"{self._session_name}:main"

    def _pane_log_path(self, pane_id: str) -> Path:
        safe = self._safe_name(pane_id.lstrip("%") or "pane")
        return self._pane_log_dir / f"{safe}.log"

    def _tmux_binary_exists(self) -> bool:
        return shutil.which(self._tmux_bin()) is not None

    @staticmethod
    def _tmux_bin() -> str:
        return os.environ.get("SKULD__TMUX_BIN") or "tmux"

    @staticmethod
    def _normalize_key(key: str) -> str:
        stripped = key.strip()
        return _KEY_ALIASES.get(stripped.lower(), stripped)

    @staticmethod
    def _safe_name(value: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
        return safe.strip("-") or "skuld-interactive"

    @staticmethod
    def _coerce_str(value: object) -> str:
        return value if isinstance(value, str) else ""

    @staticmethod
    def _coerce_int(value: object, default: int) -> int:
        try:
            return int(str(value or "").strip())
        except ValueError:
            return default

    @staticmethod
    def _bool_env(name: str, default: bool) -> bool:
        raw = os.environ.get(name, "")
        if not raw:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _clean_terminal_text(text: str) -> str:
        cleaned = _ANSI_RE.sub("", text)
        cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned

    @classmethod
    def _parse_slash_command_menu(cls, text: str) -> list[dict]:
        rows = cls._normalize_terminal_rows(text)
        commands: list[dict] = []
        current: dict | None = None
        for row in rows:
            match = _SLASH_COMMAND_ROW_RE.match(row.strip())
            if match:
                name = match.group(1)
                description = match.group(2).strip()
                current = cls._normalize_slash_command_item(
                    {
                        "name": name,
                        "description": description,
                        "kind": cls._infer_slash_command_kind(description),
                    },
                    source="tmux_autocomplete",
                )
                commands.append(current)
                continue
            if current is None:
                continue
            continuation = row.strip()
            if (
                not continuation
                or continuation.startswith("/")
                or cls._is_terminal_chrome_row(continuation)
            ):
                continue
            current["description"] = f"{current['description']} {continuation}".strip()
        return commands

    @classmethod
    def _normalize_slash_command_items(
        cls,
        items: list[dict] | list[str],
        *,
        source: str,
    ) -> list[dict]:
        normalized: list[dict] = []
        seen: set[str] = set()
        for item in items:
            command = cls._normalize_slash_command_item(item, source=source)
            if not command or command["name"] in seen:
                continue
            seen.add(command["name"])
            normalized.append(command)
        return normalized

    @classmethod
    def _normalize_slash_command_item(cls, item: dict | str, *, source: str) -> dict:
        if isinstance(item, str):
            raw_name = item
            description = ""
            kind = "command"
        else:
            raw_name = cls._coerce_str(item.get("name")) or cls._coerce_str(item.get("command"))
            description = cls._coerce_str(item.get("description"))
            kind = cls._coerce_str(item.get("kind")) or cls._infer_slash_command_kind(description)
        raw_name = raw_name.strip()
        if not raw_name:
            return {}
        name = raw_name if raw_name.startswith("/") else f"/{raw_name}"
        return {
            "name": name,
            "command": name[1:],
            "description": description.strip(),
            "kind": kind or "command",
            "source": source,
        }

    @staticmethod
    def _infer_slash_command_kind(description: str) -> str:
        lowered = description.lower()
        if "dynamic workflow" in lowered:
            return "workflow"
        if "[skill]" in lowered:
            return "skill"
        return "command"

    @classmethod
    def _normalize_terminal_rows(cls, text: str) -> list[str]:
        cleaned = cls._clean_terminal_text(text)
        rows = [row.rstrip() for row in cleaned.splitlines()]
        while rows and not rows[-1].strip():
            rows.pop()
        return rows

    @classmethod
    def _extract_assistant_response(cls, rows: list[str]) -> str:
        normalized = [row.rstrip() for row in rows]
        prompt_indices = [idx for idx, row in enumerate(normalized) if cls._is_prompt_row(row)]
        if prompt_indices:
            prompt_idx = prompt_indices[-1]
            if cls._is_empty_prompt_row(normalized[prompt_idx]) and len(prompt_indices) >= 2:
                prompt_idx = prompt_indices[-2]
            end_idx = next(
                (
                    idx
                    for idx in prompt_indices
                    if idx > prompt_idx and cls._is_empty_prompt_row(normalized[idx])
                ),
                len(normalized),
            )
            candidate_rows = normalized[prompt_idx + 1 : end_idx]
        else:
            candidate_rows = normalized

        cleaned_rows: list[str] = []
        for row in candidate_rows:
            row = cls._clean_response_row(row)
            if row is None:
                continue
            cleaned_rows.append(row)

        while cleaned_rows and not cleaned_rows[0].strip():
            cleaned_rows.pop(0)
        while cleaned_rows and not cleaned_rows[-1].strip():
            cleaned_rows.pop()
        return cls._collapse_response_rows(cleaned_rows)

    @staticmethod
    def _is_prompt_row(row: str) -> bool:
        return row.lstrip().startswith("❯")

    @staticmethod
    def _is_empty_prompt_row(row: str) -> bool:
        stripped = row.strip().replace("\u00a0", " ")
        if not stripped.startswith("❯"):
            return False
        return not stripped[1:].strip()

    @classmethod
    def _clean_response_row(cls, row: str) -> str | None:
        stripped = row.strip()
        if not stripped:
            return ""
        if cls._is_terminal_chrome_row(stripped):
            return None
        for prefix in ("●", "⏺"):
            if stripped.startswith(prefix):
                return stripped[len(prefix) :].lstrip()
        # Claude Code renders continuation lines with two leading spaces. Keep
        # meaningful indentation from code blocks, but drop one UI continuation
        # level for prose rows.
        if row.startswith("  ") and not row.startswith("    "):
            return row[2:].rstrip()
        return row.rstrip()

    @staticmethod
    def _is_terminal_chrome_row(stripped: str) -> bool:
        if stripped in {"?", "│", "╭", "╰"}:
            return True
        if set(stripped) <= {"─"}:
            return True
        if "for shortcuts" in stripped:
            return True
        if "esc to interrupt" in stripped:
            return True
        if "Esc to cancel" in stripped:
            return True
        if stripped == "Bash command":
            return True
        if stripped == "Do you want to proceed?":
            return True
        if re.fullmatch(r"[1-9]\.\s+.*", stripped):
            return True
        if stripped.startswith("⎿ Tip:"):
            return True
        if stripped.startswith("⚠ ") and "/doctor" in stripped:
            return True
        if stripped.startswith("◉ ") and "/effort" in stripped:
            return True
        if re.fullmatch(r"[✻✽✶✢*·⠂⠐⠠⠄\s]+", stripped):
            return True
        spinner_prefixes = ("✻", "✽", "✶", "✢", "*", "·")
        spinner_words = (
            "Cogitated",
            "Combobulating",
            "Gesticulating",
            "Incubating",
            "Lollygagging",
            "Synthesizing",
            "Thinking",
        )
        if stripped.startswith(spinner_prefixes) and any(
            word in stripped for word in spinner_words
        ):
            return True
        if "thinking with" in stripped and "effort" in stripped:
            return True
        if re.search(r"(?:[↑↓]\s*)?\d+(?:\.\d+)?k?\s+tokens\b", stripped):
            return True
        if re.search(r"\b(?:Thought|Cogitated) for \d+s\b", stripped, re.IGNORECASE):
            return True
        if stripped.startswith("You're now using usage credits"):
            return True
        if stripped.startswith("Your session limit resets"):
            return True
        return False

    @staticmethod
    def _collapse_response_rows(rows: list[str]) -> str:
        text = "\n".join(rows)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _common_prefix_len(left: str, right: str) -> int:
        limit = min(len(left), len(right))
        for idx in range(limit):
            if left[idx] != right[idx]:
                return idx
        return limit

    @staticmethod
    def _float_env(name: str, explicit: float | None, default: float) -> float:
        if explicit is not None:
            return explicit
        raw = os.environ.get(name, "")
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            return default
